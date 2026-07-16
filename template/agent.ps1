param(
  [Parameter(Position=0)]
  [string]$Task = "",
  [string]$Resume = "",
  [ValidateSet("", "auto", "commands", "all")]
  [string]$ApprovalMode = "",
  [switch]$NoInteractive,
  [switch]$NoRouter
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

$script:LastPythonExitCode = 0
function Invoke-Python([string[]]$ArgsList) {
  if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 @ArgsList }
  elseif (Get-Command python -ErrorAction SilentlyContinue) { & python @ArgsList }
  else { throw "Python 3.10+ introuvable" }
  $script:LastPythonExitCode = $LASTEXITCODE
}

if (-not $Task -and -not $Resume) {
  throw "Indique une mission ou utilise -Resume latest"
}
if (-not $NoRouter -and $env:MICROAGENT_SKIP_ROUTER_CHECK -ne "1" -and $env:MICROAGENT_SKIP_OLLAMA_CHECK -ne "1") {
  & "$Repo\start-model-router.ps1"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if ($ApprovalMode) {
  $Settings = Join-Path $Repo ".agent-runs\default-control-settings.json"
  @{ approval_mode = $ApprovalMode } | ConvertTo-Json | Set-Content -Path $Settings -Encoding UTF8
}

if ($Resume) {
  $Arguments = @("--repo", $Repo, "--resume", $Resume)
  if ($NoInteractive) {
    $Call = @("$Repo\.microagent\resilient_orchestrator.py") + $Arguments
    Invoke-Python $Call
    $Code = $script:LastPythonExitCode
  } else {
    $Call = @("$Repo\.microagent\interactive.py") + $Arguments
    Invoke-Python $Call
    $Code = $script:LastPythonExitCode
  }
  exit $Code
}

Invoke-Python @("$Repo\.microagent\doctor.py", $Task, "--repo", $Repo)

$Code = $script:LastPythonExitCode
if ($Code -ne 0) { exit $Code }
if ($NoInteractive) {
  Invoke-Python @("$Repo\.microagent\resilient_orchestrator.py", $Task, "--repo", $Repo)
  $Code = $script:LastPythonExitCode
} else {
  Invoke-Python @("$Repo\.microagent\interactive.py", $Task, "--repo", $Repo)
  $Code = $script:LastPythonExitCode
}
exit $Code
