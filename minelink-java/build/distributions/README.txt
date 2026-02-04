# MineLink - Quick Start Guide

## Prerequisites
Your friend needs **Java 21** installed. Download from:
https://adoptium.net/temurin/releases/?version=21

To check if Java is installed, open Command Prompt and type:
```
java -version
```

## How to Run

1. **Extract** the ZIP file
2. **Open** the extracted folder
3. **Double-click** `bin\minelink.bat` (Windows) 

Or run from Command Prompt:
```
cd minelink-2.0.0
bin\minelink.bat
```

## How to Connect

### If you are the HOST (have Minecraft world open):
1. Open Minecraft → Open to LAN
2. Note the port number shown
3. In MineLink: Select "Host" mode, enter the port
4. Click "Start Network"
5. Copy your connection code and send to friend

### If you are the CLIENT (joining friend's world):
1. Get connection code from your friend
2. In MineLink: Select "Client" mode
3. Click "Start Network"
4. Paste friend's code in "Add Peer" field
5. Click "Add Peer", then "Connect"
6. Once connected, click "Copy IP"
7. In Minecraft: Multiplayer → Add Server → Paste address

## Troubleshooting
- Make sure Windows Firewall allows Java
- Both players need to click "Start Network" first
- Connection code is the long Base64 string

Enjoy playing together! 🎮
