package com.minelink.network;

import com.minelink.model.Peer;
import com.minelink.network.protocol.Packet;
import com.minelink.network.protocol.PacketType;
import io.netty.bootstrap.Bootstrap;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.*;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.DatagramPacket;
import io.netty.channel.socket.nio.NioDatagramChannel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetSocketAddress;
import java.util.Map;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BiConsumer;
import java.util.function.Consumer;

/**
 * Reliable UDP transport using Netty.
 * Provides ordered, reliable delivery with hole punching support.
 */
public class ReliableTransport {

    private static final Logger log = LoggerFactory.getLogger(ReliableTransport.class);

    private final String myPeerId;
    private final int localPort;

    private EventLoopGroup group;
    private Channel channel;

    private final Map<String, Peer> peers = new ConcurrentHashMap<>();
    private final AtomicInteger sequenceNumber = new AtomicInteger(0);

    // Pending data awaiting ACK: seq -> (peer, data, sendTime)
    private final Map<Integer, PendingPacket> pendingAcks = new ConcurrentHashMap<>();

    // Callbacks
    private BiConsumer<String, byte[]> onDataReceived;
    private Consumer<String> onPeerConnected;
    private Consumer<String> onPeerDisconnected;

    // Background tasks
    private ScheduledExecutorService scheduler;

    private static final int MAX_RETRIES = 10;
    private static final int PING_INTERVAL_MS = 15000;
    private static final int PEER_TIMEOUT_MS = 60000;

    public ReliableTransport(String myPeerId, int localPort) {
        this.myPeerId = myPeerId;
        this.localPort = localPort;
    }

    /**
     * Start the transport.
     * 
     * @return The actual local address bound to
     */
    public InetSocketAddress start() throws Exception {
        group = new NioEventLoopGroup();

        Bootstrap bootstrap = new Bootstrap()
                .group(group)
                .channel(NioDatagramChannel.class)
                .option(ChannelOption.SO_BROADCAST, false)
                .option(ChannelOption.SO_REUSEADDR, true)
                .handler(new ChannelInitializer<NioDatagramChannel>() {
                    @Override
                    protected void initChannel(NioDatagramChannel ch) {
                        ch.pipeline().addLast(new PacketHandler());
                    }
                });

        // Bind to IPv4 specifically to avoid IPv6/IPv4 mismatch issues
        channel = bootstrap.bind("0.0.0.0", localPort).sync().channel();

        InetSocketAddress localAddr = (InetSocketAddress) channel.localAddress();
        log.info("Transport started on {}", localAddr);

        // Start background tasks
        scheduler = Executors.newScheduledThreadPool(2);
        scheduler.scheduleAtFixedRate(this::pingPeers, PING_INTERVAL_MS, PING_INTERVAL_MS, TimeUnit.MILLISECONDS);
        scheduler.scheduleAtFixedRate(this::checkTimeouts, 5000, 5000, TimeUnit.MILLISECONDS);
        scheduler.scheduleAtFixedRate(this::retransmitPending, 100, 100, TimeUnit.MILLISECONDS);

        return localAddr;
    }

    /**
     * Stop the transport.
     */
    public void stop() {
        if (scheduler != null) {
            scheduler.shutdown();
        }
        if (channel != null) {
            channel.close();
        }
        if (group != null) {
            group.shutdownGracefully();
        }
        peers.clear();
        pendingAcks.clear();
        log.info("Transport stopped");
    }

    /**
     * Add a peer to the transport.
     */
    public void addPeer(String peerId, InetSocketAddress publicAddr, InetSocketAddress localAddr) {
        Peer peer = new Peer(peerId, publicAddr, localAddr);
        peers.put(peerId, peer);
        log.info("Added peer: {} @ {}", peerId, publicAddr);
    }

    /**
     * Remove a peer from the transport.
     */
    public void removePeer(String peerId) {
        Peer peer = peers.remove(peerId);
        if (peer != null) {
            log.info("Removed peer: {}", peerId);
            if (peer.isConnected() && onPeerDisconnected != null) {
                onPeerDisconnected.accept(peerId);
            }
        }
    }

    /**
     * Perform UDP hole punching to establish connection.
     * Tries multiple addresses: localhost (for same machine), local IP (same
     * network), public IP
     * 
     * @return true if connection established
     */
    public boolean punch(String peerId) {
        Peer peer = peers.get(peerId);
        if (peer == null) {
            log.warn("Punch failed: peer {} not found", peerId);
            return false;
        }

        log.info("Starting hole punch to {}", peerId);

        Packet punchPacket = Packet.punch(myPeerId);

        // Build list of addresses to try (including port prediction for symmetric NAT)
        java.util.List<InetSocketAddress> addressesToTry = new java.util.ArrayList<>();

        // If peer has local address with same public IP as us, they might be on same
        // network
        // Try localhost first (same machine testing)
        int peerPort = peer.getLocalAddress() != null ? peer.getLocalAddress().getPort()
                : peer.getPublicAddress().getPort();
        addressesToTry.add(new InetSocketAddress("127.0.0.1", peerPort));

        // Try local address
        if (peer.getLocalAddress() != null) {
            addressesToTry.add(peer.getLocalAddress());
        }

        // Try public address
        addressesToTry.add(peer.getPublicAddress());

        // PORT PREDICTION for symmetric NAT
        // The STUN-discovered port might differ from the actual port used for this
        // connection
        // Try nearby ports (+/- 10) to handle sequential port allocation
        String publicIp = peer.getPublicAddress().getAddress().getHostAddress();
        int basePort = peer.getPublicAddress().getPort();
        for (int delta = -10; delta <= 10; delta++) {
            if (delta == 0)
                continue; // Already added
            int predictedPort = basePort + delta;
            if (predictedPort > 0 && predictedPort < 65536) {
                addressesToTry.add(new InetSocketAddress(publicIp, predictedPort));
            }
        }

        log.info("Will try {} addresses for hole punch to {}", addressesToTry.size(), peerId);

        for (int attempt = 1; attempt <= 20; attempt++) { // Increased attempts
            log.debug("Punch attempt {}/20 to {}", attempt, peerId);

            // Send to all addresses
            for (InetSocketAddress addr : addressesToTry) {
                sendRaw(punchPacket.encode(), addr);
            }

            // Wait for response
            try {
                Thread.sleep(300); // Slightly faster
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return false;
            }

            if (peer.isConnected()) {
                log.info("Hole punch successful to {}", peerId);
                return true;
            }
        }

        log.warn("Hole punch failed to {}", peerId);
        return false;
    }

    // Message ID counter for fragmentation
    private final AtomicInteger messageIdCounter = new AtomicInteger(0);

    // Safe chunk size - must fit in UDP datagram without IP fragmentation
    private static final int MAX_CHUNK_SIZE = 1200;

    /**
     * Send data to a peer reliably.
     * Large data is chunked to avoid UDP fragmentation. Chunks are sent with
     * sequential sequence numbers to ensure ordered delivery.
     */
    public boolean send(String peerId, byte[] data) {
        Peer peer = peers.get(peerId);
        if (peer == null || !peer.isConnected()) {
            log.warn("Cannot send: peer {} not connected", peerId);
            return false;
        }

        if (data.length <= MAX_CHUNK_SIZE) {
            // Small data - send directly with streamId=0 (not fragmented)
            int seq = sequenceNumber.incrementAndGet();
            Packet packet = Packet.data(myPeerId, seq, 0, data);
            byte[] encoded = packet.encode();
            pendingAcks.put(seq, new PendingPacket(peerId, encoded, System.currentTimeMillis(), 0));
            sendRaw(encoded, peer.getPublicAddress());
            log.debug("Sent data seq={} to {} ({} bytes)", seq, peerId, data.length);
            return true;
        }

        // Large data - chunk it
        // Use messageId in streamId field (high 8 bits) and chunk index (low 8 bits)
        int messageId = messageIdCounter.incrementAndGet() & 0xFF;
        int numChunks = (data.length + MAX_CHUNK_SIZE - 1) / MAX_CHUNK_SIZE;

        log.debug("Chunking {} bytes into {} chunks (msgId={})", data.length, numChunks, messageId);

        int offset = 0;
        for (int chunkIndex = 0; chunkIndex < numChunks; chunkIndex++) {
            int chunkSize = Math.min(MAX_CHUNK_SIZE - 4, data.length - offset); // Reserve 4 bytes for header
            byte[] chunk = new byte[chunkSize + 4];

            // Chunk header: [totalChunks(1), chunkIndex(1), totalLen high(1), totalLen
            // low(1)]
            chunk[0] = (byte) numChunks;
            chunk[1] = (byte) chunkIndex;
            chunk[2] = (byte) ((data.length >> 8) & 0xFF);
            chunk[3] = (byte) (data.length & 0xFF);
            System.arraycopy(data, offset, chunk, 4, chunkSize);

            int seq = sequenceNumber.incrementAndGet();
            // StreamId encodes: messageId (high byte) | marker 0x80 (indicates fragmented)
            int streamId = (messageId << 8) | 0x80 | (chunkIndex & 0x7F);

            Packet packet = Packet.data(myPeerId, seq, streamId, chunk);
            byte[] encoded = packet.encode();
            pendingAcks.put(seq, new PendingPacket(peerId, encoded, System.currentTimeMillis(), 0));
            sendRaw(encoded, peer.getPublicAddress());

            offset += chunkSize;
        }

        return true;
    }

    /**
     * Get a peer by ID.
     */
    public Peer getPeer(String peerId) {
        return peers.get(peerId);
    }

    /**
     * Get all peers.
     */
    public Map<String, Peer> getPeers() {
        return peers;
    }

    /**
     * Send punch packets to a peer to help establish connection.
     * Called from background thread for continuous punching.
     */
    public void sendPunchPackets(String peerId) {
        Peer peer = peers.get(peerId);
        if (peer == null || peer.isConnected()) {
            return;
        }

        Packet punchPacket = Packet.punch(myPeerId);
        byte[] punchData = punchPacket.encode();

        // Send to localhost (same machine)
        int peerPort = peer.getLocalAddress() != null ? peer.getLocalAddress().getPort()
                : peer.getPublicAddress().getPort();
        sendRaw(punchData, new InetSocketAddress("127.0.0.1", peerPort));

        // Send to local address
        if (peer.getLocalAddress() != null) {
            sendRaw(punchData, peer.getLocalAddress());
        }

        // Send to public address
        sendRaw(punchData, peer.getPublicAddress());

        // PORT PREDICTION for symmetric NAT - also try nearby ports
        String publicIp = peer.getPublicAddress().getAddress().getHostAddress();
        int basePort = peer.getPublicAddress().getPort();
        for (int delta = -5; delta <= 5; delta++) {
            if (delta == 0)
                continue;
            int predictedPort = basePort + delta;
            if (predictedPort > 0 && predictedPort < 65536) {
                sendRaw(punchData, new InetSocketAddress(publicIp, predictedPort));
            }
        }

        log.debug("Sent punch packets to {} (public: {})", peerId, peer.getPublicAddress());
    }

    // Callbacks
    public void setOnDataReceived(BiConsumer<String, byte[]> callback) {
        this.onDataReceived = callback;
    }

    public void setOnPeerConnected(Consumer<String> callback) {
        this.onPeerConnected = callback;
    }

    public void setOnPeerDisconnected(Consumer<String> callback) {
        this.onPeerDisconnected = callback;
    }

    /**
     * Get the local port for STUN queries.
     */
    public int getLocalPort() {
        if (channel != null) {
            InetSocketAddress addr = (InetSocketAddress) channel.localAddress();
            return addr.getPort();
        }
        return 0;
    }

    // Internal packet handler
    private class PacketHandler extends SimpleChannelInboundHandler<DatagramPacket> {
        @Override
        protected void channelRead0(ChannelHandlerContext ctx, DatagramPacket msg) {
            ByteBuf buf = msg.content();
            byte[] data = new byte[buf.readableBytes()];
            buf.readBytes(data);

            InetSocketAddress sender = msg.sender();
            // Use trace level to avoid flooding logs
            log.trace(">>> RECV {} bytes from {}", data.length, sender);
            handlePacket(data, sender);
        }

        @Override
        public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
            log.error("!!! CHANNEL EXCEPTION: {}", cause.getMessage(), cause);
        }

        @Override
        public void channelActive(ChannelHandlerContext ctx) {
            log.info("=== CHANNEL ACTIVE: ready to receive packets ===");
        }

        @Override
        public void channelInactive(ChannelHandlerContext ctx) {
            log.warn("=== CHANNEL INACTIVE: no longer receiving packets ===");
        }
    }

    private void handlePacket(byte[] data, InetSocketAddress sender) {
        Packet packet = Packet.decode(data);
        if (packet == null) {
            log.trace("Ignoring non-MineLink packet from {}", sender);
            return;
        }

        String peerId = packet.getPeerId();
        PacketType type = packet.getType();

        // Only log non-repetitive packet types at higher levels
        if (type == PacketType.DATA || type == PacketType.DISCONNECT) {
            log.debug(">>> PACKET TYPE: {} from peer: {}", type, peerId);
        } else {
            log.trace(">>> PACKET TYPE: {} from peer: {}", type, peerId);
        }

        Peer peer = peers.get(peerId);

        switch (type) {
            case PUNCH -> handlePunch(peerId, sender);
            case PUNCH_ACK -> handlePunchAck(peerId, sender);
            case PING -> handlePing(peerId, packet.getSequenceNumber(), sender);
            case PONG -> handlePong(peerId, packet.getSequenceNumber());
            case DATA -> handleData(peerId, packet);
            case ACK -> handleAck(packet.getSequenceNumber());
            case DISCONNECT -> handleDisconnect(peerId);
        }
    }

    private void handlePunch(String peerId, InetSocketAddress sender) {
        Peer peer = peers.get(peerId);
        if (peer == null) {
            log.debug("Punch from unknown peer: {}", peerId);
            return;
        }

        // CRITICAL: For hole punching to work across different NATs,
        // we need to send packets back to BOTH the sender address AND the peer's public
        // address.
        // This opens up the NAT mapping in both directions.

        // Send PUNCH_ACK back to the sender (the address where we received the punch)
        sendRaw(Packet.punchAck(myPeerId).encode(), sender);

        // Also send PUNCH packets back to establish the reverse NAT mapping
        // This is critical for symmetric NAT traversal
        sendRaw(Packet.punch(myPeerId).encode(), sender);

        // If we have a different public address for this peer, send there too
        if (!sender.equals(peer.getPublicAddress())) {
            sendRaw(Packet.punch(myPeerId).encode(), peer.getPublicAddress());
            sendRaw(Packet.punchAck(myPeerId).encode(), peer.getPublicAddress());
        }

        if (!peer.isConnected()) {
            peer.setConnected(true);
            log.info("Connection established with {} (via punch from {})", peerId, sender);
            if (onPeerConnected != null) {
                onPeerConnected.accept(peerId);
            }
        }
    }

    private void handlePunchAck(String peerId, InetSocketAddress sender) {
        Peer peer = peers.get(peerId);
        if (peer == null)
            return;

        if (!peer.isConnected()) {
            peer.setConnected(true);
            log.info("Connection established with {} (via punch ack)", peerId);
            if (onPeerConnected != null) {
                onPeerConnected.accept(peerId);
            }
        }
    }

    private void handlePing(String peerId, int seq, InetSocketAddress sender) {
        Peer peer = peers.get(peerId);
        if (peer != null) {
            peer.updateLastSeen();
            sendRaw(Packet.pong(myPeerId, seq).encode(), sender);
        }
    }

    private void handlePong(String peerId, int seq) {
        Peer peer = peers.get(peerId);
        if (peer != null) {
            peer.updateLastSeen();

            // Calculate RTT
            PendingPacket pending = pendingAcks.remove(-seq); // Ping uses negative seq
            if (pending != null) {
                double rtt = System.currentTimeMillis() - pending.sendTime;
                peer.updateRtt(rtt);
                log.debug("Pong from {}, RTT={:.0f}ms", peerId, rtt);
            }
        }
    }

    // Reassembly buffer: key = "peerId:messageId", value = chunks and metadata
    private final Map<String, ReassemblyBuffer> reassemblyBuffers = new ConcurrentHashMap<>();

    private static class ReassemblyBuffer {
        final int totalChunks;
        final int totalLength;
        final byte[][] chunks;
        int receivedCount = 0;
        long createTime = System.currentTimeMillis();

        ReassemblyBuffer(int totalChunks, int totalLength) {
            this.totalChunks = totalChunks;
            this.totalLength = totalLength;
            this.chunks = new byte[totalChunks][];
        }

        boolean addChunk(int index, byte[] data) {
            if (index >= 0 && index < totalChunks && chunks[index] == null) {
                chunks[index] = data;
                receivedCount++;
                return true;
            }
            return false;
        }

        boolean isComplete() {
            return receivedCount >= totalChunks;
        }

        byte[] reassemble() {
            byte[] result = new byte[totalLength];
            int offset = 0;
            for (byte[] chunk : chunks) {
                if (chunk != null && offset + chunk.length <= totalLength) {
                    System.arraycopy(chunk, 0, result, offset, chunk.length);
                    offset += chunk.length;
                }
            }
            return result;
        }
    }

    private void handleData(String peerId, Packet packet) {
        Peer peer = peers.get(peerId);
        if (peer == null)
            return;

        peer.updateLastSeen();

        // Send ACK immediately
        sendRaw(Packet.ack(myPeerId, packet.getSequenceNumber()).encode(), peer.getPublicAddress());

        int streamId = packet.getStreamId();
        byte[] payload = packet.getPayload();

        // Check if this is a fragmented packet (streamId has 0x80 flag)
        if (streamId == 0) {
            // Not fragmented - deliver directly
            if (onDataReceived != null) {
                onDataReceived.accept(peerId, payload);
            }
        } else if ((streamId & 0x80) != 0) {
            // Fragmented packet - reassemble
            int messageId = (streamId >> 8) & 0xFF;
            int chunkIndex = streamId & 0x7F;

            if (payload.length < 4) {
                log.warn("Invalid fragment: payload too small");
                return;
            }

            // Parse chunk header
            int totalChunks = payload[0] & 0xFF;
            int expectedIndex = payload[1] & 0xFF;
            int totalLength = ((payload[2] & 0xFF) << 8) | (payload[3] & 0xFF);

            // Extract chunk data
            byte[] chunkData = new byte[payload.length - 4];
            System.arraycopy(payload, 4, chunkData, 0, chunkData.length);

            String bufferKey = peerId + ":" + messageId;
            ReassemblyBuffer buffer = reassemblyBuffers.computeIfAbsent(bufferKey,
                    k -> new ReassemblyBuffer(totalChunks, totalLength));

            buffer.addChunk(chunkIndex, chunkData);
            log.debug("Fragment {}/{} of msg {} from {} ({} bytes)",
                    chunkIndex + 1, totalChunks, messageId, peerId, chunkData.length);

            if (buffer.isComplete()) {
                byte[] reassembled = buffer.reassemble();
                reassemblyBuffers.remove(bufferKey);
                log.debug("Reassembled message {} from {} ({} bytes)", messageId, peerId, reassembled.length);

                if (onDataReceived != null) {
                    onDataReceived.accept(peerId, reassembled);
                }
            }
        }
    }

    private void handleAck(int seq) {
        PendingPacket removed = pendingAcks.remove(seq);
        if (removed != null) {
            log.debug("ACK received for seq={}", seq);
        }
    }

    private void handleDisconnect(String peerId) {
        Peer peer = peers.get(peerId);
        if (peer != null && peer.isConnected()) {
            peer.setConnected(false);
            log.info("Peer {} disconnected", peerId);
            if (onPeerDisconnected != null) {
                onPeerDisconnected.accept(peerId);
            }
        }
    }

    private void sendRaw(byte[] data, InetSocketAddress target) {
        if (channel != null && channel.isActive()) {
            ByteBuf buf = Unpooled.wrappedBuffer(data);
            channel.writeAndFlush(new DatagramPacket(buf, target));
            // Use trace level to avoid flooding logs - only visible with
            // -Dlogback.configurationFile with TRACE level
            log.trace("<<< SENT {} bytes to {}", data.length, target);
        } else {
            log.warn("<<< SEND FAILED - channel not active! target={}", target);
        }
    }

    private void pingPeers() {
        for (Map.Entry<String, Peer> entry : peers.entrySet()) {
            Peer peer = entry.getValue();
            if (peer.isConnected()) {
                int seq = sequenceNumber.incrementAndGet();
                Packet ping = Packet.ping(myPeerId, seq);

                // Store for RTT calculation
                pendingAcks.put(-seq, new PendingPacket(entry.getKey(), ping.encode(), System.currentTimeMillis(), 0));

                sendRaw(ping.encode(), peer.getPublicAddress());
            }
        }
    }

    private void checkTimeouts() {
        long now = System.currentTimeMillis();
        for (Map.Entry<String, Peer> entry : peers.entrySet()) {
            Peer peer = entry.getValue();
            if (peer.isConnected() && now - peer.getLastSeen() > PEER_TIMEOUT_MS) {
                log.warn("Peer {} timed out", entry.getKey());
                peer.setConnected(false);
                if (onPeerDisconnected != null) {
                    onPeerDisconnected.accept(entry.getKey());
                }
            }
        }
    }

    private void retransmitPending() {
        long now = System.currentTimeMillis();

        for (Map.Entry<Integer, PendingPacket> entry : pendingAcks.entrySet()) {
            int seq = entry.getKey();
            if (seq < 0)
                continue; // Skip ping packets

            PendingPacket pending = entry.getValue();
            Peer peer = peers.get(pending.peerId);
            if (peer == null || !peer.isConnected()) {
                pendingAcks.remove(seq);
                continue;
            }

            double rto = peer.getRto();
            if (now - pending.sendTime > rto) {
                if (pending.retries >= MAX_RETRIES) {
                    log.warn("Max retries reached for seq={}", seq);
                    pendingAcks.remove(seq);
                } else {
                    pending.retries++;
                    pending.sendTime = now;
                    sendRaw(pending.data, peer.getPublicAddress());
                    log.debug("Retransmit seq={} (attempt {})", seq, pending.retries);
                }
            }
        }
    }

    private static class PendingPacket {
        final String peerId;
        final byte[] data;
        long sendTime;
        int retries;

        PendingPacket(String peerId, byte[] data, long sendTime, int retries) {
            this.peerId = peerId;
            this.data = data;
            this.sendTime = sendTime;
            this.retries = retries;
        }
    }
}
