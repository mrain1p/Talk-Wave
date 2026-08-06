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
os.environ.setdefault("LOG_TO_FILE", "0")
(_TMP / "settings.json").write_text(json.dumps({
    "llm_provider": "google", "llm_model": "gemini-3.1-flash-lite",
    "tts_mode": "local", "front_access": "guest",
}), encoding="utf-8")

sys.path.insert(0, str(ROOT / "agent-worker"))
import settings as settings_store  # noqa: E402

PORT = int(os.environ.get("PORT", "8123"))

# The slow half of the panel, answered instantly. Shapes match what
# handle_settings_options really returns; the values are fixtures.
OPTIONS = {
    "llmProviders": ["openai", "google", "anthropic", "openrouter", "ollama"],
    "llmModels": {"google": ["gemini-3.1-flash-lite", "gemini-3.6-flash"],
                  "openai": ["gpt-4.1-mini"]},
    "modelsDiscovered": {"google": True},
    "sttProviders": ["local", "deepgram", "openai", "google"],
    "sttModels": {"local": ["base.en", "tiny.en"], "deepgram": ["nova-3"]},
    "ttsModes": ["cloud", "local"],
    "ttsAdapters": ["local-vibevoice.json", "openai-cloud.json"],
    # Deliberately does NOT include the station's voice for p_default1 below:
    # that mismatch is the 0.9.81 bug, and it should be reproducible here.
    "voices": ["-Cliff1", "-Delia1", "Lily"],
    "personas": [{"id": "p_default1", "name": "Rosie"},
                 {"id": "p_e28f6a", "name": "Dawn"}],
    "voiceSource": {"adminConfigured": True, "mirroringStation": True, "count": 18},
    "stationLlm": {"model": "gemini-3.1-flash-lite"},
    "providerBaseUrls": settings_store.PROVIDER_BASE_URLS,
    "ttsBaseUrls": settings_store.TTS_BASE_URLS,
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
        self.do_GET()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        if path == "/settings":
            return self._json({
                "schema": settings_store.schema_payload(),
                "resolved": settings_store.load(),
                "overrides": settings_store.stored_only(),
                "secrets": {"google_api_key":
                            {"label": "Google", "set": True, "tail": "1234"}},
                "authConfigured": True,
                "guestConfigured": True,
            })
        if path == "/settings/options":
            return self._json(OPTIONS)
        if path == "/settings/sounds":
            return self._json({"sounds": [], "prefix": "upload:"})
        if path == "/logs":
            return self._json({
                "records": LOG_RECORDS,
                "lines": [f"{r['t']} {r['level']} {r['logger']}: {r['msg']}"
                          for r in LOG_RECORDS],
                "levels": sorted({r["level"] for r in LOG_RECORDS}),
                "sources": sorted({r["logger"] for r in LOG_RECORDS}),
            })
        if path == "/health":
            return self._json({"ok": True, "version": "dev", "livekit": "ws://stub"})
        if path == "/live":
            return self._json({
                "reachable": True, "onAir": True, "guestRequired": False,
                "callsPaused": False, "degraded": False,
                "secureOrigin": "", "theme": "auto",
                "name": "Francesca", "show": "The Piazza · Golden-era pop",
                "tagline": "Velvet Harmonies & Mediterranean Dreams.",
                "track": "I Want You — The Cadets",
                # Everything switched on, so the ask list is at its tallest —
                # which is the case the overlay exists for.
                "canAsk": {"allow_requests": True, "allow_library_search": True,
                           "allow_exact_queue": True, "allow_announcements": True,
                           "allow_skills": True},
                "controls": {"help": True, "theme": True, "settings": True},
                "limits": {"maxCallSeconds": 480, "idlePromptSecs": 20},
                "stream": {"url": "", "alternates": [], "tuneIn": False, "volume": 10},
            })

        name = "index.html" if path == "/" else path.lstrip("/")
        f = WIDGET / name
        if not f.is_file():
            return self._send(404, "not found", "text/plain")
        if name == "index.html":
            html = f.read_text(encoding="utf-8").replace(
                LIVEKIT_TAG, "<script>window.LivekitClient = {};</script>")
            return self._send(200, html, "text/html")
        ctype = {"html": "text/html", "js": "text/javascript",
                 "css": "text/css"}.get(f.suffix.lstrip("."),
                                        "application/octet-stream")
        return self._send(200, f.read_bytes(), ctype)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))


if __name__ == "__main__":
    print(f"panel dev server on http://127.0.0.1:{PORT}  (settings in {_TMP})",
          flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
