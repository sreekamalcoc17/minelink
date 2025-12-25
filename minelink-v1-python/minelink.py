"""
MineLink - Minecraft P2P Connectivity Tool
A modern, dark-themed GUI application for hosting and joining Minecraft servers.
"""

import customtkinter as ctk
from customtkinter import CTkImage
import threading
import logging
import sys
import os
from pathlib import Path
from typing import Optional
import time

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from p2p_core import NetworkManager, NetworkMode, get_public_address

# Configure logging - DEBUG level for detailed tracing
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Theme Configuration
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Colors
COLORS = {
    "bg_dark": "#0d1117",
    "bg_card": "#161b22",
    "bg_input": "#21262d",
    "accent": "#238636",
    "accent_hover": "#2ea043",
    "accent_blue": "#1f6feb",
    "accent_purple": "#8957e5",
    "text_primary": "#f0f6fc",
    "text_secondary": "#8b949e",
    "border": "#30363d",
    "success": "#238636",
    "warning": "#d29922",
    "error": "#f85149",
}


class StatusIndicator(ctk.CTkFrame):
    """A status indicator with colored dot and text."""
    
    def __init__(self, master, text: str = "Offline", color: str = "gray", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        
        self.dot = ctk.CTkLabel(
            self,
            text="●",
            font=("Segoe UI", 12),
            text_color=color,
            width=20
        )
        self.dot.pack(side="left")
        
        self.label = ctk.CTkLabel(
            self,
            text=text,
            font=("Segoe UI", 13),
            text_color=COLORS["text_secondary"]
        )
        self.label.pack(side="left", padx=(2, 0))
    
    def set_status(self, text: str, color: str):
        """Update status."""
        self.dot.configure(text_color=color)
        self.label.configure(text=text)


class PeerCard(ctk.CTkFrame):
    """A card displaying peer information."""
    
    def __init__(
        self,
        master,
        peer_id: str,
        status: str = "Saved",
        host_ip: str = "localhost",
        host_port: int = 25565,
        ping_ms: int = -1,
        on_connect: callable = None,
        on_remove: callable = None,
        on_copy_address: callable = None,
        **kwargs
    ):
        super().__init__(
            master,
            fg_color=COLORS["bg_card"],
            corner_radius=10,
            border_width=1,
            border_color=COLORS["border"],
            **kwargs
        )
        
        self.peer_id = peer_id
        self.host_ip = host_ip
        self.host_port = host_port
        self.ping_ms = ping_ms
        self.on_connect = on_connect
        self.on_remove = on_remove
        self.on_copy_address = on_copy_address
        self._is_connected = False
        
        self.grid_columnconfigure(1, weight=1)
        
        # Icon
        icon_label = ctk.CTkLabel(
            self,
            text="👤",
            font=("Segoe UI Emoji", 24),
            width=40
        )
        icon_label.grid(row=0, column=0, rowspan=2, padx=15, pady=10)
        
        # Peer ID
        id_label = ctk.CTkLabel(
            self,
            text=peer_id,
            font=("Segoe UI Semibold", 14),
            text_color=COLORS["text_primary"],
            anchor="w"
        )
        id_label.grid(row=0, column=1, sticky="w", padx=5, pady=(10, 0))
        
        # Status / Server Address frame
        self.status_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.status_frame.grid(row=1, column=1, sticky="w", padx=5, pady=(0, 10))
        
        # Status label
        self.status_label = ctk.CTkLabel(
            self.status_frame,
            text=status,
            font=("Segoe UI", 12),
            text_color=COLORS["text_secondary"],
            anchor="w"
        )
        self.status_label.pack(side="left")
        
        # Server address (shown when connected)
        self.server_addr_label = ctk.CTkLabel(
            self.status_frame,
            text="",
            font=("Consolas", 11),
            text_color=COLORS["accent_blue"],
            anchor="w"
        )
        
        # Ping label (shown when connected)
        self.ping_label = ctk.CTkLabel(
            self.status_frame,
            text="",
            font=("Segoe UI", 11),
            text_color=COLORS["text_secondary"],
            anchor="w"
        )
        
        # Buttons frame
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=0, column=2, rowspan=2, padx=10, pady=10)
        
        # Copy Server Address button (hidden initially)
        self.copy_btn = ctk.CTkButton(
            btn_frame,
            text="📋 Copy IP",
            width=85,
            height=32,
            font=("Segoe UI", 11),
            fg_color=COLORS["accent_blue"],
            hover_color="#388bfd",
            command=self._on_copy
        )
        
        # Connect button
        self.connect_btn = ctk.CTkButton(
            btn_frame,
            text="Connect",
            width=80,
            height=32,
            font=("Segoe UI", 12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_connect
        )
        self.connect_btn.pack(side="left", padx=5)
        
        # Remove button
        remove_btn = ctk.CTkButton(
            btn_frame,
            text="✕",
            width=32,
            height=32,
            font=("Segoe UI", 14),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["error"],
            command=self._on_remove
        )
        remove_btn.pack(side="left")
    
    def _on_connect(self):
        if self.on_connect:
            self.on_connect(self.peer_id)
    
    def _on_remove(self):
        if self.on_remove:
            self.on_remove(self.peer_id)
    
    def _on_copy(self):
        """Copy server address to clipboard for Minecraft.
        
        For CLIENT mode: copies localhost:port (where TCP bridge listens)
        The TCP bridge forwards traffic to the host via the P2P tunnel.
        """
        # Always use localhost since that's where the TCP bridge listens
        server_addr = f"localhost:{self.host_port}"
        self.clipboard_clear()
        self.clipboard_append(server_addr)
        # Visual feedback
        self.copy_btn.configure(text="✓ Copied!")
        self.after(1500, lambda: self.copy_btn.configure(text="📋 Copy IP"))
        if self.on_copy_address:
            self.on_copy_address(server_addr)
    
    def update_ping(self, ping_ms: int):
        """Update the ping display."""
        self.ping_ms = ping_ms
        if self._is_connected and ping_ms >= 0:
            if ping_ms < 50:
                color = COLORS["success"]
            elif ping_ms < 150:
                color = COLORS["warning"]
            else:
                color = COLORS["error"]
            self.ping_label.configure(text=f" • {ping_ms}ms", text_color=color)
            if not self.ping_label.winfo_ismapped():
                self.ping_label.pack(side="left")
    
    def set_connected(self, connected: bool, host_ip: str = None, host_port: int = None):
        """Update connection status."""
        self._is_connected = connected
        if host_ip:
            self.host_ip = host_ip
        if host_port:
            self.host_port = host_port
            
        if connected:
            server_addr = f"{self.host_ip}:{self.host_port}"
            self.status_label.configure(text="Connected • ", text_color=COLORS["success"])
            self.server_addr_label.configure(text=server_addr)
            self.server_addr_label.pack(side="left")
            self.connect_btn.configure(text="Disconnect", fg_color=COLORS["error"])
            self.copy_btn.pack(side="left", padx=(0, 5))
            self.copy_btn.lift()
        else:
            self.status_label.configure(text="Saved", text_color=COLORS["text_secondary"])
            self.server_addr_label.pack_forget()
            self.ping_label.pack_forget()
            self.connect_btn.configure(text="Connect", fg_color=COLORS["accent"])
            self.copy_btn.pack_forget()




class MineLink(ctk.CTk):
    """Main MineLink Application Window."""
    
    def __init__(self):
        super().__init__()
        
        # Window setup
        self.title("MineLink")
        self.geometry("900x650")
        self.minsize(800, 600)
        self.configure(fg_color=COLORS["bg_dark"])
        
        # Network manager
        self.network: Optional[NetworkManager] = None
        self._mode = NetworkMode.CLIENT
        self._minecraft_port = 25565  # Default Minecraft port
        
        # Peer cards
        self.peer_cards: dict = {}
        
        # Build UI
        self._create_ui()
        
        # Start update loop
        self._start_update_loop()
    
    def _create_ui(self):
        """Create the main UI layout."""
        # Configure grid
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        # ===== Header =====
        header = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], height=60)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_propagate(False)
        
        # Logo
        logo_label = ctk.CTkLabel(
            header,
            text="⛏️ MineLink",
            font=("Segoe UI Emoji", 24, "bold"),
            text_color=COLORS["text_primary"]
        )
        logo_label.pack(side="left", padx=20, pady=15)
        
        # Status indicator
        self.status = StatusIndicator(header, "Not Started", "gray")
        self.status.pack(side="right", padx=20)
        
        # ===== Main Content =====
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=1, column=0, sticky="nsew", padx=20, pady=20)
        main.grid_columnconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=2)
        main.grid_rowconfigure(0, weight=1)
        
        # ===== Left Panel - Controls =====
        left_panel = ctk.CTkFrame(
            main,
            fg_color=COLORS["bg_card"],
            corner_radius=15,
            border_width=1,
            border_color=COLORS["border"]
        )
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        
        self._create_left_panel(left_panel)
        
        # ===== Right Panel - Peers =====
        right_panel = ctk.CTkFrame(
            main,
            fg_color=COLORS["bg_card"],
            corner_radius=15,
            border_width=1,
            border_color=COLORS["border"]
        )
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        
        self._create_right_panel(right_panel)
    
    def _create_left_panel(self, parent):
        """Create the left control panel."""
        # Mode Selection
        mode_label = ctk.CTkLabel(
            parent,
            text="Mode",
            font=("Segoe UI Semibold", 14),
            text_color=COLORS["text_primary"]
        )
        mode_label.pack(anchor="w", padx=20, pady=(20, 10))
        
        self.mode_var = ctk.StringVar(value="client")
        
        mode_frame = ctk.CTkFrame(parent, fg_color="transparent")
        mode_frame.pack(fill="x", padx=20)
        
        host_radio = ctk.CTkRadioButton(
            mode_frame,
            text="Host (Server)",
            variable=self.mode_var,
            value="host",
            font=("Segoe UI", 13),
            fg_color=COLORS["accent"],
            command=self._on_mode_change
        )
        host_radio.pack(anchor="w", pady=5)
        
        client_radio = ctk.CTkRadioButton(
            mode_frame,
            text="Client (Join)",
            variable=self.mode_var,
            value="client",
            font=("Segoe UI", 13),
            fg_color=COLORS["accent"],
            command=self._on_mode_change
        )
        client_radio.pack(anchor="w", pady=5)
        
        # Minecraft Port Configuration
        self.port_label = ctk.CTkLabel(
            parent,
            text="Minecraft Server Port (Host Only)",
            font=("Segoe UI Semibold", 14),
            text_color=COLORS["text_primary"]
        )
        self.port_label.pack(anchor="w", padx=20, pady=(20, 5))
        
        self.port_desc = ctk.CTkLabel(
            parent,
            text="Host: Port where YOUR LAN world is open\nClient: You'll connect to localhost:25565",
            font=("Segoe UI", 11),
            text_color=COLORS["text_secondary"],
            justify="left"
        )
        self.port_desc.pack(anchor="w", padx=20, pady=(0, 5))
        
        self.port_entry = ctk.CTkEntry(
            parent,
            height=40,
            placeholder_text="25565",
            font=("Consolas", 13),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"]
        )
        self.port_entry.pack(fill="x", padx=20)
        self.port_entry.insert(0, "25565")  # Default value
        self.port_entry.configure(state="disabled")  # Disabled by default (Client mode)
        
        # Divider
        divider = ctk.CTkFrame(parent, fg_color=COLORS["border"], height=1)
        divider.pack(fill="x", padx=20, pady=20)
        
        # My Connection Code
        code_label = ctk.CTkLabel(
            parent,
            text="My Connection Code",
            font=("Segoe UI Semibold", 14),
            text_color=COLORS["text_primary"]
        )
        code_label.pack(anchor="w", padx=20)
        
        code_desc = ctk.CTkLabel(
            parent,
            text="Share this with friends to let them connect",
            font=("Segoe UI", 11),
            text_color=COLORS["text_secondary"]
        )
        code_desc.pack(anchor="w", padx=20, pady=(0, 10))
        
        self.code_entry = ctk.CTkEntry(
            parent,
            height=40,
            font=("Consolas", 11),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            state="readonly"
        )
        self.code_entry.pack(fill="x", padx=20)
        
        copy_btn = ctk.CTkButton(
            parent,
            text="📋 Copy Code",
            height=36,
            font=("Segoe UI", 13),
            fg_color=COLORS["accent_blue"],
            hover_color="#388bfd",
            command=self._copy_code
        )
        copy_btn.pack(fill="x", padx=20, pady=(10, 0))
        
        # Divider
        divider2 = ctk.CTkFrame(parent, fg_color=COLORS["border"], height=1)
        divider2.pack(fill="x", padx=20, pady=20)
        
        # Start/Stop Button
        self.start_btn = ctk.CTkButton(
            parent,
            text="▶ Start Network",
            height=50,
            font=("Segoe UI Semibold", 15),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._toggle_network
        )
        self.start_btn.pack(fill="x", padx=20, pady=(0, 10))
        
        # Stats
        stats_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_input"], corner_radius=10)
        stats_frame.pack(fill="x", padx=20, pady=10)
        
        self.stats_label = ctk.CTkLabel(
            stats_frame,
            text="Network Statistics\n─────────────────\nUptime: --\nPeers: 0\nSent: 0 KB\nReceived: 0 KB",
            font=("Consolas", 11),
            text_color=COLORS["text_secondary"],
            justify="left"
        )
        self.stats_label.pack(padx=15, pady=15, anchor="w")
    
    def _create_right_panel(self, parent):
        """Create the right peers panel."""
        # Header
        header_frame = ctk.CTkFrame(parent, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(20, 10))
        
        peers_label = ctk.CTkLabel(
            header_frame,
            text="Peers",
            font=("Segoe UI Semibold", 16),
            text_color=COLORS["text_primary"]
        )
        peers_label.pack(side="left")
        
        self.peers_count = ctk.CTkLabel(
            header_frame,
            text="0 connected",
            font=("Segoe UI", 12),
            text_color=COLORS["text_secondary"]
        )
        self.peers_count.pack(side="right")
        
        # Add Peer
        add_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_input"], corner_radius=10)
        add_frame.pack(fill="x", padx=20, pady=10)
        add_frame.grid_columnconfigure(0, weight=1)
        
        add_label = ctk.CTkLabel(
            add_frame,
            text="Add peer by code",
            font=("Segoe UI", 12),
            text_color=COLORS["text_secondary"]
        )
        add_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(15, 5))
        
        self.add_entry = ctk.CTkEntry(
            add_frame,
            height=40,
            placeholder_text="Paste connection code here...",
            font=("Consolas", 11),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"]
        )
        self.add_entry.grid(row=1, column=0, sticky="ew", padx=(15, 10), pady=(0, 15))
        
        add_btn = ctk.CTkButton(
            add_frame,
            text="Add",
            width=80,
            height=40,
            font=("Segoe UI", 13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._add_peer
        )
        add_btn.grid(row=1, column=1, padx=(0, 15), pady=(0, 15))
        
        # Peers List (Scrollable)
        self.peers_scroll = ctk.CTkScrollableFrame(
            parent,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_input"],
            scrollbar_button_hover_color=COLORS["border"]
        )
        self.peers_scroll.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Empty state
        self.empty_label = ctk.CTkLabel(
            self.peers_scroll,
            text="No peers added yet.\nAdd a peer using their connection code above.",
            font=("Segoe UI", 13),
            text_color=COLORS["text_secondary"],
            justify="center"
        )
        self.empty_label.pack(expand=True, pady=50)
    
    def _on_mode_change(self):
        """Handle mode change."""
        mode = self.mode_var.get()
        self._mode = NetworkMode.HOST if mode == "host" else NetworkMode.CLIENT
        logger.info(f"Mode changed to: {self._mode.value}")
        
        # Enable port field only for Host mode and update description
        if self._mode == NetworkMode.HOST:
            self.port_entry.configure(state="normal")
            self.port_label.configure(text="Minecraft LAN Port")
            self.port_desc.configure(
                text="Enter the port shown when you 'Open to LAN' in Minecraft"
            )
        else:
            self.port_entry.configure(state="disabled")
            self.port_label.configure(text="Minecraft Server Port (Host Only)")
            self.port_desc.configure(
                text="Client mode: Connect to localhost:25565 in Minecraft"
            )
    
    def _toggle_network(self):
        """Start or stop the network."""
        if self.network and self.network.is_running:
            self._stop_network()
        else:
            self._start_network()
    
    def _start_network(self):
        """Start the network."""
        self.status.set_status("Starting...", COLORS["warning"])
        self.start_btn.configure(state="disabled")
        
        # Get port from entry
        try:
            port = int(self.port_entry.get().strip() or "25565")
        except ValueError:
            port = 25565
        self._minecraft_port = port
        
        def start_thread():
            try:
                self.network = NetworkManager(
                    mode=self._mode,
                    minecraft_port=port,
                    on_status_change=self._on_status,
                    on_peer_connect=self._on_peer_connect,
                    on_peer_disconnect=self._on_peer_disconnect
                )
                
                success = self.network.start()
                
                if success:
                    self.after(0, self._on_network_started)
                else:
                    self.after(0, lambda: self._on_network_error("Failed to start"))
                    
            except Exception as e:
                logger.exception("Failed to start network")
                self.after(0, lambda: self._on_network_error(str(e)))
        
        threading.Thread(target=start_thread, daemon=True).start()
    
    def _on_network_started(self):
        """Called when network starts successfully."""
        self.status.set_status("Running", COLORS["success"])
        self.start_btn.configure(
            text="⏹ Stop Network",
            fg_color=COLORS["error"],
            hover_color="#da3633",
            state="normal"
        )
        
        # Update connection code
        if self.network and self.network.connection_code:
            self.code_entry.configure(state="normal")
            self.code_entry.delete(0, "end")
            self.code_entry.insert(0, self.network.connection_code)
            self.code_entry.configure(state="readonly")
        
        # Load saved peers
        self._refresh_peers()
    
    def _on_network_error(self, error: str):
        """Called on network error."""
        self.status.set_status(f"Error: {error}", COLORS["error"])
        self.start_btn.configure(state="normal")
    
    def _stop_network(self):
        """Stop the network."""
        if self.network:
            self.network.stop()
            self.network = None
        
        self.status.set_status("Stopped", "gray")
        self.start_btn.configure(
            text="▶ Start Network",
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"]
        )
        
        self.code_entry.configure(state="normal")
        self.code_entry.delete(0, "end")
        self.code_entry.configure(state="readonly")
    
    def _on_status(self, msg: str):
        """Handle status update from network."""
        logger.info(f"Status: {msg}")
    
    def _on_peer_connect(self, peer_id: str):
        """Handle peer connection."""
        self.after(0, lambda: self._update_peer_status(peer_id, True))
    
    def _on_peer_disconnect(self, peer_id: str):
        """Handle peer disconnection."""
        self.after(0, lambda: self._update_peer_status(peer_id, False))
    
    def _update_peer_status(self, peer_id: str, connected: bool):
        """Update peer card status."""
        if peer_id in self.peer_cards:
            self.peer_cards[peer_id].set_connected(connected)
        self._update_peer_count()
    
    def _copy_code(self):
        """Copy connection code to clipboard."""
        code = self.code_entry.get()
        if code:
            self.clipboard_clear()
            self.clipboard_append(code)
            # Visual feedback
            original_text = "📋 Copy Code"
            self.after(0, lambda: None)  # Placeholder for button text change
    
    def _add_peer(self):
        """Add a peer from the entry."""
        code = self.add_entry.get().strip()
        if not code:
            return
        
        if not self.network or not self.network.is_running:
            self.status.set_status("Start network first!", COLORS["warning"])
            return
        
        peer_id = self.network.add_peer_from_code(code)
        if peer_id:
            self._refresh_peers()
            self.add_entry.delete(0, "end")
        else:
            self.status.set_status("Invalid code", COLORS["error"])
    
    def _refresh_peers(self):
        """Refresh the peers list."""
        # Clear existing cards
        for card in self.peer_cards.values():
            card.destroy()
        self.peer_cards.clear()
        
        if not self.network:
            return
        
        saved_peers = self.network.get_saved_peers()
        connected = set(self.network.get_connected_peers())
        
        if saved_peers:
            self.empty_label.pack_forget()
            
            for peer_info in saved_peers:
                peer_id = peer_info["peer_id"]
                is_connected = peer_id in connected
                
                # Get host IP from peer info (public IP of the host)
                host_ip = peer_info.get("public_ip", "localhost")
                host_port = self._minecraft_port  # Use configured port
                
                card = PeerCard(
                    self.peers_scroll,
                    peer_id=peer_id,
                    status="Connected" if is_connected else "Saved",
                    host_ip=host_ip,
                    host_port=host_port,
                    on_connect=self._on_peer_card_connect,
                    on_remove=self._on_peer_card_remove,
                    on_copy_address=self._on_copy_server_address
                )
                card.pack(fill="x", padx=10, pady=5)
                
                if is_connected:
                    card.set_connected(True, host_ip=host_ip, host_port=host_port)
                
                self.peer_cards[peer_id] = card
        else:
            self.empty_label.pack(expand=True, pady=50)
        
        self._update_peer_count()
    
    def _on_peer_card_connect(self, peer_id: str):
        """Handle connect button on peer card."""
        if not self.network:
            return
        
        def connect_thread():
            success = self.network.connect_to_peer(peer_id)
            self.after(0, lambda: self._update_peer_status(peer_id, success))
        
        threading.Thread(target=connect_thread, daemon=True).start()
    
    def _on_peer_card_remove(self, peer_id: str):
        """Handle remove button on peer card."""
        if not self.network:
            return
        
        self.network.transport.remove_peer(peer_id)
        
        # Remove from config
        if peer_id in self.network.config.saved_peers:
            del self.network.config.saved_peers[peer_id]
            self.network.config.save(self.network.config_path)
        
        self._refresh_peers()
    
    def _on_copy_server_address(self, server_addr: str):
        """Handle server address copy - show status feedback."""
        self.status.set_status(f"Copied: {server_addr}", COLORS["success"])
    
    def _update_peer_count(self):
        """Update the peer count label."""
        if self.network:
            connected = len(self.network.get_connected_peers())
            total = len(self.network.get_saved_peers())
            self.peers_count.configure(text=f"{connected}/{total} connected")
        else:
            self.peers_count.configure(text="0 connected")
    
    def _start_update_loop(self):
        """Start the periodic update loop."""
        self._update_stats()
        self.after(1000, self._start_update_loop)
    
    def _update_stats(self):
        """Update statistics display and ping for peers."""
        if self.network and self.network.is_running:
            stats = self.network.get_stats()
            uptime = time.strftime("%H:%M:%S", time.gmtime(stats.uptime))
            sent_kb = stats.total_bytes_sent / 1024
            recv_kb = stats.total_bytes_recv / 1024
            
            text = (
                f"Network Statistics\n"
                f"─────────────────\n"
                f"Uptime: {uptime}\n"
                f"Peers: {stats.peers_connected}\n"
                f"Sent: {sent_kb:.1f} KB\n"
                f"Received: {recv_kb:.1f} KB"
            )
            self.stats_label.configure(text=text)
            
            # Update ping for each peer card
            for peer_id, card in self.peer_cards.items():
                ping_ms = self.network.get_peer_ping_ms(peer_id)
                if ping_ms >= 0:
                    card.update_ping(ping_ms)
    
    def on_closing(self):
        """Handle window close."""
        if self.network:
            self.network.stop()
        self.destroy()


def main():
    """Main entry point."""
    app = MineLink()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()


if __name__ == "__main__":
    main()
