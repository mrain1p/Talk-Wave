"""Drive the widget in a real browser and fail on what text checks cannot see.

A4, decided 2026-08-28. The Python suite reads the widget's source — script
tags, DOM ids, brace balance — and that has twice not been enough: a
duplicated selector line silently killed every CSS rule after it and the
embed re-inflated to 896px with zero errors anywhere (the suite stayed
green), and a stray */ turned a comment's tail into live CSS and ate the
rule underneath (found by LOOKING at the page, 2026-08-14). ~14,700 lines of
browser JS have no other executable check.

This is Playwright for PYTHON, deliberately: no npm, no node_modules, no
package.json — web-widget/CLAUDE.md's no-build rule stands. It is a DEV
TOOL, not part of the suite: it needs `pip install playwright` +
`playwright install chromium` in the venv, so the image and CI need nothing
new. Run it after any change to web-widget/, before a release:

    python tools/widget_check.py

It boots its own stub (tools/panel_dev_server.py) on a scratch port, drives
BOTH pages plus the embed's compact mode headlessly, and checks the things
the incidents were made of:

  - a page that throws on load (pageerror — the 0.9.63 class)
  - CSS that parsed but died (computed-style spot checks on load-bearing
    rules from every sheet each page loads)
  - the two-pages contract as the BROWSER sees it (which sheets and scripts
    actually attached, not which tags the HTML mentions)
  - compact mode actually compacting

Failed fetches against the stub's fixture gaps are reported, not fatal —
the stub is not the product; a JS exception is.

LOCALHOST ONLY. Like tools/call_harness.py, this refuses any base that is
not the loopback: pointing a browser harness at the operator's deployment
would hammer a live box to answer a dev-box question. There is no override
flag, and none should be added.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCAL = ("localhost", "127.0.0.1", "::1")


def refuse_remote(base: str) -> str:
    # urlparse().hostname, not a naive colon-split: the split parsed
    # http://[::1]:8123 as host "[" (the first colon sits inside the IPv6
    # brackets), rejecting the very ::1 the allowlist names (cloud review,
    # 2026-08-28). hostname normalises bracketed and bare IPv6 to "::1".
    from urllib.parse import urlparse

    parsed = urlparse(base if "//" in base else "//" + base)
    host = (parsed.hostname or "").lower()
    if host not in LOCAL:
        sys.exit(f"widget_check drives {LOCAL} only — never a deployment. "
                 f"Got: {host!r}")
    return base


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def boot_stub(port: int) -> subprocess.Popen:
    import os

    env = dict(os.environ)
    env["PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "tools" / "panel_dev_server.py")],
        env=env,
        cwd=str(REPO),
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except OSError:
            if proc.poll() is not None:
                sys.exit("the stub exited before it listened — run "
                         "tools/panel_dev_server.py by hand to see why")
            time.sleep(0.2)
    proc.kill()
    sys.exit("the stub never listened on its port")


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, verdict: str, name: str, detail: str = "") -> None:
        self.rows.append((verdict, name, detail))
        print(f"  {verdict:<5} {name}" + (f" — {detail}" if detail else ""))

    @property
    def failed(self) -> bool:
        return any(v == "FAIL" for v, _, _ in self.rows)


def check_page(page, rep: Report, name: str, url: str,
               sheets: set[str], scripts: set[str],
               styles: list[tuple[str, str, str]]) -> None:
    """One page: load it, then assert what the BROWSER ended up with."""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    console: list[str] = []
    page.on("console", lambda m: console.append(m.text)
            if m.type == "error" else None)
    page.goto(url, wait_until="load")
    page.wait_for_timeout(400)

    if errors:
        rep.add("FAIL", f"{name}: no JS exception on load", errors[0][:160])
    else:
        rep.add("ok", f"{name}: no JS exception on load")
    if console:
        rep.add("note", f"{name}: {len(console)} console error(s) "
                        "(stub fixture gaps are expected)",
                console[0][:120])

    got_sheets = set(page.evaluate(
        "[...document.styleSheets].map(s => s.href && s.href.split('/')"
        ".pop().split('?')[0]).filter(Boolean)"))
    if got_sheets == sheets:
        rep.add("ok", f"{name}: stylesheets attached = {sorted(sheets)}")
    else:
        rep.add("FAIL", f"{name}: stylesheet contract",
                f"expected {sorted(sheets)}, browser has {sorted(got_sheets)}")

    # CDN riders are filtered out by name: LiveKit (both pages' SDK) and the
    # Google Cast sender (the call page's Chromecast path, 2026-09-01) are
    # deliberate externals, not page-contract drift.
    got_scripts = set(page.evaluate(
        "[...document.scripts].map(s => s.src && s.src.split('/').pop()"
        ".split('?')[0]).filter(s => s && !s.startsWith('livekit')"
        " && !s.startsWith('cast_sender'))"))
    if got_scripts == scripts:
        rep.add("ok", f"{name}: scripts attached = {sorted(scripts)}")
    else:
        rep.add("FAIL", f"{name}: script contract",
                f"expected {sorted(scripts)}, browser has {sorted(got_scripts)}")

    for selector, prop, expect in styles:
        got = page.evaluate(
            "([sel, prop]) => { const el = document.querySelector(sel);"
            " return el ? getComputedStyle(el)[prop] : null; }",
            [selector, prop])
        if got is None:
            rep.add("FAIL", f"{name}: {selector} exists", "not in the DOM")
        elif expect in str(got):
            rep.add("ok", f"{name}: {selector} {prop} carries {expect!r}")
        else:
            rep.add("FAIL", f"{name}: {selector} {prop}",
                    f"wanted {expect!r} in {got!r} — the rule that sets it "
                    "is dead in the browser")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="",
                    help="drive an already-running LOCAL stub instead of "
                         "booting one (e.g. http://localhost:8123)")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright is not installed in this venv — "
                 "`pip install playwright && playwright install chromium` "
                 "(dev box only; the image never needs it)")

    proc = None
    if args.base:
        base = refuse_remote(args.base)
    else:
        port = free_port()
        proc = boot_stub(port)
        base = f"http://127.0.0.1:{port}"

    rep = Report()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                # The operator's page: both sheets, the panel scripts, and
                # one load-bearing computed style from EACH sheet it loads.
                page = browser.new_page()
                check_page(
                    page, rep, "panel", f"{base}/settings",
                    sheets={"style.css", "panel.css"},
                    scripts={"shared.js", "panel.js", "panel-sounds.js",
                             "panel-viewers.js", "panel-charts.js"},
                    styles=[
                        (".row label", "flexBasis", "168px"),   # panel.css
                        ("body", "fontFamily", ""),             # style.css base
                    ])
                page.close()

                # The caller's page, full mode.
                page = browser.new_page()
                check_page(
                    page, rep, "call", f"{base}/index.html",
                    sheets={"style.css", "skins.css"},
                    scripts={"shared.js", "call.js"},
                    styles=[
                        (".card", "borderRadius", "16px"),      # style.css
                        ("#callBtn", "display", "flex"),
                        # The whole surface never scrolls — only the sheet's
                        # middle may (operator's rule, 2026-09-01). The
                        # sheet clips; the panels inside carry the bars.
                        (".player", "overflowY", "hidden"),
                    ])
                page.close()

                # The embed's view: same page, compact, in an iframe-sized
                # viewport — the mode whose CSS silently died in the
                # incident this tool exists for. The card fills its frame
                # by design, so the re-inflation signal is the card (or the
                # page) growing PAST the frame, not any absolute width.
                page = browser.new_page(viewport={"width": 360,
                                                  "height": 640})
                page.goto(f"{base}/index.html?compact=1", wait_until="load")
                page.wait_for_timeout(400)
                compact = page.evaluate(
                    "document.body.classList.contains('compact')")
                if compact:
                    rep.add("ok", "embed: body.compact set from ?compact=1")
                else:
                    rep.add("FAIL", "embed: body.compact",
                            "?compact=1 did not compact the card")
                width = page.evaluate(
                    "(() => { const c = document.querySelector('.card');"
                    " return c ? c.getBoundingClientRect().width : 0; })()")
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth"
                    " - document.documentElement.clientWidth")
                if 0 < width <= 360 and overflow <= 0:
                    rep.add("ok", f"embed: card {width:.0f}px fits its "
                                  "360px frame, no sideways overflow")
                else:
                    rep.add("FAIL", "embed: card containment",
                            f"card {width:.0f}px, horizontal overflow "
                            f"{overflow}px in a 360px frame — the "
                            "re-inflated-embed shape")
                page.close()
            finally:
                browser.close()
    finally:
        if proc is not None:
            proc.kill()

    print()
    if rep.failed:
        sys.exit("WIDGET CHECK FAILED — see FAIL rows above")
    print("widget check: all checks passed "
          f"({sum(1 for v, _, _ in rep.rows if v == 'ok')} ok)")


if __name__ == "__main__":
    main()
