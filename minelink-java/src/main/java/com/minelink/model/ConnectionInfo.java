package com.minelink.model;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.Base64;

/**
 * Connection information for peer-to-peer connection establishment.
 * Can be serialized to/from a connection code for sharing.
 */
public class ConnectionInfo {

    private static final ObjectMapper mapper = new ObjectMapper();

    @JsonProperty("id")
    private String peerId;

    @JsonProperty("pub_ip")
    private String publicIp;

    @JsonProperty("pub_port")
    private int publicPort;

    @JsonProperty("loc_ip")
    private String localIp;

    @JsonProperty("loc_port")
    private int localPort;

    // Default constructor for Jackson
    public ConnectionInfo() {
    }

    public ConnectionInfo(String peerId, String publicIp, int publicPort, String localIp, int localPort) {
        this.peerId = peerId;
        this.publicIp = publicIp;
        this.publicPort = publicPort;
        this.localIp = localIp;
        this.localPort = localPort;
    }

    // Getters
    public String getPeerId() {
        return peerId;
    }

    public String getPublicIp() {
        return publicIp;
    }

    public int getPublicPort() {
        return publicPort;
    }

    public String getLocalIp() {
        return localIp;
    }

    public int getLocalPort() {
        return localPort;
    }

    /**
     * Encode to a shareable connection code (Base64 JSON).
     */
    public String toCode() {
        try {
            String json = mapper.writeValueAsString(this);
            return Base64.getEncoder().encodeToString(json.getBytes());
        } catch (JsonProcessingException e) {
            throw new RuntimeException("Failed to encode connection info", e);
        }
    }

    /**
     * Decode from a connection code.
     */
    public static ConnectionInfo fromCode(String code) {
        try {
            byte[] jsonBytes = Base64.getDecoder().decode(code);
            return mapper.readValue(jsonBytes, ConnectionInfo.class);
        } catch (Exception e) {
            throw new RuntimeException("Failed to decode connection code", e);
        }
    }

    @Override
    public String toString() {
        return String.format("ConnectionInfo{peer=%s, public=%s:%d, local=%s:%d}",
                peerId, publicIp, publicPort, localIp, localPort);
    }
}
