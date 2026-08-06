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
