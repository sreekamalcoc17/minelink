# Build script for MineLink
CLS
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "   MineLink Build Script" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python
Write-Host "[1/3] Checking Python environment..."
try {
    $version = python --version 2>&1
    Write-Host "Found: $version" -ForegroundColor Green
}
catch {
    Write-Host "Error: Python not found!" -ForegroundColor Red
    Write-Host "Please install Python 3.10+ and make sure to 'Add to PATH' during installation."
    Pause
    exit 1
}

# 2. Install Dependencies
Write-Host ""
Write-Host "[2/3] Installing dependencies..."
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error installing requirements!" -ForegroundColor Red
    Pause
    exit 1
}

Write-Host "Installing PyInstaller..."
pip install pyinstaller
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error installing PyInstaller!" -ForegroundColor Red
    Pause
    exit 1
}

# 3. Build EXE
Write-Host ""
Write-Host "[3/3] Building executable..."
# Clean previous builds
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "MineLink.spec") { Remove-Item -Force "MineLink.spec" }

# Run PyInstaller via python -m to avoid PATH issues
# --noconsole: Hide terminal window
# --onefile: Single .exe file
# --collect-all customtkinter: Required for UI library assets
python -m PyInstaller --noconsole --onefile --name "MineLink" --collect-all customtkinter minelink.py

if ($LASTEXITCODE -eq 0 -and (Test-Path "dist/MineLink.exe")) {
    Write-Host ""
    Write-Host "==================================" -ForegroundColor Green
    Write-Host "   BUILD SUCCESSFUL!" -ForegroundColor Green
    Write-Host "==================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Your file is ready at:"
    Write-Host "   dist\MineLink.exe" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "You can send this .exe file to your friend."
    Write-Host "They do NOT need Python installed to run it."
}
else {
    Write-Host ""
    Write-Host "BUILD FAILED" -ForegroundColor Red
    Write-Host "Check the error messages above."
}

Write-Host ""
Read-Host -Prompt "Press Enter to exit"
