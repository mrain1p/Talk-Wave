"""Serve the real widget against a fake backend, for driving it in a browser.

Dev only. It lives in tools/ because the Dockerfile copies `agent-worker/` and
`web-widget/` into the image and nothing else — so this cannot ship by
accident.

WHY THIS EXISTS. `app.js` is ~3,000 lines with no tests and no runner, and the
bug that opens most sessions is a widget bug. 0.9.63 was one word: a "have I
loaded yet?" guard testing a dict that had been changed from null to {}, so the
settings panel fetched nothing, showed nothing, and did not even prompt for a
password. It shipped. Nothing in the Python suite could see it.

The panel needs the station, the TTS server and Ollama to paint, and on a real
deployment `/settings/options` takes ten to fifteen seconds when any of them is
unreachable — long enough that people give up and "verify" by reading the diff.
This answers all of it instantly from fixtures, so exercising the panel is a
few seconds rather than a few minutes.

    python tools/panel_dev_server.py          # then open the printed URL

Under Claude Code, add it to .claude/launch.json with "autoPort": true and use
preview_start — see the wavetalk-verify skill, which has the traps.

TWO THINGS THAT COST AN HOUR EACH BEFORE THEY WERE WRITTEN DOWN:

  * ThreadingHTTPServer, never HTTPServer. Single-threaded, the preview
    harness's held-open probe connection blocks serve_forever and only `GET /`
    is ever answered — app.js and style.css are silently never requested, and
    the page looks like it loaded.
  * The LiveKit CDN <script> is stripped. The sandbox blackholes jsdelivr and a
    blocking script that never resolves stalls the parser before app.js is
    reached. LiveKit is only needed to place a call, not to render anything.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIDGET = ROOT / "web-widget"

# Point every writable path at a temp dir before importing settings, exactly
# as the test suite does. Driving the panel must never touch real settings.
_TMP = Path(tempfile.mkdtemp(prefix="wavetalk-panel-"))
os.environ.setdefault("SETTINGS_PATH", str(_TMP / "settings.json"))
os.environ.setdefault("SECRETS_PATH", str(_TMP / "secrets.json"))
os.environ.setdefault("ADMIN_AUTH_PATH", str(_TMP / "auth.json"))
os.environ.setdefault("CALLS_PATH", str(_TMP / "calls"))
os.environ.setdefault("VOICE_FX_PATH", str(_TMP / "voice-effects.json"))
os.environ.setdefault("LOG_TO_FILE", "0")
(_TMP / "settings.json").write_text(json.dumps({
    "llm_provider": "google", "llm_model": "gemini-3.1-flash-lite",
    "tts_mode": "local", "front_access": "guest",
    "show_push_to_talk": True,
    "show_voicemail_button": True,
    "live_calls_enabled": True,
    "voicemail_enabled": True,
    "voicemail_when": "closed",
}), encoding="utf-8")

sys.path.insert(0, str(ROOT / "agent-worker"))
import settings as settings_store  # noqa: E402
import secrets_store  # noqa: E402

PORT = int(os.environ.get("PORT", "8123"))

# The slow half of the panel, answered instantly. Shapes match what
# handle_settings_options really returns; the values are fixtures.
OPTIONS = {
    "llmProviders": ["openai", "google", "anthropic", "openrouter", "ollama"],
    "llmProviderLabels": settings_store.LLM_PROVIDER_LABELS,
    "llmModels": {"google": ["gemini-3.1-flash-lite", "gemini-3.6-flash"],
                  "openai": ["gpt-4.1-mini"]},
    "modelsDiscovered": {"google": True},
    "sttProviders": ["local", "deepgram", "openai", "google"],
    "sttModels": {"local": ["base.en", "tiny.en"], "deepgram": ["nova-3"]},
    "ttsModes": ["cloud", "local"],
    "ttsAdapters": ["local-vibevoice.json", "openai-cloud.json",
                    "elevenlabs-cloud.json"],
    "ttsAdapterBaseUrls": {"elevenlabs-cloud.json": "https://api.elevenlabs.io"},
    # Deliberately does NOT include the station's voice for p_default1 below:
    # that mismatch is the 0.9.81 bug, and it should be reproducible here.
    "voices": ["-Cliff1", "-Delia1", "Lily"],
    "personas": [{"id": "p_default1", "name": "Rosie"},
                 {"id": "p_e28f6a", "name": "Dawn"}],
    "voiceSource": {"adminConfigured": True, "mirroringStation": True, "count": 18},
    "stationLlm": {"model": "gemini-3.1-flash-lite"},
    "providerBaseUrls": settings_store.provider_base_urls(),
    "ttsBaseUrls": settings_store.tts_base_urls(),
}

# Fixtures for the pipeline check. Everything passes except the legs that
# genuinely cannot exist here, so a red line is the panel's own doing.
PIPELINE_ENV = {
    "ok": True,
    "livekit": {"ok": True, "url": "ws://stub"},
    "livekitAuth": {"ok": True},
    "admin": {"ok": True, "detail": "station admin credentials accepted"},
    "webhook": {"registered": True, "id": "wave_talk", "received": 4,
                "url": "http://192.168.1.40:8100/hooks/station",
                "detail": "registered"},
    "listeners": {"requestsOpen": True, "detail": "2 listening"},
    "keys": {"ok": True, "missing": []},
    "stt": {"ok": True, "detail": "local · base.en"},
    "llm": {"ok": True, "detail": "google · gemini-3.1-flash-lite"},
    "tts": {"ok": True, "detail": "local · -Cliff1"},
}

# Flip `ok` to see the other branch: a station that took the registration but
# cannot route back to the receiver, which from the panel looks identical to
# working until something asks.
HOOK_TEST = {
    "ok": True, "fired": True,
    "url": "http://192.168.1.40:8100/hooks/station",
    "detail": "the station's push reached http://192.168.1.40:8100/hooks/station",
}

LOG_RECORDS = [
    {"t": "11:20:01", "level": "INFO", "logger": "callin.token",
     "msg": "call-in widget + token server on http://localhost:8100"},
    {"t": "11:20:04", "level": "DEBUG", "logger": "callin.settings",
     "msg": "settings re-read for this call"},
    {"t": "11:20:09", "level": "WARNING", "logger": "callin.station",
     "msg": "station read /dj failed (timeout) - retrying"},
    {"t": "11:20:11", "level": "ERROR", "logger": "callin.agent",
     "msg": "TTSError: 400 Bad Request for /v1/audio/speech"},
    {"t": "11:20:12", "level": "INFO", "logger": "aiohttp.access",
     "msg": "GET /live HTTP/1.1 200"},
]

LIVEKIT_TAG = ('<script src="https://cdn.jsdelivr.net/npm/livekit-client@2.21.0'
               '/dist/livekit-client.umd.min.js"></script>')


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype: str) -> None:
        raw = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, payload) -> None:
        self._send(200, json.dumps(payload), "application/json")

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        # A posted settings patch really lands in the stub's TEMP store —
        # /live reads the store back, so the closed-line states (pause the
        # line, switch a mode off) can be driven end to end in a browser.
        # Everything else answers like a GET, which is all the panel needs.
        if self.path.split("?")[0] == "/settings":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                patch = json.loads(self.rfile.read(n) or b"{}")
                if isinstance(patch, dict):
                    settings_store.save(patch)
            except Exception:
                pass
        # Category filing answers ok so the shelf's save path can be driven;
        # the real handler persists to the sounds meta store.
        if self.path.split("?")[0] == "/settings/sounds/meta":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
            except Exception:
                pass
            return self._json({"ok": True})
        # Per-DJ effects persist too — through the REAL store, pointed at
        # the stub's temp dir, so the panel's list can be driven end to end.
        if self.path.split("?")[0] == "/settings/voice-effects":
            import voice_effects
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                voice_effects.set_effect(
                    str(body.get("personaId") or ""), str(body.get("effect") or ""))
            except Exception:
                pass
            return self._json({"ok": True, "effects": voice_effects.read()})
        self.do_GET()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        # The panel's live preview. Answered through the REAL look_payload so
        # the stub cannot drift from the thing it is standing in for — which
        # is the same reason /settings below is built from the real schema.
        if path == "/live/preview":
            from api.live import look_payload
            try:
                n = int(self.headers.get("Content-Length") or 0)
                patch = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                patch = {}
            cfg = dict(settings_store.load())
            cfg.update({k: v for k, v in patch.items()
                        if k in settings_store.FIELDS})
            return self._json(look_payload(cfg, "Francesca"))

        if path == "/settings":
            # Same content negotiation as the real handler: a browser
            # navigation gets the page, the panel's fetch gets the JSON.
            if "text/html" in (self.headers.get("Accept") or ""):
                html = (WIDGET / "panel.html").read_text(encoding="utf-8").replace(
                    LIVEKIT_TAG, "<script>window.LivekitClient = {};</script>")
                return self._send(200, html, "text/html")
            return self._json({
                "schema": settings_store.schema_payload(),
                "resolved": settings_store.load(),
                "overrides": settings_store.stored_only(),
                # Through the real status() shape, group and help included,
                # so the per-section key blocks render the way they deploy.
                "secrets": {
                    f: {"label": secrets_store.SECRET_LABELS.get(f, f),
                        "group": secrets_store.SECRET_GROUPS.get(f, "brains"),
                        "help": secrets_store.SECRET_HELP.get(f, ""),
                        "set": f == "google_api_key",
                        "source": "settings" if f == "google_api_key" else "unset",
                        "hint": "•" * 12 if f == "google_api_key" else "",
                        "visible": False}
                    for f in secrets_store.SECRET_FIELDS
                },
                "authConfigured": True,
                "guestConfigured": True,
            })
        if path == "/settings/options":
            return self._json(OPTIONS)
        if path == "/settings/sounds":
            # Fixture uploads, REAL bundled library — the board and the
            # dropdowns render the same shelf the deployed panel would.
            import sounds as sound_assets
            return self._json({"sounds": ["my-ring.mp3", "old-bell.wav"],
                               "prefix": "upload:",
                               "library": sound_assets.library(),
                               "uploads": [
                                   {"name": "my-ring.mp3", "secs": None,
                                    "category": "upload", "url": "/sounds/my-ring.mp3"},
                                   {"name": "old-bell.wav", "secs": 2.1,
                                    "category": "upload", "url": "/sounds/old-bell.wav"},
                               ]})
        if path == "/voicemail/status":
            return self._json({"personas": [
                {"id": "p_default1", "name": "Rosie", "staged": True,
                 "current": True, "renderedAt": "2026-08-07 11:00:00",
                 "voice": "-Cliff1", "overridden": False,
                 "text": "You've reached Yosemite FM. Rosie is on the air "
                         "right now — leave a request after the beep."},
                {"id": "p_e28f6a", "name": "Dawn", "staged": True,
                 "current": False, "renderedAt": "2026-08-01 09:00:00",
                 "voice": "Lily", "overridden": True,
                 "text": "Dawn here — say your piece after the tone."},
                {"id": "_station", "name": "The station (no DJ live)",
                 "staged": False, "current": False, "renderedAt": "",
                 "voice": "-Cliff1", "overridden": False,
                 "text": "You've reached Yosemite FM. Leave a request "
                         "after the beep."},
            ], "messages": 2})
        if path.startswith("/voicemail/greeting/"):
            return self._json({"ok": True})
        if path == "/voicemail/stage":
            return self._json({"ok": False, "results": [
                {"id": "p_default1", "name": "Rosie", "ok": True},
                {"id": "p_e28f6a", "name": "Dawn", "ok": False,
                 "error": "voice 'zmcVlqmyk3' not on this backend"},
            ]})
        if path == "/voicemail/messages":
            return self._json({"messages": [
                {"at": "2026-08-07 02:14:11", "text": "Play some Bowie for "
                 "the night shift", "dj": "Danny Boy", "delivered": "hold"},
                {"at": "2026-08-07 02:31:47", "text": "Tell Murph the darts "
                 "are on Saturday", "dj": "Danny Boy", "delivered": "request",
                 "note": "queued third"},
            ]})
        if path == "/sound-packs":
            return self._json({"packs": [
                {"id": "classic", "label": "Exchange", "assets": {}},
                {"id": "phone", "label": "Handset", "assets": {}},
            ]})
        if path == "/logs":
            return self._json({
                "records": LOG_RECORDS,
                "lines": [f"{r['t']} {r['level']} {r['logger']}: {r['msg']}"
                          for r in LOG_RECORDS],
                "levels": sorted({r["level"] for r in LOG_RECORDS}),
                "sources": sorted({r["logger"] for r in LOG_RECORDS}),
            })
        # The pipeline check. Answered from fixtures because it is the panel's
        # slowest surface by far — a real run is a station read, a LiveKit
        # round trip, an LLM call and a TTS call, none of which exist here.
        if path == "/test/env":
            return self._json(dict(PIPELINE_ENV))
        if path == "/test/station":
            return self._json({"ok": True, "liveDj": "Francesca", "toolCount": 9})
        # Registration only ever proved the station accepted a row; this is the
        # station pushing back at us, which is the half that fails in the wild.
        if path == "/hooks/test":
            return self._json(dict(HOOK_TEST))
        if path == "/settings/voice-effects":
            import voice_effects
            return self._json({"effects": voice_effects.read()})
        if path == "/health":
            return self._json({"ok": True, "version": "dev", "livekit": "ws://stub"})
        if path == "/live":
            return self._json({
                "reachable": True, "onAir": True, "guestRequired": False,
                # From the stub's own settings, like the real /live — the
                # closed-line states can't be driven in a browser otherwise.
                "callsPaused": bool(settings_store.load().get("calls_paused")),
                "degraded": False,
                "secureOrigin": "", "theme": "auto",
                "name": "Francesca", "show": "The Piazza · Golden-era pop",
                "tagline": "Velvet Harmonies & Mediterranean Dreams.",
                "track": "I Want You — The Cadets",
                # Everything switched on, so the ask list is at its tallest —
                # which is the case the overlay exists for.
                "canAsk": {"allow_requests": True, "allow_library_search": True,
                           "allow_exact_queue": True, "allow_announcements": True,
                           "allow_skills": True},
                # Like the real /live: _for_this_caller stamps who is asking,
                # and the card's "?" popup shows whose menu it is.
                "callerTier": "admin",
                # Resolved by the real code, like the preview above — a stub
                # whose card disagrees with the card is not a stub of it.
                **__import__("api.live", fromlist=["live"]).look_payload(
                    settings_store.load(), "Francesca"),
                "limits": {"maxCallSeconds": 480, "idlePromptSecs": 20},
                "stream": {"url": "", "alternates": [], "tuneIn": False, "volume": 10},
                # A show palette, so the theme cycle's third stop exists here.
                "stationTheme": {"mode": "dark", "tokens": {
                    "--bg": "#1a2320", "--card": "#22302a", "--ink": "#e8efe9",
                    "--muted": "#9fb3a8", "--line": "#33443c",
                    "--accent": "#d9a441", "--accent-ink": "#141a17"}},
            })

        # Same two extensionless routes token_server serves. /panel is the
        # operator's page since 0.9.105; without it here, driving the panel in
        # a browser silently tests a 404.
        if path == "/panel":
            # Mirrors the real server since 0.9.151: one address, /settings.
            self.send_response(302)
            self.send_header("Location", "/settings")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        name = {"/": "index.html", "/settings": "panel.html"}.get(path, path.lstrip("/"))
        f = WIDGET / name
        if not f.is_file():
            return self._send(404, "not found", "text/plain")
        if f.suffix == ".html":
            html = f.read_text(encoding="utf-8").replace(
                LIVEKIT_TAG, "<script>window.LivekitClient = {};</script>")
            return self._send(200, html, "text/html")
        ctype = {"html": "text/html", "js": "text/javascript",
                 "css": "text/css", "png": "image/png", "json": "application/json",
                 "webmanifest": "application/manifest+json",
                 }.get(f.suffix.lstrip("."), "application/octet-stream")
        return self._send(200, f.read_bytes(), ctype)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))


if __name__ == "__main__":
    print(f"panel dev server on http://127.0.0.1:{PORT}  (settings in {_TMP})",
          flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
