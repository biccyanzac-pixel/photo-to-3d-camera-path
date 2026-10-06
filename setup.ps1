<#
.SYNOPSIS
  Download/install every tool this project needs into .\tools (nothing system-wide).
  Safe to re-run: anything already present is skipped.

  Tools (all free / open source, official releases):
    FFmpeg 8.1      (BtbN builds)        frame extraction
    COLMAP 4.2.1    (colmap/colmap)      camera pose recovery (Structure-from-Motion)
    Brush 0.3.0     (ArthurBrussee/brush) Gaussian-splat training, WebGPU - any GPU
    Node 24 LTS     (nodejs.org, portable) only used to build SuperSplat once
    SuperSplat 3.5.2 (playcanvas/supersplat) viewer/editor: keyframes + MP4 render
    Apple SHARP     (apple/ml-sharp)     single-photo -> 3D splat (Python venv)
#>
param([switch]$SkipSharp)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
$Root = $PSScriptRoot
$T = Join-Path $Root "tools"
$DL = Join-Path $T "_dl"
New-Item -ItemType Directory -Force $DL, "$Root\input", "$Root\frames", "$Root\reconstruction",
    "$Root\camera", "$Root\renders", "$Root\output" | Out-Null

function Get-Zip($name, $url, $test) {
    if (Test-Path (Join-Path $T $test)) { Write-Host "[ok] $name"; return }
    $zip = Join-Path $DL "$name.zip"
    if (-not (Test-Path $zip)) { Write-Host "Downloading $name ..."; Invoke-WebRequest $url -OutFile $zip }
    Write-Host "Extracting $name ..."
    Expand-Archive $zip -DestinationPath (Join-Path $T $name) -Force
}

Get-Zip "ffmpeg" "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-gpl-8.1.zip" "ffmpeg\ffmpeg-n8.1-latest-win64-gpl-8.1\bin\ffmpeg.exe"
Get-Zip "colmap" "https://github.com/colmap/colmap/releases/download/4.2.1/colmap-x64-windows-cuda.zip" "colmap\bin\colmap.exe"
Get-Zip "brush"  "https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-pc-windows-msvc.zip" "brush\brush_app.exe"

# SuperSplat has no prebuilt release: build it once with a portable Node.
if (-not (Test-Path "$T\supersplat\index.html")) {
    Get-Zip "node" "https://nodejs.org/dist/v24.21.0/node-v24.21.0-win-x64.zip" "node\node-v24.21.0-win-x64\node.exe"
    $env:Path = "$T\node\node-v24.21.0-win-x64;$env:Path"
    if (-not (Test-Path "$T\supersplat-src")) {
        git clone --depth 1 --branch v3.5.2 https://github.com/playcanvas/supersplat.git "$T\supersplat-src"
    }
    Push-Location "$T\supersplat-src"
    npm ci --no-audit --no-fund; npm run build
    Pop-Location
    Copy-Item -Recurse "$T\supersplat-src\dist" "$T\supersplat"
} else { Write-Host "[ok] supersplat" }

# Python venv: Pillow for the pipeline + Apple SHARP (PyTorch) for single photos.
$venvPy = "$T\sharp-venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $base = (Get-Command python -ErrorAction Stop).Source
    & $base -m venv "$T\sharp-venv"
    & $venvPy -m pip install --upgrade pip
}
& $venvPy -m pip install -q pillow pillow-heif numpy websocket-client
if (-not $SkipSharp -and -not (Test-Path "$T\sharp-venv\Scripts\sharp.exe")) {
    if (-not (Test-Path "$T\ml-sharp")) { git clone --depth 1 https://github.com/apple/ml-sharp.git "$T\ml-sharp" }
    # cu126 wheels still include kernels for older (Pascal, GTX 10xx / Quadro Pxxx) GPUs
    & $venvPy -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
    & $venvPy -m pip install -e "$T\ml-sharp"
}
# Depth Anything 3 (Apache-2.0 DA3-BASE): AI camera poses when classic SfM can't link the images.
# Separate Python 3.11 env because DA3 pins numpy<2.
$da3Py = "$T\da3-env\python.exe"
if (-not (Test-Path $da3Py)) {
    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if ($conda) { & conda create -y -q -p "$T\da3-env" python=3.11 }
    else { Write-Warning "conda not found: install Miniconda, or create a Python 3.11 venv at tools\da3-env manually." }
}
if ((Test-Path $da3Py) -and -not (Test-Path "$T\Depth-Anything-3")) {
    git clone --depth 1 https://github.com/ByteDance-Seed/Depth-Anything-3.git "$T\Depth-Anything-3"
    & $da3Py -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
    # DA3's full dependency list includes xformers/open3d/gradio which we don't need
    & $da3Py -m pip install einops huggingface_hub safetensors omegaconf opencv-python pillow "numpy<2" `
        plyfile imageio trimesh pycolmap typer pillow_heif "moviepy==1.0.3" e3nn evo requests addict fastapi uvicorn
    & $da3Py -m pip install --no-deps -e "$T\Depth-Anything-3"
} elseif (Test-Path $da3Py) { Write-Host "[ok] depth-anything-3" }
Write-Host "`nSetup complete." -ForegroundColor Green
