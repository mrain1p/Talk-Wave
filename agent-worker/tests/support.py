"""Shared fixtures. Anything more than one subject needs lives here.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
import secrets_store
import settings as settings_store


# Where things are, resolved once. These used to be spelled
# `Path(__file__).parent.parent` inside test_sidecar.py, which sat in
# agent-worker/ and so meant the repo root. From inside tests/ that same
# expression is one directory short, and every test that reads README.md, the
# widget or .claude/ would have looked in the wrong place while still passing
# its own scan-found-something guard.
AGENT_WORKER = Path(__file__).resolve().parent.parent
REPO = AGENT_WORKER.parent

class _TempStores(unittest.TestCase):
    """Points the settings/secrets stores at temp files and scrubs the env
    vars the tests touch, restoring everything afterwards."""

    ENV_VARS = (
        "STT_MODEL", "DEEPGRAM_MODEL", "STT_PROVIDER", "LLM_PROVIDER",
        "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "TTS_MODE",
        # Set by the key-withholding tests; restored so a real key in the
        # developer's environment survives the run.
        "OPENROUTER_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY", "TTS_API_KEY",
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._old_settings_path = settings_store.SETTINGS_PATH
        self._old_secrets_path = secrets_store.SECRETS_PATH
        settings_store.SETTINGS_PATH = tmp / "settings.json"
        secrets_store.SECRETS_PATH = tmp / "secrets.json"
        # The per-DJ effects store reads its env per call, so pointing the
        # var here covers every inheriting test — a future test that saves
        # an effect must land in this temp dir, never in real data/.
        self._old_vfx = os.environ.get("VOICE_FX_PATH")
        os.environ["VOICE_FX_PATH"] = str(tmp / "voice-effects.json")
        # The webhook secret reads its path per call, like the effects store.
        # Redirected for every inheriting test so a receiver test can never
        # load — or worse, rotate — the real deployment's secret in data/.
        self._old_hook_secret = os.environ.get("CALLIN_HOOK_SECRET_PATH")
        os.environ["CALLIN_HOOK_SECRET_PATH"] = str(tmp / "hook-secret.json")
        # The listener buffer is the newest writable path; redirected here so
        # no inheriting test can touch real data/listeners.json (the sprint
        # review caught it unprotected).
        from api import stats as _stats
        self._old_listeners_path = _stats.LISTENERS_PATH
        self._old_listeners = (_stats._samples, _stats._loaded)
        _stats.LISTENERS_PATH = tmp / "listeners.json"
        _stats._samples, _stats._loaded = [], False
        # The mint-time snapshot prefetch writes into data/ too; redirected so
        # a mint or prepare test can never leave a head start lying around for
        # the developer's next real call to pick up.
        import station_prefetch as _prefetch
        self._old_prefetch_path = _prefetch.PATH
        _prefetch.PATH = tmp / "station-prefetch.json"
        self._old_env = {k: os.environ.get(k) for k in self.ENV_VARS}
        for k in self.ENV_VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        settings_store.SETTINGS_PATH = self._old_settings_path
        secrets_store.SECRETS_PATH = self._old_secrets_path
        from api import stats as _stats
        _stats.LISTENERS_PATH = self._old_listeners_path
        _stats._samples, _stats._loaded = self._old_listeners
        import station_prefetch as _prefetch
        _prefetch.PATH = self._old_prefetch_path
        if self._old_vfx is None:
            os.environ.pop("VOICE_FX_PATH", None)
        else:
            os.environ["VOICE_FX_PATH"] = self._old_vfx
        if self._old_hook_secret is None:
            os.environ.pop("CALLIN_HOOK_SECRET_PATH", None)
        else:
            os.environ["CALLIN_HOOK_SECRET_PATH"] = self._old_hook_secret
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


class _FakeRequest:
    """Just enough of an aiohttp request for _caller_key/_check_usage."""

    def __init__(self, ip="1.2.3.4", fwd=""):
        self.headers = {"X-Forwarded-For": fwd} if fwd else {}
        self.remote = ip


def widget_js(exclude=("embed.js", "sw.js")) -> dict:
    """Every JS file the widget's own pages load, by filename.

    Discovered rather than listed, because the contract tests below used to
    name `app.js` directly and that file has since been split into shared.js,
    call.js and panel.js. A named file silently stops covering the code that
    moved out of it; a glob picks the new one up the moment it lands.

    Two are excluded, and both for the same reason — no page loads them with a
    `<script src>`, so the per-page contract checks have nothing to check them
    against. embed.js is the third-party drop-in, running on somebody else's
    page against markup that is not in this repo. sw.js is the service worker,
    fetched by `navigator.serviceWorker.register()` rather than by a script
    tag; it has no DOM at all, and its own contract (which paths it must never
    answer for) is guarded by TestTheServiceWorkerStaysOutOfTheWay.
    """
    d = REPO / "web-widget"
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(d.glob("*.js")) if p.name not in exclude}
