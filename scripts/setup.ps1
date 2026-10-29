# Prepara o ambiente do JARVIS no Windows: venv, dependencias, .env e pastas.
# Uso: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Info($msg) { Write-Host "[JARVIS] $msg" -ForegroundColor Cyan }

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { throw "Python nao encontrado. Instale em https://www.python.org/downloads/ (marque 'Add to PATH')." }
Info "Python: $(& $py.Source --version)"

if (-not (Test-Path ".venv")) {
    Info "Criando ambiente virtual em .venv\"
    & $py.Source -m venv .venv
}
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
Info "Instalando dependencias (requirements.txt)"
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Info ".env criado - edite-o para adicionar suas chaves de API."
}

foreach ($d in "logs", "reports", "data", "run") {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
# Restringe o .env ao usuario atual (equivalente ao chmod 600)
icacls ".env" /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { Info "GPU NVIDIA detectada (nvidia-smi)." }
Info "Temperatura da CPU no Windows: rode o LibreHardwareMonitor (opcional) ou o JARVIS como administrador."
Info "Pronto! Inicie com: powershell -ExecutionPolicy Bypass -File scripts\run.ps1"
