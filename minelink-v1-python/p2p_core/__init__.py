"""
MineLink P2P Core
A custom P2P networking library for Minecraft server connectivity.
"""

from .stun import get_public_address, get_local_ip, STUNResult
from .protocol import (
    ConnectionInfo,
    Packet,
    PacketType,
    generate_peer_id,
    generate_session_id
)
from .transport import ReliableTransport, TCPBridge, PeerConnection
from .network_manager import NetworkManager, NetworkMode, NetworkConfig

__all__ = [
    # STUN
    'get_public_address',
    'STUNResult',
    
    # Protocol
    'ConnectionInfo',
    'Packet',
    'PacketType',
    'generate_peer_id',
    'generate_session_id',
    
    # Transport
    'ReliableTransport',
    'TCPBridge',
    'PeerConnection',
    
    # Network Manager
    'NetworkManager',
    'NetworkMode',
    'NetworkConfig',
]

__version__ = '0.1.0'

