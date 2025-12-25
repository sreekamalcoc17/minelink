package com.minelink.network.protocol;

/**
 * Packet types for the MineLink P2P protocol.
 */
public enum PacketType {
    /** UDP hole punch packet */
    PUNCH((byte) 0x01),

    /** Acknowledgment for hole punch */
    PUNCH_ACK((byte) 0x02),

    /** Ping request for latency measurement */
    PING((byte) 0x03),

    /** Pong response */
    PONG((byte) 0x04),

    /** Data packet with reliable delivery */
    DATA((byte) 0x10),

    /** Acknowledgment for data packet */
    ACK((byte) 0x11),

    /** Fragment of a large packet */
    FRAGMENT((byte) 0x12),

    /** Disconnect notification */
    DISCONNECT((byte) 0xFF);

    private final byte value;

    PacketType(byte value) {
        this.value = value;
    }

    public byte getValue() {
        return value;
    }

    public static PacketType fromValue(byte value) {
        for (PacketType type : values()) {
            if (type.value == value) {
                return type;
            }
        }
        return null;
    }
}
