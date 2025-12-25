# ⛏ MineLink

**P2P Minecraft Server Tunneling** - Play Minecraft with friends without port forwarding or VPNs!

![Java](https://img.shields.io/badge/Java-21-orange)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Mac%20%7C%20Linux-blue)
![License](https://img.shields.io/badge/License-MIT-green)

## 🎮 What is MineLink?

MineLink creates a peer-to-peer tunnel that lets your friends connect to your Minecraft LAN world over the internet. No port forwarding, no VPNs, no server hosting needed!

## ✨ Features

- 🔗 **UDP Hole Punching** - Works through most NATs
- 🔒 **Direct P2P Connection** - No relay servers, low latency
- 🎨 **Modern Dark UI** - Beautiful futuristic interface
- 💻 **Cross-Platform** - Windows, Mac, Linux

## 🚀 Quick Start

### Prerequisites
- Java 21+ ([Download](https://adoptium.net/temurin/releases/?version=21))

### Run from Source
```bash
cd minelink-java
./gradlew run          # Mac/Linux
.\gradlew.bat run      # Windows
```

### Build Distribution
```bash
./gradlew distZip
# Output: build/distributions/MineLink-2.0.0.zip
```

## 📖 How to Use

### Host (has Minecraft world):
1. Open Minecraft → Open to LAN → Note the port
2. Run MineLink → Select **Host** → Enter port
3. Click **Start Network** → Copy connection code
4. Send code to friend

### Client (joining friend's world):
1. Run MineLink → Select **Client**
2. Click **Start Network**
3. Paste friend's code → **Add Peer** → **Connect**
4. Click **Copy IP** → Use in Minecraft Multiplayer

## 🏗 Project Structure

```
minelink/
├── minelink-java/          # Java implementation (current)
│   ├── src/main/java/com/minelink/
│   │   ├── network/        # P2P networking (Netty)
│   │   ├── controller/     # JavaFX UI controllers
│   │   └── model/          # Data models
│   └── src/main/resources/ # FXML, CSS
│
└── minelink-v1-python/     # Python prototype (legacy)
```

## 🛠 Tech Stack

| Component | Technology |
|-----------|------------|
| UI | JavaFX 21 |
| Networking | Netty 4.x |
| Build | Gradle |
| NAT Traversal | STUN + UDP Hole Punching |

## 📄 License

MIT License - Feel free to use and modify!

---
Made with ❤️ for Minecraft players
