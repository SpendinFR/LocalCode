param(
  [Parameter(Mandatory=$true, Position=0)]
  [ValidateSet("note", "pause", "resume", "review", "revise", "replan", "abort", "status")]
  [string]$Action,
  [Parameter(Position=1)]
  [string]$Message = "",
  [string]$Target = "current",
  [string[]]$File = @(),
  [string]$Run = "latest"
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Arguments = @("$Repo\.microagent\control.py", $Action)
if ($Message) { $Arguments += $Message }
$Arguments += @("--repo", $Repo, "--run", $Run, "--target", $Target)
foreach ($Item in $File) { $Arguments += @("--file", $Item) }
if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 @Arguments
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  & python @Arguments
} else {
  throw "Python 3.10+ introuvable"
}
exit $LASTEXITCODE
