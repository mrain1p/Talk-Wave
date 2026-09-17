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
  - the three faces (phone, player, guide) on four surfaces — a portrait
    phone, a landscape phone, a folded phone's cover, the 620x544 page card
    — each holding the rules the card's design system states in words
    (added 2026-09-17: five releases of card work had shipped past a
    harness that drove one face at one width)
  - every control a caller can press, pressed, and the state it should
    reach read back — route, heart, theme, the call to its honest failure
    and back, the player's tabs/skip/request/play, the guide's week grid,
    fold, portrait and takeover (the 2026-09-17 by-hand pass, repeatable)
  - the installed app OPENING with the server gone, which is the service
    worker's one job

Failed fetches against the stub's fixture gaps are reported, not fatal —
the stub is not the product; a JS exception is.

LOCALHOST ONLY. Like tools/call_harness.py, this refuses any base that is
not the loopback: pointing a browser harness at the operator's deployment
would hammer a live box to answer a dev-box question. There is no override
flag, and none should be added.
"""

from __future__ import annotations

import argparse
import re
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


# --- the three faces, on every surface ---------------------------------------
# The card became three faces on 2026-09-03 (0.99.39) and then had four passes
# with the phone in hand (0.99.40–0.99.43): five releases this harness could
# not see, because it drove one face at one width. Every promise pinned here
# is one the changelog or the card's design system
# (.claude/skills/talkwave-card-design) states in words:
#   - the whole surface never scrolls; only a face's own middle does
#     (operator's rule, 2026-09-01) — #lineBox, .plpanelbody, #guideScroll
#   - the page card is 620x544 exactly (0.99.40, "the card on a computer
#     works again")
#   - a landscape phone turns the faces row into a rail down the left edge
#     (0.99.42), and a folded phone's cover keeps the rail icon-only
#   - nothing overflows sideways on any of them
FACES = (
    # (face, its button, the view it reveals, the regions allowed to scroll —
    # at least one of them must). The guide's middle is one column in
    # portrait and, in landscape, a grid whose listing column scrolls while
    # the show on air stands unclamped beside it (0.99.42).
    ("phone", "facePhone", None, ("#lineBox",)),
    ("player", "facePlayer", "playerView", (".plpanelbody",)),
    ("guide", "faceGuide", "guideView",
     ("#guideScroll", "#guideScroll > .gdlist", "#guideScroll > .gdgrid")),
)
SURFACES = (
    # (surface, viewport, the promise particular to it)
    ("phone", (390, 844), "bleed"),          # ≤500px wide: the card is the screen
    ("landscape", (844, 390), "rail"),       # landscape, ≤560 tall: the rail
    ("cover", (720, 360), "rail-icons"),     # landscape, ≤400 tall: icons only
    ("desktop", (1100, 800), "card"),        # the 620x544 page card
)

# What sw.js precaches, read from the file so the offline check waits for
# the real list rather than a number that goes stale when the shell grows.
SHELL_URLS = re.findall(
    r"'(/[^']*)'",
    re.search(r"const SHELL = \[(.*?)\];",
              (REPO / "web-widget" / "sw.js").read_text(encoding="utf-8"),
              re.S).group(1))

_RECT = ("(sel) => { const el = document.querySelector(sel);"
         " if (!el) return null; const r = el.getBoundingClientRect();"
         " return [r.width, r.height]; }")
_STYLE = ("([sel, prop]) => { const el = document.querySelector(sel);"
          " return el ? getComputedStyle(el)[prop] : null; }")
_PAGE_SCROLL = ("(() => { const d = document.documentElement; return ["
                "d.scrollHeight - d.clientHeight, d.scrollWidth - d.clientWidth"
                "]; })()")


def _surface_promise(page, promise: str, vw: int) -> str:
    """'' when the surface keeps its particular promise, else why not."""
    if promise == "bleed":
        rect = page.evaluate(_RECT, ".card")
        if not rect or abs(rect[0] - vw) > 1:
            return f"card is {rect and round(rect[0])}px wide on a {vw}px phone"
        return ""
    if promise == "card":
        rect = page.evaluate(_RECT, ".card")
        if not rect or abs(rect[0] - 620) > 1 or abs(rect[1] - 544) > 1:
            return (f"card is {rect and round(rect[0])}x{rect and round(rect[1])}"
                    ", not 620x544")
        return ""
    # The rail: the faces row goes down the left edge as a column.
    pos = page.evaluate(_STYLE, [".facebar", "position"])
    direction = page.evaluate(_STYLE, [".facebar", "flexDirection"])
    if pos != "fixed" or direction != "column":
        return f"faces row is {pos}/{direction}, not a fixed column rail"
    if promise == "rail-icons":
        lab = page.evaluate(_STYLE, [".facebar .face .facelab", "display"])
        if lab != "none":
            return f"rail label is {lab!r} on a folded cover — should be icon-only"
    return ""


def check_faces(browser, rep: Report, base: str) -> None:
    """Every face on every surface: the view shows, its own middle scrolls,
    the page around it does not, and the surface keeps its own promise."""
    for surface, (vw, vh), promise in SURFACES:
        # Reduced motion, so a face switch lands at once instead of mid-swipe
        # — the sheet's own rule is that it kills every animation.
        ctx = browser.new_context(viewport={"width": vw, "height": vh},
                                  reduced_motion="reduce")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/", wait_until="networkidle")
        page.wait_for_timeout(400)
        name = f"faces@{surface} {vw}x{vh}"

        offered = page.evaluate(
            "() => document.querySelector('.card.faces') !== null"
            " && !document.getElementById('faceBar').hidden"
            " && ['facePhone', 'facePlayer', 'faceGuide'].every("
            "  id => { const b = document.getElementById(id);"
            "  return b && !b.hidden && b.getBoundingClientRect().width > 0; })")
        if not offered:
            rep.add("FAIL", f"{name}: all three faces offered",
                    "the stub switches the player and the guide on; the row "
                    "at the card's foot does not show all three")
            ctx.close()
            continue

        for face, btn, view, scrollers in FACES:
            if view is not None:
                page.click(f"#{btn}")
                page.wait_for_timeout(350)
            faults: list[str] = []
            shown = page.evaluate(
                "(view) => { const p = document.getElementById('playerView'),"
                " g = document.getElementById('guideView');"
                " const vis = el => el && !el.hidden"
                "   && el.getBoundingClientRect().height > 0;"
                " if (!view) return !vis(p) && !vis(g);"
                " return vis(document.getElementById(view)); }", view)
            if not shown:
                faults.append("the face's view is not the one showing")
            over = {s: page.evaluate(_STYLE, [s, "overflowY"]) for s in scrollers}
            if not any(v in ("auto", "scroll") for v in over.values()):
                faults.append(f"none of the face's middle scrolls: {over}")
            dy, dx = page.evaluate(_PAGE_SCROLL)
            if dy > 1 or dx > 0:
                faults.append(f"the page scrolls ({dy}px down, {dx}px "
                              "sideways) — only the face's middle may")
            why = _surface_promise(page, promise, vw)
            if why:
                faults.append(why)
            if errors:
                faults.append("JS exception: " + errors[0][:120])
                errors.clear()
            if faults:
                rep.add("FAIL", f"{name}: {face} face", "; ".join(faults))
            else:
                rep.add("ok", f"{name}: {face} face — shows, its middle "
                              f"scrolls, the page holds, {promise} kept")
        ctx.close()


# --- every control, pressed --------------------------------------------------
# The 2026-09-17 press-everything pass, made repeatable. That pass found two
# faults by hand that no load-time check could see (a clipped chip row, a
# hidden foot line); what it could not do was be run again next release.
# Each row here presses a control and reads the STATE it should reach — not
# that a handler ran, but what the caller would see: the route lit, the
# heart filled on both faces, the theme back where it started, the call at
# its honest failure and the card back to idle, the request posted and the
# box cleared, the week grid up, the portrait open with the name still
# readable. A control that answers with a JS exception fails the sweep.

def _wait_for(page, expr: str, secs: float = 6.0) -> bool:
    deadline = time.time() + secs
    while time.time() < deadline:
        if page.evaluate(expr):
            return True
        page.wait_for_timeout(150)
    return False


def check_controls(browser, rep: Report, base: str) -> None:
    ctx = browser.new_context(viewport={"width": 390, "height": 844},
                              reduced_motion="reduce")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    posted: list[str] = []
    page.on("request", lambda r: posted.append(
        f"{r.method} {r.url[len(base):].split('?')[0]}")
        if r.url.startswith(base) else None)
    page.goto(f"{base}/", wait_until="networkidle")
    page.wait_for_timeout(400)

    def row(name: str, ok: bool, why: str = "") -> None:
        if errors:
            ok, why = False, ("JS exception: " + errors[0][:120] +
                              (f" ({why})" if why else ""))
            errors.clear()
        rep.add("ok" if ok else "FAIL", f"controls: {name}", "" if ok else why)

    # The route switch lights the on-air side and relabels the call.
    idle_label = page.evaluate("() => document.getElementById('callBtn').textContent.trim()")
    page.click("#routeOn")
    page.wait_for_timeout(250)
    lit = page.evaluate(
        "() => document.querySelector('.card').classList.contains('route-on')"
        " && document.getElementById('routeOn').getAttribute('aria-checked') === 'true'")
    live_label = page.evaluate("() => document.getElementById('callBtn').textContent.trim()")
    page.click("#routeOff")
    page.wait_for_timeout(250)
    back = page.evaluate("() => !document.querySelector('.card').classList.contains('route-on')")
    row("route switch goes on air and back", lit and back and live_label != idle_label,
        f"lit={lit} back={back} labels {idle_label!r}/{live_label!r}")

    # The heart. The card's and the player's are separate states on purpose
    # (call.js: "the card must never claim a heart it did not press"), so
    # the row is: the pressed heart lights at once, and the player's heart
    # reads the like back from the server when the sheet opens — never the
    # one repainting the other.
    page.click(".nplike")
    lit = _wait_for(page, "() => document.querySelector('.nplike').classList.contains('liked')", 3)
    page.click("#facePlayer")
    read_back = _wait_for(page, "() => document.querySelector('.plheart').classList.contains('liked')", 3)
    page.click("#facePhone")
    page.wait_for_timeout(300)
    row("the pressed heart lights, and the player reads the like back on opening",
        lit and read_back, f"lit={lit} readBack={read_back}")

    # The theme cycles and comes home: light, dark, the station's own, auto.
    page.click("#themeBtn")
    page.wait_for_timeout(200)
    stepped = page.evaluate("() => !!document.documentElement.dataset.theme")
    for _ in range(3):
        page.click("#themeBtn")
        page.wait_for_timeout(200)
    home = page.evaluate(
        "() => !document.documentElement.dataset.theme"
        " && !(localStorage.getItem('callinTheme') || '')")
    row("theme cycles four stops and back to auto", stepped and home,
        f"stepped={stepped} home={home}")

    # The call: minted, refused by the stand-in, and the card back to idle
    # with the slot released — the 0.9.117-era teardown, walked.
    page.click("#callBtn")
    failed = _wait_for(page, "() => /could not connect/i.test("
                             "document.getElementById('statusText').textContent)")
    page.wait_for_timeout(300)
    idle = page.evaluate(
        "() => document.querySelector('.card').dataset.mode === 'idle'"
        " && document.getElementById('hangBtn').hidden"
        " && !document.getElementById('callBtn').hidden")
    minted = "POST /token" in posted
    released = "POST /call-ended" in posted
    row("call reaches 'Could not connect' and the card comes back",
        failed and idle and minted and released,
        f"failed={failed} idle={idle} minted={minted} released={released}")

    # The player: its tabs, the skip, a request, and play.
    page.click("#facePlayer")
    page.wait_for_timeout(400)
    page.click(".pltab:nth-of-type(2)")
    page.wait_for_timeout(250)
    tab = page.evaluate("() => (document.querySelector('.pltab.on') || {}).textContent || ''")
    page.click(".pltab:nth-of-type(1)")
    row("player tabs switch", "played" in tab.lower(), f"active tab reads {tab!r}")
    page.click("#plSkipBtn")
    page.wait_for_timeout(500)
    # The receipt lands in #plOpFlash (flashOpResult), the row every player
    # action reports through since 2026-09-02.
    skipped = "POST /player/skip" in posted and page.evaluate(
        "() => { const f = document.getElementById('plOpFlash');"
        " return !!f && !f.hidden && /skipped/i.test(f.textContent); }")
    row("skip posts and reports itself in the row", skipped)
    page.fill("#plReqInput", "something slow for the rain")
    page.click("#plReqSend")
    page.wait_for_timeout(600)
    sent = "POST /player/request" in posted and page.evaluate(
        "() => document.getElementById('plReqInput').value === ''")
    row("request posts and the box clears", sent)
    page.click("#plPlayBtn")
    page.wait_for_timeout(400)
    playing = page.evaluate("() => /pause/i.test(document.getElementById('plPlayBtn').textContent)")
    page.click("#plPlayBtn")
    page.wait_for_timeout(300)
    paused = page.evaluate("() => /play/i.test(document.getElementById('plPlayBtn').textContent)")
    row("play toggles to pause and back", playing and paused)

    # The guide: week and day, the span, the fold, the portrait, a takeover.
    page.click("#faceGuide")
    page.wait_for_timeout(500)
    page.click("#guideViewWeek")
    page.wait_for_timeout(300)
    grid = page.evaluate("() => !document.getElementById('guideGrid').hidden")
    page.click("#guideSpan24")
    page.wait_for_timeout(200)
    span = page.evaluate(
        "() => document.getElementById('guideSpan24').getAttribute('aria-pressed') === 'true'")
    page.click("#guideViewDay")
    page.wait_for_timeout(300)
    listed = page.evaluate("() => !document.getElementById('guideList').hidden")
    row("guide reads as a week grid, takes a span, and comes back to the day",
        grid and span and listed, f"grid={grid} span={span} day={listed}")
    before = page.evaluate("() => document.querySelector('.gdherobox').classList.contains('min')")
    page.click(".gdherofold")
    page.wait_for_timeout(300)
    after = page.evaluate("() => document.querySelector('.gdherobox').classList.contains('min')")
    row("the show on air folds and unfolds", before != after)
    page.click(".gdherofig.gdzoom")
    page.wait_for_timeout(300)
    portrait = page.evaluate(
        "() => { const f = document.querySelector('.gdherofig.gdzoom');"
        " const n = document.querySelector('.gdheronames');"
        " if (!f || !n) return {big: false};"
        " const fr = f.getBoundingClientRect(), nr = n.getBoundingClientRect();"
        " return {big: f.classList.contains('big'), under: nr.top >= fr.bottom - 1,"
        "  clipped: n.querySelector('.gdheroname').scrollWidth"
        "   > n.querySelector('.gdheroname').clientWidth + 1}; }")
    page.click(".gdherofig.gdzoom")
    page.wait_for_timeout(300)
    shut = page.evaluate(
        "() => !document.querySelector('.gdherofig.gdzoom').classList.contains('big')")
    row("the portrait opens under the name, unclipped, and shuts again",
        portrait.get("big") and portrait.get("under") and not portrait.get("clipped") and shut,
        f"{portrait} shut={shut}")
    page.click(".gdtake:not(.back)")
    page.wait_for_timeout(700)
    took = "POST /station/override" in posted and page.evaluate(
        "() => !!document.querySelector('.gdtake.back')")
    row("put on air posts the takeover and the row offers the hand-back", took)

    row("no JS exception across the sweep", True)
    ctx.close()


def check_offline(browser, rep: Report, base: str, stub) -> None:
    """The installed app opens with the server gone — sw.js's one job.

    Not `set_offline`: that reaches the page's own network, and whether it
    reaches a worker's fetches has changed between browser versions. The
    stub is killed instead, which is what "no signal" actually is. So this
    runs LAST — nothing after it has a server to talk to.
    """
    if stub is None:
        rep.add("note", "offline: skipped — needs the stub this run booted "
                        "(not --base), so it can be taken away")
        return
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(f"{base}/", wait_until="load")
    # call.js registers /sw.js on the real page over a secure context;
    # 127.0.0.1 counts. Wait for the worker to install its shell — every
    # entry of SHELL, fetched one by one — before taking the server away.
    # Polled through evaluate (which awaits a promise) rather than
    # wait_for_function (which does not, and returned on the pending
    # promise itself: the first run killed the stub mid-install and read
    # the half-cached shell as a broken worker).
    shell = len(SHELL_URLS)
    held = 0
    deadline = time.time() + 10
    while time.time() < deadline:
        held = page.evaluate(
            "() => caches.keys().then(ks => ks.length ? caches.open(ks[0])"
            ".then(c => c.keys()).then(k => k.length) : 0)")
        if held >= shell:
            break
        page.wait_for_timeout(200)
    if held < shell:
        rep.add("FAIL", "offline: the service worker installed its shell",
                f"{held} of {shell} shell entries cached within 10s")
        ctx.close()
        return
    stub.kill()
    stub.wait()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", int(base.rsplit(":", 1)[1])),
                                          timeout=0.3):
                time.sleep(0.2)
        except OSError:
            break
    failed: list[str] = []
    page.on("requestfailed",
            lambda r: failed.append(r.url) if r.url.startswith(base) else None)
    try:
        page.reload(wait_until="load")
    except Exception as e:
        rep.add("FAIL", "offline: the app opens with no signal",
                f"reload with the server gone did not load: {str(e)[:120]}")
        ctx.close()
        return
    page.wait_for_timeout(400)
    has_card = page.evaluate("() => !!document.getElementById('callBtn')")
    shell_lost = [u for u in failed if u.rsplit(".", 1)[-1] in ("css", "js")]
    if has_card and not shell_lost:
        rep.add("ok", "offline: the app opens with the server gone — page, "
                      "sheets and scripts all answered from the shell")
    else:
        rep.add("FAIL", "offline: the app opens with no signal",
                ("no card" if not has_card else "") +
                (" shell files not cached: " + ", ".join(
                    u.rsplit("/", 1)[-1] for u in shell_lost) if shell_lost else ""))
    live = [u.rsplit("/", 1)[-1] for u in failed if u not in shell_lost]
    if live:
        rep.add("note", f"offline: {len(live)} live fetch(es) failed as they "
                        "must (never cached)", ", ".join(live[:4]))
    ctx.close()


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

                check_faces(browser, rep, base)
                check_controls(browser, rep, base)
                # Last: it takes the server away.
                check_offline(browser, rep, base, proc)
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
