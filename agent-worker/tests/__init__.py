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

# And the aired-line ledger (station.note_aired_by_us), on disk since the
# 2026-11-15 review. Every test that drives dj_say feeds it, and none of them
# inherit a redirect for it — so without this floor the suite would write
# hashes of fixture lines into the developer's real data/aired-by-us.json,
# and a real call's briefing would go looking through them.
os.environ.setdefault(
    "AIRED_BY_US_PATH",
    os.path.join(_tempfile.mkdtemp(prefix="callin-test-aired-"),
                 "aired-by-us.json"))

# And the day log (call/daylog.py), which later callers have READ BACK to them
# by subwave_booth_log. Some tests redirect it for themselves and some never
# did: the developer's data/day-log.json held sixteen "play some Bowie" rows
# and six "a voicemail song request" rows that no caller ever made, and two
# runs of the suite inside the deployed worker put two of the latter into the
# LIVE station's log, where the next sweep's DJ read them out as real
# (2026-11-16). A floor, so no test can reach the real file whatever it
# forgets; setdefault, so a test's own redirect still wins.
os.environ.setdefault(
    "DAYLOG_PATH",
    os.path.join(_tempfile.mkdtemp(prefix="callin-test-daylog-"),
                 "day-log.json"))
