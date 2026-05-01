$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")
Set-Location $repoRoot

$outRoot = Join-Path $repoRoot "share"
$outDir = Join-Path $outRoot "hologram_manager"
$zipPath = Join-Path $outRoot "hologram_manager.zip"
$bundledFfmpeg = Join-Path $repoRoot "tools\ffmpeg\ffmpeg.exe"
$bundledFfmpegBin = Join-Path $repoRoot "tools\ffmpeg\bin\ffmpeg.exe"
$bundledUnixFfmpeg = Join-Path $repoRoot "tools\ffmpeg\ffmpeg"
$bundledUnixFfmpegBin = Join-Path $repoRoot "tools\ffmpeg\bin\ffmpeg"

if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $outDir "src") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $outDir "tools\ffmpeg") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $outDir "tools\win") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $outDir "tools\mac") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $outDir "vendor\wheels") | Out-Null

Write-Host "Copying runtime files to: $outDir"

Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination (Join-Path $outDir "README.md") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "requirements.txt") -Destination (Join-Path $outDir "requirements.txt") -Force

robocopy (Join-Path $repoRoot "src") (Join-Path $outDir "src") /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD "__pycache__" /XF "*.pyc" "*.pyo" "*.log" ".DS_Store" "settings.json" "FTL.LIS" | Out-Host
if ($LASTEXITCODE -ge 8) { throw "robocopy src failed with exit code $LASTEXITCODE" }

if (Test-Path (Join-Path $repoRoot "vendor\wheels")) {
  robocopy (Join-Path $repoRoot "vendor\wheels") (Join-Path $outDir "vendor\wheels") /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XF ".DS_Store" | Out-Host
  if ($LASTEXITCODE -ge 8) { throw "robocopy vendor/wheels failed with exit code $LASTEXITCODE" }
}

if (Test-Path (Join-Path $repoRoot "tools\ffmpeg")) {
  robocopy (Join-Path $repoRoot "tools\ffmpeg") (Join-Path $outDir "tools\ffmpeg") /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XF ".DS_Store" | Out-Host
  if ($LASTEXITCODE -ge 8) { throw "robocopy tools/ffmpeg failed with exit code $LASTEXITCODE" }
}

Copy-Item -LiteralPath (Join-Path $repoRoot "tools\win\start-windows.bat") -Destination (Join-Path $outDir "tools\win\start-windows.bat") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "tools\mac\start-mac.command") -Destination (Join-Path $outDir "tools\mac\start-mac.command") -Force

@'
@echo off
call "%~dp0tools\win\start-windows.bat"
'@ | Set-Content -LiteralPath (Join-Path $outDir "start-windows.bat") -Encoding ASCII

@'
#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/tools/mac/start-mac.command"
'@ | Set-Content -LiteralPath (Join-Path $outDir "start-mac.command") -Encoding ASCII

$ffmpegSource = $null
if (Test-Path $bundledFfmpeg) {
  $ffmpegSource = $bundledFfmpeg
} elseif (Test-Path $bundledFfmpegBin) {
  $ffmpegSource = $bundledFfmpegBin
} else {
  $cmd = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
  if (-not $cmd) { $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue }
  if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
    $ffmpegSource = $cmd.Source
  }
}

$destFfmpegDir = Join-Path $outDir "tools\ffmpeg"
if ($ffmpegSource) {
  $destFfmpeg = Join-Path $destFfmpegDir "ffmpeg.exe"
  Copy-Item -LiteralPath $ffmpegSource -Destination $destFfmpeg -Force
  Write-Host "Bundled Windows ffmpeg from: $ffmpegSource"
} else {
  Write-Warning "Windows ffmpeg.exe not found. The package can still run, but MP4 conversion on Windows will require ffmpeg in PATH or a later bundled copy."
}

$unixFfmpegSource = $null
if (Test-Path $bundledUnixFfmpeg) {
  $unixFfmpegSource = $bundledUnixFfmpeg
} elseif (Test-Path $bundledUnixFfmpegBin) {
  $unixFfmpegSource = $bundledUnixFfmpegBin
}

if ($unixFfmpegSource) {
  $destUnixFfmpeg = Join-Path $destFfmpegDir "ffmpeg"
  Copy-Item -LiteralPath $unixFfmpegSource -Destination $destUnixFfmpeg -Force
  Write-Host "Included mac/Linux ffmpeg from repo: $unixFfmpegSource"
}

Write-Host "Creating zip: $zipPath"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "Done."
Write-Host "Folder: $outDir"
Write-Host "Zip:    $zipPath"
