"""Boot Talk Wave's widget surfaces headlessly and drive them.

Talk Wave is a LiveKit call-in + text + voicemail sidecar for an AI radio
DJ: a Python worker + token server (agent-worker/) and a plain-JS widget
(web-widget/). The FULL stack needs external services a clean machine does
not have — a SUB/WAVE station, a LiveKit server, a TTS backend — so "run the
whole app and place a call" is not a headless operation. What IS fully
drivable here, with no external anything, is the WIDGET: the caller's page,
the operator's settings panel, and the embed, served by the repo's own stub
(tools/panel_dev_server.py, a fake backend) and driven with Playwright.

This driver is the programmatic handle a headless agent needs. It boots the
stub on a scratch port, opens the pages in headless Chromium, and:

    python .claude/skills/run-talk-wave/driver.py shots [OUTDIR]
        screenshot the call page, the settings panel, and the embed

    python .claude/skills/run-talk-wave/driver.py check
        run the source-and-render smoke test (tools/widget_check.py)

    python .claude/skills/run-talk-wave/driver.py drive
        open the call page, press the Call button, and report the state the
        widget moved to (proves the state machine is reachable, not just the
        paint)

Needs, in the repo venv: `pip install playwright && playwright install
chromium`. The stub needs nothing but the venv. LOCALHOST ONLY by
construction — it binds 127.0.0.1 and there is no flag to point it anywhere
else; driving a browser harness at a deployment would hammer a live box.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STUB = REPO / "tools" / "panel_dev_server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _boot(port: int) -> subprocess.Popen:
    env = dict(os.environ, PORT=str(port))
    proc = subprocess.Popen([sys.executable, str(STUB)], env=env, cwd=str(REPO))
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except OSError:
            if proc.poll() is not None:
                sys.exit("stub exited before it listened — run "
                         "tools/panel_dev_server.py by hand to see why")
            time.sleep(0.2)
    proc.kill()
    sys.exit("stub never listened on its port")


def _play():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright not in this venv — "
                 "`pip install playwright && playwright install chromium`")
    return sync_playwright


def shots(outdir: str) -> None:
    out = Path(outdir or (REPO / "widget-shots"))
    out.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    proc = _boot(port)
    base = f"http://127.0.0.1:{port}"
    try:
        with _play()() as pw:
            b = pw.chromium.launch()
            for name, url, vp in (
                ("call-page", f"{base}/index.html", {"width": 480, "height": 820}),
                ("settings-panel", f"{base}/settings", {"width": 1100, "height": 900}),
                ("embed-compact", f"{base}/index.html?compact=1",
                 {"width": 360, "height": 640}),
            ):
                p = b.new_page(viewport=vp)
                # networkidle, not load: the panel paints its masthead and
                # its 39 setting sections only after ~two dozen fixture
                # fetches (options, settings, schema, calls, live…) resolve.
                # Shot at `load` it is a blank cream page — the sections are
                # there in the DOM but nothing has drawn yet.
                p.goto(url, wait_until="networkidle")
                p.wait_for_timeout(800)
                dest = out / f"{name}.png"
                p.screenshot(path=str(dest))
                print(f"  {name}: {dest}")
                p.close()
            b.close()
    finally:
        proc.kill()
    print(f"screenshots in {out}")


def check() -> None:
    r = subprocess.run([sys.executable, str(REPO / "tools" / "widget_check.py")],
                       cwd=str(REPO))
    sys.exit(r.returncode)


def drive() -> None:
    port = _free_port()
    proc = _boot(port)
    base = f"http://127.0.0.1:{port}"
    try:
        with _play()() as pw:
            b = pw.chromium.launch()
            p = b.new_page(viewport={"width": 480, "height": 820})
            p.goto(f"{base}/index.html", wait_until="load")
            p.wait_for_timeout(500)
            btn = p.query_selector("#callBtn")
            label_before = (btn.inner_text() if btn else "<no #callBtn>").strip()
            status = p.evaluate(
                "() => { const e = document.getElementById('statusText')"
                " || document.querySelector('.eyebrow'); "
                "return e ? e.textContent.trim() : null; }")
            print(f"  call button reads: {label_before!r}")
            print(f"  status line reads: {status!r}")
            if btn:
                btn.click()
                p.wait_for_timeout(1500)
                after = p.evaluate(
                    "() => { const e = document.getElementById('statusText')"
                    " || document.querySelector('.eyebrow'); "
                    "return e ? e.textContent.trim() : null; }")
                cls = p.evaluate("() => document.body.className")
                print(f"  after Call press, status: {after!r}")
                print(f"  body class: {cls!r}")
                print("  (the stub has no LiveKit backend, so the call cannot "
                      "connect — reaching a 'connecting'/'no answer' state "
                      "proves the state machine left idle)")
            b.close()
    finally:
        proc.kill()


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "shots"
    if cmd == "shots":
        shots(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "check":
        check()
    elif cmd == "drive":
        drive()
    else:
        sys.exit(f"unknown command {cmd!r} — shots | check | drive")


if __name__ == "__main__":
    main()
