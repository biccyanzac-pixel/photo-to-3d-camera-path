"""Render a camera move through a splat scene to MP4 - headless, using SuperSplat's renderer.

Two ways to describe the move:

1. A ready-made SHOT built around one of the original photo/frame positions:
       python scripts/render_path.py grotto_photos --shot dolly-right --view 7
   shots: dolly-left, dolly-right, push-in, pull-out, orbit-left, orbit-right,
          crane-up, crane-down, flythrough (visits the original cameras in order)

2. A camera path JSON (e.g. saved earlier with --save-path, or written by hand):
       python scripts/render_path.py grotto_photos --path camera/my_shot.json

The JSON uses SuperSplat world coordinates:
    {"fps": 30, "frames": 120, "smoothness": 1,
     "keys": [{"frame": 0,   "position": [x,y,z], "target": [x,y,z], "fov": 60},
              {"frame": 119, "position": [x,y,z], "target": [x,y,z], "fov": 60}]}

Open the same path interactively with:  .\\view.ps1 <scene> -Path camera\\my_shot.json
"""

import argparse
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preview_ply import load_ply  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOPROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SHOTS = ["still", "dolly-left","dolly-right", "push-in", "pull-out", "orbit-left", "orbit-right",
         "crane-up", "crane-down", "flythrough"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ----------------------------------------------------------------------------- cameras

def qvec2rot(qw, qx, qy, qz):
    return np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]])


def read_cameras(scene_dir, scene):
    """Original cameras from <scene>_images.txt (+ intrinsics from the training dataset)."""
    rows = [l.split() for l in (scene_dir / f"{scene}_images.txt").read_text().splitlines()
            if l and not l.startswith("#")]
    intr = {}
    ds = ROOT / "reconstruction" / scene / "txt" / "cameras.txt"
    if ds.exists():
        for l in ds.read_text().splitlines():
            if l and not l.startswith("#"):
                p = l.split()
                w, h, fx = float(p[2]), float(p[3]), float(p[4])
                fy = float(p[5]) if p[1] in ("PINHOLE", "OPENCV", "FULL_OPENCV") else fx
                intr[p[0]] = (2 * math.degrees(math.atan(w / (2 * fx))),
                              2 * math.degrees(math.atan(h / (2 * fy))))
    cams = []
    for r in rows:
        if len(r) != 10:
            continue
        R = qvec2rot(*map(float, r[1:5]))
        t = np.array(list(map(float, r[5:8])))
        fovx, fovy = intr.get(r[8], (60.0, 60.0))
        cams.append(dict(name=r[9], R=R, t=t, C=-R.T @ t, fovx=fovx, fovy=fovy,
                         fov=max(fovx, fovy)))
    cams.sort(key=lambda c: c["name"])
    return cams


def to_ss(v):
    """COLMAP/PLY world -> SuperSplat world (SuperSplat shows PLYs rotated 180 deg about Z)."""
    return [float(-v[0]), float(-v[1]), float(v[2])]


def focus_distance(xyz, cam):
    """Median depth of scene points visible from this camera."""
    pc = xyz @ cam["R"].T + cam["t"]
    z = pc[:, 2]
    m = z > 1e-3
    half = math.tan(math.radians(cam["fov"]) / 2)
    m &= (np.abs(pc[:, 0]) < z * half) & (np.abs(pc[:, 1]) < z * half)
    return float(np.median(z[m])) if m.sum() > 100 else float(np.median(np.abs(z)))


def out_fov(cam, landscape):
    """SuperSplat applies fov to the output's larger axis. Use the photo's fov on that same
    axis so the rendered frame stays inside what the photo actually saw."""
    return cam["fovx"] if landscape else cam["fovy"]


def build_shot(shot, cams, xyz, view, amount, frames, fov, landscape=True):
    if shot == "views":  # diagnostic: one frame exactly at each original camera
        keys = []
        for i, c in enumerate(cams):
            D = focus_distance(xyz, c)
            fwd = c["R"].T @ np.array([0, 0, 1.0])
            keys.append(dict(frame=i, position=to_ss(c["C"]), target=to_ss(c["C"] + fwd * D),
                             fov=fov or out_fov(c, landscape)))
        return keys
    if shot == "flythrough":
        step = max(1, len(cams) // 12)
        sel = cams[::step]
        keys = []
        for i, c in enumerate(sel):
            D = focus_distance(xyz, c)
            fwd = c["R"].T @ np.array([0, 0, 1.0])
            keys.append(dict(frame=round(i * (frames - 1) / max(1, len(sel) - 1)),
                             position=to_ss(c["C"]), target=to_ss(c["C"] + fwd * D),
                             fov=fov or out_fov(c, landscape)))
        return keys

    c = cams[view % len(cams)]
    D = focus_distance(xyz, c)
    R = c["R"]
    fwd, right, down = R.T @ [0, 0, 1.0], R.T @ [1.0, 0, 0], R.T @ [0, 1.0, 0]
    C, T = c["C"], c["C"] + fwd * D
    d = amount * D
    f = fov or out_fov(c, landscape)
    if shot == "still":
        p0 = p1 = C
        t0 = t1 = T
    elif shot in ("dolly-left", "dolly-right"):
        s = 1 if shot == "dolly-right" else -1
        p0, p1, t0, t1 = C - s * right * d / 2, C + s * right * d / 2, T - s * right * d / 2, T + s * right * d / 2
    elif shot in ("push-in", "pull-out"):
        a, b = C - fwd * d * 0.3, C + fwd * d
        p0, p1 = (a, b) if shot == "push-in" else (b, a)
        t0 = t1 = T
    elif shot in ("crane-up", "crane-down"):
        a, b = C + down * d / 2, C - down * d / 2
        p0, p1 = (a, b) if shot == "crane-up" else (b, a)
        t0 = t1 = T
    elif shot in ("orbit-left", "orbit-right"):
        ang = math.radians(60 * amount) * (1 if shot == "orbit-right" else -1)
        keys = []
        n = 5
        for i in range(n):
            th = -ang / 2 + ang * i / (n - 1)
            # rotate the camera around the target about the camera's own 'up' axis
            off = C - T
            up = -down / np.linalg.norm(down)
            off_rot = (off * math.cos(th) + np.cross(up, off) * math.sin(th)
                       + up * np.dot(up, off) * (1 - math.cos(th)))
            keys.append(dict(frame=round(i * (frames - 1) / (n - 1)), position=to_ss(T + off_rot),
                             target=to_ss(T), fov=f))
        return keys
    else:
        raise SystemExit(f"unknown shot {shot}")
    return [dict(frame=0, position=to_ss(p0), target=to_ss(t0), fov=f),
            dict(frame=frames - 1, position=to_ss(p1), target=to_ss(t1), fov=f)]


# ----------------------------------------------------------------------------- browser

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def find_browser():
    for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]:
        if Path(p).exists():
            return p
    raise SystemExit("Microsoft Edge or Google Chrome is required.")


class CDP:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=600, suppress_origin=True)
        self.i = 0

    def call(self, method, **params):
        self.i += 1
        my = self.i
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr, await_promise=False):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=await_promise,
                      returnByValue=True, timeout=3_600_000)
        if "exceptionDetails" in r:
            raise RuntimeError("JS error: " + json.dumps(r["exceptionDetails"])[:600])
        return r.get("result", {}).get("value")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("scene")
    ap.add_argument("--shot", choices=SHOTS + ["views"])
    ap.add_argument("--path", help="camera path JSON")
    ap.add_argument("--view", type=int, default=0, help="which original camera the shot is built around")
    ap.add_argument("--amount", type=float, default=0.3, help="size of the move (fraction of focus distance)")
    ap.add_argument("--seconds", type=float, default=4)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--fov", type=float, help="field of view in degrees (larger image axis)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--out", help="output .mp4 (default renders/<scene>_<shot>.mp4)")
    ap.add_argument("--save-path", help="also save the generated keyframes as JSON here")
    ap.add_argument("--list-views", action="store_true", help="list original cameras and exit")
    ap.add_argument("--show", action="store_true", help="show the browser window while rendering")
    a = ap.parse_args()

    scene_dir = ROOT / "output" / a.scene
    plys = sorted(scene_dir.glob("*.ply"))
    if not plys:
        sys.exit(f"no .ply in {scene_dir}")
    ply = plys[0]

    if a.path:
        path = json.loads(Path(a.path).read_text())
        name = Path(a.path).stem
    else:
        cams = read_cameras(scene_dir, a.scene)
        if a.list_views:
            for i, c in enumerate(cams):
                print(f"{i:3d}  {c['name']}")
            return
        if not a.shot:
            sys.exit("give --shot or --path (see --help)")
        xyz = load_ply(ply)[0]
        frames = max(2, round(a.seconds * a.fps))
        keys = build_shot(a.shot, cams, xyz, a.view, a.amount, frames, a.fov,
                          landscape=a.width >= a.height)
        path = dict(fps=a.fps, frames=frames, smoothness=1, keys=keys)
        if a.shot == "views":
            path = dict(fps=1, frames=len(keys), smoothness=0, keys=keys)
        name = f"{a.shot}_v{a.view}"
        if a.save_path:
            Path(a.save_path).parent.mkdir(parents=True, exist_ok=True)
            Path(a.save_path).write_text(json.dumps(path, indent=1))
            log(f"saved camera path -> {a.save_path}")

    out = Path(a.out) if a.out else ROOT / "renders" / f"{a.scene}_{name}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    dl = Path(tempfile.mkdtemp(prefix="ssdl_"))

    port, dbg = free_port(), free_port()
    py = sys.executable
    server = subprocess.Popen([py, str(ROOT / "scripts" / "serve.py"), "--no-browser", "--no-poses",
                               "--port", str(port), a.scene], stdout=subprocess.DEVNULL)
    profile = tempfile.mkdtemp(prefix="ssedge_")
    args = [find_browser(), f"--remote-debugging-port={dbg}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--ignore-gpu-blocklist",
            "--enable-unsafe-swiftshader", f"--window-size={max(a.width, 1280)},{max(a.height, 800)}",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows", "about:blank"]
    if not a.show:
        args.insert(1, "--headless=new")
    browser = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                tabs = json.loads(NOPROXY.open(f"http://127.0.0.1:{dbg}/json/list", timeout=2).read())
                page = next(t for t in tabs if t.get("type") == "page")
                ver = json.loads(NOPROXY.open(f"http://127.0.0.1:{dbg}/json/version", timeout=2).read())
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("browser did not start")
        CDP(ver["webSocketDebuggerUrl"]).call("Browser.setDownloadBehavior", behavior="allow",
                                              downloadPath=str(dl), eventsEnabled=False)
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.call("Runtime.enable")
        url = (f"http://localhost:{port}/?load=" +
               urllib.request.quote(f"/output/{a.scene}/{ply.name}", safe=""))
        # wait for the server, then load the editor
        for _ in range(120):
            try:
                NOPROXY.open(f"http://127.0.0.1:{port}/", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        log(f"loading {ply.name} in SuperSplat ...")
        cdp.call("Page.navigate", url=url)
        t0 = time.time()
        while True:
            n = cdp.js("(()=>{try{const m=document.body.innerText.match(/Splats\\s+([\\d,]+)/);"
                       "return m?parseInt(m[1].replace(/,/g,'')):0}catch(e){return 0}})()")
            if n:
                break
            if time.time() - t0 > 600:
                raise RuntimeError("scene did not load within 10 minutes")
            time.sleep(1)
        log(f"scene loaded ({n:,} splats); setting {len(path['keys'])} keyframes")
        cdp.js(f"""(()=>{{
            const ev = window.scene.events, p = {json.dumps(path)};
            ev.fire('timeline.setFrameRate', p.fps);
            ev.fire('timeline.setFrames', p.frames);
            ev.fire('timeline.setSmoothness', p.smoothness ?? 1);
            ev.fire('timeline.setLoop', false);
            const v = a => ({{x:a[0], y:a[1], z:a[2]}});
            ev.fire('camera.loadPoses', p.keys.map((k,i) => ({{name:'key_'+i, frame:k.frame,
                position:v(k.position), target:v(k.target), fov:k.fov}})));
        }})()""")
        log(f"rendering {path['frames']} frames at {a.width}x{a.height} ...")
        settings = dict(startFrame=0, endFrame=path["frames"] - 1, frameRate=path["fps"],
                        width=a.width, height=a.height, bitrate=12_000_000, transparentBg=False,
                        showDebug=False, format="mp4", codec="h264", projection="standard")
        ok = cdp.js(f"window.scene.events.invoke('render.video', {json.dumps(settings)}, null)",
                    await_promise=True)
        if not ok:
            raise RuntimeError("render was cancelled or failed")
        for _ in range(240):
            files = [f for f in dl.iterdir() if f.suffix.lower() == ".mp4"]
            if files and not list(dl.glob("*.crdownload")):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("no video was produced")
        shutil.move(str(files[0]), out)
        log(f"DONE -> {out}  ({time.time() - t0:.0f}s)")
    finally:
        browser.terminate()
        server.terminate()
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(dl, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        log(f"ERROR: {e}")
        sys.exit(1)
