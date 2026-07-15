param([string]$Repo = ".")
$ErrorActionPreference = "Stop"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js 22+ requis" }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw "Ollama requis" }
if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.10+ requis" }

npm install -g @qwen-code/qwen-code@latest
ollama pull qwen3:8b
ollama pull qwen2.5-coder:7b

$Installer = Join-Path $PSScriptRoot "install_into_repo.py"
if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 $Installer $Repo
} else {
  & python $Installer $Repo
}
exit $LASTEXITCODE
