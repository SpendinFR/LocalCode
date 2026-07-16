param([int]$Port = 8080, [string]$ApiKey = "local")
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Repo ".agent-runs\router\llama-router.pid"
$LegacyPidFile = Join-Path $Repo ".microagent\llama-router.pid"
if (-not (Test-Path $PidFile) -and (Test-Path $LegacyPidFile)) { $PidFile = $LegacyPidFile }
if (-not (Test-Path $PidFile)) { Write-Host "Aucun PID enregistré."; exit 0 }
$Value = (Get-Content $PidFile -Raw).Trim()
if ($Value -match '^\d+$') {
  $Process = Get-Process -Id ([int]$Value) -ErrorAction SilentlyContinue
  if ($Process) { Stop-Process -Id $Process.Id -Force; Write-Host "Routeur arrêté: PID $($Process.Id)" }
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
