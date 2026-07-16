param(
  [Parameter(Mandatory=$true, Position=0)]
  [string]$Task,
  [switch]$NoInteractive,
  [switch]$NoRouter
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-Python([string[]]$ArgsList) {
  if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 @ArgsList }
  elseif (Get-Command python -ErrorAction SilentlyContinue) { & python @ArgsList }
  else { throw "Python 3.10+ introuvable" }
  return $LASTEXITCODE
}

if (-not $NoRouter) {
  & "$Repo\start-model-router.ps1"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$Code = Invoke-Python @("$Repo\.microagent\doctor.py", $Task, "--repo", $Repo)
if ($Code -ne 0) { exit $Code }
if ($NoInteractive) {
  $Code = Invoke-Python @("$Repo\.microagent\orchestrator.py", $Task, "--repo", $Repo)
} else {
  $Code = Invoke-Python @("$Repo\.microagent\interactive.py", $Task, "--repo", $Repo)
}
exit $Code
