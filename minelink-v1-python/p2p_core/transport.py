"""
MineLink Reliable Transport Layer
Implements reliable delivery over UDP with retransmission and ordering.
"""

import socket
import threading
import queue
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple, Callable, List
from collections import deque
import heapq

from .protocol import (
    Packet, PacketType, PacketHeader, DataPayload,
    create_data_packet, create_ack_packet, create_ping_packet,
    create_punch_packet, MAX_PAYLOAD_SIZE, generate_session_id
)

logger = logging.getLogger(__name__)

# Transport Constants
DEFAULT_RTT = 0.2  # 200ms initial RTT estimate
MAX_RTT = 2.0      # Maximum RTT cap
MIN_RTO = 0.1      # Minimum retransmission timeout
MAX_RTO = 5.0      # Maximum retransmission timeout
MAX_RETRIES = 10   # Max retransmission attempts
WINDOW_SIZE = 64   # Sliding window size
KEEP_ALIVE_INTERVAL = 5.0  # Seconds between keep-alives
PUNCH_INTERVAL = 0.5  # Seconds between punch attempts
PUNCH_ATTEMPTS = 10   # Number of punch attempts


@dataclass
class PendingPacket:
    """Packet waiting for acknowledgment."""
    sequence: int
    data: bytes
    send_time: float
    retries: int = 0
    
    def __lt__(self, other):
        """For priority queue ordering."""
        return self.send_time < other.send_time


@dataclass
class PeerConnection:
    """Represents a connection to a remote peer."""
    peer_id: str
    public_addr: Tuple[str, int]
    local_addr: Optional[Tuple[str, int]] = None
    session_id: int = 0
    
    # State
    connected: bool = False
    last_seen: float = 0
    
    # Sequence numbers
    send_seq: int = 0
    recv_seq: int = 0
    
    # RTT estimation (Jacobson's algorithm)
    srtt: float = DEFAULT_RTT  # Smoothed RTT
    rttvar: float = DEFAULT_RTT / 2  # RTT variance
    rto: float = 1.0  # Retransmission timeout
    
    # Reliability
    pending: Dict[int, PendingPacket] = field(default_factory=dict)
    ack_waitlist: List[int] = field(default_factory=list)
    recv_buffer: Dict[int, bytes] = field(default_factory=dict)  # Out-of-order packets
    
    # Stats
    packets_sent: int = 0
    packets_recv: int = 0
    packets_lost: int = 0
    bytes_sent: int = 0
    bytes_recv: int = 0
    
    def update_rtt(self, sample: float):
        """Update RTT estimate using Jacobson's algorithm."""
        if self.srtt == DEFAULT_RTT:
            # First sample
            self.srtt = sample
            self.rttvar = sample / 2
        else:
            alpha = 0.125
            beta = 0.25
            self.rttvar = (1 - beta) * self.rttvar + beta * abs(self.srtt - sample)
            self.srtt = (1 - alpha) * self.srtt + alpha * sample
        
        self.rto = min(max(self.srtt + 4 * self.rttvar, MIN_RTO), MAX_RTO)
    
    def next_seq(self) -> int:
        """Get next sequence number."""
        seq = self.send_seq
        self.send_seq = (self.send_seq + 1) & 0xFFFFFFFF
        return seq


class ReliableTransport:
    """
    Reliable UDP transport with:
    - Automatic retransmission
    - Packet ordering
    - Congestion control
    - Keep-alive
    """
    
    def __init__(
        self,
        local_port: int = 0,
        on_data: Optional[Callable[[str, int, bytes], None]] = None,
        on_connect: Optional[Callable[[str], None]] = None,
        on_disconnect: Optional[Callable[[str], None]] = None
    ):
        """
        Initialize transport.
        
        Args:
            local_port: Local port to bind (0 for auto).
            on_data: Callback for received data (peer_id, stream_id, data).
            on_connect: Callback when peer connects.
            on_disconnect: Callback when peer disconnects.
        """
        self.local_port = local_port
        self.on_data = on_data
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        
        # Socket
        self.sock: Optional[socket.socket] = None
        self.local_addr: Optional[Tuple[str, int]] = None
        
        # Peers
        self.peers: Dict[str, PeerConnection] = {}
        self.addr_to_peer: Dict[Tuple[str, int], str] = {}
        
        # Session
        self.session_id = generate_session_id()
        
        # Threading
        self._running = False
        self._recv_thread: Optional[threading.Thread] = None
        self._timer_thread: Optional[threading.Thread] = None
        self._send_queue: queue.Queue = queue.Queue()
        
        # Lock for thread safety
        self._lock = threading.RLock()
    
    def start(self) -> Tuple[str, int]:
        """
        Start the transport.
        Returns: Local (ip, port) tuple.
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.local_port))
        self.sock.setblocking(False)
        
        self.local_addr = self.sock.getsockname()
        logger.info(f"Transport started on {self.local_addr}")
        
        self._running = True
        
        # Start receive thread
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        
        # Start timer thread (retransmission, keep-alive)
        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()
        
        return self.local_addr
    
    def stop(self):
        """Stop the transport."""
        self._running = False
        
        if self._recv_thread:
            self._recv_thread.join(timeout=1)
        if self._timer_thread:
            self._timer_thread.join(timeout=1)
        
        if self.sock:
            self.sock.close()
            self.sock = None
        
        logger.info("Transport stopped")
    
    def add_peer(
        self,
        peer_id: str,
        public_addr: Tuple[str, int],
        local_addr: Optional[Tuple[str, int]] = None
    ) -> PeerConnection:
        """
        Add a peer to connect to.
        
        Args:
            peer_id: Unique peer identifier.
            public_addr: (ip, port) of peer's public address.
            local_addr: Optional (ip, port) for LAN connection.
        """
        with self._lock:
            peer = PeerConnection(
                peer_id=peer_id,
                public_addr=public_addr,
                local_addr=local_addr,
                session_id=self.session_id
            )
            self.peers[peer_id] = peer
            self.addr_to_peer[public_addr] = peer_id
            if local_addr:
                self.addr_to_peer[local_addr] = peer_id
            
            logger.info(f"Added peer: {peer_id} @ {public_addr}")
            return peer
    
    def remove_peer(self, peer_id: str):
        """Remove a peer."""
        with self._lock:
            if peer_id in self.peers:
                peer = self.peers[peer_id]
                del self.peers[peer_id]
                if peer.public_addr in self.addr_to_peer:
                    del self.addr_to_peer[peer.public_addr]
                if peer.local_addr and peer.local_addr in self.addr_to_peer:
                    del self.addr_to_peer[peer.local_addr]
                logger.info(f"Removed peer: {peer_id}")
    
    def punch(self, peer_id: str) -> bool:
        """
        Initiate hole punching to a peer.
        Blocks until connection established or timeout.
        
        Returns: True if connected.
        """
        with self._lock:
            if peer_id not in self.peers:
                logger.error(f"Unknown peer: {peer_id}")
                return False
            peer = self.peers[peer_id]
        
        logger.info(f"Starting hole punch to {peer_id}")
        
        for attempt in range(PUNCH_ATTEMPTS):
            if peer.connected:
                return True
            
            # Send punch packet to both public and local addresses
            punch_data = create_punch_packet(self.session_id)
            
            self._send_raw(punch_data, peer.public_addr)
            if peer.local_addr:
                self._send_raw(punch_data, peer.local_addr)
            
            logger.debug(f"Punch attempt {attempt + 1}/{PUNCH_ATTEMPTS} to {peer_id}")
            time.sleep(PUNCH_INTERVAL)
        
        return peer.connected
    
    def send(self, peer_id: str, stream_id: int, data: bytes) -> bool:
        """
        Send reliable data to a peer.
        
        Args:
            peer_id: Target peer.
            stream_id: Stream identifier (for multiplexing).
            data: Data to send.
        
        Returns: True if queued successfully.
        """
        with self._lock:
            if peer_id not in self.peers:
                return False
            peer = self.peers[peer_id]
            if not peer.connected:
                return False
        
        # Fragment if necessary
        offset = 0
        fragments = []
        max_data = MAX_PAYLOAD_SIZE - DataPayload.HEADER_SIZE
        
        while offset < len(data):
            chunk = data[offset:offset + max_data]
            fragments.append(chunk)
            offset += len(chunk)
        
        # Send each fragment
        for i, chunk in enumerate(fragments):
            with self._lock:
                seq = peer.next_seq()
                flags = 0
                if len(fragments) > 1:
                    flags |= DataPayload.FLAG_FRAGMENT
                
                packet_data = create_data_packet(
                    session_id=self.session_id,
                    sequence=seq,
                    stream_id=stream_id,
                    data=chunk,
                    flags=flags
                )
                
                # Add to pending for retransmission
                peer.pending[seq] = PendingPacket(
                    sequence=seq,
                    data=packet_data,
                    send_time=time.time()
                )
                
                self._send_raw(packet_data, peer.public_addr)
                peer.packets_sent += 1
                peer.bytes_sent += len(packet_data)
        
        return True
    
    def _send_raw(self, data: bytes, addr: Tuple[str, int]):
        """Send raw bytes to an address."""
        if self.sock:
            try:
                self.sock.sendto(data, addr)
            except socket.error as e:
                logger.warning(f"Send error to {addr}: {e}")
    
    def _recv_loop(self):
        """Receive loop running in background thread."""
        while self._running:
            try:
                if not self.sock:
                    break
                
                # Non-blocking receive with select
                import select
                readable, _, _ = select.select([self.sock], [], [], 0.1)
                
                if not readable:
                    continue
                
                data, addr = self.sock.recvfrom(2048)
                self._handle_packet(data, addr)
                
            except socket.error:
                continue
            except Exception as e:
                logger.exception(f"Receive error: {e}")
    
    def _handle_packet(self, data: bytes, addr: Tuple[str, int]):
        """Handle an incoming packet."""
        try:
            packet = Packet.unpack(data)
        except Exception as e:
            logger.warning(f"Invalid packet from {addr}: {e}")
            return
        
        ptype = packet.header.packet_type
        
        # Identify peer
        with self._lock:
            peer_id = self.addr_to_peer.get(addr)
            peer = self.peers.get(peer_id) if peer_id else None
        
        # Handle punch (can come from unknown peer)
        if ptype == PacketType.PUNCH:
            self._handle_punch(packet, addr)
            return
        
        if ptype == PacketType.PUNCH_ACK:
            self._handle_punch_ack(packet, addr, peer)
            return
        
        if peer is None:
            logger.debug(f"Packet from unknown address: {addr}")
            return
        
        # Update last seen
        peer.last_seen = time.time()
        peer.packets_recv += 1
        peer.bytes_recv += len(data)
        
        # Handle by type
        if ptype == PacketType.DATA:
            self._handle_data(packet, peer)
        elif ptype == PacketType.DATA_ACK:
            self._handle_ack(packet, peer)
        elif ptype == PacketType.PING:
            self._handle_ping(packet, peer)
        elif ptype == PacketType.PONG:
            self._handle_pong(packet, peer)
        elif ptype == PacketType.DISCONNECT:
            self._handle_disconnect(peer)
    
    def _handle_punch(self, packet: Packet, addr: Tuple[str, int]):
        """Handle incoming punch packet."""
        with self._lock:
            peer_id = self.addr_to_peer.get(addr)
            if peer_id and peer_id in self.peers:
                peer = self.peers[peer_id]
                
                # Send punch ACK
                ack = Packet.create(
                    PacketType.PUNCH_ACK,
                    session_id=self.session_id
                ).pack()
                self._send_raw(ack, addr)
                
                if not peer.connected:
                    peer.connected = True
                    peer.last_seen = time.time()
                    logger.info(f"Punch received from {peer_id}, connection established")
                    
                    if self.on_connect:
                        threading.Thread(
                            target=self.on_connect,
                            args=(peer_id,),
                            daemon=True
                        ).start()
    
    def _handle_punch_ack(
        self,
        packet: Packet,
        addr: Tuple[str, int],
        peer: Optional[PeerConnection]
    ):
        """Handle punch acknowledgment."""
        if peer and not peer.connected:
            peer.connected = True
            peer.last_seen = time.time()
            logger.info(f"Punch ACK from {peer.peer_id}, connection established")
            
            if self.on_connect:
                threading.Thread(
                    target=self.on_connect,
                    args=(peer.peer_id,),
                    daemon=True
                ).start()
    
    def _handle_data(self, packet: Packet, peer: PeerConnection):
        """Handle incoming data packet."""
        seq = packet.header.sequence
        
        # Send ACK immediately
        ack = create_ack_packet(self.session_id, seq)
        self._send_raw(ack, peer.public_addr)
        
        # Check sequence
        if seq < peer.recv_seq:
            # Already received, ignore (duplicate)
            return
        
        if seq > peer.recv_seq:
            # Out of order, buffer it
            peer.recv_buffer[seq] = packet.payload
            return
        
        # In order, process
        try:
            data_payload = DataPayload.unpack(packet.payload)
            
            if self.on_data:
                self.on_data(peer.peer_id, data_payload.stream_id, data_payload.data)
            
            peer.recv_seq += 1
            
            # Check buffer for next expected packets
            while peer.recv_seq in peer.recv_buffer:
                buffered = peer.recv_buffer.pop(peer.recv_seq)
                buffered_payload = DataPayload.unpack(buffered)
                
                if self.on_data:
                    self.on_data(
                        peer.peer_id,
                        buffered_payload.stream_id,
                        buffered_payload.data
                    )
                
                peer.recv_seq += 1
                
        except Exception as e:
            logger.warning(f"Error processing data: {e}")
    
    def _handle_ack(self, packet: Packet, peer: PeerConnection):
        """Handle data acknowledgment."""
        acked_seq = packet.header.sequence
        
        with self._lock:
            if acked_seq in peer.pending:
                pending = peer.pending.pop(acked_seq)
                rtt_sample = time.time() - pending.send_time
                peer.update_rtt(rtt_sample)
                logger.debug(f"ACK for seq {acked_seq}, RTT={rtt_sample*1000:.1f}ms")
    
    def _handle_ping(self, packet: Packet, peer: PeerConnection):
        """Handle ping packet."""
        # Extract timestamp from payload
        if len(packet.payload) >= 8:
            import struct
            ping_time = struct.unpack(">Q", packet.payload[:8])[0]
            
            from .protocol import create_pong_packet
            pong = create_pong_packet(self.session_id, packet.header.sequence, ping_time)
            self._send_raw(pong, peer.public_addr)
    
    def _handle_pong(self, packet: Packet, peer: PeerConnection):
        """Handle pong packet."""
        if len(packet.payload) >= 8:
            import struct
            ping_time = struct.unpack(">Q", packet.payload[:8])[0]
            now = int(time.time() * 1000)
            rtt = (now - ping_time) / 1000.0
            peer.update_rtt(rtt)
            logger.debug(f"Pong from {peer.peer_id}, RTT={rtt*1000:.1f}ms")
    
    def _handle_disconnect(self, peer: PeerConnection):
        """Handle disconnect packet."""
        peer.connected = False
        logger.info(f"Peer {peer.peer_id} disconnected")
        
        if self.on_disconnect:
            threading.Thread(
                target=self.on_disconnect,
                args=(peer.peer_id,),
                daemon=True
            ).start()
    
    def _timer_loop(self):
        """Timer loop for retransmission and keep-alive."""
        last_keepalive = time.time()
        
        while self._running:
            now = time.time()
            
            with self._lock:
                for peer_id, peer in list(self.peers.items()):
                    if not peer.connected:
                        continue
                    
                    # Check for retransmissions
                    for seq, pending in list(peer.pending.items()):
                        if now - pending.send_time > peer.rto:
                            if pending.retries >= MAX_RETRIES:
                                # Don't give up! This is a TCP tunnel, we CANNOT drop packets.
                                # Just log a warning and keep trying, but back off slightly more?
                                # Actually, just cap the RTO (which is already done) and keep going.
                                if pending.retries % 10 == 0:
                                    logger.warning(
                                        f"Packet {seq} to {peer_id} requires {pending.retries} retries "
                                        f"(Link quality poor?) - RTO={peer.rto:.2f}"
                                    )
                                # Continue retransmitting
                                pending.retries += 1
                                pending.send_time = now
                                self._send_raw(pending.data, peer.public_addr)
                            else:
                                # Retransmit
                                pending.retries += 1
                                pending.send_time = now
                                self._send_raw(pending.data, peer.public_addr)
                                logger.debug(
                                    f"Retransmit seq {seq} to {peer_id} "
                                    f"(attempt {pending.retries})"
                                )
                    
                    # Keep-alive
                    if now - last_keepalive > KEEP_ALIVE_INTERVAL:
                        if now - peer.last_seen > KEEP_ALIVE_INTERVAL:
                            ping = create_ping_packet(self.session_id, peer.send_seq)
                            self._send_raw(ping, peer.public_addr)
            
            if now - last_keepalive > KEEP_ALIVE_INTERVAL:
                last_keepalive = now
            
            time.sleep(0.05)  # 50ms tick


class TCPBridge:
    """
    Bridges local TCP connections to a ReliableTransport.
    Used to tunnel Minecraft TCP traffic over UDP.
    """
    
    def __init__(
        self,
        transport: ReliableTransport,
        peer_id: str,
        local_port: int = 25565,
        stream_id: int = 0
    ):
        """
        Initialize TCP bridge.
        
        Args:
            transport: The ReliableTransport to use.
            peer_id: Target peer for data.
            local_port: Local TCP port to listen on.
            stream_id: Stream ID for multiplexing.
        """
        self.transport = transport
        self.peer_id = peer_id
        self.local_port = local_port
        self.stream_id = stream_id
        
        self._server_sock: Optional[socket.socket] = None
        self._clients: Dict[int, socket.socket] = {}
        self._running = False
        self._accept_thread: Optional[threading.Thread] = None
    
    def start(self) -> bool:
        """
        Start listening for TCP connections.
        
        Returns:
            True if started successfully, False otherwise.
        """
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Try to bind, with fallback to alternative ports
            port_to_try = self.local_port
            for attempt in range(10):
                try:
                    self._server_sock.bind(("127.0.0.1", port_to_try))
                    self.local_port = port_to_try
                    break
                except OSError as e:
                    if e.errno in (48, 98, 10048):  # Address already in use (macOS, Linux, Windows)
                        logger.warning(f"Port {port_to_try} in use, trying {port_to_try + 1}")
                        port_to_try += 1
                    else:
                        raise
            else:
                logger.error(f"Could not bind to any port starting from {self.local_port}")
                return False
            
            self._server_sock.listen(5)
            self._server_sock.setblocking(False)
            
            self._running = True
            self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._accept_thread.start()
            
            logger.info(f"TCP Bridge listening on 127.0.0.1:{self.local_port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start TCP bridge: {e}")
            return False
    
    def stop(self):
        """Stop the bridge."""
        self._running = False
        
        for sock in self._clients.values():
            sock.close()
        self._clients.clear()
        
        if self._server_sock:
            self._server_sock.close()
        
        if self._accept_thread:
            self._accept_thread.join(timeout=1)
    
    def _accept_loop(self):
        """Accept incoming TCP connections."""
        import select
        
        while self._running:
            try:
                readable, _, _ = select.select([self._server_sock], [], [], 0.5)
                if readable:
                    client_sock, addr = self._server_sock.accept()
                    client_id = id(client_sock)
                    self._clients[client_id] = client_sock
                    
                    logger.info(f"TCP client connected from {addr}")
                    
                    # Start read thread for this client
                    threading.Thread(
                        target=self._client_read_loop,
                        args=(client_id, client_sock),
                        daemon=True
                    ).start()
                    
            except socket.error:
                continue
    
    def _client_read_loop(self, client_id: int, client_sock: socket.socket):
        """Read data from a TCP client and send over transport."""
        client_sock.setblocking(False)
        total_bytes_sent = 0
        
        logger.info(f"[TCP Bridge] Starting read loop for client {client_id}")
        
        while self._running and client_id in self._clients:
            try:
                import select
                readable, _, _ = select.select([client_sock], [], [], 0.5)
                if readable:
                    data = client_sock.recv(4096)
                    if not data:
                        logger.info(f"[TCP Bridge] Client {client_id} closed connection (no data)")
                        break
                    
                    total_bytes_sent += len(data)
                    logger.debug(
                        f"[TCP Bridge] Received {len(data)} bytes from Minecraft client, "
                        f"forwarding to peer {self.peer_id} (total: {total_bytes_sent})"
                    )
                    
                    # Send over UDP tunnel
                    success = self.transport.send(self.peer_id, self.stream_id, data)
                    if not success:
                        logger.warning(f"[TCP Bridge] Failed to send data to peer {self.peer_id}")
                    
            except socket.error as e:
                logger.warning(f"[TCP Bridge] Socket error in read loop: {e}")
                break
        
        # Cleanup
        if client_id in self._clients:
            del self._clients[client_id]
        client_sock.close()
        logger.info(f"[TCP Bridge] Client {client_id} disconnected (sent {total_bytes_sent} bytes total)")
    
    def receive_data(self, data: bytes):
        """Called when data is received from the transport."""
        num_clients = len(self._clients)
        
        if num_clients == 0:
            logger.warning(f"[TCP Bridge] Received {len(data)} bytes but no Minecraft clients connected!")
            return
        
        logger.debug(f"[TCP Bridge] Forwarding {len(data)} bytes to {num_clients} Minecraft client(s)")
        
        # Forward to all connected TCP clients
        for client_id, client_sock in list(self._clients.items()):
            try:
                client_sock.sendall(data)
            except socket.error as e:
                logger.warning(f"[TCP Bridge] Failed to forward to client {client_id}: {e}")


if __name__ == "__main__":
    # Basic test
    logging.basicConfig(level=logging.DEBUG)
    
    def on_data(peer_id, stream_id, data):
        print(f"[{peer_id}] Stream {stream_id}: {data}")
    
    def on_connect(peer_id):
        print(f"Connected to {peer_id}")
    
    transport = ReliableTransport(
        local_port=0,
        on_data=on_data,
        on_connect=on_connect
    )
    
    addr = transport.start()
    print(f"Transport running on {addr}")
    
    try:
        input("Press Enter to stop...\n")
    finally:
        transport.stop()
