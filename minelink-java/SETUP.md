# MineLink - Cross-Platform Quick Start

## Step 1: Install Java 21

### Windows
Download from: https://adoptium.net/temurin/releases/?version=21
Run the installer.

### Mac
```bash
brew install temurin21
```
Or download from: https://adoptium.net/temurin/releases/?version=21

### Linux (Ubuntu/Debian)
```bash
sudo apt update
sudo apt install temurin-21-jdk
```
Or download from: https://adoptium.net/temurin/releases/?version=21

---

## Step 2: Run MineLink

### Windows
1. Extract the ZIP
2. Double-click `bin\minelink.bat`

### Mac / Linux
1. Extract the ZIP
2. Open Terminal in the extracted folder
3. Run:
```bash
chmod +x bin/minelink
./bin/minelink
```

---

## Step 3: Connect to Friends

### If your friend is HOST (has the Minecraft world):
1. Ask them for their **Connection Code**
2. Select **Client** mode in MineLink
3. Click **Start Network**
4. Paste their code → **Add Peer** → **Connect**
5. Click **Copy IP** 
6. Use that address in Minecraft Multiplayer

### If YOU are HOST:
1. Open your Minecraft world → Open to LAN
2. Note the port number
3. Select **Host** mode, enter the port
4. Click **Start Network**
5. Send your **Connection Code** to friends

---

## Troubleshooting

**"Permission denied" on Mac/Linux:**
```bash
chmod +x bin/minelink
```

**Java not found:**
Make sure Java 21 is installed and in your PATH:
```bash
java -version
```

**Connection failing:**
- Both players must click "Start Network" first
- Make sure you're using the correct connection code

---

Enjoy playing Minecraft with friends! 🎮
