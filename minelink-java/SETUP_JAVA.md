# MineLink (Java Edition) Setup Guide

This is the **High-Performance** version of MineLink, built using **Java and Netty** (the same networking engine used by Minecraft itself).

## Why use this version?
- **Unbreakable Connection**: Uses "infinite retry" logic (like TCP) so your connection never drops, even with packet loss.
- **Native Performance**: No overhead from Python; runs directly on the JVM.
- **Modded Friendly**: Handles large/complex packets from modded servers without corruption.

## Prerequisites
- **Java 21** (The build script will handle this, but good to have).

## How to Run

### Option 1: Quick Run (Developer Mode)
1. Open PowerShell in the `minelink-java` folder.
2. Run:
   ```powershell
   .\gradlew.bat run
   ```
   *Note: The first time you run this, it will download necessary components. Be patient.*

### Option 2: Build Executable (Distribution)
1. Open PowerShell in the `minelink-java` folder.
2. Run:
   ```powershell
   .\gradlew.bat distZip
   ```
3. Look in `minelink-java/build/distributions/`.
4. You will find a `.zip` file. Unzip it anywhere.
5. Run `bin/minelink.bat` to start the app.

## Sharing with Friends
1. Send your friend the `.zip` file from `build/distributions`.
2. They unzip it and run `bin/minelink.bat`.
3. Follow the same connection steps as before (One is Host, one is Client).
