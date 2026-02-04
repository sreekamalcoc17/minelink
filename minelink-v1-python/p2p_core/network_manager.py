"""
MineLink Network Manager
Manages the virtual network (Hub-and-Spoke topology).
Handles multiple peer connections and acts as a "Virtual Switch".
"""

import json
import socket
import threading
import logging
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Callable, Tuple
from enum import Enum
import time

from .stun import get_public_address, get_public_address_with_socket, STUNResult
from .protocol import ConnectionInfo, generate_peer_id, generate_session_id
from .transport import ReliableTransport, TCPBridge, PeerConnection

logger = logging.getLogger(__name__)


class NetworkMode(Enum):
    """Operating mode of the network manager."""
    HOST = "host"      # Server mode: Accept connections, bridge to Minecraft
    CLIENT = "client"  # Client mode: Connect to host, bridge local port


@dataclass
class NetworkConfig:
    """Network configuration saved to disk."""
    my_peer_id: str = ""
    my_display_name: str = ""
    saved_peers: Dict[str, dict] = field(default_factory=dict)  # peer_id -> ConnectionInfo
    last_session_id: int = 0
    
    def save(self, path: Path):
        """Save config to JSON file."""
        data = asdict(self)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    
    @classmethod
    def load(cls, path: Path) -> 'NetworkConfig':
        """Load config from JSON file."""
        if not path.exists():
            return cls()
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            return cls(**data)
        except Exception as e:
            logger.warning(f"Failed to load config: {e}")
            return cls()


@dataclass
class NetworkStats:
    """Network statistics."""
    uptime: float = 0
    peers_connected: int = 0
    total_bytes_sent: int = 0
    total_bytes_recv: int = 0
    packets_lost: int = 0


class NetworkManager:
    """
    Manages the MineLink virtual network.
    
    In HOST mode:
        - Listens for incoming peer connections
        - Bridges traffic between peers and local Minecraft server
        - Acts as the central "Hub"
    
    In CLIENT mode:
        - Connects to a host
        - Bridges local TCP port to the host's Minecraft server
    """
    
    def __init__(
        self,
        config_dir: Optional[Path] = None,
        mode: NetworkMode = NetworkMode.CLIENT,
        minecraft_port: int = 25565,
        on_peer_connect: Optional[Callable[[str], None]] = None,
        on_peer_disconnect: Optional[Callable[[str], None]] = None,
        on_status_change: Optional[Callable[[str], None]] = None
    ):
        """
        Initialize the network manager.
        
        Args:
            config_dir: Directory to store config files.
            mode: Operating mode (HOST or CLIENT).
            minecraft_port: Minecraft server/client port.
            on_peer_connect: Callback when peer connects.
            on_peer_disconnect: Callback when peer disconnects.
            on_status_change: Callback for status updates.
        """
        self.mode = mode
        self.minecraft_port = minecraft_port
        
        # Callbacks
        self.on_peer_connect = on_peer_connect
        self.on_peer_disconnect = on_peer_disconnect
        self.on_status_change = on_status_change
        
        # Config
        if config_dir is None:
            config_dir = Path.home() / ".minelink"
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "config.json"
        self.config = NetworkConfig.load(self.config_path)
        
        # Initialize peer ID if not set
        if not self.config.my_peer_id:
            self.config.my_peer_id = generate_peer_id("minelink")
            self.config.save(self.config_path)
        
        # Transport layer
        self.transport: Optional[ReliableTransport] = None
        self.tcp_bridge: Optional[TCPBridge] = None
        
        # State
        self._running = False
        self._public_address: Optional[STUNResult] = None
        self._connection_code: Optional[str] = None
        self._start_time: float = 0
        
        # Minecraft connection (for HOST mode)
        self._mc_socket: Optional[socket.socket] = None
        self._mc_connected = False
        
        # Thread for forwarding data
        self._forward_thread: Optional[threading.Thread] = None
        
        # Lock
        self._lock = threading.RLock()
    
    @property
    def peer_id(self) -> str:
        """Get my peer ID."""
        return self.config.my_peer_id
    
    @property
    def is_running(self) -> bool:
        """Check if network is running."""
        return self._running
    
    @property
    def public_address(self) -> Optional[Tuple[str, int]]:
        """Get my public address."""
        if self._public_address:
            return (self._public_address.public_ip, self._public_address.public_port)
        return None
    
    @property
    def connection_code(self) -> Optional[str]:
        """Get my connection code for sharing."""
        return self._connection_code
    
    def get_stats(self) -> NetworkStats:
        """Get current network statistics."""
        stats = NetworkStats()
        
        if self._start_time:
            stats.uptime = time.time() - self._start_time
        
        if self.transport:
            with self.transport._lock:
                stats.peers_connected = sum(
                    1 for p in self.transport.peers.values() if p.connected
                )
                for peer in self.transport.peers.values():
                    stats.total_bytes_sent += peer.bytes_sent
                    stats.total_bytes_recv += peer.bytes_recv
                    stats.packets_lost += peer.packets_lost
        
        return stats
    
    def get_peer_ping_ms(self, peer_id: str) -> int:
        """
        Get the ping latency to a peer in milliseconds.
        
        Args:
            peer_id: The peer to get ping for.
        
        Returns:
            Ping in ms, or -1 if not connected/unknown.
        """
        if not self.transport:
            return -1
        
        with self.transport._lock:
            if peer_id in self.transport.peers:
                peer = self.transport.peers[peer_id]
                if peer.connected:
                    # srtt is in seconds, convert to ms
                    return int(peer.srtt * 1000)
        return -1
    
    def _status(self, msg: str):
        """Update status."""
        logger.info(msg)
        if self.on_status_change:
            self.on_status_change(msg)
    
    def start(self, local_port: int = 0) -> bool:
        """
        Start the network.
        
        Args:
            local_port: Local UDP port to bind (0 for auto).
        
        Returns:
            True if started successfully.
        """
        if self._running:
            return True
        
        self._status("Starting network...")
        
        try:
            # Create transport
            self.transport = ReliableTransport(
                local_port=local_port,
                on_data=self._on_data_received,
                on_connect=self._on_peer_connected,
                on_disconnect=self._on_peer_disconnected
            )
            
            local_addr = self.transport.start()
            self._status(f"Listening on {local_addr[0]}:{local_addr[1]}")
            
            # Get public address via STUN
            self._status("Discovering public address...")
            self._public_address = get_public_address_with_socket(
                self.transport.sock,
                timeout=5.0
            )
            
            if self._public_address:
                self._status(
                    f"Public address: {self._public_address.public_ip}:"
                    f"{self._public_address.public_port}"
                )
                
                # Generate connection code
                info = ConnectionInfo(
                    peer_id=self.config.my_peer_id,
                    public_ip=self._public_address.public_ip,
                    public_port=self._public_address.public_port,
                    local_ip=self._public_address.local_ip,
                    local_port=self._public_address.local_port
                )
                self._connection_code = info.to_code()
            else:
                self._status("Warning: Could not determine public address")
            
            # Set running flag BEFORE mode-specific setup
            # (forward loop thread checks this flag)
            self._running = True
            self._start_time = time.time()
            
            # Mode-specific setup (may start threads that check _running)
            if self.mode == NetworkMode.HOST:
                self._setup_host()
            else:
                self._setup_client()
            
            self._status("Network started!")
            return True
            
        except Exception as e:
            logger.exception("Failed to start network")
            self._status(f"Failed to start: {e}")
            self.stop()
            return False
    
    def _setup_host(self):
        """Setup for HOST mode."""
        self._status("Running in HOST mode")
        # Don't connect to Minecraft immediately - wait for peer data
        # Connection will be established on-demand when data arrives from a peer
        self._status(f"Waiting for peers (will connect to MC on port {self.minecraft_port} when needed)")
    
    def _setup_client(self):
        """Setup for CLIENT mode."""
        self._status("Running in CLIENT mode")
        # We'll create TCP bridge when connecting to a host
    
    def _connect_to_minecraft(self) -> bool:
        """Connect to local Minecraft server (HOST mode)."""
        logger.info(f"[NetworkManager] Attempting to connect to Minecraft server at 127.0.0.1:{self.minecraft_port}")
        
        try:
            self._mc_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._mc_socket.settimeout(5)  # 5 second timeout for connect
            self._mc_socket.connect(("127.0.0.1", self.minecraft_port))
            self._mc_socket.settimeout(0.5)  # Short timeout for recv operations
            self._mc_connected = True
            
            logger.info(f"[NetworkManager] Successfully connected to Minecraft server!")
            
            # Start forwarding thread
            self._forward_thread = threading.Thread(
                target=self._minecraft_forward_loop,
                daemon=True
            )
            self._forward_thread.start()
            
            self._status(f"Connected to Minecraft server on port {self.minecraft_port}")
            return True
            
        except socket.error as e:
            logger.warning(f"[NetworkManager] Could not connect to Minecraft server: {e}")
            self._status(f"Note: Minecraft server not running yet ({e})")
            self._mc_connected = False
            return False
    
    def _try_reconnect_minecraft(self):
        """Try to reconnect to Minecraft server if not connected."""
        if self._mc_connected and self._mc_socket:
            return True
        
        logger.info("[NetworkManager] Attempting to reconnect to Minecraft server...")
        return self._connect_to_minecraft()
    
    def _minecraft_forward_loop(self):
        """Forward data from Minecraft server to peers."""
        
        logger.info("[NetworkManager] Starting Minecraft server forward loop")
        logger.info(f"[NetworkManager] Loop conditions: _running={self._running}, _mc_socket={self._mc_socket is not None}, _mc_connected={self._mc_connected}")
        total_bytes_forwarded = 0
        loop_iterations = 0
        
        while True:
            # Debug: Check conditions each iteration
            if not self._running:
                logger.info("[NetworkManager] Forward loop exiting: _running is False")
                break
            if not self._mc_socket:
                logger.info("[NetworkManager] Forward loop exiting: _mc_socket is None")
                break
            if not self._mc_connected:
                logger.info("[NetworkManager] Forward loop exiting: _mc_connected is False")
                break
            
            loop_iterations += 1
            
            try:
                # Socket has 0.5s timeout set, so recv will timeout and loop will continue
                try:
                    data = self._mc_socket.recv(8192)
                    logger.debug(f"[NetworkManager] recv() returned: {len(data) if data else 0} bytes")
                except socket.timeout:
                    # No data available within timeout, continue waiting
                    if loop_iterations % 10 == 0:  # Log every 5 seconds (10 * 0.5s)
                        logger.debug(f"[NetworkManager] Forward loop waiting for data... (iteration {loop_iterations})")
                    continue
                except socket.error as e:
                    # Connection error
                    logger.warning(f"[NetworkManager] Socket recv error: {e} (errno: {e.errno})")
                    self._mc_connected = False
                    break
                
                if not data:
                    logger.info("[NetworkManager] Minecraft server closed connection (recv returned empty bytes)")
                    self._mc_connected = False
                    # Send FIN to peers
                    if self.transport:
                        from .protocol import DataPayload
                        with self.transport._lock:
                            connected_peers = [pid for pid, p in self.transport.peers.items() if p.connected]
                        for peer_id in connected_peers:
                            self.transport.send(peer_id, 0, b'', DataPayload.FLAG_FIN)
                    break
                
                total_bytes_forwarded += len(data)
                logger.info(f"[NetworkManager] Received {len(data)} bytes from Minecraft server (total: {total_bytes_forwarded})")
                
                # Broadcast to all connected peers
                if self.transport:
                    with self.transport._lock:
                        connected_peers = [
                            pid for pid, p in self.transport.peers.items() if p.connected
                        ]
                    
                    if connected_peers:
                        logger.info(
                            f"[NetworkManager] Forwarding {len(data)} bytes from MC server "
                            f"to {len(connected_peers)} peer(s)"
                        )
                        for peer_id in connected_peers:
                            self.transport.send(peer_id, 0, data)
                    else:
                        logger.debug(
                            f"[NetworkManager] Received {len(data)} bytes from MC server "
                            f"but no peers connected"
                        )
                                    
            except Exception as e:
                logger.warning(f"[NetworkManager] Error in forward loop: {e}", exc_info=True)
                # Don't break on transient errors, just log and continue
                continue
        
        logger.info(f"[NetworkManager] Minecraft forward loop ended after {loop_iterations} iterations (total: {total_bytes_forwarded} bytes)")
        self._status("Minecraft connection closed")
    
    def stop(self):
        """Stop the network."""
        self._running = False
        
        if self._mc_socket:
            try:
                self._mc_socket.close()
            except:
                pass
            self._mc_socket = None
        
        if self.tcp_bridge:
            self.tcp_bridge.stop()
            self.tcp_bridge = None
        
        if self.transport:
            self.transport.stop()
            self.transport = None
        
        self._public_address = None
        self._connection_code = None
        
        self._status("Network stopped")
    
    def add_peer_from_code(self, code: str) -> Optional[str]:
        """
        Add a peer using their connection code.
        
        Args:
            code: Base64 connection code.
        
        Returns:
            Peer ID if successful, None otherwise.
        """
        try:
            logger.debug(f"Decoding connection code: {code[:20]}...")
            info = ConnectionInfo.from_code(code)
            logger.debug(f"Decoded peer: id={info.peer_id}, public={info.public_ip}:{info.public_port}, local={info.local_ip}:{info.local_port}")
            
            # Save to config
            self.config.saved_peers[info.peer_id] = {
                "peer_id": info.peer_id,
                "public_ip": info.public_ip,
                "public_port": info.public_port,
                "local_ip": info.local_ip,
                "local_port": info.local_port
            }
            self.config.save(self.config_path)
            logger.debug(f"Saved peer to config: {info.peer_id}")
            
            # Add to transport
            if self.transport:
                local_addr = None
                # Only use local_ip if it's a valid routable address (not 0.0.0.0)
                if info.local_ip and info.local_port and info.local_ip not in ('0.0.0.0', '', '127.0.0.1'):
                    local_addr = (info.local_ip, info.local_port)
                
                logger.debug(f"Adding peer to transport: peer_id={info.peer_id}, public_addr=({info.public_ip}, {info.public_port}), local_addr={local_addr}")
                self.transport.add_peer(
                    peer_id=info.peer_id,
                    public_addr=(info.public_ip, info.public_port),
                    local_addr=local_addr
                )
                logger.debug(f"Peer added. Current transport peers: {list(self.transport.peers.keys())}")
            else:
                logger.warning("Transport not initialized, peer only saved to config")
            
            self._status(f"Added peer: {info.peer_id}")
            return info.peer_id
            
        except Exception as e:
            logger.exception(f"Failed to add peer from code: {e}")
            self._status(f"Invalid code: {e}")
            return None
    
    def connect_to_peer(self, peer_id: str) -> bool:
        """
        Initiate connection to a peer (hole punching).
        
        Args:
            peer_id: The peer to connect to.
        
        Returns:
            True if connection established.
        """
        logger.debug(f"connect_to_peer called with peer_id: {peer_id}")
        
        if not self.transport:
            logger.error("Transport not initialized!")
            return False
        
        # Check if peer exists in transport
        logger.debug(f"Current transport peers: {list(self.transport.peers.keys())}")
        
        # If peer is not in transport but is in saved_peers, add it now
        if peer_id not in self.transport.peers:
            logger.warning(f"Peer {peer_id} not in transport, checking saved_peers...")
            if peer_id in self.config.saved_peers:
                peer_info = self.config.saved_peers[peer_id]
                logger.debug(f"Found in saved_peers: {peer_info}")
                local_addr = None
                if peer_info.get("local_ip") and peer_info.get("local_port"):
                    lip = peer_info["local_ip"]
                    if lip not in ('0.0.0.0', '', '127.0.0.1'):
                        local_addr = (lip, peer_info["local_port"])
                
                self.transport.add_peer(
                    peer_id=peer_id,
                    public_addr=(peer_info["public_ip"], peer_info["public_port"]),
                    local_addr=local_addr
                )
                logger.debug(f"Added peer from saved_peers to transport")
            else:
                logger.error(f"Peer {peer_id} not found in saved_peers either!")
                self._status(f"Unknown peer: {peer_id}")
                return False
        
        self._status(f"Connecting to {peer_id}...")
        
        # Perform hole punching
        success = self.transport.punch(peer_id)
        
        if success:
            self._status(f"Connected to {peer_id}!")
            
            # In CLIENT mode, setup TCP bridge
            if self.mode == NetworkMode.CLIENT:
                logger.info(f"[NetworkManager] CLIENT mode - creating TCP bridge on port {self.minecraft_port}")
                self.tcp_bridge = TCPBridge(
                    transport=self.transport,
                    peer_id=peer_id,
                    local_port=self.minecraft_port,
                    stream_id=0
                )
                if self.tcp_bridge.start():
                    # Report the actual port (might differ if fallback was used)
                    actual_port = self.tcp_bridge.local_port
                    logger.info(f"[NetworkManager] TCP Bridge started on localhost:{actual_port}")
                    self._status(
                        f"TCP Bridge active on localhost:{actual_port} - Connect Minecraft to this address!"
                    )
                else:
                    logger.error("[NetworkManager] TCP Bridge failed to start!")
                    self._status("Warning: TCP Bridge failed to start")
            else:
                logger.info(f"[NetworkManager] HOST mode - waiting for data from peer {peer_id}")
        else:
            self._status(f"Failed to connect to {peer_id}")
        
        return success
    
    def get_saved_peers(self) -> List[Dict]:
        """Get list of saved peers."""
        return list(self.config.saved_peers.values())
    
    def get_connected_peers(self) -> List[str]:
        """Get list of currently connected peer IDs."""
        if not self.transport:
            return []
        
        with self.transport._lock:
            return [
                peer_id for peer_id, peer in self.transport.peers.items()
                if peer.connected
            ]
    
    def _on_data_received(self, peer_id: str, stream_id: int, data: bytes, flags: int = 0):
        """Handle data received from a peer."""
        logger.info(f"[NetworkManager] Received {len(data)} bytes from peer {peer_id} (stream {stream_id})")
        
        # Check for FIN flag (stream closed)
        from .protocol import DataPayload
        if flags & DataPayload.FLAG_FIN:
            logger.info(f"[NetworkManager] Received FIN from {peer_id}")
            if self.mode == NetworkMode.HOST:
                # Close connection to Minecraft server to reset state
                if self._mc_socket:
                    logger.info("[NetworkManager] Closing Minecraft server connection (resetting state)")
                    try:
                        self._mc_socket.close()
                    except:
                        pass
                    self._mc_socket = None
                    self._mc_connected = False
            else:
                # Client mode: Close local TCP bridge clients
                if self.tcp_bridge:
                    # We can't easily close specific clients with current arch, 
                    # but usually client mode initiates the close.
                    pass
            return

        if self.mode == NetworkMode.HOST:
            # Forward to Minecraft server
            # Connect on-demand if needed
            if not self._mc_connected or not self._mc_socket:
                logger.info("[NetworkManager] Minecraft not connected, connecting now (on-demand)...")
                self._status("Connecting to Minecraft server...")
                if not self._connect_to_minecraft():
                    logger.warning("[NetworkManager] Could not connect to Minecraft server")
                    self._status("Failed to connect to Minecraft server")
                    return
                self._status(f"Connected to Minecraft on port {self.minecraft_port}")
            
            try:
                self._mc_socket.sendall(data)
                logger.info(f"[NetworkManager] Forwarded {len(data)} bytes to Minecraft server")
            except socket.error as e:
                logger.warning(f"[NetworkManager] Failed to forward to Minecraft: {e}")
                self._mc_connected = False
                # Try to reconnect and resend
                if self._connect_to_minecraft():
                    try:
                        self._mc_socket.sendall(data)
                        logger.info(f"[NetworkManager] Forwarded {len(data)} bytes after reconnect")
                    except socket.error as e2:
                        logger.error(f"[NetworkManager] Failed even after reconnect: {e2}")
        else:
            # Forward to TCP bridge (local Minecraft client)
            if self.tcp_bridge:
                logger.info(f"[NetworkManager] Forwarding {len(data)} bytes to TCP bridge")
                self.tcp_bridge.receive_data(data)
            else:
                logger.warning("[NetworkManager] No TCP bridge available to forward data")
    
    def _on_peer_connected(self, peer_id: str):
        """Handle peer connection."""
        self._status(f"Peer connected: {peer_id}")
        if self.on_peer_connect:
            self.on_peer_connect(peer_id)
    
    def _on_peer_disconnected(self, peer_id: str):
        """Handle peer disconnection."""
        self._status(f"Peer disconnected: {peer_id}")
        if self.on_peer_disconnect:
            self.on_peer_disconnect(peer_id)


# Convenience functions for quick testing
def create_host(minecraft_port: int = 25565) -> NetworkManager:
    """Create a host network manager."""
    return NetworkManager(
        mode=NetworkMode.HOST,
        minecraft_port=minecraft_port
    )


def create_client(minecraft_port: int = 25565) -> NetworkManager:
    """Create a client network manager."""
    return NetworkManager(
        mode=NetworkMode.CLIENT,
        minecraft_port=minecraft_port
    )


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    mode = sys.argv[1] if len(sys.argv) > 1 else "host"
    
    if mode == "host":
        print("Starting in HOST mode...")
        manager = create_host()
    else:
        print("Starting in CLIENT mode...")
        manager = create_client()
    
    manager.start()
    
    print(f"\n=== MineLink {mode.upper()} ===")
    print(f"Peer ID: {manager.peer_id}")
    print(f"Public Address: {manager.public_address}")
    print(f"Connection Code: {manager.connection_code}")
    print("\nCommands:")
    print("  add <code>  - Add peer from code")
    print("  connect <id> - Connect to peer")
    print("  peers       - List peers")
    print("  stats       - Show stats")
    print("  quit        - Exit")
    
    try:
        while True:
            cmd = input("\n> ").strip().split()
            if not cmd:
                continue
            
            if cmd[0] == "quit":
                break
            elif cmd[0] == "add" and len(cmd) > 1:
                manager.add_peer_from_code(cmd[1])
            elif cmd[0] == "connect" and len(cmd) > 1:
                manager.connect_to_peer(cmd[1])
            elif cmd[0] == "peers":
                print("Saved:", manager.get_saved_peers())
                print("Connected:", manager.get_connected_peers())
            elif cmd[0] == "stats":
                stats = manager.get_stats()
                print(f"Uptime: {stats.uptime:.1f}s")
                print(f"Peers: {stats.peers_connected}")
                print(f"Sent: {stats.total_bytes_sent} bytes")
                print(f"Recv: {stats.total_bytes_recv} bytes")
            else:
                print("Unknown command")
                
    except KeyboardInterrupt:
        pass
    finally:
        manager.stop()
