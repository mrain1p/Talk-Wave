"""Boot the token server for the repo's preview browser, with the guardrail
a dev boot needs.

The preview launcher (.claude/launch.json) cannot set environment variables,
and a bare `python token_server.py` beside a real deployment does one quiet,
destructive thing: it claims the station's webhook row (one row per id,
registration is an upsert), re-pointing the LIVE instance's pushes at this
machine. Measured 2026-08-18 — the operator's deployment lost its voice.*
pushes to a widget-check boot and its ducking degraded with nothing logged.

setdefault, so exporting CALLIN_HOOK_REGISTER=1 first still wins when the
webhook code itself is what you are testing.
"""

import os
import runpy
import sys
from pathlib import Path

os.environ.setdefault("CALLIN_HOOK_REGISTER", "0")

worker = Path(__file__).resolve().parent.parent / "agent-worker"
sys.path.insert(0, str(worker))
os.chdir(worker)
runpy.run_path(str(worker / "token_server.py"), run_name="__main__")
