# Instructions for AI coding agents (Claude Code, Codex, etc.)

This repo turns a user's existing **photos and/or videos of a real place** into a 3D Gaussian
splat. The user then flies a virtual camera through it and renders camera moves to MP4. It
runs locally on Windows using free, open-source tools. Human docs are in `README.md`; this
file tells you exactly what to do.

The usual request is: *"I have new photos/videos of a scene, make me a 3D render and let me
do camera shots."* Follow the steps below in order.

---

## 0. Ground rules

- **Windows + PowerShell.** Run the `.ps1` scripts from the repo root. If scripts are
  blocked, use `powershell -ExecutionPolicy Bypass -File .\process.ps1 ...`.
- **Never modify or delete the user's original media.** Copy it into `input\<scene>\`.
  Everything else (`frames\`, `reconstruction\`, `output\`) is regenerated.
- **Run only ONE GPU job at a time** (training, Depth Anything 3, or rendering). The
  reference PC has a 4 GB GPU; two jobs at once overflow VRAM and both slow to a crawl.
- Long jobs are normal: draft training takes about 15 min, normal about 1 h. Run them in
  the background, poll the log, and **tell the user what you're waiting for** so they don't
  think you've stopped.
- `tools\` and all media/outputs are git-ignored. Don't commit them.

## 1. One-time setup (skip if `tools\` already exists)

Check: `Test-Path tools\brush\brush_app.exe, tools\supersplat\index.html, tools\sharp-venv\Scripts\python.exe, tools\da3-env\python.exe`

If anything is missing, run:

```powershell
.\setup.ps1          # downloads FFmpeg, COLMAP, Brush, Node(portable)->builds SuperSplat,
                     # Python venv (Pillow, PyTorch cu126, Apple SHARP), DA3 env (conda py3.11)
```

Prerequisites on the machine: **Git**, **Miniconda/Anaconda** (`python` and `conda` on
PATH; Python 3.13 is fine for the main venv), **Microsoft Edge or Chrome**, a
DirectX12/Vulkan GPU (NVIDIA is best). Setup downloads about 10 GB and takes 30–60 min.
It's safe to re-run, because it skips anything already present.

Model weights download on first use: DA3-BASE (Hugging Face, small) and SHARP (2.6 GB, into
`tools\models\`).

## 2. New scene: the standard workflow

```powershell
# a) put the media in a scene folder (copy, don't move, the user's files)
New-Item -ItemType Directory input\myscene
Copy-Item "C:\path\to\media\*" input\myscene\        # .jpg/.png/.heic and/or .mp4/.mov

# b) quick draft first (~15 min on a 4 GB laptop GPU)
.\process.ps1 myscene -Preset draft

# c) check quality: render every original view next to its photo
tools\sharp-venv\Scripts\python.exe scripts\eval_views.py myscene
#    -> output\myscene\check.png (look at it!) + per-view PSNR in the console

# d) if the poses look right, train properly (reuses poses, ~1 h)
.\process.ps1 myscene -From train -Preset normal

# e) hand over to the user
.\view.ps1 myscene                                       # opens the SuperSplat editor in the browser
.\render-shot.ps1 myscene -ListViews                     # original camera positions
.\render-shot.ps1 myscene -Shot orbit-left -View 5       # scripted move -> renders\myscene_*.mp4
```

What `process.ps1` does (code in `scripts\pipeline.py`):
1. **Frames:** FFmpeg extracts video frames (it keeps the sharpest of every 3, and frame
   count is budgeted to about 12 images per GB of VRAM). Photos are EXIF-rotated and
   resized to at most 1600 px. Output goes to `frames\<scene>\images\`.
2. **Camera poses:** **Depth Anything 3** (`scripts\da3_poses.py`, run in `tools\da3-env`)
   is used for up to 150 images. Above that it uses **COLMAP**, falling back to DA3 if
   COLMAP fails. The result is a COLMAP-format dataset in `reconstruction\<scene>\da3\`
   (or `undistorted\`), whose path is recorded in `reconstruction\<scene>\dataset.txt`.
3. **Training:** **Brush** (`tools\brush\brush_app.exe`, WebGPU) writes
   `output\<scene>\<scene>.ply`, plus `<scene>_images.txt` (camera poses, which SuperSplat
   imports as keyframes) and `report.txt`.

Useful `process.ps1` flags: `-Preset draft|normal|high`, `-Poses auto|da3|colmap`,
`-Fps 3`, `-PosesOnly` (stop before training), `-From frames|poses|train`, `-Steps N`,
`-Force`, `-Options "<raw pipeline.py args>"` (e.g. `"--max-splats 300000 --da3-res 392"`).
Full list: `tools\sharp-venv\Scripts\python.exe scripts\pipeline.py --help`.

Logs: `reconstruction\<scene>\da3.log`, `colmap.log`, `brush.log`. Brush prints no
progress, so check that `brush_app.exe` is running and the GPU is at 100%
(`nvidia-smi`).

## 3. Camera paths and MP4

- **Interactive (what the user normally wants):** `.\view.ps1 <scene>` serves
  `tools\supersplat` (locally built SuperSplat 3.5.2) and `output\` on
  `http://localhost:8642`. In the editor, the Timeline panel works like this: move the
  camera, press **Enter** to add a keyframe, **Space** to preview, then
  **Render → Video** to export the MP4. The orange **Save path** button (injected by
  `scripts\supersplat_ext.js`) saves keyframes to `camera\<name>.json`.
- **Scripted:** `render-shot.ps1` (`scripts\render_path.py`) opens SuperSplat in a hidden
  Edge window via the DevTools protocol, loads keyframes and calls its `render.video`.
  - Shots: `still`, `dolly-left`/`dolly-right`, `push-in`/`pull-out`,
    `orbit-left`/`orbit-right`, `crane-up`/`crane-down`, `flythrough`. `-Amount` is the
    size of the move relative to subject distance (0.15–0.35 is safe).
  - `-Path camera\x.json` renders a saved or hand-written path. The JSON format is in
    `camera\example_path.json`, using SuperSplat world coordinates.
  - `-SavePath camera\x.json` stores the generated keys, so the user can tweak them in
    `.\view.ps1 <scene> -Path camera\x.json`.
- **Choosing good views:** build shots around views that look right in `check.png`. Moves
  near the original photo positions look best; flying into areas no photo saw gives
  smears.

## 4. Single photo (AI-inferred, not a reconstruction)

```powershell
.\single-photo.ps1 "input\myscene\photo.jpg"     # Apple SHARP -> output\photo_sharp\photo_sharp.ply
.\view.ps1 photo_sharp
```

Tell the user that geometry outside what the photo shows is hallucinated, so only small
camera moves look right.

## 5. Known gotchas (learned on the reference PC: Quadro P520, Pascal, 4 GB)

| Symptom | Cause / fix |
|---|---|
| COLMAP finds 0 matches, log says "no kernel image is available" | The official COLMAP CUDA build has no Pascal kernels. The pipeline auto-switches to CPU and writes `tools\colmap_cpu_only.txt`. Delete that file after a GPU upgrade. |
| COLMAP registers only a few images | Typical for casual phone photos and short blurry video. Use DA3 (the default for ≤150 images). |
| DA3 "BFloat16 ... bias type" error | Pascal GPUs: `da3_poses.py` forces fp32 automatically. Keep that patch. |
| DA3 very slow, VRAM at 100% | Too many images. Use fewer frames (`-Fps 2`), or `-Options "--da3-res 392"`. About 48 images max on 4 GB. |
| `import torch` takes ~2 min | Normal on this PC (antivirus scanning). Not a hang. |
| Background PowerShell `*>` logs look empty to bash `grep` | They're UTF-16. Read them with PowerShell `Get-Content`. |
| Moving people in a video | They become ghosts in the splat. Prefer photos, or cut those frames out. |
| Blurry sky/treetops, streaks away from photo positions | Inherent with few views. More overlapping photos help, not more training. |

Experiments that did **not** help (don't repeat them): COLMAP bundle adjustment on top of
DA3 poses, DA3 at 728 px with ray pose, DA3-LARGE, and adding blurry video frames to good
photos.

## 6. Repo map

```
process.ps1        one command: input\<scene> -> output\<scene>\<scene>.ply
view.ps1           open SuperSplat editor (keyframes, Render -> Video, Save path)
render-shot.ps1    scripted camera moves / saved paths -> renders\*.mp4
single-photo.ps1   one photo -> SHARP splat
setup.ps1          download/build all tools into tools\
scripts\pipeline.py      frames -> poses (DA3/COLMAP) -> Brush training
scripts\da3_poses.py     Depth Anything 3 -> COLMAP text model (+ crops, fp32 patch)
scripts\render_path.py   headless SuperSplat renderer + shot generator
scripts\serve.py         local web server for SuperSplat (+ /save-path endpoint)
scripts\supersplat_ext.js  "Save path" button + ?path= loader injected into SuperSplat
scripts\eval_views.py    render original views vs photos -> check.png
scripts\preview_ply.py   quick CPU preview of a .ply (no browser)
scripts\single_photo.py  SHARP wrapper
input\ frames\ reconstruction\ output\ renders\ camera\   data folders (git-ignored)
```
