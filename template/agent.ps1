param(
  [Parameter(Mandatory=$true, Position=0)]
  [string]$Task
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-Python([string[]]$ArgsList) {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @ArgsList
  } elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python @ArgsList
  } else {
    throw "Python 3.10+ introuvable"
  }
  return $LASTEXITCODE
}

$code = Invoke-Python @("$Repo\.microagent\doctor.py", $Task, "--repo", $Repo)
if ($code -ne 0) { exit $code }
$code = Invoke-Python @("$Repo\.microagent\orchestrator.py", $Task, "--repo", $Repo)
exit $code
