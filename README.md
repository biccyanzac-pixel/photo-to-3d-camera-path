# Photos / video → 3D Gaussian Splat → virtual camera → MP4

A free, local, open-source workflow for Windows that turns photos or videos you already have
into a navigable 3D scene. You can fly a virtual camera through it, set keyframes and
render the move as an MP4.

```
input\<scene>\  (photos and/or videos)
   │  FFmpeg            sharpest video frames + EXIF-rotated photos   → frames\<scene>\
   │  Depth Anything 3  AI camera poses + depth (default, ≤150 images) ┐
   │  COLMAP 4.2        classic Structure-from-Motion (large sets)    ├→ reconstruction\<scene>\
   │  Brush 0.3         Gaussian-splat training on the GPU (WebGPU)   → output\<scene>\<scene>.ply
   ▼
SuperSplat 3.5 (local, in your browser): fly around, set keyframes, preview, Render → MP4
   or  render-shot.ps1: ready-made camera moves / saved paths → MP4 (no clicking)  → renders\
```

Single photo: **Apple SHARP** predicts a 3D splat from one image (AI-inferred, see *Limitations*).

---

## First-time setup (new PC or fresh clone)

The repo holds only the scripts. Tools, media and results are not in git.

1. Install **Git**, **Miniconda** (so `python` and `conda` work in PowerShell), and have
   **Edge** or **Chrome**. An NVIDIA GPU is recommended; any DirectX12/Vulkan GPU works for
   training.
2. Clone and set up (about 10 GB download, 30–60 min, nothing installed system-wide):

```powershell
git clone https://github.com/<you>/<repo>.git
cd <repo>
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

> Using an AI coding agent (Claude Code etc.)? Point it at **`AGENTS.md`**. It contains the
> exact step-by-step procedure for processing a new scene.

## Quick start

Open PowerShell in this folder. If scripts are blocked, run this once per window:
`Set-ExecutionPolicy -Scope Process Bypass`.

```powershell
# 1. Put media in a scene folder (any mix of .mp4/.mov and .jpg/.png/.heic)
#    input\kitchen\clip.mp4, input\kitchen\IMG_001.jpg, ...

# 2. Build the 3D scene (one command)
.\process.ps1 kitchen

# 3. Open it with the camera tools
.\view.ps1 kitchen
```

`.\process.ps1` with no arguments processes **every scene folder in `input\` that has no
result yet**. Files dropped loose into `input\` are moved into a new dated folder
`input\scene_YYYYMMDD_HHMM\` first. Nothing is ever deleted from `input\`.

### Example: the grotto scene (processed on the original PC; not in git)

| Scene | Source | Notes |
|---|---|---|
| `grotto_all` | 20 photos + video (24 sharpest frames) | more coverage, but blurrier, and the person in the video appears as a ghost |
| `grotto_photos` | the 20 photos only | **best looking; use this one** (66 min training) |
| `WhatsApp_Image_2026-10-05_at_21.00.58_sharp` | one photo (SHARP) | single-photo demo |

Example shots already rendered to `renders\` (from `grotto_photos`). The best ones are the
orbit around the "Subway to Terrace Gardens" sign (`orbit-left_v5`) and the sideways dolly
along the barrier (`dolly-right_v8`).

```powershell
.\view.ps1 grotto_photos                                       # fly around, keyframe, render
.\render-shot.ps1 grotto_photos -ListViews                     # positions to build shots around
.\render-shot.ps1 grotto_photos -Shot orbit-left -View 5       # ready-made move -> renders\
```

The `-View` numbers differ between scenes, so check `-ListViews` for each one.

---

## Folder layout

| Folder | What's in it |
|---|---|
| `input\<scene>\` | **your** photos/videos (only read, never changed) |
| `frames\<scene>\` | extracted video frames + normalised photos (regenerated each run) |
| `reconstruction\<scene>\` | camera poses, logs (`colmap.log`, `da3.log`, `brush.log`) |
| `output\<scene>\` | `<scene>.ply` (the 3D scene), `<scene>_images.txt` (original camera positions), `report.txt` |
| `camera\` | save your SuperSplat projects (`.ssproj` = scene + camera keyframes) here |
| `renders\` | save your rendered MP4s here |
| `scripts\` | the pipeline code |
| `tools\` | all downloaded software (portable, nothing installed system-wide) |
| `media\` | your original test media (untouched) |

---

## Processing options

```powershell
.\process.ps1 kitchen                      # preset "normal" (about 1 h on this laptop)
.\process.ps1 kitchen -Preset draft        # quick test (about 15 min)
.\process.ps1 kitchen -Preset high         # best quality, slow (30000 steps, several hours here)
.\process.ps1 kitchen -Force               # redo a scene that's already done
.\process.ps1 kitchen -Poses colmap        # classic COLMAP SfM instead of Depth Anything 3
.\process.ps1 kitchen -Fps 3               # frames per second to take from videos
.\process.ps1 kitchen -PosesOnly           # only find camera poses (quick check)
.\process.ps1 kitchen -From train -Steps 15000        # re-train only, reuse poses
.\process.ps1 kitchen -Options "--max-splats 300000"  # any other pipeline.py option
```

A good routine is to run `-Preset draft` first, check the result in the viewer, then
re-train the same scene with `-From train -Preset normal` (this reuses the poses).

All options: `tools\sharp-venv\Scripts\python.exe scripts\pipeline.py --help`.

**How camera poses are found (`--poses auto`, the default).** A set of up to 150 images
goes to **Depth Anything 3**, which is fast and robust for casual captures. A larger set
uses **COLMAP**, falling back to DA3 if COLMAP can't link enough images. COLMAP is more
precise when you have lots of overlapping, sharp frames, e.g. a slow, long walk-around
video.

On your test media, COLMAP linked only 3 of the 20 photos and broke the blurry 8-second
video into 4 fragments. Depth Anything 3 linked all 20 photos in 34 seconds.

**Videos.** Frames are sampled automatically to fit the GPU: Depth Anything 3 processes all
images at once, so the budget is about 12 images per GB of VRAM (about 48 on this 4 GB
GPU), minus the number of photos. From each group of 3 candidate frames, the sharpest one
is kept. Phone rotation is applied automatically.

**Mixing photos and video in one folder works.** If their shapes differ, all images are
center-cropped to the shape of whichever source carries more image data.

---

## Creating a camera path and rendering an MP4

`.\view.ps1 <scene>` starts a small local web server and opens the **SuperSplat** editor in
your browser with the scene loaded. Everything runs on your PC; nothing is uploaded. Keep
the PowerShell window open while you work, and press Ctrl+C to stop.

The original camera positions are imported too. They show up as **camera poses / keyframes
on the timeline**, which makes a handy starting point.

### Navigate
- **Left-drag** orbit · **Right-drag** pan · **Wheel** zoom/dolly
- **Fly mode**: hold right mouse button + **W A S D** (Q/E down/up). Shift = faster.
- **F** focuses on the selection. Double-click sets the orbit pivot on a point.
- If the scene looks tilted, select the splat and use the **Rotate** tool (or the
  transform panel) to level it. This only changes the viewer.

### Keyframes (Timeline panel at the bottom, Ctrl+T toggles it)
1. Move the playhead to a frame (click the timeline).
2. Position the camera.
3. Press **Enter** (or click **+**) to add a keyframe (yellow diamond).
4. Repeat for more positions. **Shift+Enter** removes the keyframe at the playhead.
   Drag diamonds to retime them, Shift-drag to copy, Ctrl-click to overwrite with the
   current view.
5. Set **FPS** and **timeline length** (total frames). Use **Smoothness** (0–1) for gentler
   curves.
6. **Space** plays the path (preview). `,` / `.` step frames, `<` / `>` jump between keys.

To start from the imported original cameras, delete the ones you don't want and retime
the rest.

**Example: sideways dolly through the grotto.** Key 1 at frame 0 in front of the arch,
then key 2 at frame 90 about a metre to the right, same height and same look direction.
Smoothness 1. At 30 fps that's a 3-second move.

### Render the MP4
1. **Render → Video**
2. Choose resolution (e.g. 1920×1080, or Portrait), **MP4 / H.264**, 30 fps, bitrate High,
   and the frame range.
3. **Render**. The playhead runs through the path; then choose where to save. Pick
   this project's `renders\` folder. (If the browser auto-downloads instead, move the file
   from Downloads.)

### Save your camera path
- The orange **Save path** button (bottom-left, added by this project) writes the timeline
  keyframes to `camera\<name>.json`. Re-open it later with
  `.\view.ps1 grotto_photos -Path camera\<name>.json`, or render it without the editor with
  `.\render-shot.ps1 grotto_photos -Path camera\<name>.json`.
- **File → Save** stores the scene plus keyframes as a SuperSplat `.ssproj` project. Save it
  into `camera\`, then drag it into the editor later to continue.

---

## Ready-made shots without clicking: `render-shot.ps1`

This renders a camera move straight to `renders\` using SuperSplat's renderer in a hidden
browser window. The move is built around one of the original photo positions, which is
where the reconstruction looks best.

```powershell
.\render-shot.ps1 grotto_photos -ListViews                      # numbered list of photo positions
.\render-shot.ps1 grotto_photos -Shot dolly-right -View 8       # sideways track at photo 8
.\render-shot.ps1 grotto_photos -Shot push-in -View 3 -Seconds 6
.\render-shot.ps1 grotto_photos -Shot orbit-left -View 18 -Amount 0.2 -Width 1920 -Height 1080
.\render-shot.ps1 grotto_photos -Shot still -View 5             # exactly the original photo view
.\render-shot.ps1 grotto_photos -Path camera\my_shot.json       # a path you saved in the editor
```

| Shot | Move |
|---|---|
| `dolly-left` / `dolly-right` | camera slides sideways, looking straight ahead (parallax shot) |
| `push-in` / `pull-out` | camera moves forward / backward towards the subject |
| `orbit-left` / `orbit-right` | camera arcs around the subject |
| `crane-up` / `crane-down` | camera rises / lowers while looking at the subject |
| `still` | holds the original photo position |
| `flythrough` | glides through the original camera positions in order (crosses gaps between photos, so expect holes) |

- `-Amount` sets the size of the move as a fraction of the distance to the subject (default
  0.3). With few photos, keep it at 0.15–0.35.
- Add `-SavePath camera\x.json` to keep the generated keyframes, then open them in the editor
  (`.\view.ps1 scene -Path camera\x.json`) to fine-tune and re-render.
- For portrait video use `-Width 1080 -Height 1920`.

## Checking a reconstruction

```powershell
tools\sharp-venv\Scripts\python.exe scripts\eval_views.py grotto_photos
```

This renders the scene from every original photo position and writes
`output\<scene>\check.png` (render next to photo, with a similarity score). Views that look
right are good places to build shots around. Views that look wrong mean the scene is weak
there.

---

## Single photo → 3D

```powershell
.\single-photo.ps1 "C:\path\to\photo.jpg"
.\view.ps1 photo_sharp
```

Uses **Apple SHARP** (open-source, Dec 2025). The first run downloads its 2.6 GB model into
`tools\models\`. It takes about 5 min per photo on this laptop's GPU and falls back to the
CPU if the GPU is too small. The timeline gets one keyframe at the original photo position.

**Be honest with yourself about what this is:** the 3D shape is *predicted by a neural
network*, not measured. What's visible in the photo is reproduced well. Anything hidden
(behind objects, around corners, the back of things) is missing or invented. Keep camera
moves small, roughly a step sideways/forward and slight rotations. Bigger moves show
holes and stretched surfaces.

---

## GPU requirements and what this PC does

Tested on: **Intel i7-10510U, 16 GB RAM, NVIDIA Quadro P520 (Pascal, 4 GB)**, Windows 11.

| Stage | Hardware used here | Time for the 20-photo test |
|---|---|---|
| Frame prep (FFmpeg/Pillow) | CPU | ~15 s |
| COLMAP | **CPU**: the official CUDA build has no kernels for Pascal GPUs; the pipeline detects this and switches to CPU automatically (`tools\colmap_cpu_only.txt`) | 1 min extract + 12 min matching (and it failed on this data) |
| Depth Anything 3 (Base) | **GPU**, fp32 (bf16/fp16 are emulated/slow on Pascal) | ~35 s |
| Brush training | **GPU** via WebGPU/Vulkan (any NVIDIA/AMD/Intel GPU) | ~13 min draft (3000 steps), ~66 min normal (10000 steps) |
| SuperSplat viewer/render | **GPU** via WebGL in the browser | real-time |
| SHARP (single photo) | GPU (CUDA) or CPU | ~5 min |

- **Minimum:** any DirectX12/Vulkan GPU with about 2 GB for training small scenes, plus 8 GB RAM.
- **4 GB VRAM** (this PC): the presets cap splat counts at 400k / 800k / 1.5M. If Brush
  crashes or the PC stutters, use `-Preset draft` or `-Options "--max-splats 300000"`.
- **Faster GPU (RTX 20xx+):** COLMAP will use GPU SIFT automatically (delete
  `tools\colmap_cpu_only.txt` after a GPU upgrade) and everything runs several times faster.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `running scripts is disabled` | `Set-ExecutionPolicy -Scope Process Bypass` in that window |
| "COLMAP only registered N images" | Normal for few or far-apart photos; the pipeline then uses Depth Anything 3 automatically. Force it with `-Poses da3`. |
| Scene is blurry / smeary | Train longer (`-Preset normal` or `high`, or `-From train -Steps 20000`). Add more overlapping views. Avoid motion-blurred video (pan slowly). |
| Floaters / fog in the air | In SuperSplat use the selection tools (brush/sphere/box) then **Delete** to clean, and export a clean `.ply` via File → Export. |
| Scene upside down or tilted | Rotate it in SuperSplat (transform panel). The pipeline tries to level scenes, but it can't always. |
| Viewer page is blank | Use Edge or Chrome. Make sure the `view.ps1` window is still open. Try another port: `.\view.ps1 scene -Port 8700`. |
| Brush crashes / out of memory | `-Preset draft` or `-Options "--max-splats 300000"`. Close other GPU apps (browser tabs with video, games). |
| DA3 "out of memory" | It retries on CPU automatically. Or lower `-Options "--da3-res 392"`, or use fewer frames (`-Fps 2`). Running two GPU jobs at once (e.g. training + DA3) makes both crawl: run one at a time. |
| Very slow PC while processing | That's expected: CPU feature matching and GPU training use the whole machine. |
| Want to reinstall tools | `.\setup.ps1` (re-downloads only what's missing). Delete a folder in `tools\` to force it. |

Logs for every step are in `reconstruction\<scene>\` (`colmap.log`, `da3.log`, `brush.log`).

---

## Limitations (please read)

- **Only what was photographed can be reconstructed.** Areas no photo saw will be holes or
  blur. The further the virtual camera moves from where the real photos were taken, the
  worse it looks. Moves *between* and *near* the original viewpoints look best.
- **Few, far-apart photos** (like the 20 test photos) give a scene that looks right from
  near each photo but has gaps between them. The space between distant photos is only
  thinly covered. For a cinematic move, keep the path inside a well-covered region.
- **Depth Anything 3 poses are AI estimates.** They're robust but less exact than a
  successful COLMAP run, so fine detail may be softer.
- **Moving things** (people, leaves in wind) become ghosts or blur. Clean them up in
  SuperSplat.
- **Single-photo (SHARP) results are hallucinated beyond what the photo shows.** Use
  small moves only.
- Training on this 4 GB laptop GPU is slow; a "normal" scene takes about 1 hour, a draft about 15 min.

---

## Why these tools (Oct 2026)

- **Brush** trains Gaussian splats on *any* GPU through WebGPU. The usual CUDA trainers
  (gsplat, Nerfstudio, Inria 3DGS) need a CUDA compiler toolchain and recent-GPU support,
  which is a poor fit for a 4 GB Pascal laptop GPU.
- **COLMAP 4.2** is the standard SfM tool (GLOMAP is now built in as `global_mapper`) with
  official Windows builds.
- **Depth Anything 3** (ByteDance, Apache-2.0 Base model) is a feed-forward multi-view model
  that predicts camera poses and depth without feature matching. It rescues captures that
  COLMAP can't handle, and DA3-Base fits in 4 GB. VGGT/MASt3R need far more VRAM.
- **SuperSplat** (PlayCanvas, MIT) is the only free tool that combines splat
  viewing/cleanup, a keyframe timeline and MP4 export. It's built locally here so it runs
  offline.
- **Apple SHARP** is the current open single-image → 3DGS model; it's fast and outputs
  standard `.ply`.
- **FFmpeg** is used for frame extraction.

All tools live in `tools\`. `setup.ps1` re-creates them from official sources.
