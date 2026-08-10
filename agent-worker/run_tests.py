#!/usr/bin/env python
"""Run the whole suite in PARALLEL, one process per test module.

`python -m unittest test_sidecar` runs 700+ tests in one process, serially —
~2 minutes, paid on every commit (the pre-commit hook) and every CI run. The
tests are already split into tests/test_*.py by subject, and nothing crosses
between them at runtime, so they parallelise cleanly: this runs each module in
its own process at once and the wall time collapses to roughly the slowest
single module.

Coverage is identical to test_sidecar: TestEveryTestClassIsAggregated pins
that the aggregator names every class under tests/, so running the modules
directly can neither miss nor add a test. This is a FASTER WAY to run the same
suite, not a different suite.

    python run_tests.py            # all modules, in parallel
    python run_tests.py -v         # per-module PASS/FAIL as they finish

Stdlib only (subprocess, concurrent.futures, tempfile) — the venv still needs
nothing new. Each worker gets its OWN temp dir for the writable-path env vars,
so parallel modules can never scribble on each other's settings/secrets/auth.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _modules() -> list[str]:
    """Every test module, as a dotted name. Discovered, not listed — a new
    tests/test_x.py is picked up the moment it lands, the same property the
    aggregator's own guard relies on."""
    return sorted(
        f"tests.{p.stem}"
        for p in (HERE / "tests").glob("test_*.py")
    )


def _run_one(module: str) -> tuple[str, int, str]:
    """One module in its own process, with private writable paths so parallel
    workers never collide on /tmp/settings.json and friends."""
    with tempfile.TemporaryDirectory(prefix="wt-test-") as tmp:
        env = dict(os.environ)
        env.update(
            LOG_TO_FILE="0",
            SETTINGS_PATH=str(Path(tmp) / "settings.json"),
            SECRETS_PATH=str(Path(tmp) / "secrets.json"),
            ADMIN_AUTH_PATH=str(Path(tmp) / "auth.json"),
            CALLS_PATH=str(Path(tmp) / "calls"),
        )
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", module],
            cwd=str(HERE), env=env, capture_output=True, text=True,
        )
        # unittest writes its summary to stderr.
        return module, proc.returncode, (proc.stderr or "") + (proc.stdout or "")


def main() -> int:
    verbose = "-v" in sys.argv[1:]
    modules = _modules()
    started = time.monotonic()
    failures: list[tuple[str, str]] = []

    # One worker per CPU is the right size: each module is CPU-bound Python in
    # its own interpreter, and the network is never touched.
    workers = min(len(modules), (os.cpu_count() or 4))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for module, code, output in pool.map(_run_one, modules):
            ok = code == 0
            if verbose:
                print(f"  {'ok  ' if ok else 'FAIL'}  {module}")
            if not ok:
                failures.append((module, output))

    elapsed = time.monotonic() - started
    if failures:
        for module, output in failures:
            print(f"\n===== {module} FAILED =====\n{output.strip()}")
        print(f"\n{len(failures)} of {len(modules)} modules failed "
              f"in {elapsed:.0f}s.")
        return 1
    print(f"\nAll {len(modules)} test modules passed in {elapsed:.0f}s "
          f"({workers} workers).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
