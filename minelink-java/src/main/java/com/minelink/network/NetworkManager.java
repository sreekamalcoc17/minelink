package com.minelink.network;

import com.minelink.model.ConnectionInfo;
import com.minelink.model.Peer;
import io.netty.bootstrap.Bootstrap;
import io.netty.buffer.ByteBuf;
import io.netty.channel.*;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.SocketChannel;
import io.netty.channel.socket.nio.NioSocketChannel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetSocketAddress;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/**
 * NetworkManager orchestrates all networking for MineLink.
 * Manages transport, TCP bridge, and Minecraft server connection.
 */
public class NetworkManager {

    private static final Logger log = LoggerFactory.getLogger(NetworkManager.class);

    public enum Mode {
        HOST, CLIENT
    }

    private final String myPeerId;
    private int minecraftPort;
    private Mode mode;

    private ReliableTransport transport;
    private TcpBridge tcpBridge;

    // For HOST mode: connection to Minecraft server
    private Channel minecraftChannel;
    private EventLoopGroup minecraftGroup;
    private volatile boolean minecraftConnected;

    // Saved peers
    private final ConcurrentHashMap<String, ConnectionInfo> savedPeers = new ConcurrentHashMap<>();

    // Connection info
    private ConnectionInfo myConnectionInfo;

    // Callbacks
    private Consumer<String> onStatusChange;
    private Consumer<String> onPeerConnected;
    private Consumer<String> onPeerDisconnected;

    private ExecutorService executor;
    private volatile boolean running;

    public NetworkManager() {
        this.myPeerId = "minelink-" + UUID.randomUUID().toString().substring(0, 8);
        this.minecraftPort = 25565;
        this.mode = Mode.CLIENT;
    }

    public void setMode(Mode mode) {
        this.mode = mode;
    }

    public Mode getMode() {
        return mode;
    }

    public void setMinecraftPort(int port) {
        this.minecraftPort = port;
    }

    public int getMinecraftPort() {
        return minecraftPort;
    }

    public String getMyPeerId() {
        return myPeerId;
    }

    public ConnectionInfo getMyConnectionInfo() {
        return myConnectionInfo;
    }

    // Callbacks
    public void setOnStatusChange(Consumer<String> callback) {
        this.onStatusChange = callback;
    }

    public void setOnPeerConnected(Consumer<String> callback) {
        this.onPeerConnected = callback;
    }

    public void setOnPeerDisconnected(Consumer<String> callback) {
        this.onPeerDisconnected = callback;
    }

    private void status(String msg) {
        log.info(msg);
        if (onStatusChange != null) {
            onStatusChange.accept(msg);
        }
    }

    /**
     * Start the network.
     */
    public boolean start() {
        if (running) {
            return false;
        }

        executor = Executors.newCachedThreadPool();

        try {
            status("Starting network...");

            // Create transport
            transport = new ReliableTransport(myPeerId, 0); // Random port
            InetSocketAddress localAddr = transport.start();

            status("Listening on " + localAddr);

            // Set up transport callbacks
            transport.setOnDataReceived(this::onDataReceived);
            transport.setOnPeerConnected(peerId -> {
                status("Peer connected: " + peerId);
                if (onPeerConnected != null)
                    onPeerConnected.accept(peerId);
            });
            transport.setOnPeerDisconnected(peerId -> {
                status("Peer disconnected: " + peerId);
                if (onPeerDisconnected != null)
                    onPeerDisconnected.accept(peerId);
            });

            // Discover public address via STUN (using a separate socket)
            status("Discovering public address...");
            StunClient.StunResult stun = StunClient.discover(5000);

            if (stun != null) {
                myConnectionInfo = new ConnectionInfo(
                        myPeerId, stun.publicIp, stun.publicPort,
                        localAddr.getAddress().getHostAddress(), localAddr.getPort());
                status("Public address: " + stun.publicIp + ":" + stun.publicPort);
            } else {
                status("Warning: Could not determine public address");
                myConnectionInfo = new ConnectionInfo(
                        myPeerId, "0.0.0.0", localAddr.getPort(),
                        localAddr.getAddress().getHostAddress(), localAddr.getPort());
            }

            // Mode-specific setup
            if (mode == Mode.HOST) {
                status("Running in HOST mode");
                status("Waiting for peers (will connect to MC on port " + minecraftPort + " when needed)");
            } else {
                status("Running in CLIENT mode");
            }

            running = true;
            status("Network started!");
            return true;

        } catch (Exception e) {
            log.error("Failed to start network", e);
            status("Failed to start: " + e.getMessage());
            stop();
            return false;
        }
    }

    /**
     * Stop the network.
     */
    public void stop() {
        running = false;

        if (tcpBridge != null) {
            tcpBridge.stop();
            tcpBridge = null;
        }

        if (minecraftChannel != null) {
            minecraftChannel.close();
            minecraftChannel = null;
        }

        if (minecraftGroup != null) {
            minecraftGroup.shutdownGracefully();
            minecraftGroup = null;
        }

        if (transport != null) {
            transport.stop();
            transport = null;
        }

        if (executor != null) {
            executor.shutdown();
            executor = null;
        }

        status("Network stopped");
    }

    /**
     * Add a peer from a connection code.
     */
    public boolean addPeer(String code) {
        try {
            ConnectionInfo info = ConnectionInfo.fromCode(code);

            if (info.getPeerId().equals(myPeerId)) {
                status("Cannot add yourself as a peer");
                return false;
            }

            savedPeers.put(info.getPeerId(), info);

            if (transport != null) {
                transport.addPeer(
                        info.getPeerId(),
                        new InetSocketAddress(info.getPublicIp(), info.getPublicPort()),
                        new InetSocketAddress(info.getLocalIp(), info.getLocalPort()));

                // Automatically start punching when peer is added
                // This helps establish connection when both sides add each other's codes
                executor.submit(() -> {
                    status("Starting auto-punch to " + info.getPeerId());
                    startBackgroundPunching(info.getPeerId());
                });
            }

            status("Added peer: " + info.getPeerId());
            return true;

        } catch (Exception e) {
            status("Invalid connection code: " + e.getMessage());
            return false;
        }
    }

    /**
     * Background punching - sends punch packets periodically to help establish
     * connection.
     */
    private void startBackgroundPunching(String peerId) {
        // Send punch packets in background for 30 seconds
        for (int i = 0; i < 30 && running; i++) {
            if (transport != null && transport.getPeer(peerId) != null) {
                if (transport.getPeer(peerId).isConnected()) {
                    log.info("Background punch: {} is now connected!", peerId);
                    return;
                }
                // Send punch packets to try to establish connection
                transport.sendPunchPackets(peerId);
            }
            try {
                Thread.sleep(1000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }

    /**
     * Connect to a peer.
     */
    public void connectToPeer(String peerId) {
        if (transport == null || !running) {
            status("Network not running");
            return;
        }

        executor.submit(() -> {
            status("Connecting to " + peerId + "...");

            boolean success = transport.punch(peerId);

            if (success) {
                status("Connected to " + peerId + "!");

                // In CLIENT mode, setup TCP bridge
                if (mode == Mode.CLIENT) {
                    tcpBridge = new TcpBridge(transport, peerId, minecraftPort);
                    if (tcpBridge.start()) {
                        status("TCP Bridge active on localhost:" + tcpBridge.getActualPort() +
                                " - Connect Minecraft to this address!");
                    } else {
                        status("Warning: TCP Bridge failed to start");
                    }
                }
            } else {
                status("Failed to connect to " + peerId);
            }
        });
    }

    /**
     * Get the connection code for sharing.
     */
    public String getConnectionCode() {
        if (myConnectionInfo != null) {
            return myConnectionInfo.toCode();
        }
        return null;
    }

    /**
     * Get peer by ID.
     */
    public Peer getPeer(String peerId) {
        if (transport != null) {
            return transport.getPeer(peerId);
        }
        return null;
    }

    /**
     * Get saved peers.
     */
    public ConcurrentHashMap<String, ConnectionInfo> getSavedPeers() {
        return savedPeers;
    }

    /**
     * Handle data received from a peer.
     */
    private void onDataReceived(String peerId, byte[] data) {
        log.debug("Received {} bytes from peer {}", data.length, peerId);

        if (mode == Mode.HOST) {
            // Forward to Minecraft server
            forwardToMinecraft(data);
        } else {
            // Forward to TCP bridge (Minecraft client)
            if (tcpBridge != null) {
                tcpBridge.receiveData(data);
            }
        }
    }

    /**
     * Forward data to Minecraft server (HOST mode).
     */
    private void forwardToMinecraft(byte[] data) {
        // Connect on-demand
        if (!minecraftConnected || minecraftChannel == null || !minecraftChannel.isActive()) {
            if (!connectToMinecraft()) {
                log.warn("Failed to connect to Minecraft server");
                return;
            }
        }

        minecraftChannel.writeAndFlush(io.netty.buffer.Unpooled.wrappedBuffer(data));
        log.debug("Forwarded {} bytes to Minecraft server", data.length);
    }

    /**
     * Connect to local Minecraft server (HOST mode).
     */
    private boolean connectToMinecraft() {
        try {
            if (minecraftGroup == null) {
                minecraftGroup = new NioEventLoopGroup();
            }

            Bootstrap bootstrap = new Bootstrap()
                    .group(minecraftGroup)
                    .channel(NioSocketChannel.class)
                    .handler(new ChannelInitializer<SocketChannel>() {
                        @Override
                        protected void initChannel(SocketChannel ch) {
                            ch.pipeline().addLast(new MinecraftServerHandler());
                        }
                    });

            status("Connecting to Minecraft server on port " + minecraftPort + "...");
            minecraftChannel = bootstrap.connect("127.0.0.1", minecraftPort).sync().channel();
            minecraftConnected = true;

            status("Connected to Minecraft server on port " + minecraftPort);
            return true;

        } catch (Exception e) {
            log.warn("Failed to connect to Minecraft server: {}", e.getMessage());
            status("Failed to connect to Minecraft server: " + e.getMessage());
            minecraftConnected = false;
            return false;
        }
    }

    /**
     * Handler for Minecraft server responses.
     */
    private class MinecraftServerHandler extends ChannelInboundHandlerAdapter {
        @Override
        public void channelRead(ChannelHandlerContext ctx, Object msg) {
            ByteBuf buf = (ByteBuf) msg;
            try {
                byte[] data = new byte[buf.readableBytes()];
                buf.readBytes(data);

                log.debug("Received {} bytes from Minecraft server", data.length);

                // Forward to all connected peers
                if (transport != null) {
                    for (Peer peer : transport.getPeers().values()) {
                        if (peer.isConnected()) {
                            transport.send(peer.getPeerId(), data);
                        }
                    }
                }
            } finally {
                buf.release();
            }
        }

        @Override
        public void channelInactive(ChannelHandlerContext ctx) {
            log.info("Minecraft server connection closed");
            minecraftConnected = false;
            status("Minecraft server connection closed");
        }

        @Override
        public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
            log.warn("Minecraft server error: {}", cause.getMessage());
            ctx.close();
            minecraftConnected = false;
        }
    }
}
