package com.minelink.controller;

import com.minelink.model.ConnectionInfo;
import com.minelink.model.Peer;
import com.minelink.network.NetworkManager;
import javafx.application.Platform;
import javafx.fxml.FXML;
import javafx.fxml.Initializable;
import javafx.geometry.Insets;
import javafx.scene.control.*;
import javafx.scene.input.Clipboard;
import javafx.scene.input.ClipboardContent;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.URL;
import java.util.HashMap;
import java.util.Map;
import java.util.ResourceBundle;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Main controller for the MineLink UI.
 * Handles user interactions and coordinates with the network layer.
 */
public class MainController implements Initializable {

    private static final Logger log = LoggerFactory.getLogger(MainController.class);

    // Mode selection
    @FXML
    private ToggleButton hostRadio;
    @FXML
    private ToggleButton clientRadio;
    @FXML
    private ToggleGroup modeGroup;

    // Port configuration
    @FXML
    private Label portLabel;
    @FXML
    private Label portDescription;
    @FXML
    private TextField portField;

    // Connection code
    @FXML
    private TextArea connectionCodeArea;
    @FXML
    private Button copyCodeButton;

    // Add peer
    @FXML
    private TextField peerCodeField;

    // Controls
    @FXML
    private Button startButton;
    @FXML
    private Label statusLabel;

    // Peers
    @FXML
    private ScrollPane peersScrollPane;
    @FXML
    private VBox peersContainer;

    // Stats
    @FXML
    private Label uptimeLabel;
    @FXML
    private Label peersCountLabel;
    @FXML
    private Label sentLabel;
    @FXML
    private Label receivedLabel;

    // Network
    private NetworkManager networkManager;
    private ScheduledExecutorService statsUpdater;
    private long startTime;

    // Peer cards
    private final Map<String, VBox> peerCards = new HashMap<>();

    @Override
    public void initialize(URL location, ResourceBundle resources) {
        log.info("Initializing MainController");

        networkManager = new NetworkManager();

        // Setup mode change listener
        modeGroup.selectedToggleProperty().addListener((obs, oldVal, newVal) -> {
            onModeChanged();
        });

        // Setup network callbacks
        networkManager.setOnStatusChange(this::updateStatus);
        networkManager.setOnPeerConnected(this::onPeerConnected);
        networkManager.setOnPeerDisconnected(this::onPeerDisconnected);

        // Initial state
        updateStatus("Ready - Select mode and start network");
    }

    private void onModeChanged() {
        boolean isHostMode = hostRadio.isSelected();
        log.info("Mode changed to: {}", isHostMode ? "HOST" : "CLIENT");

        networkManager.setMode(isHostMode ? NetworkManager.Mode.HOST : NetworkManager.Mode.CLIENT);

        if (isHostMode) {
            portField.setDisable(false);
            portLabel.setText("Minecraft LAN Port");
            portDescription.setText("Enter the port shown when you 'Open to LAN' in Minecraft");
        } else {
            portField.setDisable(true);
            portLabel.setText("Minecraft Server Port (Host Only)");
            portDescription.setText("Client mode: Connect to localhost:25565 in Minecraft");
        }
    }

    @FXML
    private void onToggleNetwork() {
        if (networkManager.getMode() == null) {
            networkManager.setMode(hostRadio.isSelected() ? NetworkManager.Mode.HOST : NetworkManager.Mode.CLIENT);
        }

        if (startButton.getText().contains("Stop")) {
            stopNetwork();
        } else {
            startNetwork();
        }
    }

    private void startNetwork() {
        log.info("Starting network in {} mode", networkManager.getMode());

        // Parse port
        int port = 25565;
        try {
            port = Integer.parseInt(portField.getText().trim());
        } catch (NumberFormatException e) {
            // Use default
        }
        networkManager.setMinecraftPort(port);

        // Disable mode selection while running
        hostRadio.setDisable(true);
        clientRadio.setDisable(true);
        portField.setDisable(true);

        // Start network in background
        new Thread(() -> {
            boolean success = networkManager.start();

            Platform.runLater(() -> {
                if (success) {
                    startButton.setText("⏹ Stop Network");
                    startButton.getStyleClass().remove("primary-button");
                    startButton.getStyleClass().add("stop-button");

                    // Show connection code
                    String code = networkManager.getConnectionCode();
                    if (code != null) {
                        connectionCodeArea.setText(code);
                    }

                    // Start stats updater
                    startTime = System.currentTimeMillis();
                    startStatsUpdater();

                    // Add any saved peers
                    refreshPeerCards();
                } else {
                    hostRadio.setDisable(false);
                    clientRadio.setDisable(false);
                    portField.setDisable(hostRadio.isSelected() ? false : true);
                }
            });
        }).start();
    }

    private void stopNetwork() {
        log.info("Stopping network");

        if (statsUpdater != null) {
            statsUpdater.shutdown();
            statsUpdater = null;
        }

        networkManager.stop();

        // Reset UI
        startButton.setText("▶ Start Network");
        startButton.getStyleClass().remove("stop-button");
        startButton.getStyleClass().add("primary-button");

        hostRadio.setDisable(false);
        clientRadio.setDisable(false);
        portField.setDisable(!hostRadio.isSelected());

        connectionCodeArea.clear();
        peersContainer.getChildren().clear();
        peerCards.clear();
    }

    @FXML
    private void onCopyCode() {
        String code = connectionCodeArea.getText();
        if (code != null && !code.isEmpty()) {
            Clipboard clipboard = Clipboard.getSystemClipboard();
            ClipboardContent content = new ClipboardContent();
            content.putString(code);
            clipboard.setContent(content);

            copyCodeButton.setText("✓ Copied!");
            new Thread(() -> {
                try {
                    Thread.sleep(1500);
                } catch (InterruptedException ignored) {
                }
                Platform.runLater(() -> copyCodeButton.setText("📋 Copy Code"));
            }).start();
        }
    }

    @FXML
    private void onAddPeer() {
        String code = peerCodeField.getText().trim();
        if (code.isEmpty()) {
            updateStatus("Please enter a connection code");
            return;
        }

        if (networkManager.addPeer(code)) {
            peerCodeField.clear();
            refreshPeerCards();
        }
    }

    private void onPeerConnected(String peerId) {
        Platform.runLater(() -> {
            refreshPeerCards();
        });
    }

    private void onPeerDisconnected(String peerId) {
        Platform.runLater(() -> {
            refreshPeerCards();
        });
    }

    private void refreshPeerCards() {
        peersContainer.getChildren().clear();
        peerCards.clear();

        for (Map.Entry<String, ConnectionInfo> entry : networkManager.getSavedPeers().entrySet()) {
            String peerId = entry.getKey();
            ConnectionInfo info = entry.getValue();
            Peer peer = networkManager.getPeer(peerId);

            VBox card = createPeerCard(peerId, info, peer);
            peerCards.put(peerId, card);
            peersContainer.getChildren().add(card);
        }
    }

    private VBox createPeerCard(String peerId, ConnectionInfo info, Peer peer) {
        VBox card = new VBox(14);
        card.getStyleClass().add("peer-card");

        // Top row: name and status
        HBox topRow = new HBox(12);
        topRow.setAlignment(javafx.geometry.Pos.CENTER_LEFT);
        
        // Name with clean styling
        Label nameLabel = new Label(peerId);
        nameLabel.setStyle("-fx-font-size: 16px; -fx-font-weight: 600; -fx-text-fill: #ffffff;");

        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);

        // Status badge with iOS colors
        Label statusBadge = new Label();
        boolean isConnected = peer != null && peer.isConnected();
        if (isConnected) {
            int ping = peer.getPingMs();
            statusBadge.setText("● " + (ping > 0 ? ping + "ms" : "Online"));
            statusBadge.setStyle("-fx-text-fill: #30d158; -fx-font-weight: 600; -fx-font-size: 12px;");
        } else {
            statusBadge.setText("○ Offline");
            statusBadge.setStyle("-fx-text-fill: #636366; -fx-font-size: 12px;");
        }

        topRow.getChildren().addAll(nameLabel, spacer, statusBadge);

        // Address info - more subtle
        Label addrLabel = new Label(info.getPublicIp() + ":" + info.getPublicPort());
        addrLabel.setStyle("-fx-text-fill: #8e8e93; -fx-font-size: 12px;");

        // Buttons row with proper spacing
        HBox btnRow = new HBox(10);
        btnRow.setAlignment(javafx.geometry.Pos.CENTER_LEFT);

        Button connectBtn = new Button(isConnected ? "Connected" : "Connect");
        connectBtn.setStyle("-fx-font-size: 12px; -fx-padding: 8 16;");
        if (!isConnected) {
            connectBtn.getStyleClass().addAll("button", "primary-button");
            connectBtn.setStyle("-fx-font-size: 12px; -fx-padding: 8 16;");
        } else {
            connectBtn.getStyleClass().addAll("button", "secondary-button");
            connectBtn.setStyle("-fx-font-size: 12px; -fx-padding: 8 16;");
        }
        connectBtn.setDisable(isConnected);
        connectBtn.setOnAction(e -> {
            networkManager.connectToPeer(peerId);
            connectBtn.setText("Connecting...");
            connectBtn.setDisable(true);
        });

        Button copyBtn = new Button("Copy");
        copyBtn.getStyleClass().addAll("button", "secondary-button");
        copyBtn.setStyle("-fx-font-size: 12px; -fx-padding: 8 16;");
        copyBtn.setOnAction(e -> {
            String addr = "localhost:25565";
            Clipboard clipboard = Clipboard.getSystemClipboard();
            ClipboardContent content = new ClipboardContent();
            content.putString(addr);
            clipboard.setContent(content);
            copyBtn.setText("✓");
            new Thread(() -> {
                try {
                    Thread.sleep(1500);
                } catch (InterruptedException ignored) {
                }
                Platform.runLater(() -> copyBtn.setText("Copy"));
            }).start();
        });
        copyBtn.setVisible(isConnected);
        copyBtn.setManaged(isConnected);

        Region btnSpacer = new Region();
        HBox.setHgrow(btnSpacer, Priority.ALWAYS);

        Button removeBtn = new Button("✕");
        removeBtn.setStyle("-fx-font-size: 14px; -fx-padding: 6 12; -fx-background-color: transparent; -fx-text-fill: #636366;");
        removeBtn.setOnAction(e -> {
            networkManager.getSavedPeers().remove(peerId);
            refreshPeerCards();
        });

        btnRow.getChildren().addAll(connectBtn, copyBtn, btnSpacer, removeBtn);

        card.getChildren().addAll(topRow, addrLabel, btnRow);
        return card;
    }

    private void startStatsUpdater() {
        statsUpdater = Executors.newSingleThreadScheduledExecutor();
        statsUpdater.scheduleAtFixedRate(() -> {
            long uptimeSeconds = (System.currentTimeMillis() - startTime) / 1000;
            int peerCount = (int) networkManager.getSavedPeers().values().stream()
                    .filter(info -> {
                        Peer peer = networkManager.getPeer(info.getPeerId());
                        return peer != null && peer.isConnected();
                    }).count();

            Platform.runLater(() -> {
                long hours = uptimeSeconds / 3600;
                long minutes = (uptimeSeconds % 3600) / 60;
                long seconds = uptimeSeconds % 60;
                uptimeLabel.setText(String.format("Uptime: %02d:%02d:%02d", hours, minutes, seconds));
                peersCountLabel.setText("Peers: " + peerCount);

                // Refresh peer cards to update ping
                refreshPeerCards();
            });
        }, 1, 5, TimeUnit.SECONDS);
    }

    private void updateStatus(String message) {
        Platform.runLater(() -> statusLabel.setText(message));
        log.info("Status: {}", message);
    }

    public void shutdown() {
        if (statsUpdater != null) {
            statsUpdater.shutdown();
        }
        if (networkManager != null) {
            networkManager.stop();
        }
    }
}
