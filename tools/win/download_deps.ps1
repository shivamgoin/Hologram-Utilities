$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent | Split-Path -Parent
Set-Location $repoRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Python not found in PATH. Install Python and re-run."
}

$wheelDir = Join-Path $repoRoot "vendor\wheels"
New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null

python -m pip download -r (Join-Path $repoRoot "requirements.txt") -d $wheelDir
Write-Host "Downloaded wheels to: $wheelDir"
