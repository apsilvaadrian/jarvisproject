# Gerencia o processo do JARVIS no Windows usando um arquivo de PID.
# Uso: powershell -ExecutionPolicy Bypass -File scripts\run.ps1 [start|stop|status|cli]
param([ValidateSet("start", "stop", "restart", "status", "cli")][string]$Acao = "start")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path "run", "logs" | Out-Null
$PidFile = "run\jarvis.pid"

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
$py = if (Test-Path $venvPy) { $venvPy } else { "python" }
$env:PYTHONPATH = Join-Path $Root "src"
$port = 8000
if (Test-Path ".env") {
    $line = Select-String -Path ".env" -Pattern "^JARVIS_PORT=(\d+)" | Select-Object -First 1
    if ($line) { $port = [int]$line.Matches[0].Groups[1].Value }
}
$url = "http://127.0.0.1:$port"

function Get-JarvisProcess {
    if (-not (Test-Path $PidFile)) { return $null }
    $procId = [int](Get-Content $PidFile)
    return Get-Process -Id $procId -ErrorAction SilentlyContinue
}

function Open-Ui {
    # Abre como "aplicativo" (sem barra de endereco) no Edge ou Chrome, se existirem
    $edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    $chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
    if (Test-Path $edge) { Start-Process $edge "--app=$url --start-maximized" }
    elseif (Test-Path $chrome) { Start-Process $chrome "--app=$url --start-maximized" }
    else { Start-Process $url }
}

# Servidor de voz clonada (opcional): ambiente criado por scripts\setup_voz.ps1
$VozPy = if ($env:JARVIS_VOZ_PYTHON) { $env:JARVIS_VOZ_PYTHON } else { "$env:USERPROFILE\.jarvis\venv-voz\Scripts\python.exe" }
$VozPidFile = "run\voz.pid"

function Start-Voz {
    if (-not ((Test-Path $VozPy) -and (Test-Path "data\voz\referencia.wav"))) { return }
    if ((Test-Path $VozPidFile) -and (Get-Process -Id ([int](Get-Content $VozPidFile)) -ErrorAction SilentlyContinue)) { return }
    $v = Start-Process -FilePath $VozPy -ArgumentList "-u", "voz\servidor_voz.py" -WorkingDirectory $Root `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput "logs\voz.out" -RedirectStandardError "logs\voz.err"
    Set-Content -Path $VozPidFile -Value $v.Id
    Write-Host "Servidor de voz clonada iniciado (PID $($v.Id)); o modelo leva ~1 min para carregar."
}

function Stop-Voz {
    if (-not (Test-Path $VozPidFile)) { return }
    Stop-Process -Id ([int](Get-Content $VozPidFile)) -ErrorAction SilentlyContinue
    Remove-Item $VozPidFile -ErrorAction SilentlyContinue
}

function Start-Jarvis {
    Start-Voz
    $p = Get-JarvisProcess
    if ($p) { Write-Host "JARVIS ja esta em execucao (PID $($p.Id))."; Open-Ui; return }
    Write-Host "Iniciando JARVIS em $url ..."
    $p = Start-Process -FilePath $py -ArgumentList "-m", "jarvis", "--no-browser" -WorkingDirectory $Root `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput "logs\servidor.out" -RedirectStandardError "logs\servidor.err"
    Set-Content -Path $PidFile -Value $p.Id
    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-RestMethod "$url/health" -TimeoutSec 1 | Out-Null
            Write-Host "JARVIS online (PID $($p.Id))." -ForegroundColor Green
            Open-Ui
            return
        } catch { Start-Sleep -Milliseconds 500 }
    }
    Write-Host "O servidor nao respondeu. Veja logs\servidor.err" -ForegroundColor Red
}

function Stop-Jarvis {
    Stop-Voz
    $p = Get-JarvisProcess
    if (-not $p) { Write-Host "JARVIS nao esta em execucao."; Remove-Item $PidFile -ErrorAction SilentlyContinue; return }
    Write-Host "Encerrando PID $($p.Id) ..."
    Stop-Process -Id $p.Id
    $p.WaitForExit(5000) | Out-Null
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    Write-Host "JARVIS encerrado."
}

switch ($Acao) {
    "start"   { Start-Jarvis }
    "stop"    { Stop-Jarvis }
    "restart" { Stop-Jarvis; Start-Jarvis }
    "status"  {
        $p = Get-JarvisProcess
        if ($p) { $p | Select-Object Id, ProcessName, StartTime, CPU, @{n = "MemoriaMB"; e = { [math]::Round($_.WorkingSet64 / 1MB, 1) } } | Format-List }
        else { Write-Host "JARVIS parado." }
    }
    "cli"     { & $py -m jarvis cli }
}
