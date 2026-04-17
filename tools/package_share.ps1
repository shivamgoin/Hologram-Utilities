$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..")
Set-Location $repoRoot

$outRoot = Join-Path $repoRoot "share"
$outDir = Join-Path $outRoot "hologram_manager"
$zipPath = Join-Path $outRoot "hologram_manager.zip"
$bundledFfmpeg = Join-Path $repoRoot "tools\\ffmpeg\\ffmpeg.exe"
$bundledFfmpegBin = Join-Path $repoRoot "tools\\ffmpeg\\bin\\ffmpeg.exe"

if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Write-Host "Copying files to: $outDir"

$xd = @(
  "/XD", (Join-Path $repoRoot ".git"),
  "/XD", (Join-Path $repoRoot ".idea"),
  "/XD", (Join-Path $repoRoot ".venv"),
  "/XD", (Join-Path $repoRoot ".tmp_build"),
  "/XD", (Join-Path $repoRoot "exe"),
  "/XD", (Join-Path $repoRoot "share"),
  "/XD", (Join-Path $repoRoot "__pycache__")
)

# Exclude any leftover broken git folders if present
Get-ChildItem -Force -Directory -Path $repoRoot | Where-Object { $_.Name -like '.git.broken_*' } | ForEach-Object {
  $xd += @('/XD', $_.FullName)
}

robocopy $repoRoot $outDir /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP @xd /XF "*.pyc" "*.pyo" "*.log" "settings.json" "FTL.LIS" | Out-Host

# Robocopy exit codes: 0..7 are success, >=8 are failure
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }

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

if (-not $ffmpegSource) {
  throw "ffmpeg.exe not found. Put it in tools\\ffmpeg or install ffmpeg before creating a share package."
}

$destFfmpegDir = Join-Path $outDir "tools\\ffmpeg"
New-Item -ItemType Directory -Force -Path $destFfmpegDir | Out-Null
$destFfmpeg = Join-Path $destFfmpegDir "ffmpeg.exe"
Copy-Item -LiteralPath $ffmpegSource -Destination $destFfmpeg -Force
Write-Host "Bundled ffmpeg from: $ffmpegSource"

Write-Host "Creating zip: $zipPath"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "Done."
Write-Host "Folder: $outDir"
Write-Host "Zip:    $zipPath"
