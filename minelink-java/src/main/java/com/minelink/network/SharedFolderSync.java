package com.minelink.network;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;
import java.nio.file.*;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * Shared Folder Sync - Uses a shared folder (Google Drive, Dropbox, etc.)
 * as a signaling channel for automatic peer discovery and reconnection.
 * 
 * Each client writes its connection info to:
 * [shared_folder]/minelink_[peerId].json
 * Each client watches for other files and automatically adds them as peers.
 */
public class SharedFolderSync {

    private static final Logger log = LoggerFactory.getLogger(SharedFolderSync.class);
    private static final ObjectMapper mapper = new ObjectMapper();
    private static final int SYNC_INTERVAL_MS = 3000; // Check every 3 seconds

    private final String myPeerId;
    private File sharedFolder;
    private File myInfoFile;

    private ScheduledExecutorService scheduler;
    private Consumer<String> onPeerCodeDiscovered;
    private long lastModified = 0;

    public SharedFolderSync(String myPeerId) {
        this.myPeerId = myPeerId;
    }

    /**
     * Set callback for when a peer's connection code is discovered.
     */
    public void setOnPeerCodeDiscovered(Consumer<String> callback) {
        this.onPeerCodeDiscovered = callback;
    }

    /**
     * Start syncing to the specified shared folder.
     */
    public boolean start(String folderPath) {
        sharedFolder = new File(folderPath);

        if (!sharedFolder.exists() || !sharedFolder.isDirectory()) {
            log.error("Shared folder does not exist: {}", folderPath);
            return false;
        }

        myInfoFile = new File(sharedFolder, "minelink_" + sanitizeId(myPeerId) + ".json");

        scheduler = Executors.newSingleThreadScheduledExecutor();
        scheduler.scheduleAtFixedRate(this::syncLoop, 0, SYNC_INTERVAL_MS, TimeUnit.MILLISECONDS);

        log.info("SharedFolderSync started, watching: {}", sharedFolder.getAbsolutePath());
        return true;
    }

    /**
     * Stop syncing.
     */
    public void stop() {
        if (scheduler != null) {
            scheduler.shutdown();
            scheduler = null;
        }

        // Clean up our file
        if (myInfoFile != null && myInfoFile.exists()) {
            myInfoFile.delete();
            log.info("Removed own info file");
        }
    }

    /**
     * Update our connection info in the shared folder.
     */
    public void updateMyInfo(String connectionCode) {
        if (myInfoFile == null)
            return;

        try {
            ObjectNode node = mapper.createObjectNode();
            node.put("peerId", myPeerId);
            node.put("connectionCode", connectionCode);
            node.put("timestamp", System.currentTimeMillis());

            mapper.writeValue(myInfoFile, node);
            log.debug("Updated my info file: {}", myInfoFile.getName());
        } catch (IOException e) {
            log.error("Failed to write info file", e);
        }
    }

    /**
     * Main sync loop - check for new/updated peer files.
     */
    private void syncLoop() {
        if (sharedFolder == null || !sharedFolder.exists())
            return;

        try {
            File[] files = sharedFolder.listFiles((dir, name) -> name.startsWith("minelink_") && name.endsWith(".json")
                    && !name.equals(myInfoFile.getName()));

            if (files == null)
                return;

            for (File file : files) {
                try {
                    // Only process if modified since last check
                    if (file.lastModified() <= lastModified)
                        continue;

                    ObjectNode node = (ObjectNode) mapper.readTree(file);
                    String peerId = node.get("peerId").asText();
                    String code = node.get("connectionCode").asText();
                    long timestamp = node.get("timestamp").asLong();

                    // Ignore old files (older than 5 minutes)
                    if (System.currentTimeMillis() - timestamp > 300000) {
                        log.debug("Ignoring stale peer file: {} ({}ms old)", file.getName(),
                                System.currentTimeMillis() - timestamp);
                        continue;
                    }

                    log.info("Discovered peer from shared folder: {} -> {}", peerId, code.substring(0, 20) + "...");

                    if (onPeerCodeDiscovered != null) {
                        onPeerCodeDiscovered.accept(code);
                    }

                } catch (Exception e) {
                    log.debug("Error reading peer file {}: {}", file.getName(), e.getMessage());
                }
            }

            lastModified = System.currentTimeMillis();

        } catch (Exception e) {
            log.error("Sync loop error", e);
        }
    }

    private String sanitizeId(String id) {
        return id.replaceAll("[^a-zA-Z0-9_-]", "_");
    }

    public File getSharedFolder() {
        return sharedFolder;
    }
}
