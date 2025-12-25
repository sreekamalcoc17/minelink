"""
MineLink Protocol Definition
Custom binary protocol for reliable P2P communication.
"""

import struct
import json
import base64
import hashlib
import secrets
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import time

# Protocol Version
PROTOCOL_VERSION = 1

# Magic bytes to identify MineLink packets
MINELINK_MAGIC = b'ML'

# Maximum payload size (MTU-safe)
MAX_PAYLOAD_SIZE = 1200
MAX_PACKET_SIZE = 1400


class PacketType(IntEnum):
    """Packet type identifiers."""
    # Connection Management
    HANDSHAKE_INIT = 0x01      # Initial handshake (Client -> Host)
    HANDSHAKE_ACK = 0x02       # Handshake acknowledgment (Host -> Client)
    HANDSHAKE_COMPLETE = 0x03  # Handshake complete (Client -> Host)
    
    # Keep-Alive
    PING = 0x10
    PONG = 0x11
    
    # Data Transfer (Reliable)
    DATA = 0x20
    DATA_ACK = 0x21
    DATA_NACK = 0x22  # Negative ACK (request retransmit)
    
    # Data Transfer (Unreliable - for future use)
    DATA_UNRELIABLE = 0x30
    
    # Control
    DISCONNECT = 0x40
    ERROR = 0x41
    
    # Hole Punching
    PUNCH = 0x50
    PUNCH_ACK = 0x51


class ErrorCode(IntEnum):
    """Error codes for ERROR packets."""
    UNKNOWN = 0x00
    VERSION_MISMATCH = 0x01
    AUTH_FAILED = 0x02
    SESSION_EXPIRED = 0x03
    RATE_LIMITED = 0x04
    PROTOCOL_ERROR = 0x05


@dataclass
class PacketHeader:
    """
    Packet Header Format (12 bytes):
    - Magic (2 bytes): 'ML'
    - Version (1 byte): Protocol version
    - Type (1 byte): PacketType enum
    - Session ID (4 bytes): Unique session identifier
    - Sequence (4 bytes): Packet sequence number
    """
    magic: bytes = MINELINK_MAGIC
    version: int = PROTOCOL_VERSION
    packet_type: PacketType = PacketType.DATA
    session_id: int = 0
    sequence: int = 0
    
    HEADER_SIZE = 12
    
    def pack(self) -> bytes:
        """Serialize header to bytes."""
        return struct.pack(
            ">2sBBII",
            self.magic,
            self.version,
            self.packet_type,
            self.session_id,
            self.sequence
        )
    
    @classmethod
    def unpack(cls, data: bytes) -> 'PacketHeader':
        """Deserialize header from bytes."""
        if len(data) < cls.HEADER_SIZE:
            raise ValueError("Data too short for header")
        
        magic, version, ptype, session_id, seq = struct.unpack(
            ">2sBBII", data[:cls.HEADER_SIZE]
        )
        
        if magic != MINELINK_MAGIC:
            raise ValueError(f"Invalid magic: {magic}")
        
        return cls(
            magic=magic,
            version=version,
            packet_type=PacketType(ptype),
            session_id=session_id,
            sequence=seq
        )


@dataclass
class Packet:
    """Complete packet with header and payload."""
    header: PacketHeader
    payload: bytes = b''
    
    def pack(self) -> bytes:
        """Serialize complete packet."""
        return self.header.pack() + self.payload
    
    @classmethod
    def unpack(cls, data: bytes) -> 'Packet':
        """Deserialize complete packet."""
        header = PacketHeader.unpack(data)
        payload = data[PacketHeader.HEADER_SIZE:]
        return cls(header=header, payload=payload)
    
    @classmethod
    def create(
        cls,
        packet_type: PacketType,
        session_id: int = 0,
        sequence: int = 0,
        payload: bytes = b''
    ) -> 'Packet':
        """Create a new packet."""
        header = PacketHeader(
            packet_type=packet_type,
            session_id=session_id,
            sequence=sequence
        )
        return cls(header=header, payload=payload)


# ============================================================================
# Payload Structures
# ============================================================================

@dataclass
class HandshakePayload:
    """
    Handshake payload for connection establishment.
    Contains peer identity and capabilities.
    """
    peer_id: str           # Unique peer identifier (e.g., "sreek-mc")
    peer_name: str         # Display name
    public_ip: str         # Public IP from STUN
    public_port: int       # Public port from STUN
    local_ip: str          # Local IP (for LAN detection)
    local_port: int        # Local port
    timestamp: int = 0     # Unix timestamp
    nonce: bytes = field(default_factory=lambda: secrets.token_bytes(8))
    
    def pack(self) -> bytes:
        """Serialize to JSON bytes."""
        data = {
            "peer_id": self.peer_id,
            "peer_name": self.peer_name,
            "public_ip": self.public_ip,
            "public_port": self.public_port,
            "local_ip": self.local_ip,
            "local_port": self.local_port,
            "timestamp": self.timestamp or int(time.time()),
            "nonce": base64.b64encode(self.nonce).decode()
        }
        return json.dumps(data, separators=(',', ':')).encode('utf-8')
    
    @classmethod
    def unpack(cls, data: bytes) -> 'HandshakePayload':
        """Deserialize from JSON bytes."""
        obj = json.loads(data.decode('utf-8'))
        return cls(
            peer_id=obj["peer_id"],
            peer_name=obj["peer_name"],
            public_ip=obj["public_ip"],
            public_port=obj["public_port"],
            local_ip=obj["local_ip"],
            local_port=obj["local_port"],
            timestamp=obj.get("timestamp", 0),
            nonce=base64.b64decode(obj.get("nonce", ""))
        )


@dataclass
class DataPayload:
    """
    Reliable data payload with sequencing.
    
    Format:
    - Stream ID (1 byte): For multiplexing (e.g., different MC connections)
    - Flags (1 byte): FIN, SYN, etc.
    - Fragment ID (2 bytes): For fragmented packets
    - Fragment Total (2 bytes): Total fragments
    - Data Length (2 bytes): Payload length
    - Data (variable): Actual payload
    """
    stream_id: int = 0
    flags: int = 0
    fragment_id: int = 0
    fragment_total: int = 1
    data: bytes = b''
    
    # Flags
    FLAG_FIN = 0x01       # Final packet of stream
    FLAG_SYN = 0x02       # Stream start
    FLAG_FRAGMENT = 0x04  # This is a fragment
    
    HEADER_SIZE = 8
    
    def pack(self) -> bytes:
        """Serialize payload."""
        header = struct.pack(
            ">BBHHH",
            self.stream_id,
            self.flags,
            self.fragment_id,
            self.fragment_total,
            len(self.data)
        )
        return header + self.data
    
    @classmethod
    def unpack(cls, data: bytes) -> 'DataPayload':
        """Deserialize payload."""
        if len(data) < cls.HEADER_SIZE:
            raise ValueError("Data too short for DataPayload")
        
        stream_id, flags, frag_id, frag_total, data_len = struct.unpack(
            ">BBHHH", data[:cls.HEADER_SIZE]
        )
        
        payload_data = data[cls.HEADER_SIZE:cls.HEADER_SIZE + data_len]
        
        return cls(
            stream_id=stream_id,
            flags=flags,
            fragment_id=frag_id,
            fragment_total=frag_total,
            data=payload_data
        )


# ============================================================================
# Connection Code (for Manual Exchange)
# ============================================================================

@dataclass
class ConnectionInfo:
    """
    Information needed to connect to a peer.
    This is what gets encoded into a "Connection Code".
    """
    peer_id: str
    public_ip: str
    public_port: int
    local_ip: Optional[str] = None
    local_port: Optional[int] = None
    session_key: Optional[str] = None  # Pre-shared key for this session
    
    def to_code(self) -> str:
        """
        Encode connection info to a shareable code.
        Format: Base64(JSON)
        """
        data = {
            "id": self.peer_id,
            "ip": self.public_ip,
            "port": self.public_port,
        }
        if self.local_ip:
            data["lip"] = self.local_ip
            data["lport"] = self.local_port
        if self.session_key:
            data["key"] = self.session_key
        
        json_str = json.dumps(data, separators=(',', ':'))
        return base64.urlsafe_b64encode(json_str.encode()).decode()
    
    @classmethod
    def from_code(cls, code: str) -> 'ConnectionInfo':
        """Decode connection info from a code."""
        try:
            # Handle padding
            padding = 4 - len(code) % 4
            if padding != 4:
                code += '=' * padding
            
            json_str = base64.urlsafe_b64decode(code).decode()
            data = json.loads(json_str)
            
            return cls(
                peer_id=data["id"],
                public_ip=data["ip"],
                public_port=data["port"],
                local_ip=data.get("lip"),
                local_port=data.get("lport"),
                session_key=data.get("key")
            )
        except Exception as e:
            raise ValueError(f"Invalid connection code: {e}")


# ============================================================================
# Helper Functions
# ============================================================================

def generate_session_id() -> int:
    """Generate a random 32-bit session ID."""
    return secrets.randbelow(0xFFFFFFFF)


def generate_peer_id(name: str) -> str:
    """Generate a unique peer ID from a name."""
    # Add some randomness to make it unique
    random_part = secrets.token_hex(4)
    # Create a short hash
    combined = f"{name}-{random_part}"
    return combined.lower().replace(" ", "-")


def create_punch_packet(session_id: int) -> bytes:
    """Create a hole-punching packet."""
    return Packet.create(
        PacketType.PUNCH,
        session_id=session_id,
        sequence=0
    ).pack()


def create_ping_packet(session_id: int, sequence: int) -> bytes:
    """Create a ping packet."""
    return Packet.create(
        PacketType.PING,
        session_id=session_id,
        sequence=sequence,
        payload=struct.pack(">Q", int(time.time() * 1000))
    ).pack()


def create_pong_packet(session_id: int, sequence: int, ping_time: int) -> bytes:
    """Create a pong packet."""
    return Packet.create(
        PacketType.PONG,
        session_id=session_id,
        sequence=sequence,
        payload=struct.pack(">Q", ping_time)
    ).pack()


def create_data_packet(
    session_id: int,
    sequence: int,
    stream_id: int,
    data: bytes,
    flags: int = 0
) -> bytes:
    """Create a reliable data packet."""
    payload = DataPayload(
        stream_id=stream_id,
        flags=flags,
        data=data
    )
    return Packet.create(
        PacketType.DATA,
        session_id=session_id,
        sequence=sequence,
        payload=payload.pack()
    ).pack()


def create_ack_packet(session_id: int, acked_sequence: int) -> bytes:
    """Create a data acknowledgment packet."""
    return Packet.create(
        PacketType.DATA_ACK,
        session_id=session_id,
        sequence=acked_sequence
    ).pack()


if __name__ == "__main__":
    # Test code generation
    info = ConnectionInfo(
        peer_id="sreek-mc",
        public_ip="203.0.113.10",
        public_port=45678
    )
    code = info.to_code()
    print(f"Connection Code: {code}")
    
    # Decode it back
    decoded = ConnectionInfo.from_code(code)
    print(f"Decoded: {decoded}")
    
    # Test packet creation
    pkt = Packet.create(PacketType.PING, session_id=12345, sequence=1)
    packed = pkt.pack()
    print(f"\nPing packet ({len(packed)} bytes): {packed.hex()}")
    
    unpacked = Packet.unpack(packed)
    print(f"Unpacked: type={unpacked.header.packet_type.name}, session={unpacked.header.session_id}")
