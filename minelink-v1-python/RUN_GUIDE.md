# How to Run and Share MineLink

## Option 1: Send to a Friend (Recommended)
You can create a single `.exe` file that your friend can run without installing Python.

1.  **Open the folder** `minelink-v1-python` in File Explorer.
2.  **Right-click** on `build.ps1` and select **"Run with PowerShell"**.
3.  Wait for the script to finish. It will verify Python, install dependencies, and build the app.
4.  Once done, you will find a new folder called `dist`.
5.  Inside `dist`, you will find **`MineLink.exe`**.
6.  **Send `MineLink.exe` to your friend.** They can just double-click it to play!

---

## Option 2: Run from Source (For Developers)
If you want to run it yourself to test changes:

1.  Open a terminal in this folder.
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
3.  Run the app:
    ```bash
    python minelink.py
    ```

---

## How to Play Together

### 1. For YOU (The Host)
1.  Open Minecraft and start a single-player world.
2.  Press **Esc** -> **Open to LAN** -> **Start LAN World**.
3.  Note the **Port Number** shown in the chat (e.g., `54321`).
4.  Open **MineLink**.
5.  Select **"Host (Server)"** mode.
6.  Enter that port number (e.g., `54321`) into the "Minecraft LAN Port" box.
7.  Click **Start Network**.
8.  Copy the **Connection Code** and send it to your friend.

### 2. For YOUR FRIEND (The Client)
1.  Open **MineLink**.
2.  Leave it on **"Client (Join)"** mode.
3.  Paste the **Connection Code** you sent them into the "Add Peer" box and click **Add**.
4.  Click **Connect** on your card in the list.
5.  Once the status says **"Connected"**, click **"Copy IP"**.
6.  Open Minecraft -> **Multiplayer** -> **Direct Connection**.
7.  Paste the address (it will look like `localhost:25565`).
8.  Join!

