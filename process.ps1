<#
.SYNOPSIS
  Turn photos/videos in input\ into a Gaussian-splat 3D scene in output\.

.EXAMPLE
  .\process.ps1                          # process every new scene folder in input\
  .\process.ps1 kitchen                  # process input\kitchen\
  .\process.ps1 kitchen -Preset draft    # quick test (~15 min on a 4 GB laptop GPU)
  .\process.ps1 kitchen -Preset high     # slower, better quality
  .\process.ps1 kitchen -Poses colmap    # classic COLMAP instead of Depth Anything 3
  .\process.ps1 kitchen -Fps 3           # frames per second taken from videos
  .\process.ps1 kitchen -PosesOnly       # stop after camera poses (quick check)
  .\process.ps1 kitchen -From train -Steps 15000   # re-train only, reuse poses
  .\process.ps1 kitchen -Options "--max-splats 300000 --da3-res 392"   # any pipeline.py option

.NOTES
  Presets: draft (3k steps), normal (10k, default), high (30k).
  Loose files dropped directly into input\ are moved into a new dated
  sub-folder (input\scene_YYYYMMDD_HHMM\) first - nothing is deleted.
#>
param(
    [string]$Scene,
    [ValidateSet("draft", "normal", "high")][string]$Preset = "normal",
    [ValidateSet("auto", "da3", "colmap")][string]$Poses = "auto",
    [string]$Fps = "auto",
    [switch]$PosesOnly,
    [ValidateSet("frames", "poses", "train")][string]$From = "frames",
    [int]$Steps = 0,
    [string]$Options = "",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\scripts\common.ps1"
$py = Get-ProjectPython
$inputDir = Join-Path $Root "input"
$media = "\.(mp4|mov|m4v|avi|mkv|webm|mts|3gp|jpe?g|png|heic|heif|webp|tiff?|bmp)$"

# Loose files in input\ -> their own scene folder
$loose = Get-ChildItem $inputDir -File | Where-Object { $_.Name -match $media }
if ($loose) {
    $name = "scene_" + (Get-Date -Format "yyyyMMdd_HHmm")
    $dest = New-Item -ItemType Directory -Path (Join-Path $inputDir $name) -Force
    $loose | Move-Item -Destination $dest
    Write-Host "Moved $($loose.Count) loose file(s) into input\$name" -ForegroundColor Cyan
    if (-not $Scene) { $Scene = $name }
}

if ($Scene) {
    $scenes = @($Scene)
} else {
    # every input sub-folder that has no finished .ply yet (or all, with -Force)
    $scenes = Get-ChildItem $inputDir -Directory | Where-Object {
        $Force -or -not (Test-Path (Join-Path $Root "output\$($_.Name)\$($_.Name).ply"))
    } | ForEach-Object Name
}
if (-not $scenes) { Write-Host "Nothing new to process in input\ (use -Force to redo)."; exit 0 }

$failed = @()
foreach ($s in $scenes) {
    if (-not (Test-Path (Join-Path $inputDir $s))) { throw "input\$s does not exist" }
    Write-Host "`n=== Processing scene '$s' (preset $Preset) ===" -ForegroundColor Green
    $pyArgs = @("-u", (Join-Path $Root "scripts\pipeline.py"), $s, "--preset", $Preset,
                "--poses", $Poses, "--fps", $Fps, "--from", $From)
    if ($PosesOnly) { $pyArgs += @("--stop-after", "poses") }
    if ($Steps -gt 0) { $pyArgs += @("--steps", $Steps) }
    if ($Options) { $pyArgs += ($Options -split "\s+" | Where-Object { $_ }) }
    & $py @pyArgs
    if ($LASTEXITCODE -ne 0) { $failed += $s }
}
if ($failed) { Write-Host "`nFailed: $($failed -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "`nAll done. Open a scene with:  .\view.ps1 <scene>   or render a shot with  .\render-shot.ps1 <scene> -Shot dolly-right" -ForegroundColor Green
