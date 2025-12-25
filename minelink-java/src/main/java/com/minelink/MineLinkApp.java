package com.minelink;

import com.minelink.controller.MainController;
import javafx.application.Application;
import javafx.fxml.FXMLLoader;
import javafx.scene.Parent;
import javafx.scene.Scene;
import javafx.stage.Stage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * MineLink - P2P Minecraft Server Tunneling Application
 * 
 * Allows players to connect to Minecraft LAN worlds over the internet
 * without port forwarding or VPNs.
 */
public class MineLinkApp extends Application {

    private static final Logger log = LoggerFactory.getLogger(MineLinkApp.class);

    public static final String APP_NAME = "MineLink";
    public static final String APP_VERSION = "2.0.0";

    private MainController controller;

    @Override
    public void start(Stage primaryStage) {
        try {
            log.info("Starting {} v{}", APP_NAME, APP_VERSION);

            // Load FXML
            FXMLLoader loader = new FXMLLoader(getClass().getResource("/fxml/main.fxml"));
            Parent root = loader.load();
            controller = loader.getController();

            // Setup scene with dark theme
            Scene scene = new Scene(root, 1000, 700);
            scene.getStylesheets().add(getClass().getResource("/css/dark-theme.css").toExternalForm());

            // Configure stage
            primaryStage.setTitle(APP_NAME + " - P2P Minecraft Tunneling");
            primaryStage.setScene(scene);
            primaryStage.setMinWidth(800);
            primaryStage.setMinHeight(600);

            primaryStage.show();

            log.info("{} started successfully!", APP_NAME);

        } catch (Exception e) {
            log.error("Failed to start application", e);
            throw new RuntimeException("Failed to start MineLink", e);
        }
    }

    @Override
    public void stop() {
        log.info("Shutting down {}...", APP_NAME);
        if (controller != null) {
            controller.shutdown();
        }
    }

    public static void main(String[] args) {
        launch(args);
    }
}
