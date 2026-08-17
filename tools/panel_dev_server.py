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
preview_start — see the talkwave-verify skill, which has the traps.

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
_TMP = Path(tempfile.mkdtemp(prefix="talkwave-panel-"))
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
    "modelsFromEndpoint": "",
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
    "webhook": {"registered": True, "id": "talk_wave", "received": 4,
                "url": "http://192.168.1.40:8100/hooks/station",
                "detail": "registered"},
    "listeners": {"requestsOpen": True, "detail": "2 listening"},
    "keys": {"ok": True, "missing": []},
    "stt": {"ok": True, "detail": "local · base.en"},
    "llm": {"ok": True, "detail": "google · gemini-3.1-flash-lite"},
    "tts": {"ok": True, "detail": "local · -Cliff1"},
}

# The model check, with the numbers its verdict is graded against. 6185ms is a
# real reading from a tester's ollama/qwen2.5:7b (2026-08-13), the one the panel
# used to call "the call will lag" while every turn on that box was in fact
# timing out — so the fixture ships in the state that was reported wrong.
LLM_TEST = {
    "ok": True, "provider": "ollama", "model": "qwen2.5:7b",
    "firstTokenMs": 6185, "totalMs": 8102, "toolCalling": True,
    "parallelTools": True, "followUp": "ok", "followUpError": "",
    "reply": "Dreams by Fleetwood Mac, coming up.",
    "measuredWith": "measured with the DJ's real prompt and 17 station tool(s)",
    "promptChars": 7400, "toolCount": 19, "desiredMs": 1500, "budgetMs": 30000,
}

TTS_TEST = {
    "ok": True, "voice": "-Cliff1", "firstAudioMs": 420, "realtimeFactor": 0.31,
}

# Flip `ok` to see the other branch: a station that took the registration but
# cannot route back to the receiver, which from the panel looks identical to
# working until something asks.
HOOK_TEST = {
    "ok": True, "fired": True,
    "url": "http://192.168.1.40:8100/hooks/station",
    "detail": "the station's push reached http://192.168.1.40:8100/hooks/station",
}

# Enough spread — kinds, tiers, tools, ratings, verdicts, DAYS — that every
# calls-toolbar filter has at least two answers, stacked filters leave a
# checkable remainder, and the ACTIVITY charts get a week of buckets with a
# DJ first-word on the live calls (time-to-first-word needs a dj turn).
# Timestamps are minted relative to now so the strip never renders empty
# just because the fixture aged.
def _make_calls():
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc).astimezone()
    iso = lambda d, secs=0: (d + _dt.timedelta(seconds=secs)).isoformat(timespec="seconds")
    ago = lambda days, hours=0: now - _dt.timedelta(days=days, hours=hours)
    rows = [
        ("c1", "call", 0, 2, dict(rating="down", tier="open", ttfw=2,
         tools=[("subwave_search_library", "3 result(s)")],
         problems=["station read timed out"])),
        ("c2", "call", 0, 3, dict(tier="open", silent=True,
         problems=["no caller audio"])),
        ("c3", "chat", 1, 2, dict(tier="admin",
         tools=[("subwave_takeover_show", "pinned")])),
        ("c4", "voicemail", 2, 4, dict(tier="guest")),
        ("c5", "call", 3, 1, dict(rating="up", tier="guest", ttfw=6,
         tools=[("subwave_request_song", "queued")])),
        ("c6", "chat", 4, 5, dict(rating="down", tier="open")),
        ("c7", "call", 5, 2, dict(tier="open", ttfw=3)),
        ("c8", "call", 6, 6, dict(tier="guest", ttfw=2)),
    ]
    out = []
    for cid, kind, days, hours, d in rows:
        start = ago(days, hours)
        silent = d.get("silent")
        turns = [] if silent else [
            {"t": iso(start, 1), "who": "caller", "text": "hi there"}]
        if not silent and kind == "call":
            turns.append({"t": iso(start, d.get("ttfw", 3)),
                          "who": "dj", "text": "you're on the air"})
        rec_first_word = (None if silent or kind != "call"
                          else iso(start, d.get("ttfw", 3)))
        rec = {"id": cid, "room": "r-" + cid, "kind": kind,
               "startedAt": iso(start), "durationSecs": 60,
               "callerTurns": 0 if silent else 2,
               "persona": {"name": "Francesca"},
               "config": {"llm": "x", "callerTier": d.get("tier", "open")},
               "tools": [{"t": iso(start, 9), "name": n, "result": r}
                         for n, r in d.get("tools", [])],
               "turns": turns,
               "problems": [{"what": p} for p in d.get("problems", [])]}
        if rec_first_word:
            rec["firstWordAt"] = rec_first_word
        if d.get("rating"):
            rec["rating"] = d["rating"]
        out.append(rec)
    return out


CALLS = _make_calls()


# Three days of listener samples at 10-minute steps, with a deliberate
# gap yesterday afternoon — the chart must show a broken line there, and
# a stub that never exercises the gap path would hide a regression in it.
def _make_listeners():
    import math
    import time as _time

    now = int(_time.time())
    out = []
    for i in range(3 * 144):                 # 3 days × 144 ten-minute steps
        t = now - (3 * 144 - i) * 600
        hours_ago = (now - t) / 3600
        if 20 <= hours_ago <= 26:            # the gap: sampler saw no answer
            continue
        day_phase = ((t % 86400) / 86400) * 2 * math.pi
        out.append({"t": t, "n": max(0, round(7 + 6 * math.sin(day_phase)))})
    return out


LISTENERS = _make_listeners()

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

_STREAM_WAV: bytes | None = None


def _stream_wav() -> bytes:
    """Six seconds of a quiet 220Hz tone, built once, stdlib only."""
    global _STREAM_WAV
    if _STREAM_WAV is None:
        import io
        import math
        import struct
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(b"".join(
                struct.pack("<h", int(6000 * math.sin(2 * math.pi * 220 * t / 22050)))
                for t in range(22050 * 6)))
        _STREAM_WAV = buf.getvalue()
    return _STREAM_WAV


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
        # The player's listener actions, from fixtures — enough to drive the
        # heart filling and the SENT beat without a station.
        if self.path.split("?")[0] == "/player/like":
            return self._json({"ok": True, "liked": True, "count": 4})
        if self.path.split("?")[0] == "/player/request":
            return self._json({"success": True, "message": "Sent to the booth"})
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
        # The operator's own verdict on a call. Lands in the in-memory CALLS
        # fixture so the mark can be driven end to end here — pressed, cleared,
        # and read back by the thumbs filters — without a real transcript
        # directory to write into.
        if self.path.split("?")[0].endswith("/mark"):
            rid = self.path.split("?")[0].split("/")[2]
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            mark = str(body.get("mark") or "")
            for c in CALLS:
                if c.get("id") == rid:
                    if mark:
                        c["opRating"] = mark
                    else:
                        c.pop("opRating", None)
                    return self._json({"ok": True, "mark": mark})
            return self._send(404, json.dumps({"error": "no such call record"}),
                              "application/json")
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
                # What a cleared field falls back to. The panel labels its own
                # blank option from this, so a stub without it renders every
                # dropdown as "Not set" and cannot show the bug it exists for.
                "beneath": settings_store.beneath(),
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
            # A previewed llm_base_url answers the way the real handler does:
            # the endpoint's own list replaces the provider's catalogue and
            # modelsFromEndpoint says so — so the panel's "read from your
            # endpoint" path can be driven here without a live server.
            from urllib.parse import parse_qs, urlparse

            q = parse_qs(urlparse(self.path).query)
            if q.get("llm_base_url", [""])[0].strip():
                cfg = settings_store.load()
                p = str(cfg.get("llm_provider") or "google").lower()
                echoed = dict(OPTIONS)
                echoed["llmModels"] = dict(OPTIONS["llmModels"])
                echoed["llmModels"][p] = ["llama-3.1-8b-q6", "qwen3-30b"]
                echoed["modelsDiscovered"] = dict(OPTIONS["modelsDiscovered"])
                echoed["modelsDiscovered"][p] = True
                echoed["modelsFromEndpoint"] = p
                return self._json(echoed)
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
                                    "category": "custom", "url": "/sounds/my-ring.mp3"},
                                   {"name": "old-bell.wav", "secs": 2.1,
                                    "category": "custom", "url": "/sounds/old-bell.wav"},
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
        if path == "/calls":
            return self._json({"calls": CALLS})
        # The webhook health the dashboard reads on load. Fixed as the FAULT
        # (rejections, nothing accepted), because the healthy shape shows
        # nothing and a stub that only reproduces the invisible state cannot
        # be used to look at the row that reports it.
        if path == "/hooks/recent":
            return self._json({"registered": {
                "registered": True, "id": "talk_wave",
                "url": "http://192.0.2.7:8100/hooks/station",
                "station": "http://192.0.2.7:7700/api",
                "events": ["voice.queued", "voice.start", "voice.end"],
                "received": 0, "rejected": 14, "detail": "registered",
            }, "events": []})
        if path == "/stats/listeners":
            return self._json({"samples": LISTENERS, "intervalSecs": 600})
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
        # The two stages that were NOT stubbed, so the pipeline check could
        # never be read end to end here — and they are the two whose verdicts
        # have bands rather than a yes/no. Edit LLM_TEST's firstTokenMs to see
        # each: under desiredMs passes, over it warns, at or over budgetMs
        # fails outright.
        if path == "/test/llm":
            return self._json(dict(LLM_TEST))
        if path == "/test/tts":
            return self._json(dict(TTS_TEST))
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
                # The structured record the player sheet renders. The art
                # points at a file this origin actually serves, so the art
                # path is exercised for real.
                "nowPlaying": {
                    "title": "I Want You", "artist": "The Cadets",
                    "album": "Parisian Café", "year": 2009,
                    "genres": ["Contemporary Jazz", "Jazz"],
                    "bpm": 92.3, "key": "5A",
                    "moods": ["Reflective", "Calm"],
                    "art": "/icon-512.png",
                },
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
                # A stream the sandbox can actually play: the stub's own
                # /stream WAV. Real deployments carry the station's mount
                # here; what the card needs from the fixture is only that a
                # URL resolved and audio comes out when asked.
                "stream": {"url": "/stream", "alternates": [],
                           "tuneIn": False, "volume": 10},
                # Like callsPaused above: from the stub's own settings, so
                # ticking the box in the panel offers the player on the card.
                "swipePlayer": bool(settings_store.load().get("swipe_player")),
                "playerStart": bool(settings_store.load().get("start_on_player")),
                # One queued record and a weather line, so the player's
                # panels and header exercise their filled states.
                "upNext": [
                    {"title": "Esta noche", "artist": "Federico Aubele",
                     "requestedBy": "Marco"},
                    {"title": "Final Transmission", "artist": "All India Radio",
                     "requestedBy": None},
                    {"title": "Weightless Part 5", "artist": "Marconi Union",
                     "requestedBy": "Ana"},
                    {"title": "Otherworlds", "artist": "Abakus",
                     "requestedBy": None},
                ],
                # The studio's door, so the machine flow can be driven here:
                # the button appears, and it opens the browser studio.
                "voicemailWhen": "always",
                "voicemailFlow": "studio",
                "playerDuck": 10,
                "booth": {"text": "This one's for anyone still up with the "
                                  "windows open — Beegie Adair, gentle as "
                                  "ever, and the rain agrees.",
                          "kind": "track-intro"},
                "weather": "cloudy 70°F",
                # A show palette, so the theme cycle's third stop exists here.
                "stationTheme": {"mode": "dark", "tokens": {
                    "--bg": "#1a2320", "--card": "#22302a", "--ink": "#e8efe9",
                    "--muted": "#9fb3a8", "--line": "#33443c",
                    "--accent": "#d9a441", "--accent-ink": "#141a17"}},
            })

        # A playable stand-in for the station stream: six seconds of a soft
        # tone, synthesized on first ask. Enough for the card's player to
        # reach `playing` for real — the sandbox has no station to pull.
        if path == "/stream":
            return self._send(200, _stream_wav(), "audio/wav")

        # The heart's current state, from a fixture.
        if path == "/player/like":
            return self._json({"enabled": True, "songId": "s1",
                               "liked": False, "count": 3})

        # Same two extensionless routes token_server serves — /settings is
        # the panel's one address; the old /panel 404s here like it does on
        # the real server since 0.10.8.
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
