# Instala a voz clonada (XTTS-v2, local e gratuita) e prepara o audio de referencia.
# Uso: powershell -ExecutionPolicy Bypass -File scripts\setup_voz.ps1 -Audio "C:\caminho\minha_voz.mp3"
# O modelo usa a Coqui Public Model License (somente uso nao comercial).
param([Parameter(Mandatory = $true)][string]$Audio)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Ambiente em caminho curto: algumas DLLs do PyTorch falham em caminhos com mais de 260 caracteres
$VenvDir = "$env:USERPROFILE\.jarvis\venv-voz"
$Py = "$VenvDir\Scripts\python.exe"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Instale o uv: https://docs.astral.sh/uv/ (powershell -c 'irm https://astral.sh/uv/install.ps1 | iex')" }

if (-not (Test-Path $Py)) {
    Write-Host "[voz] Criando ambiente Python 3.13 em $VenvDir" -ForegroundColor Cyan
    uv venv $VenvDir --python 3.13
}
Write-Host "[voz] Instalando coqui-tts e PyTorch (CPU)..." -ForegroundColor Cyan
# torch 2.8: as versoes mais novas do torchaudio exigem FFmpeg instalado no sistema
uv pip install --python $Py "coqui-tts" "torch==2.8.*" "torchaudio==2.8.*" --index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cpu

# Baixa o modelo com curl (a biblioteca do Hugging Face pode travar no Windows)
$ModelDir = "$env:USERPROFILE\.jarvis\xtts-v2"
New-Item -ItemType Directory -Force $ModelDir | Out-Null
foreach ($f in "config.json", "vocab.json", "speakers_xtts.pth", "LICENSE.txt", "model.pth") {
    Write-Host "[voz] Baixando $f"
    curl.exe -fL --retry 3 -C - -o "$ModelDir\$f" "https://huggingface.co/tts-hub/XTTS-v2/resolve/main/$f"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao baixar $f" }
}

Write-Host "[voz] Preparando o audio de referencia" -ForegroundColor Cyan
& $Py -W ignore voz\preparar_voz.py $Audio

Write-Host "[voz] Pronto. O run.ps1 inicia o servidor de voz automaticamente." -ForegroundColor Green
Write-Host "[voz] Modelo salvo em $ModelDir (~1,9 GB)."
