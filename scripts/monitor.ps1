# Verifica recursos do Windows (CPU, memoria, discos, processos) e grava relatorio em reports\.
# Uso: powershell -ExecutionPolicy Bypass -File scripts\monitor.ps1 [-LimiteCpu 85] [-LimiteMem 90] [-LimiteDisco 90]
# Agendar a cada 30 min (Agendador de Tarefas):
#   schtasks /Create /SC MINUTE /MO 30 /TN "JARVIS Monitor" /TR "powershell -ExecutionPolicy Bypass -File C:\caminho\jarvis\scripts\monitor.ps1"
param([int]$LimiteCpu = 85, [int]$LimiteMem = 90, [int]$LimiteDisco = 90)
$Root = Split-Path -Parent $PSScriptRoot
$ReportDir = Join-Path $Root "reports"
$AlertLog = Join-Path $Root "logs\alertas.log"
New-Item -ItemType Directory -Force -Path $ReportDir, (Join-Path $Root "logs") | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$out = Join-Path $ReportDir "monitor_$stamp.txt"
$alerts = New-Object System.Collections.Generic.List[string]
function Add-Alert($msg) {
    $alerts.Add($msg)
    Add-Content -Path $AlertLog -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ALERTA $msg"
}

$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cpuLoad = [int](Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$memTotal = $os.TotalVisibleMemorySize * 1KB
$memFree = $os.FreePhysicalMemory * 1KB
$memPct = [int](100 * ($memTotal - $memFree) / $memTotal)
$uptime = (Get-Date) - $os.LastBootUpTime

if ($cpuLoad -ge $LimiteCpu) { Add-Alert "CPU em $cpuLoad% (limite $LimiteCpu%)" }
if ($memPct -ge $LimiteMem) { Add-Alert "Memoria em $memPct% (limite $LimiteMem%)" }

$lines = @()
$lines += "============================================================"
$lines += "  J.A.R.V.I.S. - MONITOR DE RECURSOS (PowerShell)"
$lines += "  $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')  host: $env:COMPUTERNAME  usuario: $env:USERNAME"
$lines += "============================================================"
$lines += ""
$lines += "[SISTEMA]"
$lines += "  SO       : $($os.Caption) $($os.Version)"
$lines += "  Uptime   : $($uptime.Days)d $($uptime.Hours)h $($uptime.Minutes)min"
$lines += "  Processos: $((Get-Process).Count)"
$lines += ""
$lines += "[CPU]"
$lines += "  Modelo   : $($cpu.Name.Trim())"
$lines += "  Nucleos  : $($cpu.NumberOfCores) fisicos / $($cpu.NumberOfLogicalProcessors) logicos"
$lines += "  Uso      : $cpuLoad%"
$lines += ""
$lines += "[MEMORIA]"
$lines += "  Total    : $([math]::Round($memTotal / 1GB, 2)) GB"
$lines += "  Livre    : $([math]::Round($memFree / 1GB, 2)) GB"
$lines += "  Uso      : $memPct%"
$lines += ""
$lines += "[DISCOS]"
foreach ($d in Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3") {
    $pct = [int](100 * ($d.Size - $d.FreeSpace) / $d.Size)
    $lines += "  $($d.DeviceID) $([math]::Round($d.FreeSpace / 1GB, 1)) GB livres de $([math]::Round($d.Size / 1GB, 1)) GB ($pct% usado, $($d.FileSystem))"
    if ($pct -ge $LimiteDisco) { Add-Alert "Disco $($d.DeviceID) em $pct% (limite $LimiteDisco%)" }
}
$lines += ""
$lines += "[TOP 5 PROCESSOS - MEMORIA]"
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 5 | ForEach-Object {
    $lines += ("  {0,7}  {1,9} MB  {2}" -f $_.Id, [math]::Round($_.WorkingSet64 / 1MB, 1), $_.ProcessName)
}
$lines += ""
$lines += "[TOP 5 PROCESSOS - TEMPO DE CPU]"
Get-Process | Where-Object { $_.CPU } | Sort-Object CPU -Descending | Select-Object -First 5 | ForEach-Object {
    $lines += ("  {0,7}  {1,9} s   {2}" -f $_.Id, [math]::Round($_.CPU, 1), $_.ProcessName)
}
$lines += ""
$lines += "Alertas emitidos: $($alerts.Count)"
foreach ($a in $alerts) { $lines += "  [ALERTA] $a" }

$lines | Set-Content -Path $out -Encoding UTF8
$lines | ForEach-Object { Write-Host $_ }
Write-Host ""
Write-Host "Relatorio salvo em: $out" -ForegroundColor Cyan

# Rotacao: mantem os 30 relatorios mais recentes
Get-ChildItem $ReportDir -Filter "monitor_*.txt" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 | Remove-Item -Force

if ($alerts.Count -gt 0) { exit 1 } else { exit 0 }
