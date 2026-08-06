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
        self._old_env = {k: os.environ.get(k) for k in self.ENV_VARS}
        for k in self.ENV_VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        settings_store.SETTINGS_PATH = self._old_settings_path
        secrets_store.SECRETS_PATH = self._old_secrets_path
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


def widget_js(exclude=("embed.js",)) -> dict:
    """Every JS file the widget's own pages load, by filename.

    Discovered rather than listed, because the contract tests below used to
    name `app.js` directly and that file has since been split into shared.js,
    call.js and panel.js. A named file silently stops covering the code that
    moved out of it; a glob picks the new one up the moment it lands.

    embed.js is excluded by default: it is the third-party drop-in, it fetches
    nothing and it reaches for no id in this repo's markup.
    """
    d = REPO / "web-widget"
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(d.glob("*.js")) if p.name not in exclude}
