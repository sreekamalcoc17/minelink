package com.minelink.model;

import java.net.InetSocketAddress;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Represents a peer in the P2P network.
 */
public class Peer {

    private final String peerId;
    private final InetSocketAddress publicAddress;
    private final InetSocketAddress localAddress;

    private volatile boolean connected;
    private volatile long lastSeen;
    private volatile double srtt; // Smoothed RTT in milliseconds
    private volatile double rttVar;
    private volatile double rto; // Retransmission timeout

    // Ordered receive tracking
    private volatile int recvSeq = 1; // Next expected receive sequence number (starts at 1)
    private final Map<Integer, byte[]> recvBuffer = new ConcurrentHashMap<>(); // Out-of-order packets

    // Ordered send tracking (for DATA packets only)
    private final AtomicInteger sendSeq = new AtomicInteger(0);

    private static final double DEFAULT_RTT = 1000.0; // 1 second default
    private static final double MIN_RTO = 200.0;
    private static final double MAX_RTO = 10000.0;

    public Peer(String peerId, InetSocketAddress publicAddress, InetSocketAddress localAddress) {
        this.peerId = peerId;
        this.publicAddress = publicAddress;
        this.localAddress = localAddress;
        this.connected = false;
        this.lastSeen = System.currentTimeMillis();
        this.srtt = DEFAULT_RTT;
        this.rttVar = DEFAULT_RTT / 2;
        this.rto = DEFAULT_RTT;
    }

    public String getPeerId() {
        return peerId;
    }

    public InetSocketAddress getPublicAddress() {
        return publicAddress;
    }

    public InetSocketAddress getLocalAddress() {
        return localAddress;
    }

    public boolean isConnected() {
        return connected;
    }

    public long getLastSeen() {
        return lastSeen;
    }

    public double getSrtt() {
        return srtt;
    }

    public double getRto() {
        return rto;
    }

    public void setConnected(boolean connected) {
        this.connected = connected;
        this.lastSeen = System.currentTimeMillis();
    }

    public void updateLastSeen() {
        this.lastSeen = System.currentTimeMillis();
    }

    /**
     * Update RTT estimate using Jacobson's algorithm.
     * 
     * @param sample RTT sample in milliseconds
     */
    public void updateRtt(double sample) {
        if (srtt == DEFAULT_RTT) {
            // First sample
            srtt = sample;
            rttVar = sample / 2;
        } else {
            double alpha = 0.125;
            double beta = 0.25;
            rttVar = (1 - beta) * rttVar + beta * Math.abs(srtt - sample);
            srtt = (1 - alpha) * srtt + alpha * sample;
        }

        rto = Math.min(Math.max(srtt + 4 * rttVar, MIN_RTO), MAX_RTO);
    }

    /**
     * Get ping in milliseconds.
     */
    public int getPingMs() {
        if (!connected || srtt == DEFAULT_RTT) {
            return -1;
        }
        return (int) Math.round(srtt);
    }

    // Ordered receive tracking methods
    public int getRecvSeq() {
        return recvSeq;
    }

    public void incrementRecvSeq() {
        recvSeq++;
    }

    public Map<Integer, byte[]> getRecvBuffer() {
        return recvBuffer;
    }

    // Ordered send tracking methods
    public int incrementAndGetSendSeq() {
        return sendSeq.incrementAndGet();
    }

    @Override
    public String toString() {
        return String.format("Peer{id=%s, connected=%s, ping=%dms}",
                peerId, connected, getPingMs());
    }
}
