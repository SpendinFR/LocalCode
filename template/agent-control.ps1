param(
  [Parameter(Mandatory=$true, Position=0)]
  [ValidateSet(
    "note", "constraint", "pause", "resume", "review", "revise", "replan", "abort",
    "status", "stats", "ask", "answer", "approve", "deny", "approval"
  )]
  [string]$Action,
  [Parameter(Position=1)]
  [string]$Message = "",
  [string]$Target = "current",
  [string[]]$File = @(),
  [string]$Run = "latest",
  [string]$RequestId = "",
  [ValidateSet("once", "run")]
  [string]$Scope = "once"
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Arguments = @("$Repo\.microagent\control.py", $Action)
if ($Message) { $Arguments += $Message }
$Arguments += @("--repo", $Repo, "--run", $Run, "--target", $Target, "--scope", $Scope)
if ($RequestId) { $Arguments += @("--request-id", $RequestId) }
foreach ($Item in $File) { $Arguments += @("--file", $Item) }
if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 @Arguments
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  & python @Arguments
} else {
  throw "Python 3.10+ introuvable"
}
exit $LASTEXITCODE
