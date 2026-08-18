"""The test suite, split by subject.

One file per subject rather than one file for everything: test_sidecar.py had
reached 6,169 lines and 94 classes appended chronologically, so finding what
covered a thing meant reading all of it. The directory listing is now the map,
and it cannot go stale the way a written index would.

`python -m unittest test_sidecar` is still the one command — test_sidecar.py
imports every class from here, so CI, the pre-commit hook and every existing
instruction keep working unchanged.

LOG_TO_FILE is set HERE, before anything else in the package is imported,
because a package's __init__ runs ahead of its submodules and several of them
import modules that call log_setup.setup() at import time. Set it any later and
the run pollutes the real data/logs/worker.log.
"""

import os

os.environ["LOG_TO_FILE"] = "0"

# The push file (hook-air.json) gets the same treatment, for the same reason,
# and it must be process-wide rather than per-_TempStores class: the air
# guard's tests read it through call/air_verdict._air_path, and the ones that
# never touch settings never inherited the redirect — so a REAL push file in
# data/ made six of them flip. Not hypothetical: booting the local token
# server for a widget check registers this machine with the live station,
# and one real voice.start later the suite read "the station is speaking
# right now" out of the repo's own data directory (2026-08-18, an afternoon
# of six confusing failures). setdefault, so an explicit path from outside
# still wins.
import tempfile as _tempfile

os.environ.setdefault(
    "CALLIN_HOOK_AIR_PATH",
    os.path.join(_tempfile.mkdtemp(prefix="callin-test-air-"),
                 "hook-air.json"))
