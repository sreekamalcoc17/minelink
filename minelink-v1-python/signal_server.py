"""
MineLink Signal Server (Optional)
A lightweight signaling server for automatic peer discovery.

Deploy this on a VPS/Cloud server with a public IP.
Users can then create "networks" and discover each other automatically.
"""

import json
import socket
import threading
import time
import logging
import argparse
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, Tuple
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class PeerInfo:
    """Information about a registered peer."""
    peer_id: str
    display_name: str
    public_ip: str
    public_port: int
    local_ip: str = ""
    local_port: int = 0
    last_seen: float = 0
    network_id: Optional[str] = None


@dataclass 
class Network:
    """A virtual network (room) that peers can join."""
    network_id: str
    password_hash: str  # SHA256 hash of password
    created_at: float
    owner_peer_id: str
    peers: Dict[str, PeerInfo] = field(default_factory=dict)
    
    def is_peer_active(self, peer_id: str, timeout: float = 60) -> bool:
        """Check if a peer is still active."""
        if peer_id not in self.peers:
            return False
        return time.time() - self.peers[peer_id].last_seen < timeout


class SignalServer:
    """
    Signal Server for MineLink.
    
    Provides two functionalities:
    1. HTTP API for network/peer management
    2. UDP endpoint for NAT traversal (STUN-like)
    """
    
    def __init__(self, http_port: int = 8080, udp_port: int = 3478):
        self.http_port = http_port
        self.udp_port = udp_port
        
        # Storage
        self.networks: Dict[str, Network] = {}
        self.peers: Dict[str, PeerInfo] = {}  # peer_id -> info
        
        # Lock
        self._lock = threading.RLock()
        
        # Servers
        self._http_server: Optional[HTTPServer] = None
        self._udp_socket: Optional[socket.socket] = None
        self._running = False
    
    def start(self):
        """Start the signal server."""
        self._running = True
        
        # Start HTTP server
        self._start_http()
        
        # Start UDP server
        self._start_udp()
        
        # Start cleanup thread
        threading.Thread(target=self._cleanup_loop, daemon=True).start()
        
        logger.info(f"Signal Server started")
        logger.info(f"  HTTP: http://0.0.0.0:{self.http_port}")
        logger.info(f"  UDP:  udp://0.0.0.0:{self.udp_port}")
    
    def _start_http(self):
        """Start the HTTP server."""
        server = self
        
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                logger.debug(f"HTTP: {args[0]}")
            
            def send_json(self, data: dict, status: int = 200):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            
            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()
            
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                query = urllib.parse.parse_qs(parsed.query)
                
                if path == "/status":
                    self.send_json({
                        "status": "ok",
                        "networks": len(server.networks),
                        "peers": len(server.peers)
                    })
                
                elif path.startswith("/network/"):
                    network_id = path.split("/")[2]
                    result = server.get_network_peers(network_id)
                    if result is None:
                        self.send_json({"error": "Network not found"}, 404)
                    else:
                        self.send_json({"peers": result})
                
                elif path.startswith("/peer/"):
                    peer_id = path.split("/")[2]
                    result = server.get_peer(peer_id)
                    if result is None:
                        self.send_json({"error": "Peer not found"}, 404)
                    else:
                        self.send_json(result)
                
                else:
                    self.send_json({"error": "Not found"}, 404)
            
            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                
                # Read body
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                
                try:
                    data = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    self.send_json({"error": "Invalid JSON"}, 400)
                    return
                
                if path == "/network/create":
                    result = server.create_network(
                        network_id=data.get("network_id"),
                        password=data.get("password", ""),
                        owner_peer_id=data.get("peer_id")
                    )
                    if "error" in result:
                        self.send_json(result, 400)
                    else:
                        self.send_json(result)
                
                elif path == "/network/join":
                    result = server.join_network(
                        network_id=data.get("network_id"),
                        password=data.get("password", ""),
                        peer_info=data.get("peer")
                    )
                    if "error" in result:
                        self.send_json(result, 400)
                    else:
                        self.send_json(result)
                
                elif path == "/peer/register":
                    # Get client's public IP from connection
                    client_ip = self.client_address[0]
                    result = server.register_peer(
                        peer_id=data.get("peer_id"),
                        display_name=data.get("display_name", ""),
                        public_ip=client_ip,
                        public_port=data.get("public_port", 0),
                        local_ip=data.get("local_ip", ""),
                        local_port=data.get("local_port", 0)
                    )
                    self.send_json(result)
                
                elif path == "/peer/heartbeat":
                    result = server.heartbeat(data.get("peer_id"))
                    self.send_json(result)
                
                else:
                    self.send_json({"error": "Not found"}, 404)
        
        self._http_server = HTTPServer(("0.0.0.0", self.http_port), Handler)
        
        def serve():
            while self._running:
                self._http_server.handle_request()
        
        threading.Thread(target=serve, daemon=True).start()
    
    def _start_udp(self):
        """Start the UDP server for NAT traversal."""
        self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_socket.bind(("0.0.0.0", self.udp_port))
        self._udp_socket.settimeout(1.0)
        
        def serve():
            while self._running:
                try:
                    data, addr = self._udp_socket.recvfrom(1024)
                    self._handle_udp(data, addr)
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.warning(f"UDP error: {e}")
        
        threading.Thread(target=serve, daemon=True).start()
    
    def _handle_udp(self, data: bytes, addr: Tuple[str, int]):
        """Handle UDP packet (STUN-like response)."""
        # Simple echo of the client's public address
        response = json.dumps({
            "public_ip": addr[0],
            "public_port": addr[1]
        }).encode()
        self._udp_socket.sendto(response, addr)
    
    def _cleanup_loop(self):
        """Periodically remove stale peers."""
        while self._running:
            time.sleep(30)
            self._cleanup_stale_peers()
    
    def _cleanup_stale_peers(self, timeout: float = 120):
        """Remove peers not seen for `timeout` seconds."""
        now = time.time()
        
        with self._lock:
            # Cleanup global peers
            stale = [
                pid for pid, p in self.peers.items()
                if now - p.last_seen > timeout
            ]
            for pid in stale:
                del self.peers[pid]
                logger.info(f"Removed stale peer: {pid}")
            
            # Cleanup network peers
            for network in self.networks.values():
                stale = [
                    pid for pid, p in network.peers.items()
                    if now - p.last_seen > timeout
                ]
                for pid in stale:
                    del network.peers[pid]
    
    # ===== API Methods =====
    
    def create_network(
        self,
        network_id: str,
        password: str,
        owner_peer_id: str
    ) -> dict:
        """Create a new network."""
        import hashlib
        
        if not network_id or not owner_peer_id:
            return {"error": "Missing required fields"}
        
        with self._lock:
            if network_id in self.networks:
                return {"error": "Network already exists"}
            
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            
            network = Network(
                network_id=network_id,
                password_hash=password_hash,
                created_at=time.time(),
                owner_peer_id=owner_peer_id
            )
            self.networks[network_id] = network
            
            logger.info(f"Network created: {network_id} by {owner_peer_id}")
            return {"success": True, "network_id": network_id}
    
    def join_network(
        self,
        network_id: str,
        password: str,
        peer_info: dict
    ) -> dict:
        """Join an existing network."""
        import hashlib
        
        if not network_id or not peer_info:
            return {"error": "Missing required fields"}
        
        with self._lock:
            if network_id not in self.networks:
                return {"error": "Network not found"}
            
            network = self.networks[network_id]
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            
            if network.password_hash != password_hash:
                return {"error": "Invalid password"}
            
            # Add peer to network
            peer = PeerInfo(
                peer_id=peer_info.get("peer_id"),
                display_name=peer_info.get("display_name", ""),
                public_ip=peer_info.get("public_ip", ""),
                public_port=peer_info.get("public_port", 0),
                local_ip=peer_info.get("local_ip", ""),
                local_port=peer_info.get("local_port", 0),
                last_seen=time.time(),
                network_id=network_id
            )
            network.peers[peer.peer_id] = peer
            
            logger.info(f"Peer {peer.peer_id} joined network {network_id}")
            
            # Return list of other peers
            other_peers = [
                asdict(p) for pid, p in network.peers.items()
                if pid != peer.peer_id and time.time() - p.last_seen < 60
            ]
            
            return {
                "success": True,
                "peers": other_peers
            }
    
    def get_network_peers(self, network_id: str) -> Optional[list]:
        """Get active peers in a network."""
        with self._lock:
            if network_id not in self.networks:
                return None
            
            network = self.networks[network_id]
            now = time.time()
            
            return [
                asdict(p) for p in network.peers.values()
                if now - p.last_seen < 60
            ]
    
    def register_peer(
        self,
        peer_id: str,
        display_name: str,
        public_ip: str,
        public_port: int,
        local_ip: str = "",
        local_port: int = 0
    ) -> dict:
        """Register a peer globally (for direct connection without networks)."""
        with self._lock:
            peer = PeerInfo(
                peer_id=peer_id,
                display_name=display_name,
                public_ip=public_ip,
                public_port=public_port,
                local_ip=local_ip,
                local_port=local_port,
                last_seen=time.time()
            )
            self.peers[peer_id] = peer
            
            return {
                "success": True,
                "public_ip": public_ip,
                "public_port": public_port
            }
    
    def get_peer(self, peer_id: str) -> Optional[dict]:
        """Get peer info."""
        with self._lock:
            if peer_id in self.peers:
                peer = self.peers[peer_id]
                if time.time() - peer.last_seen < 60:
                    return asdict(peer)
            return None
    
    def heartbeat(self, peer_id: str) -> dict:
        """Update peer's last seen time."""
        with self._lock:
            if peer_id in self.peers:
                self.peers[peer_id].last_seen = time.time()
                return {"success": True}
            
            # Check networks
            for network in self.networks.values():
                if peer_id in network.peers:
                    network.peers[peer_id].last_seen = time.time()
                    return {"success": True}
            
            return {"error": "Peer not found"}
    
    def stop(self):
        """Stop the signal server."""
        self._running = False
        
        if self._http_server:
            self._http_server.shutdown()
        
        if self._udp_socket:
            self._udp_socket.close()


def main():
    parser = argparse.ArgumentParser(description="MineLink Signal Server")
    parser.add_argument("--http-port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--udp-port", type=int, default=3478, help="UDP port")
    args = parser.parse_args()
    
    server = SignalServer(
        http_port=args.http_port,
        udp_port=args.udp_port
    )
    
    try:
        server.start()
        print("\nSignal Server running. Press Ctrl+C to stop.\n")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()


if __name__ == "__main__":
    main()
