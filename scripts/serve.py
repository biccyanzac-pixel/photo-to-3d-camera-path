"""Local web server for the SuperSplat editor (camera keyframes + MP4 rendering).

    /            -> tools/supersplat   (locally built SuperSplat editor, + supersplat_ext.js)
    /output/...  -> output/            (your reconstructed .ply files)
    /camera/...  -> camera/            (saved camera paths)
    POST /save-path?name=x  -> writes camera/x.json (used by the "Save path" button)

Usage: python scripts/serve.py [scene-or-ply] [--path camera/x.json] [--port 8642] [--no-browser]
"""

import argparse
import http.server
import json
import mimetypes
import re
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "tools" / "supersplat"
EXT = Path(__file__).resolve().parent / "supersplat_ext.js"
MOUNTS = {"/output/": ROOT / "output", "/camera/": ROOT / "camera"}

# Windows' registry often maps .js to text/plain, which breaks ES modules.
for ext, typ in {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
                 ".json": "application/json", ".wasm": "application/wasm", ".ply": "application/octet-stream",
                 ".webp": "image/webp", ".txt": "text/plain", ".svg": "image/svg+xml"}.items():
    mimetypes.add_type(typ, ext)


def _safe(base, sub):
    p = (base / unquote(sub)).resolve()
    if base.resolve() not in p.parents and p != base.resolve():
        return base / "__forbidden__"
    if p.is_dir():
        p = p / "index.html"
    return p


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        for prefix, base in MOUNTS.items():
            if clean.startswith(prefix):
                return str(_safe(base, clean[len(prefix):]))
        if clean == "/_ext.js":
            return str(EXT)
        return str(_safe(APP, clean.lstrip("/")))

    def do_GET(self):
        clean = self.path.split("?", 1)[0]
        if clean in ("/", "/index.html"):
            html = (APP / "index.html").read_text(encoding="utf-8")
            html = html.replace("</body>", '<script src="/_ext.js"></script></body>')
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/save-path":
            self.send_error(404)
            return
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", parse_qs(u.query).get("name", ["path"])[0]) or "path"
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            data = json.loads(body)
            assert len(data["keys"]) >= 2
        except Exception:
            self.send_error(400, "invalid camera path")
            return
        dest = ROOT / "camera" / f"{name}.json"
        dest.parent.mkdir(exist_ok=True)
        if dest.exists():  # never overwrite: add a number
            i = 2
            while (ROOT / "camera" / f"{name}_{i}.json").exists():
                i += 1
            dest = ROOT / "camera" / f"{name}_{i}.json"
        dest.write_text(json.dumps(data, indent=1), encoding="utf-8")
        msg = f"Saved camera\\{dest.name}  -  render it with:  .\\render-shot.ps1 <scene> -Path camera\\{dest.name}"
        print(msg, flush=True)
        out = msg.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # keep the console quiet


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)  # browsers abort requests routinely


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", help="scene name in output/, or path to a .ply")
    ap.add_argument("--path", help="camera path JSON (in camera/) to load onto the timeline")
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-poses", action="store_true", help="don't import the original camera poses")
    a = ap.parse_args()

    if not (APP / "index.html").exists():
        sys.exit("SuperSplat is not built (tools/supersplat missing). Run setup.ps1.")

    url = f"http://localhost:{a.port}/"
    params = []
    if a.target:
        t = Path(a.target)
        out = ROOT / "output"
        if t.suffix.lower() in (".ply", ".splat", ".spz", ".sog"):
            t = t.resolve()
            if out.resolve() not in t.parents:
                sys.exit(f"Put the file inside {out} so the viewer can load it.")
            params.append("load=" + quote("/output/" + t.relative_to(out.resolve()).as_posix(), safe=""))
        else:
            scene = out / a.target
            plys = sorted(scene.glob("*.ply"))
            if not plys:
                sys.exit(f"No .ply found in {scene}. Run process.ps1 {a.target} first.")
            params.append("load=" + quote(f"/output/{a.target}/{plys[0].name}", safe=""))
            poses = scene / f"{a.target}_images.txt"
            if poses.exists() and not a.no_poses and not a.path:
                params.append("load=" + quote(f"/output/{a.target}/{poses.name}", safe=""))
    if a.path:
        p = Path(a.path).resolve()
        cam = (ROOT / "camera").resolve()
        if cam not in p.parents:
            sys.exit(f"Camera paths must be inside {cam}")
        params.append("path=" + quote("/camera/" + p.relative_to(cam).as_posix(), safe=""))
    if params:
        url += "?" + "&".join(params)

    with Server(("127.0.0.1", a.port), Handler) as httpd:
        print(f"SuperSplat running at {url}", flush=True)
        print("Leave this window open while you work. Press Ctrl+C to stop.", flush=True)
        if not a.no_browser:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
