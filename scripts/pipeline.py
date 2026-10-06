"""Photos/video -> COLMAP poses -> Brush Gaussian splat (.ply) for SuperSplat.

Usage (normally via ..\\process.ps1):
    python scripts/pipeline.py <scene> [--preset normal] [--fps auto] ...

Layout (relative to the project root):
    input/<scene>/          your videos (.mp4 .mov ...) and/or photos (.jpg .png .heic)
    frames/<scene>/images/  prepared frames (video frames + normalised photos)
    reconstruction/<scene>/ COLMAP database, sparse model, undistorted dataset, logs
    output/<scene>/         <scene>.ply (the splat) + <scene>_images.txt (camera poses)
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

try:  # optional iPhone HEIC support
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
FFMPEG = next((TOOLS / "ffmpeg").glob("*/bin/ffmpeg.exe"), None)
FFPROBE = next((TOOLS / "ffmpeg").glob("*/bin/ffprobe.exe"), None)
COLMAP = TOOLS / "colmap" / "bin" / "colmap.exe"
BRUSH = TOOLS / "brush" / "brush_app.exe"
DA3_PY = TOOLS / "da3-env" / "python.exe"
DA3_MAX_AUTO = 150  # --poses auto: DA3 up to this many images (fits a 4 GB GPU), COLMAP above

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".3gp"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp"}

# Tuned for a 4 GB GPU. "max_splats" is the main VRAM knob for Brush.
PRESETS = {
    "draft":  dict(steps=3000,  max_splats=400_000,   train_res=1024),
    "normal": dict(steps=10000, max_splats=800_000,   train_res=1280),
    "high":   dict(steps=30000, max_splats=1_500_000, train_res=1600),
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, logfile=None, check=True):
    cmd = [str(c) for c in cmd]
    log("$ " + " ".join(Path(cmd[0]).name if i == 0 else c for i, c in enumerate(cmd)))
    if logfile:
        with open(logfile, "a", encoding="utf-8", errors="replace") as fh:
            fh.write("\n$ " + " ".join(cmd) + "\n")
            fh.flush()
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
    else:
        proc = subprocess.run(cmd)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {Path(cmd[0]).name} {cmd[1]}"
                           + (f"\n  see log: {logfile}" if logfile else ""))
    return proc.returncode


# --------------------------------------------------------------------------- frames

def safe_name(stem):
    """COLMAP text files are space-separated: keep names free of spaces/odd characters."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem)

def sharpness(path):
    im = Image.open(path).convert("L")
    im.thumbnail((640, 640))
    return ImageStat.Stat(im.filter(ImageFilter.FIND_EDGES)).var[0]


def probe_duration(video):
    out = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", str(video)], capture_output=True, text=True).stdout
    try:
        return float(json.loads(out)["format"]["duration"])
    except Exception:
        return 0.0


def gpu_vram_gb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout
        return float(out.split()[0]) / 1024
    except Exception:
        return 4.0


def auto_fps(videos, n_photos):
    """Depth Anything 3 sees all images at once, so the frame budget follows GPU memory
    (~12 images per GB; 4 GB -> ~48 images in total). Sharpest frames are kept."""
    total = sum(probe_duration(v) for v in videos) or 1.0
    budget = max(15, int(12 * gpu_vram_gb()) - n_photos)
    return min(8.0, max(0.5, budget / total))


def extract_video(video, dest, fps, max_res, tmp):
    """Extract ~fps frames/sec, picking the sharpest frame out of every 3 candidates."""
    dur = probe_duration(video)
    fps = float(fps)
    cand = 3
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    scale = f"scale='if(gt(iw,ih),min({max_res},iw),-2)':'if(gt(iw,ih),-2,min({max_res},ih))'"
    run([FFMPEG, "-v", "error", "-y", "-i", video, "-vf", f"fps={fps * cand},{scale}",
         "-q:v", "2", tmp / "c_%05d.jpg"])
    cands = sorted(tmp.glob("c_*.jpg"))
    kept = 0
    for i in range(0, len(cands), cand):
        group = cands[i:i + cand]
        best = max(group, key=sharpness)
        kept += 1
        shutil.move(str(best), dest / f"{safe_name(video.stem)}_{kept:05d}.jpg")
    shutil.rmtree(tmp)
    log(f"  {video.name}: {dur:.1f}s -> {kept} sharpest frames at {fps:.2f} fps")
    return kept


def prepare_photo(src, dest, max_res):
    im = ImageOps.exif_transpose(Image.open(src))  # honour phone rotation
    if im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((max_res, max_res), Image.LANCZOS)
    out = dest / (safe_name(src.stem) + ".jpg")
    im.save(out, quality=95)
    return out


def step_frames(scene, args):
    src = ROOT / "input" / scene
    files = sorted(p for p in src.rglob("*") if p.is_file())
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXT]
    photos = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    if not videos and not photos:
        raise SystemExit(f"No videos or photos found in {src}")

    fdir = ROOT / "frames" / scene / "images"
    if fdir.exists():
        shutil.rmtree(fdir)  # derived data only - originals in input/ are never touched
    log(f"Preparing frames: {len(videos)} video(s), {len(photos)} photo(s)")

    fps = args.fps
    if videos and fps == "auto":
        fps = auto_fps(videos, len(photos)) if args.poses in ("auto", "da3") else 4.0
    for v in videos:  # one sub-folder per video => one shared camera per video
        d = fdir / ("vid_" + safe_name(v.stem))
        d.mkdir(parents=True)
        extract_video(v, d, fps, args.max_res, ROOT / "frames" / scene / "_tmp")
    if photos:  # photos may come from different lenses => one camera per photo
        d = fdir / "photos"
        d.mkdir(parents=True)
        for p in photos:
            prepare_photo(p, d, args.max_res)
        log(f"  photos: {len(photos)} normalised (EXIF-rotated, max {args.max_res}px)")
    return fdir


# --------------------------------------------------------------------------- COLMAP

# The official COLMAP CUDA build has no kernels for older GPUs (e.g. Pascal / Quadro P520):
# its GPU SIFT then silently yields zero features. We detect that once and remember it.
NO_GPU_MARKER = TOOLS / "colmap_cpu_only.txt"


def extract_features(rdir, fdir, db, logf, args, video_imgs, photo_imgs, gpu):
    common = [COLMAP, "feature_extractor", "--database_path", db, "--image_path", fdir,
              "--FeatureExtraction.use_gpu", gpu,
              "--ImageReader.camera_model", "OPENCV" if args.distortion else "SIMPLE_RADIAL"]
    if args.features_type == "aliked":
        common += ["--FeatureExtraction.type", "ALIKED_N16ROT",
                   "--AlikedExtraction.max_num_features", str(args.features)]
    else:
        # CPU matching cost grows ~quadratically with features: keep it modest on CPU
        common += ["--SiftExtraction.max_num_features", "8192" if gpu == "1" else str(args.features)]
    for imgs, opts, name in ((video_imgs, ["--ImageReader.single_camera_per_folder", "1"], "videos"),
                             (photo_imgs, [], "photos")):
        if imgs:
            lst = rdir / f"list_{name}.txt"
            lst.write_text("\n".join(imgs) + "\n", encoding="utf-8")
            run(common + ["--image_list_path", lst] + opts, logf)


def step_colmap(scene, args, fdir):
    rdir = ROOT / "reconstruction" / scene
    if rdir.exists():
        shutil.rmtree(rdir)
    rdir.mkdir(parents=True)
    logf = rdir / "colmap.log"
    db = rdir / "database.db"
    gpu = "0" if args.cpu_colmap or NO_GPU_MARKER.exists() else "1"

    rel = lambda p: p.relative_to(fdir).as_posix()
    video_imgs = [rel(p) for p in sorted(fdir.glob("vid_*/*.jpg"))]
    photo_imgs = [rel(p) for p in sorted(fdir.glob("photos/*.jpg"))]
    n = len(video_imgs) + len(photo_imgs)

    log(f"COLMAP feature extraction ({n} images, {'GPU' if gpu == '1' else 'CPU'}) ...")
    extract_features(rdir, fdir, db, logf, args, video_imgs, photo_imgs, gpu)
    if gpu == "1" and "no kernel image is available" in logf.read_text(errors="replace"):
        log("  COLMAP's CUDA build does not support this GPU -> switching to CPU SIFT (remembered).")
        NO_GPU_MARKER.write_text("COLMAP GPU SIFT unsupported on this GPU; delete to retry.\n")
        gpu = "0"
        db.unlink()
        extract_features(rdir, fdir, db, logf, args, video_imgs, photo_imgs, gpu)

    matcher = args.matcher
    if matcher == "auto":
        # video frames are ordered -> match neighbours only (much faster on CPU);
        # unordered photos need every pair compared
        matcher = "exhaustive" if photo_imgs and n <= 400 else "sequential"
    log(f"COLMAP matching ({matcher}, {args.features_type}) ...")
    mtype = (["--FeatureMatching.type", "ALIKED_LIGHTGLUE"] if args.features_type == "aliked"
             else ["--FeatureMatching.guided_matching", gpu])
    if matcher == "exhaustive":
        run([COLMAP, "exhaustive_matcher", "--database_path", db,
             "--FeatureMatching.use_gpu", gpu] + mtype, logf)
    else:
        run([COLMAP, "sequential_matcher", "--database_path", db,
             "--FeatureMatching.use_gpu", gpu, "--SequentialMatching.overlap", "20"] + mtype, logf)

    mapper = args.mapper
    if mapper == "auto":
        mapper = "incremental" if n < 250 else "global"
    sparse = rdir / "sparse"
    sparse.mkdir()
    log(f"COLMAP mapping ({mapper}) ...")
    if mapper == "global":
        run([COLMAP, "global_mapper", "--database_path", db, "--image_path", fdir,
             "--output_path", sparse, "--GlobalMapper.gp_use_gpu", gpu,
             "--GlobalMapper.ba_ceres_use_gpu", gpu], logf)
    else:
        run([COLMAP, "mapper", "--database_path", db, "--image_path", fdir,
             "--output_path", sparse, "--Mapper.ba_refine_principal_point", "0"], logf)

    models = [m for m in sparse.iterdir() if (m / "images.bin").exists()]
    if not models:
        raise RuntimeError("COLMAP could not reconstruct any cameras. See " + str(logf) +
                           "\nTry: more overlap between views, --mapper global, or --distortion.")
    # pick the model with the most registered images
    stats = {m: model_stats(m, rdir) for m in models}
    best = max(stats, key=stats.get)
    reg, pts = stats[best]
    log(f"Registered {reg}/{n} images, {pts} 3D points "
        f"(model {best.name}; {len(models)} model(s) found)")
    if reg < max(3, min(8, n * 0.3)) or pts < 300:
        raise RuntimeError(
            f"COLMAP only registered {reg}/{n} images ({pts} points) - not enough to train a scene.\n"
            "  The views probably don't overlap enough. Try, in order:\n"
            "   1. --features-type aliked   (learned features; better for wide-baseline photos, slower on CPU)\n"
            "   2. add more views of the same area (e.g. a video + the photos in one scene folder)\n"
            "   3. split the photos into smaller scene folders that each cover one area\n"
            "   4. for a single good photo, use single-photo.ps1 instead")
    if reg < n * 0.5:
        log("WARNING: fewer than half the images were registered - the scene may be incomplete.")

    # Gravity-align so 'up' is consistent (SuperSplat then shows the scene upright).
    aligned = rdir / "aligned"
    aligned.mkdir()
    rc = run([COLMAP, "model_orientation_aligner", "--image_path", fdir, "--input_path", best,
              "--output_path", aligned, "--method", "IMAGE-ORIENTATION"], logf, check=False)
    model = aligned if rc == 0 and (aligned / "images.bin").exists() else best

    log("Undistorting images for training ...")
    und = rdir / "undistorted"
    run([COLMAP, "image_undistorter", "--image_path", fdir, "--input_path", model,
         "--output_path", und, "--output_type", "COLMAP", "--max_image_size", str(args.max_res)], logf)
    # COLMAP writes undistorted/sparse/*.bin; Brush wants sparse/0/
    s0 = und / "sparse" / "0"
    s0.mkdir(exist_ok=True)
    for f in (und / "sparse").glob("*.bin"):
        shutil.move(str(f), s0 / f.name)
    return und, reg, n


def model_stats(model, rdir):
    """(registered images, 3D points) of a sparse model."""
    txt = rdir / "_count"
    txt.mkdir(exist_ok=True)
    subprocess.run([str(COLMAP), "model_converter", "--input_path", str(model),
                    "--output_path", str(txt), "--output_type", "TXT"], capture_output=True)

    def rows(name):
        f = txt / name
        return [l for l in f.read_text(errors="replace").splitlines()
                if l and not l.startswith("#")] if f.exists() else []

    f = txt / "images.txt"
    head = f.read_text(errors="replace")[:2000] if f.exists() else ""
    m = re.search(r"Number of images: (\d+)", head)
    imgs = int(m.group(1)) if m else 0
    pts = len(rows("points3D.txt"))
    shutil.rmtree(txt)
    return imgs, pts


# --------------------------------------------------------------------------- Depth Anything 3

def step_da3(scene, args, fdir):
    """Feed-forward poses + depth (no matching): robust for few/wide photos and shaky video."""
    if not DA3_PY.exists():
        raise RuntimeError("Depth Anything 3 is not installed (tools/da3-env). Run setup.ps1.")
    rdir = ROOT / "reconstruction" / scene
    rdir.mkdir(parents=True, exist_ok=True)
    logf = rdir / "da3.log"
    d = rdir / "da3"
    if d.exists():
        shutil.rmtree(d)
    n = sum(1 for _ in fdir.rglob("*.jpg"))
    log(f"Depth Anything 3: estimating camera poses + depth for {n} images "
        f"(model {args.da3_model}, {args.da3_res}px) ...")
    run([DA3_PY, "-u", ROOT / "scripts" / "da3_poses.py", fdir, d,
         "--model", args.da3_model, "--res", args.da3_res]
        + (["--ray-pose"] if args.da3_ray_pose else []), logf)
    s0 = d / "sparse" / "0"
    # TXT -> BIN, then gravity-align like the COLMAP path
    run([COLMAP, "model_converter", "--input_path", s0, "--output_path", s0,
         "--output_type", "BIN"], logf)
    for f in s0.glob("*.txt"):
        f.unlink()
    aligned = d / "aligned"
    aligned.mkdir()
    rc = run([COLMAP, "model_orientation_aligner", "--image_path", d / "images", "--input_path", s0,
              "--output_path", aligned, "--method", "IMAGE-ORIENTATION"], logf, check=False)
    if rc == 0 and (aligned / "images.bin").exists():
        for f in aligned.glob("*.bin"):
            shutil.move(str(f), s0 / f.name)
    shutil.rmtree(aligned)
    return d, n, n


# --------------------------------------------------------------------------- Brush

def step_train(scene, args, und):
    out = ROOT / "output" / scene
    out.mkdir(parents=True, exist_ok=True)
    p = dict(PRESETS[args.preset])
    if args.steps:
        p["steps"] = args.steps
    if args.max_splats:
        p["max_splats"] = args.max_splats
    log(f"Training Gaussian splat with Brush: {p['steps']} steps, <= {p['max_splats']:,} splats, "
        f"{p['train_res']}px (preset '{args.preset}')")
    cmd = [BRUSH, und, "--total-steps", p["steps"], "--max-splats", p["max_splats"],
           "--max-resolution", p["train_res"], "--export-path", out,
           "--export-name", f"{scene}.ply", "--export-every", p["steps"],
           "--eval-every", 10 ** 9]
    if args.viewer:
        cmd.append("--with-viewer")
    run(cmd, ROOT / "reconstruction" / scene / "brush.log")
    ply = out / f"{scene}.ply"
    if not ply.exists():
        raise RuntimeError("Brush finished but no .ply was written; see reconstruction/"
                           f"{scene}/brush.log")
    return ply


def export_poses(scene, und):
    """Write COLMAP images.txt so SuperSplat can import the original camera path."""
    out = ROOT / "output" / scene
    tmp = ROOT / "reconstruction" / scene / "txt"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    run([COLMAP, "model_converter", "--input_path", und / "sparse" / "0",
         "--output_path", tmp, "--output_type", "TXT"],
        ROOT / "reconstruction" / scene / "colmap.log")
    shutil.copy(tmp / "images.txt", out / f"{scene}_images.txt")


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("scene", help="folder name under input/")
    ap.add_argument("--preset", choices=PRESETS, default="normal")
    ap.add_argument("--steps", type=int, help="override training steps")
    ap.add_argument("--max-splats", type=int, help="override max number of splats (VRAM)")
    ap.add_argument("--fps", default="auto", help="frames/sec to keep from videos (default auto)")
    ap.add_argument("--max-res", type=int, default=1600, help="max image side for COLMAP (px)")
    ap.add_argument("--features-type", choices=["sift", "aliked"], default="sift",
                    help="sift (fast, default) or aliked (ALIKED+LightGlue: better for few/wide photos)")
    ap.add_argument("--features", type=int, default=4096,
                    help="features per image (SIFT on CPU / ALIKED; default 4096)")
    ap.add_argument("--matcher",choices=["auto", "exhaustive", "sequential"], default="auto")
    ap.add_argument("--mapper", choices=["auto", "incremental", "global"], default="auto")
    ap.add_argument("--distortion", action="store_true", help="use OPENCV model (wide/action cams)")
    ap.add_argument("--cpu-colmap", action="store_true", help="disable COLMAP GPU SIFT")
    ap.add_argument("--viewer", action="store_true", help="show Brush's live viewer while training")
    ap.add_argument("--poses", choices=["auto", "colmap", "da3"], default="auto",
                    help="camera poses from COLMAP (classic SfM) or Depth Anything 3 (AI, robust).\n"
                         f"auto = DA3 for <= {DA3_MAX_AUTO} images, else COLMAP with DA3 fallback")
    ap.add_argument("--da3-model", default="depth-anything/DA3-BASE",
                    help="DA3 checkpoint (DA3-SMALL / DA3-BASE are Apache-2.0)")
    ap.add_argument("--da3-res", type=int, default=504, help="DA3 processing resolution")
    ap.add_argument("--da3-ray-pose", action="store_true", help="DA3 ray-based pose estimation")
    ap.add_argument("--from", dest="start", choices=["frames", "poses", "train"], default="frames",
                    help="resume from a stage (reuses earlier results)")
    ap.add_argument("--stop-after", choices=["poses"], help="stop before training")
    args = ap.parse_args()

    for tool in (FFMPEG, COLMAP, BRUSH):
        if not tool or not Path(tool).exists():
            raise SystemExit(f"Missing tool {tool}. Run setup.ps1 first.")

    scene = args.scene
    t0 = time.time()
    fdir = ROOT / "frames" / scene / "images"
    rdir = ROOT / "reconstruction" / scene
    reg = total = None
    if args.start == "frames":
        fdir = step_frames(scene, args)
    if args.start in ("frames", "poses"):
        n_imgs = sum(1 for _ in fdir.rglob("*.jpg"))
        if args.poses == "auto" and n_imgs <= DA3_MAX_AUTO and DA3_PY.exists():
            log(f"{n_imgs} images: using Depth Anything 3 for camera poses "
                "(robust for casual captures; force classic SfM with --poses colmap)")
            args.poses = "da3"
        if args.poses == "da3":
            und, reg, total = step_da3(scene, args, fdir)
        else:
            try:
                und, reg, total = step_colmap(scene, args, fdir)
            except RuntimeError as e:
                if args.poses == "colmap":
                    raise
                log(f"COLMAP failed: {str(e).splitlines()[0]}")
                log("Falling back to Depth Anything 3 poses ...")
                und, reg, total = step_da3(scene, args, fdir)
        (rdir / "dataset.txt").write_text(str(und))
        if args.stop_after == "poses":
            log(f"Stopped after poses ({und}). Continue with:  .\\process.ps1 {scene} -From train")
            return
    else:
        und = Path((rdir / "dataset.txt").read_text().strip())
    ply = step_train(scene, args, und)
    export_poses(scene, und)

    mins = (time.time() - t0) / 60
    report = ROOT / "output" / scene / "report.txt"
    report.write_text(
        f"scene: {scene}\npreset: {args.preset}\n"
        + (f"registered images: {reg}/{total}\n" if reg is not None else "")
        + f"splat: {ply.name}\nposes: {scene}_images.txt\nminutes: {mins:.1f}\n", encoding="utf-8")
    log(f"DONE in {mins:.1f} min -> {ply}")
    log(f"Open it with:  .\\view.ps1 {scene}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        log(f"ERROR: {e}")
        sys.exit(1)
