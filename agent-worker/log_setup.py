"""
One logging setup for both processes: console (as before) plus a rotating
file per process under data/logs/, with timestamps.

Why: locally the stack runs as console windows, and closing one (or a crash)
threw the only copy of the logs away — reviewing yesterday's bad call was
impossible. Files rotate so they can be left on forever, and each process
writes its own file because two Windows processes rotating one file fight
over the lock.

Env knobs:
    LOG_LEVEL    level for both sinks (default INFO)
    LOG_DIR      where files go (default <repo>/data/logs)
    LOG_TO_FILE  set to 0/false to go console-only (e.g. under docker,
                 where stdout is already captured)
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Last few hundred formatted lines, kept in memory regardless of file/stdout
# settings — this is what the settings panel's log viewer reads, so an
# operator can check "what just happened" without docker access.
from collections import deque

RECENT: deque = deque(maxlen=500)


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            RECENT.append(self.format(record))
        except Exception:
            pass


def recent_lines(n: int = 300) -> list[str]:
    return list(RECENT)[-n:]


def setup(process_name: str, console: bool = True) -> None:
    """Call once at process start, before any logging happens.

    Idempotent: the token server's test endpoints import main.py, whose
    module-level setup("worker") call would otherwise add a second handler
    and double every subsequent log line.

    `console=False` for the worker. livekit-agents' cli.run_app calls its own
    setup_logging(), which unconditionally adds a StreamHandler to the ROOT
    logger — so a console handler of ours is a second sink for the same
    records, and every line appeared twice in `docker logs` (once plain, once
    as livekit's JSON). We keep the ring buffer and the file, and let livekit
    own stdout in the process it controls.
    """
    root = logging.getLogger()
    if getattr(root, "_callin_log_setup", False):
        return
    root._callin_log_setup = True

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root.setLevel(level)

    if console:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)

    ring = _RingHandler()
    ring.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(ring)

    # Third-party chatter drowns the lines that matter. httpx logs every
    # single station request at INFO (the keep-warm loop alone is dozens a
    # minute), and the access log repeats the widget's 20-second /live poll
    # forever. Real events — calls, settings changes, failures — stay.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    class _DropPollNoise(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            return "GET /live " not in msg and "GET /health " not in msg

    logging.getLogger("aiohttp.access").addFilter(_DropPollNoise())

    if os.environ.get("LOG_TO_FILE", "1").strip().lower() in ("0", "false", "no"):
        return

    # A blank LOG_DIR= in .env arrives as empty string, not unset.
    log_dir = Path(
        os.environ.get("LOG_DIR") or Path(__file__).parent.parent / "data" / "logs"
    )
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / f"{process_name}.log",
            maxBytes=2_000_000, backupCount=3, encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(file_handler)
    except OSError as e:
        # A read-only volume must not stop the app — console still works.
        root.warning("file logging disabled (%s)", e)
