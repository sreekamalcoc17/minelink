"""
STUN Client Implementation (RFC 5389)
Discovers Public IP and Port behind NAT using STUN servers.
"""

import socket
import struct
import secrets
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# STUN Message Types
STUN_BINDING_REQUEST = 0x0001
STUN_BINDING_RESPONSE = 0x0101
STUN_BINDING_ERROR = 0x0111

# STUN Attribute Types
ATTR_MAPPED_ADDRESS = 0x0001
ATTR_XOR_MAPPED_ADDRESS = 0x0020
ATTR_ERROR_CODE = 0x0009

# Magic Cookie (RFC 5389)
MAGIC_COOKIE = 0x2112A442

# Default STUN Servers
DEFAULT_STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
    ("stun2.l.google.com", 19302),
    ("stun.stunprotocol.org", 3478),
]


@dataclass
class STUNResult:
    """Result of a STUN binding request."""
    public_ip: str
    public_port: int
    local_ip: str
    local_port: int


class STUNError(Exception):
    """STUN operation failed."""
    pass


def get_local_ip() -> str:
    """
    Get the local IP address of this machine.
    This returns the IP that would be used to connect to the internet,
    not 0.0.0.0 or 127.0.0.1.
    """
    try:
        # Create a socket and connect to an external address
        # This doesn't actually send any data, just determines the route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        # Connect to Google's DNS (doesn't actually send anything)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        # Fallback: try to get from hostname
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            if local_ip.startswith("127."):
                return "127.0.0.1"
            return local_ip
        except Exception:
            return "127.0.0.1"


def _create_binding_request() -> Tuple[bytes, bytes]:
    """
    Create a STUN Binding Request message.
    Returns: (message_bytes, transaction_id)
    """
    # Transaction ID: 96 bits (12 bytes) of random data
    transaction_id = secrets.token_bytes(12)
    
    # Message Type: Binding Request (0x0001)
    # Message Length: 0 (no attributes in request)
    header = struct.pack(
        ">HHI",
        STUN_BINDING_REQUEST,  # Message Type (2 bytes)
        0,                      # Message Length (2 bytes)
        MAGIC_COOKIE           # Magic Cookie (4 bytes)
    )
    
    message = header + transaction_id
    return message, transaction_id


def _parse_binding_response(data: bytes, expected_txn_id: bytes) -> Tuple[str, int]:
    """
    Parse a STUN Binding Response message.
    Returns: (public_ip, public_port)
    """
    if len(data) < 20:
        raise STUNError("Response too short")
    
    # Parse header
    msg_type, msg_len, magic = struct.unpack(">HHI", data[:8])
    txn_id = data[8:20]
    
    # Validate response
    if msg_type == STUN_BINDING_ERROR:
        raise STUNError("STUN server returned error")
    
    if msg_type != STUN_BINDING_RESPONSE:
        raise STUNError(f"Unexpected message type: {msg_type:#06x}")
    
    if magic != MAGIC_COOKIE:
        raise STUNError("Invalid magic cookie")
    
    if txn_id != expected_txn_id:
        raise STUNError("Transaction ID mismatch")
    
    # Parse attributes
    offset = 20
    public_ip = None
    public_port = None
    
    while offset < 20 + msg_len:
        if offset + 4 > len(data):
            break
        
        attr_type, attr_len = struct.unpack(">HH", data[offset:offset + 4])
        offset += 4
        attr_data = data[offset:offset + attr_len]
        
        if attr_type == ATTR_XOR_MAPPED_ADDRESS:
            # XOR-Mapped Address (preferred)
            public_ip, public_port = _parse_xor_mapped_address(attr_data)
        elif attr_type == ATTR_MAPPED_ADDRESS and public_ip is None:
            # Fallback to Mapped Address
            public_ip, public_port = _parse_mapped_address(attr_data)
        
        # Align to 4-byte boundary
        offset += attr_len + (4 - attr_len % 4) % 4
    
    if public_ip is None:
        raise STUNError("No mapped address in response")
    
    return public_ip, public_port


def _parse_xor_mapped_address(data: bytes) -> Tuple[str, int]:
    """Parse XOR-MAPPED-ADDRESS attribute."""
    if len(data) < 8:
        raise STUNError("XOR-MAPPED-ADDRESS too short")
    
    _, family, xor_port = struct.unpack(">BBH", data[:4])
    
    # XOR with magic cookie
    port = xor_port ^ (MAGIC_COOKIE >> 16)
    
    if family == 0x01:  # IPv4
        xor_ip = struct.unpack(">I", data[4:8])[0]
        ip_int = xor_ip ^ MAGIC_COOKIE
        ip = socket.inet_ntoa(struct.pack(">I", ip_int))
    else:
        raise STUNError(f"Unsupported address family: {family}")
    
    return ip, port


def _parse_mapped_address(data: bytes) -> Tuple[str, int]:
    """Parse MAPPED-ADDRESS attribute (legacy)."""
    if len(data) < 8:
        raise STUNError("MAPPED-ADDRESS too short")
    
    _, family, port = struct.unpack(">BBH", data[:4])
    
    if family == 0x01:  # IPv4
        ip = socket.inet_ntoa(data[4:8])
    else:
        raise STUNError(f"Unsupported address family: {family}")
    
    return ip, port


def get_public_address(
    stun_servers: list = None,
    timeout: float = 3.0,
    local_port: int = 0
) -> Optional[STUNResult]:
    """
    Discover public IP and port using STUN.
    
    Args:
        stun_servers: List of (host, port) tuples. Uses defaults if None.
        timeout: Socket timeout in seconds.
        local_port: Local port to bind (0 for auto-assign).
    
    Returns:
        STUNResult with public and local addresses, or None on failure.
    """
    if stun_servers is None:
        stun_servers = DEFAULT_STUN_SERVERS
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    
    try:
        # Bind to local port
        sock.bind(("0.0.0.0", local_port))
        local_addr = sock.getsockname()
        
        for server_host, server_port in stun_servers:
            try:
                logger.debug(f"Trying STUN server: {server_host}:{server_port}")
                
                # Resolve server
                server_ip = socket.gethostbyname(server_host)
                
                # Send binding request
                request, txn_id = _create_binding_request()
                sock.sendto(request, (server_ip, server_port))
                
                # Receive response
                data, _ = sock.recvfrom(1024)
                
                # Parse response
                public_ip, public_port = _parse_binding_response(data, txn_id)
                
                logger.info(f"STUN success: {public_ip}:{public_port}")
                
                # Get actual local IP (not 0.0.0.0)
                local_ip = get_local_ip() if local_addr[0] in ('0.0.0.0', '') else local_addr[0]
                
                return STUNResult(
                    public_ip=public_ip,
                    public_port=public_port,
                    local_ip=local_ip,
                    local_port=local_addr[1]
                )
                
            except (socket.timeout, socket.gaierror, STUNError) as e:
                logger.warning(f"STUN server {server_host} failed: {e}")
                continue
        
        logger.error("All STUN servers failed")
        return None
        
    finally:
        sock.close()


def get_public_address_with_socket(
    sock: socket.socket,
    stun_servers: list = None,
    timeout: float = 3.0
) -> Optional[STUNResult]:
    """
    Get public address using an existing socket.
    This is useful to discover the public mapping of a socket already in use.
    
    Args:
        sock: An existing UDP socket.
        stun_servers: List of (host, port) tuples.
        timeout: Socket timeout.
    
    Returns:
        STUNResult or None on failure.
    """
    if stun_servers is None:
        stun_servers = DEFAULT_STUN_SERVERS
    
    old_timeout = sock.gettimeout()
    sock.settimeout(timeout)
    local_addr = sock.getsockname()
    
    try:
        for server_host, server_port in stun_servers:
            try:
                server_ip = socket.gethostbyname(server_host)
                request, txn_id = _create_binding_request()
                sock.sendto(request, (server_ip, server_port))
                
                # We might receive packets from other sources, so loop
                for _ in range(5):
                    try:
                        data, addr = sock.recvfrom(1024)
                        if addr[0] == server_ip:
                            public_ip, public_port = _parse_binding_response(data, txn_id)
                            # Get actual local IP (not 0.0.0.0)
                            local_ip = get_local_ip() if local_addr[0] in ('0.0.0.0', '') else local_addr[0]
                            return STUNResult(
                                public_ip=public_ip,
                                public_port=public_port,
                                local_ip=local_ip,
                                local_port=local_addr[1]
                            )
                    except socket.timeout:
                        break
                        
            except (socket.timeout, socket.gaierror, STUNError) as e:
                logger.warning(f"STUN server {server_host} failed: {e}")
                continue
        
        return None
        
    finally:
        sock.settimeout(old_timeout)


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.DEBUG)
    result = get_public_address()
    if result:
        print(f"\n✓ Public Address: {result.public_ip}:{result.public_port}")
        print(f"  Local Address:  {result.local_ip}:{result.local_port}")
    else:
        print("\n✗ Failed to get public address")
