package com.minelink.network;

import io.netty.bootstrap.ServerBootstrap;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.*;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.SocketChannel;
import io.netty.channel.socket.nio.NioServerSocketChannel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetSocketAddress;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * TCP Bridge for forwarding Minecraft traffic over the P2P tunnel.
 * 
 * In CLIENT mode:
 * - Listens on localhost:port for Minecraft client connections
 * - Forwards TCP data to the remote peer via ReliableTransport
 * - Receives data from ReliableTransport and sends to Minecraft client
 */
public class TcpBridge {

    private static final Logger log = LoggerFactory.getLogger(TcpBridge.class);

    private final ReliableTransport transport;
    private final String peerId;
    private final int localPort;

    private EventLoopGroup bossGroup;
    private EventLoopGroup workerGroup;
    private Channel serverChannel;

    private final Map<Integer, Channel> clients = new ConcurrentHashMap<>();
    private final AtomicInteger clientIdCounter = new AtomicInteger(0);

    private int actualPort;

    public TcpBridge(ReliableTransport transport, String peerId, int localPort) {
        this.transport = transport;
        this.peerId = peerId;
        this.localPort = localPort;
    }

    /**
     * Start the TCP bridge server.
     * 
     * @return true if started successfully
     */
    public boolean start() {
        bossGroup = new NioEventLoopGroup(1);
        workerGroup = new NioEventLoopGroup();

        try {
            ServerBootstrap bootstrap = new ServerBootstrap()
                    .group(bossGroup, workerGroup)
                    .channel(NioServerSocketChannel.class)
                    .childHandler(new ChannelInitializer<SocketChannel>() {
                        @Override
                        protected void initChannel(SocketChannel ch) {
                            int clientId = clientIdCounter.incrementAndGet();
                            clients.put(clientId, ch);

                            ch.pipeline().addLast(new TcpClientHandler(clientId));

                            log.info("[TcpBridge] Minecraft client connected (id={})", clientId);
                        }
                    })
                    .option(ChannelOption.SO_BACKLOG, 128)
                    .childOption(ChannelOption.SO_KEEPALIVE, true);

            // Try to bind to port, with fallback
            int portToTry = localPort;
            for (int attempt = 0; attempt < 10; attempt++) {
                try {
                    serverChannel = bootstrap.bind("127.0.0.1", portToTry).sync().channel();
                    actualPort = portToTry;
                    break;
                } catch (Exception e) {
                    log.warn("[TcpBridge] Port {} in use, trying {}", portToTry, portToTry + 1);
                    portToTry++;
                }
            }

            if (serverChannel == null) {
                log.error("[TcpBridge] Could not bind to any port");
                return false;
            }

            log.info("[TcpBridge] Listening on 127.0.0.1:{}", actualPort);
            return true;

        } catch (Exception e) {
            log.error("[TcpBridge] Failed to start", e);
            stop();
            return false;
        }
    }

    /**
     * Stop the TCP bridge.
     */
    public void stop() {
        for (Channel client : clients.values()) {
            client.close();
        }
        clients.clear();

        if (serverChannel != null) {
            serverChannel.close();
        }
        if (workerGroup != null) {
            workerGroup.shutdownGracefully();
        }
        if (bossGroup != null) {
            bossGroup.shutdownGracefully();
        }

        log.info("[TcpBridge] Stopped");
    }

    /**
     * Get the actual port the bridge is listening on.
     */
    public int getActualPort() {
        return actualPort;
    }

    /**
     * Receive data from the P2P transport and forward to Minecraft clients.
     */
    public void receiveData(byte[] data) {
        log.debug("[MC BRIDGE] Received {} bytes from P2P to forward to Minecraft client", data.length);

        if (clients.isEmpty()) {
            log.warn("[MC BRIDGE] No Minecraft clients connected to receive data! Connect Minecraft to localhost:{}",
                    actualPort);
            return;
        }

        ByteBuf buf = Unpooled.wrappedBuffer(data);
        int clientsForwarded = 0;

        for (Map.Entry<Integer, Channel> entry : clients.entrySet()) {
            Channel client = entry.getValue();
            if (client.isActive()) {
                client.writeAndFlush(buf.retainedDuplicate());
                clientsForwarded++;
                log.debug("[MC BRIDGE] Forwarded {} bytes to Minecraft client {}", data.length, entry.getKey());
            }
        }

        if (clientsForwarded == 0) {
            log.warn("[MC BRIDGE] No active Minecraft clients to forward data to!");
        }

        buf.release();
    }

    /**
     * Handler for TCP client connections.
     */
    private class TcpClientHandler extends ChannelInboundHandlerAdapter {

        private final int clientId;

        TcpClientHandler(int clientId) {
            this.clientId = clientId;
        }

        @Override
        public void channelRead(ChannelHandlerContext ctx, Object msg) {
            ByteBuf buf = (ByteBuf) msg;
            try {
                byte[] data = new byte[buf.readableBytes()];
                buf.readBytes(data);

                log.debug("[TcpBridge] Received {} bytes from Minecraft client {}", data.length, clientId);

                // Forward to peer via transport
                if (transport != null) {
                    transport.send(peerId, data);
                }
            } finally {
                buf.release();
            }
        }

        @Override
        public void channelInactive(ChannelHandlerContext ctx) {
            log.info("[TcpBridge] Minecraft client {} disconnected", clientId);
            clients.remove(clientId);
            // NOTE: Do NOT disconnect the P2P peer here!
            // Minecraft closes TCP connections frequently (e.g., server list ping).
            // The P2P tunnel should stay alive for the next connection.
        }

        @Override
        public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
            log.warn("[TcpBridge] Client {} error: {}", clientId, cause.getMessage());
            ctx.close();
            clients.remove(clientId);
        }
    }
}
