package com.minelink.network;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.BiConsumer;
import java.util.function.Consumer;

/**
 * Production-Ready Shared Folder Sync
 * 
 * Uses a shared folder (Google Drive, Dropbox, etc.) as a signaling channel
 * for automatic peer discovery, connection, and reconnection.
 * 
 * Features:
 * - HOST/CLIENT file differentiation
 * - Desktop name in file names for clarity
 * - Timestamp-based latest-file selection
 * - Auto-refresh every 60 seconds
 * - Stale file cleanup
 * - Manual refresh trigger
 */
public class SharedFolderSync {

    private static final Logger log = LoggerFactory.getLogger(SharedFolderSync.class);
    private static final ObjectMapper mapper = new ObjectMapper();

    // Timing constants
    private static final int SYNC_INTERVAL_MS = 3000; // Check for peer files every 3s
    private static final int REFRESH_INTERVAL_MS = 60000; // Re-STUN and write new file every 60s
    private static final int STALE_THRESHOLD_MS = 120000; // Ignore files older than 2 minutes
    private static final int DELETE_THRESHOLD_MS = 300000; // Delete files older than 5 minutes

    private static final int FILE_VERSION = 2;

    private final String myPeerId;
    private final String desktopName;
    private final boolean isHost;

    private File sharedFolder;
    private File myInfoFile;

    private ScheduledExecutorService scheduler;
    private ScheduledFuture<?> syncTask;
    private ScheduledFuture<?> refreshTask;

    // Callbacks
    private Consumer<String> onPeerCodeDiscovered; // When a peer's connection code is found
    private Consumer<String> onRefreshNeeded; // When we need to re-STUN (provides current code)
    private BiConsumer<String, String> onStatusUpdate; // Status updates for UI (type, message)

    // State
    private volatile String currentConnectionCode;
    private volatile long lastSuccessfulSync = 0;
    private volatile String lastConnectedPeerId = null;

    public SharedFolderSync(String myPeerId, boolean isHost) {
        this.myPeerId = myPeerId;
        this.isHost = isHost;

        // Get desktop/computer name
        String envName = System.getenv("COMPUTERNAME");
        if (envName == null || envName.isEmpty()) {
            envName = System.getenv("HOSTNAME");
        }
        if (envName == null || envName.isEmpty()) {
            try {
                envName = java.net.InetAddress.getLocalHost().getHostName();
            } catch (Exception e) {
                envName = "Unknown";
            }
        }
        this.desktopName = sanitizeName(envName);

        log.info("SharedFolderSync initialized: peerId={}, desktop={}, isHost={}",
                myPeerId, desktopName, isHost);
    }

    // ========== Configuration ==========

    public void setOnPeerCodeDiscovered(Consumer<String> callback) {
        this.onPeerCodeDiscovered = callback;
    }

    public void setOnRefreshNeeded(Consumer<String> callback) {
        this.onRefreshNeeded = callback;
    }

    public void setOnStatusUpdate(BiConsumer<String, String> callback) {
        this.onStatusUpdate = callback;
    }

    // ========== Lifecycle ==========

    /**
     * Start syncing to the specified shared folder.
     */
    public boolean start(String folderPath) {
        sharedFolder = new File(folderPath);

        if (!sharedFolder.exists() || !sharedFolder.isDirectory()) {
            log.error("Shared folder does not exist: {}", folderPath);
            statusUpdate("error", "Shared folder not found: " + folderPath);
            return false;
        }

        // Determine file name based on role
        String filePrefix = isHost ? "minelink_HOST_" : "minelink_CLIENT_";
        myInfoFile = new File(sharedFolder, filePrefix + desktopName + ".json");

        // Clean up any old files from this desktop
        cleanupOwnStaleFiles();

        scheduler = Executors.newScheduledThreadPool(2);

        // Sync task: check for peer files frequently
        syncTask = scheduler.scheduleAtFixedRate(
                this::syncLoop, 0, SYNC_INTERVAL_MS, TimeUnit.MILLISECONDS);

        // Refresh task: re-STUN and update our file periodically
        refreshTask = scheduler.scheduleAtFixedRate(
                this::triggerRefresh, REFRESH_INTERVAL_MS, REFRESH_INTERVAL_MS, TimeUnit.MILLISECONDS);

        log.info("SharedFolderSync started: folder={}, file={}",
                sharedFolder.getAbsolutePath(), myInfoFile.getName());
        statusUpdate("started", "Sync active: " + sharedFolder.getName());

        return true;
    }

    /**
     * Stop syncing and cleanup.
     */
    public void stop() {
        if (syncTask != null) {
            syncTask.cancel(false);
            syncTask = null;
        }
        if (refreshTask != null) {
            refreshTask.cancel(false);
            refreshTask = null;
        }
        if (scheduler != null) {
            scheduler.shutdown();
            scheduler = null;
        }

        // Remove our file
        if (myInfoFile != null && myInfoFile.exists()) {
            myInfoFile.delete();
            log.info("Removed own sync file");
        }

        statusUpdate("stopped", "Sync stopped");
    }

    // ========== File Operations ==========

    /**
     * Update our connection info in the shared folder.
     * Called after network start and after each re-STUN.
     */
    public void updateMyInfo(String connectionCode, String publicIp, int publicPort) {
        if (myInfoFile == null) {
            log.warn("Cannot update info - sync not started");
            return;
        }

        this.currentConnectionCode = connectionCode;

        try {
            ObjectNode node = mapper.createObjectNode();
            node.put("peerId", myPeerId);
            node.put("desktopName", desktopName);
            node.put("isHost", isHost);
            node.put("connectionCode", connectionCode);
            node.put("publicIp", publicIp);
            node.put("publicPort", publicPort);
            node.put("timestamp", System.currentTimeMillis());
            node.put("version", FILE_VERSION);

            mapper.writerWithDefaultPrettyPrinter().writeValue(myInfoFile, node);

            log.info("Updated sync file: {} ({}:{})", myInfoFile.getName(), publicIp, publicPort);
            statusUpdate("synced", "Published: " + publicIp + ":" + publicPort);

        } catch (IOException e) {
            log.error("Failed to write sync file", e);
            statusUpdate("error", "Write failed: " + e.getMessage());
        }
    }

    /**
     * Force an immediate refresh (manual trigger).
     */
    public void forceRefresh() {
        log.info("Manual refresh triggered");
        statusUpdate("refreshing", "Manual refresh...");
        triggerRefresh();
    }

    // ========== Internal Logic ==========

    /**
     * Main sync loop - discover peers from shared folder.
     */
    private void syncLoop() {
        if (sharedFolder == null || !sharedFolder.exists())
            return;

        try {
            long now = System.currentTimeMillis();

            // Clean up stale files (older than 5 minutes)
            cleanupStaleFiles(now);

            // Find peer files to connect to
            File[] files = sharedFolder.listFiles((dir, name) -> {
                // Clients look for HOST files, Hosts look for CLIENT files
                if (isHost) {
                    return name.startsWith("minelink_CLIENT_") && name.endsWith(".json");
                } else {
                    return name.startsWith("minelink_HOST_") && name.endsWith(".json");
                }
            });

            if (files == null || files.length == 0)
                return;

            // Find the file with the newest timestamp
            File bestFile = null;
            long bestTimestamp = 0;
            ObjectNode bestNode = null;

            for (File file : files) {
                try {
                    ObjectNode node = (ObjectNode) mapper.readTree(file);

                    // Skip files without required fields
                    if (!node.has("timestamp") || !node.has("connectionCode") || !node.has("peerId")) {
                        continue;
                    }

                    long timestamp = node.get("timestamp").asLong();

                    // Skip stale files (older than 2 minutes)
                    if (now - timestamp > STALE_THRESHOLD_MS) {
                        log.debug("Skipping stale file: {} ({}s old)",
                                file.getName(), (now - timestamp) / 1000);
                        continue;
                    }

                    // Skip our own peer ID
                    String peerId = node.get("peerId").asText();
                    if (peerId.equals(myPeerId)) {
                        continue;
                    }

                    // Keep the newest
                    if (timestamp > bestTimestamp) {
                        bestTimestamp = timestamp;
                        bestFile = file;
                        bestNode = node;
                    }

                } catch (Exception e) {
                    log.debug("Error reading file {}: {}", file.getName(), e.getMessage());
                }
            }

            // Connect to the best (newest) peer
            if (bestNode != null) {
                String peerId = bestNode.get("peerId").asText();
                String code = bestNode.get("connectionCode").asText();
                String desktop = bestNode.has("desktopName") ? bestNode.get("desktopName").asText() : "Unknown";

                // Only trigger if it's a new peer or code changed
                if (!peerId.equals(lastConnectedPeerId) ||
                        bestTimestamp > lastSuccessfulSync) {

                    log.info("Discovered peer: {} ({}) from {} - connecting...",
                            peerId, desktop, bestFile.getName());
                    statusUpdate("discovered", "Found: " + desktop);

                    if (onPeerCodeDiscovered != null) {
                        onPeerCodeDiscovered.accept(code);
                    }

                    lastConnectedPeerId = peerId;
                    lastSuccessfulSync = bestTimestamp;
                }
            }

        } catch (Exception e) {
            log.error("Sync loop error", e);
        }
    }

    /**
     * Trigger a connection refresh (re-STUN).
     */
    private void triggerRefresh() {
        log.info("Triggering connection refresh (re-STUN)");
        statusUpdate("refreshing", "Refreshing connection...");

        if (onRefreshNeeded != null) {
            onRefreshNeeded.accept(currentConnectionCode);
        }
    }

    /**
     * Clean up stale files from other peers.
     */
    private void cleanupStaleFiles(long now) {
        File[] allFiles = sharedFolder.listFiles((dir, name) -> name.startsWith("minelink_") && name.endsWith(".json"));

        if (allFiles == null)
            return;

        for (File file : allFiles) {
            // Don't delete our own file here
            if (file.equals(myInfoFile))
                continue;

            try {
                ObjectNode node = (ObjectNode) mapper.readTree(file);
                if (node.has("timestamp")) {
                    long timestamp = node.get("timestamp").asLong();
                    if (now - timestamp > DELETE_THRESHOLD_MS) {
                        log.info("Deleting stale file: {} ({}s old)",
                                file.getName(), (now - timestamp) / 1000);
                        file.delete();
                    }
                }
            } catch (Exception e) {
                // If we can't read it, it's probably corrupt - delete it
                log.debug("Deleting unreadable file: {}", file.getName());
                file.delete();
            }
        }
    }

    /**
     * Clean up our own old files (from previous sessions).
     */
    private void cleanupOwnStaleFiles() {
        String prefix = isHost ? "minelink_HOST_" + desktopName : "minelink_CLIENT_" + desktopName;

        File[] ownFiles = sharedFolder.listFiles((dir, name) -> name.startsWith(prefix) && name.endsWith(".json"));

        if (ownFiles != null) {
            for (File file : ownFiles) {
                log.info("Removing old session file: {}", file.getName());
                file.delete();
            }
        }
    }

    private void statusUpdate(String type, String message) {
        if (onStatusUpdate != null) {
            onStatusUpdate.accept(type, message);
        }
    }

    private String sanitizeName(String name) {
        return name.replaceAll("[^a-zA-Z0-9_-]", "_");
    }

    // ========== Getters ==========

    public File getSharedFolder() {
        return sharedFolder;
    }

    public String getDesktopName() {
        return desktopName;
    }

    public boolean isHost() {
        return isHost;
    }

    public boolean isActive() {
        return scheduler != null && !scheduler.isShutdown();
    }
}
