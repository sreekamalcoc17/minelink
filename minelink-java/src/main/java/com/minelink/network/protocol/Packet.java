package com.minelink.network.protocol;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;

/**
 * Represents a protocol packet with encoding/decoding capabilities.
 */
public class Packet {

    private final PacketType type;
    private final String peerId;
    private final int sequenceNumber;
    private final int streamId;
    private final byte[] payload;

    // Header: type(1) + peerIdLen(1) + peerId(N) + seq(4) + streamId(2) +
    // payloadLen(2)

    public Packet(PacketType type, String peerId, int sequenceNumber, int streamId, byte[] payload) {
        this.type = type;
        this.peerId = peerId;
        this.sequenceNumber = sequenceNumber;
        this.streamId = streamId;
        this.payload = payload != null ? payload : new byte[0];
    }

    public PacketType getType() {
        return type;
    }

    public String getPeerId() {
        return peerId;
    }

    public int getSequenceNumber() {
        return sequenceNumber;
    }

    public int getStreamId() {
        return streamId;
    }

    public byte[] getPayload() {
        return payload;
    }

    /**
     * Encode packet to bytes for transmission.
     */
    public byte[] encode() {
        byte[] peerIdBytes = peerId.getBytes();

        ByteBuf buf = Unpooled.buffer();
        try {
            buf.writeByte(type.getValue());
            buf.writeByte(peerIdBytes.length);
            buf.writeBytes(peerIdBytes);
            buf.writeInt(sequenceNumber);
            buf.writeShort(streamId);
            buf.writeShort(payload.length);
            buf.writeBytes(payload);

            byte[] result = new byte[buf.readableBytes()];
            buf.readBytes(result);
            return result;
        } finally {
            buf.release();
        }
    }

    /**
     * Decode packet from received bytes.
     * Returns null for invalid or non-MineLink packets (like STUN responses).
     */
    public static Packet decode(byte[] data) {
        // Minimum packet size: type(1) + peerIdLen(1) + seq(4) + streamId(2) +
        // payloadLen(2) = 10 bytes
        if (data == null || data.length < 10) {
            return null;
        }

        ByteBuf buf = Unpooled.wrappedBuffer(data);
        try {
            byte typeValue = buf.readByte();
            PacketType type = PacketType.fromValue(typeValue);
            if (type == null) {
                // Not a valid MineLink packet type - could be STUN response or garbage
                return null;
            }

            int peerIdLen = buf.readByte() & 0xFF;

            // Validate peer ID length is reasonable (max 100 chars, and fits in remaining
            // data)
            // Remaining bytes after type+peerIdLen: data.length - 2
            // We need: peerIdLen + seq(4) + streamId(2) + payloadLen(2) = peerIdLen + 8
            if (peerIdLen > 100 || peerIdLen + 8 > data.length - 2) {
                return null;
            }

            // Check we have enough bytes for peer ID
            if (buf.readableBytes() < peerIdLen) {
                return null;
            }

            byte[] peerIdBytes = new byte[peerIdLen];
            buf.readBytes(peerIdBytes);
            String peerId = new String(peerIdBytes);

            // Check we have enough bytes for header remainder
            if (buf.readableBytes() < 8) {
                return null;
            }

            int sequenceNumber = buf.readInt();
            int streamId = buf.readShort() & 0xFFFF;
            int payloadLen = buf.readShort() & 0xFFFF;

            // Validate payload length
            if (payloadLen > 65535 || buf.readableBytes() < payloadLen) {
                return null;
            }

            byte[] payload = new byte[payloadLen];
            buf.readBytes(payload);

            return new Packet(type, peerId, sequenceNumber, streamId, payload);
        } catch (Exception e) {
            // Any parsing error means it's not a valid MineLink packet
            return null;
        } finally {
            buf.release();
        }
    }

    /**
     * Create a PUNCH packet for hole punching.
     */
    public static Packet punch(String myPeerId) {
        return new Packet(PacketType.PUNCH, myPeerId, 0, 0, null);
    }

    /**
     * Create a PUNCH_ACK packet.
     */
    public static Packet punchAck(String myPeerId) {
        return new Packet(PacketType.PUNCH_ACK, myPeerId, 0, 0, null);
    }

    /**
     * Create a PING packet.
     */
    public static Packet ping(String myPeerId, int seq) {
        return new Packet(PacketType.PING, myPeerId, seq, 0, null);
    }

    /**
     * Create a PONG packet.
     */
    public static Packet pong(String myPeerId, int seq) {
        return new Packet(PacketType.PONG, myPeerId, seq, 0, null);
    }

    /**
     * Create a DATA packet.
     */
    public static Packet data(String myPeerId, int seq, int streamId, byte[] payload) {
        return new Packet(PacketType.DATA, myPeerId, seq, streamId, payload);
    }

    /**
     * Create an ACK packet.
     */
    public static Packet ack(String myPeerId, int seq) {
        return new Packet(PacketType.ACK, myPeerId, seq, 0, null);
    }

    @Override
    public String toString() {
        return String.format("Packet{type=%s, peer=%s, seq=%d, stream=%d, payloadLen=%d}",
                type, peerId, sequenceNumber, streamId, payload.length);
    }
}
