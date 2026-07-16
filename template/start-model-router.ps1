param(
  [int]$Port = 8080,
  [string]$ApiKey = "local",
  [switch]$Foreground,
  [switch]$Restart
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Preset = Join-Path $Repo ".microagent\models.ini"
$LogDir = Join-Path $Repo ".agent-runs\router"
$PidFile = Join-Path $LogDir "llama-router.pid"
$StartLock = Join-Path $LogDir "start.lock"

function Find-LlamaServer {
  foreach ($Name in @("llama-server", "llama-server.exe")) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($Command) { return $Command.Source }
  }
  foreach ($Candidate in @(
    "$env:LOCALAPPDATA\Microsoft\WinGet\Links\llama-server.exe",
    "$env:USERPROFILE\llama.cpp\llama-server.exe",
    "C:\llama.cpp\llama-server.exe"
  )) {
    if (Test-Path $Candidate) { return $Candidate }
  }
  throw "llama-server introuvable. Installe llama.cpp ou ouvre un nouveau PowerShell après WinGet."
}

function Test-Router {
  try {
    $Headers = @{ Authorization = "Bearer $ApiKey" }
    $null = Invoke-RestMethod "http://127.0.0.1:$Port/models" -Headers $Headers -TimeoutSec 3
    return $true
  } catch { return $false }
}

if ($Restart) { & (Join-Path $Repo "stop-model-router.ps1") }
if (Test-Router) {
  Write-Host "Routeur llama.cpp déjà actif sur le port $Port."
  exit 0
}
if (-not (Test-Path $Preset)) { throw "Preset absent: $Preset. Relance install.ps1 -Models yes." }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$LockAcquired = $false
$LockDeadline = (Get-Date).AddSeconds(90)
do {
  try {
    New-Item -ItemType Directory -Path $StartLock -ErrorAction Stop | Out-Null
    $LockAcquired = $true
    break
  } catch {
    if (Test-Router) {
      Write-Host "Routeur démarré par un autre processus."
      exit 0
    }
    $Item = Get-Item $StartLock -ErrorAction SilentlyContinue
    if ($Item -and $Item.LastWriteTime -lt (Get-Date).AddMinutes(-3)) {
      Remove-Item $StartLock -Recurse -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 250
  }
} while ((Get-Date) -lt $LockDeadline)
if (-not $LockAcquired) { throw "Impossible d'acquérir le verrou de démarrage du routeur" }

try {
  if (Test-Router) {
    Write-Host "Routeur déjà actif après acquisition du verrou."
    exit 0
  }
  $Server = Find-LlamaServer
  $Arguments = @(
    "--models-preset", $Preset,
    "--models-max", "1",
    "--models-autoload",
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--api-key", $ApiKey
  )
  if ($Foreground) {
    & $Server @Arguments
    exit $LASTEXITCODE
  }
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
  $OutLog = Join-Path $LogDir "llama-router.out.log"
  $ErrLog = Join-Path $LogDir "llama-router.err.log"
  $Process = Start-Process -FilePath $Server -ArgumentList $Arguments -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
  Set-Content -Path $PidFile -Value $Process.Id -Encoding ascii
  $Deadline = (Get-Date).AddSeconds(90)
  do {
    Start-Sleep -Milliseconds 500
    if ($Process.HasExited) { throw "Routeur arrêté; consulte $ErrLog" }
    if (Test-Router) {
      Write-Host "Routeur prêt, PID $($Process.Id), un modèle maximum chargé."
      exit 0
    }
  } while ((Get-Date) -lt $Deadline)
  throw "Routeur non prêt après 90 secondes; consulte $ErrLog"
} finally {
  Remove-Item $StartLock -Recurse -Force -ErrorAction SilentlyContinue
}
