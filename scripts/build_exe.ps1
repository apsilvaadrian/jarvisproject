# Gera dist\JARVIS.exe (arquivo unico, sem precisar de Python no PC de destino).
# Uso: powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Rode scripts\setup.ps1 primeiro." }

Write-Host "[build] Instalando PyInstaller e Pillow (somente para o build)..." -ForegroundColor Cyan
& $py -m pip install --quiet pyinstaller pillow pystray uiautomation beautifulsoup4

Write-Host "[build] Gerando o icone..." -ForegroundColor Cyan
& $py scripts\gerar_icone.py

Write-Host "[build] Empacotando (leva 1-3 minutos)..." -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name JARVIS `
    --icon assets\jarvis.ico `
    --paths src `
    --add-data "src\jarvis\web;jarvis\web" `
    --add-data "src\jarvis\extensao_whatsapp;jarvis\extensao_whatsapp" `
    --collect-submodules jarvis `
    --collect-submodules uvicorn `
    --collect-submodules websockets `
    --collect-submodules pystray `
    --collect-all icalendar --collect-all recurring_ical_events --collect-all x_wr_timezone --collect-data tzdata --collect-all uiautomation --collect-submodules bs4 `
    --exclude-module pytest --exclude-module tkinter `
    jarvis_app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou." }

$exe = Join-Path $Root "dist\JARVIS.exe"
"[build] Pronto: {0} ({1:N1} MB)" -f $exe, ((Get-Item $exe).Length / 1MB)
