"""ONE photo -> AI-inferred 3D Gaussian splat with Apple SHARP (ml-sharp).

This is NOT a reconstruction: geometry is predicted by a neural network from a single
view. It looks right for small camera moves (roughly: a step sideways / forward) and
degrades the further you move away from the original viewpoint. Hidden areas are
missing or smeared.

Usage: python scripts/single_photo.py <image> [<image> ...] [--device cpu|cuda]
Output: output/<name>_sharp/<name>_sharp.ply  (+ _images.txt with the photo's viewpoint)
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SHARP = ROOT / "tools" / "sharp-venv" / "Scripts" / "sharp.exe"
CKPT_DIR = ROOT / "tools" / "models"
CKPT_URL = "https://ml-site.cdn-apple.com/models/sharp/sharp_2572gikvuh.pt"


def pick_device(requested):
    if requested != "auto":
        return requested
    try:
        import torch
        if torch.cuda.is_available():
            free, _ = torch.cuda.mem_get_info()
            # SHARP needs roughly 3+ GB free; small GPUs fall back to the CPU.
            if free > 3.2e9:
                (torch.ones(8, device="cuda") * 2).sum().item()  # kernel sanity check
                return "cuda"
    except Exception:
        pass
    return "cpu"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    a = ap.parse_args()

    if not SHARP.exists():
        sys.exit("SHARP is not installed. Run .\\setup.ps1")
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    ckpt = CKPT_DIR / Path(CKPT_URL).name
    if not ckpt.exists():
        print(f"Downloading SHARP model (~2.6 GB, once) to {ckpt} ...", flush=True)
        import torch
        torch.hub.download_url_to_file(CKPT_URL, str(ckpt), progress=True)

    device = pick_device(a.device)
    print(f"Running SHARP on {device.upper()}" + (" (slow but works; ~1-3 min per photo)" if device == "cpu" else ""))

    for img in map(Path, a.images):
        if not img.exists():
            print(f"skip (not found): {img}")
            continue
        name = img.stem.replace(" ", "_") + "_sharp"
        out = ROOT / "output" / name
        out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            # normalise: apply EXIF rotation, convert HEIC/PNG etc. to JPEG
            src = Path(td) / f"{name}.jpg"
            im = ImageOps.exif_transpose(Image.open(img)).convert("RGB")
            exif = Image.open(img).getexif()  # keep focal length info if present
            im.save(src, quality=95, exif=exif.tobytes() if exif else b"")
            subprocess.run([str(SHARP), "predict", "-i", str(src), "-o", td, "-c", str(ckpt),
                            "--device", device], check=True)
            shutil.move(str(Path(td) / f"{name}.ply"), out / f"{name}.ply")
        # A single camera pose at the original photo viewpoint (identity, OpenCV convention),
        # so SuperSplat can jump back to it / use it as the first keyframe.
        (out / f"{name}_images.txt").write_text(
            "# IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME\n1 1 0 0 0 0 0 0 1 original_photo.jpg\n\n")
        print(f"-> {out / (name + '.ply')}\n   Open with:  .\\view.ps1 {name}")


if __name__ == "__main__":
    main()
