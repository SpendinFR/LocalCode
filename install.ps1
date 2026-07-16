param(
  [string]$Repo = ".",
  [ValidateSet("ask", "yes", "no")]
  [string]$Models = "ask",
  [string]$ModelsDir = "$env:LOCALAPPDATA\LocalCode\models",
  [string[]]$ExistingModelsDir = @(
    "$env:USERPROFILE\Downloads",
    "$env:USERPROFILE\.cache\lm-studio\models",
    "$env:LOCALAPPDATA\LM Studio\models"
  ),
  [string]$Qwen35Path = "",
  [string]$Qwen3CoderPath = "",
  [string]$ReviewerPath = ""
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path $Repo).Path

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git requis" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js 22+ requis" }
if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.10+ requis" }

function Invoke-Python([string[]]$ArgsList) {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @ArgsList
  } else {
    & python @ArgsList
  }
  if ($LASTEXITCODE -ne 0) { throw "Commande Python échouée: $($ArgsList -join ' ')" }
}

if ($Models -eq "ask") {
  Write-Host "Modèles recommandés:"
  Write-Host "  planner/scout/juge : Qwen3.5-9B-Q4_K_M.gguf"
  Write-Host "  codeur             : Qwen3-Coder-30B-A3B-Instruct-UD-IQ2_XXS.gguf"
  Write-Host "  reviewers          : Qwen2.5-Coder-14B-Instruct Q3_K_M"
  $Answer = Read-Host "Installer/configurer ces modèles maintenant ? [O/n]"
  $Models = if ($Answer -match '^[Nn]') { "no" } else { "yes" }
}

npm install -g @qwen-code/qwen-code@latest
if ($LASTEXITCODE -ne 0) { throw "Installation de Qwen Code échouée" }

$TrackedPython = & git -C $Repo ls-files "*.py" | Select-Object -First 1
if ($LASTEXITCODE -eq 0 -and $TrackedPython) {
  Invoke-Python @("-m", "pip", "install", "--upgrade", "python-lsp-server")
}

Invoke-Python @((Join-Path $PSScriptRoot "install_into_repo.py"), $Repo)

if ($Models -eq "yes") {
  if (-not (Get-Command llama-server -ErrorAction SilentlyContinue) -and -not (Get-Command llama-server.exe -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
      throw "llama.cpp absent et WinGet introuvable. Installe llama.cpp, puis relance avec -Models yes."
    }
    winget install llama.cpp --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Installation de llama.cpp échouée" }
    Write-Host "llama.cpp installé. Ouvre un nouveau PowerShell si llama-server n'est pas encore dans PATH."
  }

  Invoke-Python @("-m", "pip", "install", "--upgrade", "huggingface_hub")
  $Arguments = @(
    (Join-Path $PSScriptRoot "setup_models.py"),
    "--repo", $Repo,
    "--models-dir", $ModelsDir
  )
  foreach ($Directory in $ExistingModelsDir) {
    if ($Directory -and (Test-Path $Directory)) {
      $Arguments += @("--existing-dir", (Resolve-Path $Directory).Path)
    }
  }
  if ($Qwen35Path) { $Arguments += @("--qwen35-path", $Qwen35Path) }
  if ($Qwen3CoderPath) { $Arguments += @("--qwen3coder-path", $Qwen3CoderPath) }
  if ($ReviewerPath) { $Arguments += @("--reviewer-path", $ReviewerPath) }
  Invoke-Python $Arguments
} else {
  Write-Host "Téléchargement des modèles ignoré. Tu pourras relancer: .\install.ps1 -Repo `"$Repo`" -Models yes"
}

Write-Host "Installation terminée."
Write-Host "Lancement: .\agent.ps1 .tasks\TASK-XXX.md"
