package com.minelink.network;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.*;
import java.nio.ByteBuffer;
import java.security.SecureRandom;

/**
 * STUN client for discovering public IP address and port.
 * Implements RFC 5389 basic binding request.
 */
public class StunClient {

    private static final Logger log = LoggerFactory.getLogger(StunClient.class);

    private static final String[] STUN_SERVERS = {
            "stun.l.google.com:19302",
            "stun1.l.google.com:19302",
            "stun2.l.google.com:19302",
            "stun.cloudflare.com:3478"
    };

    // STUN message types
    private static final short BINDING_REQUEST = 0x0001;
    private static final int MAGIC_COOKIE = 0x2112A442;

    // STUN attribute types
    private static final short MAPPED_ADDRESS = 0x0001;
    private static final short XOR_MAPPED_ADDRESS = 0x0020;

    /**
     * Result of STUN query.
     */
    public static class StunResult {
        public final String publicIp;
        public final int publicPort;
        public final String localIp;
        public final int localPort;

        public StunResult(String publicIp, int publicPort, String localIp, int localPort) {
            this.publicIp = publicIp;
            this.publicPort = publicPort;
            this.localIp = localIp;
            this.localPort = localPort;
        }

        @Override
        public String toString() {
            return String.format("StunResult{public=%s:%d, local=%s:%d}",
                    publicIp, publicPort, localIp, localPort);
        }
    }

    /**
     * Discover public address using STUN with a new socket.
     */
    public static StunResult discover(int timeout) {
        try (DatagramSocket socket = new DatagramSocket()) {
            socket.setSoTimeout(timeout);
            return discoverWithSocket(socket, timeout);
        } catch (Exception e) {
            log.error("STUN discovery failed", e);
            return null;
        }
    }

    /**
     * Discover public address using STUN with an existing socket.
     */
    public static StunResult discoverWithSocket(DatagramSocket socket, int timeout) {
        int originalTimeout = 0;
        try {
            originalTimeout = socket.getSoTimeout();
            socket.setSoTimeout(timeout);
        } catch (SocketException e) {
            // Ignore
        }

        try {
            for (String server : STUN_SERVERS) {
                StunResult result = queryServer(socket, server);
                if (result != null) {
                    return result;
                }
            }
        } finally {
            try {
                socket.setSoTimeout(originalTimeout);
            } catch (SocketException e) {
                // Ignore
            }
        }

        return null;
    }

    private static StunResult queryServer(DatagramSocket socket, String server) {
        try {
            String[] parts = server.split(":");
            String host = parts[0];
            int port = Integer.parseInt(parts[1]);

            InetAddress addr = InetAddress.getByName(host);

            // Build STUN binding request
            byte[] request = buildBindingRequest();

            // Send request
            DatagramPacket sendPacket = new DatagramPacket(request, request.length, addr, port);
            socket.send(sendPacket);

            // Receive response
            byte[] response = new byte[512];
            DatagramPacket recvPacket = new DatagramPacket(response, response.length);
            socket.receive(recvPacket);

            // Parse response
            return parseBindingResponse(response, recvPacket.getLength(),
                    socket.getLocalAddress().getHostAddress(), socket.getLocalPort());

        } catch (SocketTimeoutException e) {
            log.debug("STUN timeout for {}", server);
            return null;
        } catch (Exception e) {
            log.debug("STUN query failed for {}: {}", server, e.getMessage());
            return null;
        }
    }

    private static byte[] buildBindingRequest() {
        ByteBuffer buf = ByteBuffer.allocate(20);

        // Message type: Binding Request
        buf.putShort(BINDING_REQUEST);

        // Message length: 0 (no attributes)
        buf.putShort((short) 0);

        // Magic cookie
        buf.putInt(MAGIC_COOKIE);

        // Transaction ID (96 bits)
        SecureRandom random = new SecureRandom();
        byte[] transactionId = new byte[12];
        random.nextBytes(transactionId);
        buf.put(transactionId);

        return buf.array();
    }

    private static StunResult parseBindingResponse(byte[] data, int length, String localIp, int localPort) {
        if (length < 20) {
            return null;
        }

        ByteBuffer buf = ByteBuffer.wrap(data);

        // Skip message type and length
        buf.getShort();
        short msgLength = buf.getShort();

        // Verify magic cookie
        int cookie = buf.getInt();
        if (cookie != MAGIC_COOKIE) {
            return null;
        }

        // Skip transaction ID
        buf.position(buf.position() + 12);

        // Parse attributes
        int attrEnd = 20 + msgLength;
        while (buf.position() < attrEnd && buf.position() < length) {
            short attrType = buf.getShort();
            short attrLength = buf.getShort();

            if (attrType == XOR_MAPPED_ADDRESS || attrType == MAPPED_ADDRESS) {
                // Parse address
                buf.get(); // Reserved
                byte family = buf.get();

                if (family == 0x01) { // IPv4
                    int port;
                    byte[] ipBytes = new byte[4];

                    if (attrType == XOR_MAPPED_ADDRESS) {
                        // XOR with magic cookie
                        port = (buf.getShort() & 0xFFFF) ^ (MAGIC_COOKIE >> 16);
                        for (int i = 0; i < 4; i++) {
                            ipBytes[i] = (byte) (buf.get() ^ ((MAGIC_COOKIE >> (24 - i * 8)) & 0xFF));
                        }
                    } else {
                        port = buf.getShort() & 0xFFFF;
                        buf.get(ipBytes);
                    }

                    String ip = String.format("%d.%d.%d.%d",
                            ipBytes[0] & 0xFF, ipBytes[1] & 0xFF,
                            ipBytes[2] & 0xFF, ipBytes[3] & 0xFF);

                    log.debug("STUN discovered: {}:{}", ip, port);
                    return new StunResult(ip, port, localIp, localPort);
                }
            } else {
                // Skip unknown attribute
                buf.position(buf.position() + attrLength);
            }

            // Align to 4 bytes
            int padding = (4 - (attrLength % 4)) % 4;
            buf.position(buf.position() + padding);
        }

        return null;
    }
}
