// Small add-on injected into the local SuperSplat editor by scripts/serve.py.
//  - ?path=/camera/<file>.json  loads a camera path (keyframes) after the scene loads
//  - "Save path" button (bottom-left) writes the current keyframes to camera\<name>.json,
//    which scripts/render_path.py --path can render headlessly.
(() => {
    const params = new URLSearchParams(location.search);

    const whenReady = (cb) => {
        const t = setInterval(() => {
            const m = document.body && document.body.innerText.match(/Splats\s+([\d,]+)/);
            if (window.scene && window.scene.events && m && parseInt(m[1].replace(/,/g, ''), 10) > 0) {
                clearInterval(t);
                cb(window.scene.events);
            }
        }, 500);
    };

    const loadPath = async (ev, url) => {
        const p = await (await fetch(url, { cache: 'no-store' })).json();
        ev.fire('timeline.setFrameRate', p.fps || 30);
        ev.fire('timeline.setFrames', p.frames || 120);
        ev.fire('timeline.setSmoothness', p.smoothness ?? 1);
        const v = a => ({ x: a[0], y: a[1], z: a[2] });
        ev.fire('camera.loadPoses', p.keys.map((k, i) => ({
            name: 'key_' + i, frame: k.frame, position: v(k.position), target: v(k.target), fov: k.fov
        })));
        ev.fire('timeline.setFrame', 0);
    };

    const savePath = async (ev) => {
        const poses = ev.invoke('camera.poses') || [];
        if (poses.length < 2) { alert('Add at least 2 keyframes first (Enter on the timeline).'); return; }
        const name = prompt('Save camera path as (saved in the project camera\\ folder):', 'my_shot');
        if (!name) return;
        const a3 = v => [v.x, v.y, v.z];
        const body = {
            fps: ev.invoke('timeline.frameRate'),
            frames: ev.invoke('timeline.frames'),
            smoothness: ev.invoke('timeline.smoothness'),
            keys: poses.slice().sort((a, b) => a.frame - b.frame)
                .map(p => ({ frame: p.frame, position: a3(p.position), target: a3(p.target), fov: p.fov }))
        };
        const r = await fetch('/save-path?name=' + encodeURIComponent(name), { method: 'POST', body: JSON.stringify(body) });
        alert(r.ok ? await r.text() : 'Save failed: ' + r.status);
    };

    whenReady((ev) => {
        if (params.get('path')) loadPath(ev, params.get('path')).catch(e => alert('Could not load camera path: ' + e));
        const b = document.createElement('button');
        b.textContent = 'Save path';
        b.title = 'Save the timeline keyframes to camera\\<name>.json';
        Object.assign(b.style, {
            position: 'fixed', left: '12px', bottom: '44px', zIndex: 1000, padding: '6px 10px',
            background: '#f60', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer',
            font: '12px sans-serif'
        });
        b.onclick = () => savePath(ev);
        document.body.appendChild(b);
    });
})();
