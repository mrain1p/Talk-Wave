"""One on-disk JSON store idiom, written once.

Nine write blocks across eight modules under data/ had independently grown the
same shape: make the parent, write JSON through a neighbouring temp file, chmod
it (a Synology share hands new files mode 000 — root walks through it, a
non-root container cannot read past it), then os.replace onto the real name so a
concurrent reader never sees a half-written file. This is that shape, with the
two axes that genuinely differ between sites — the file mode and whether the
directory is chmod'd too — as parameters. Everything else is identical here.

Consolidated at Batch 6 (2026-08-29). Platform layer: imported downward by the
surfaces and the stores; imports nothing internal.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("callin.jsonstore")


def store_path(env_var: str, default) -> Path:
    """The store's path: the env override, else the built-in default. Accepts a
    str or Path default (os.environ.get returns it untouched when unset) and
    always returns a Path — the three lines every store opened with."""
    return Path(os.environ.get(env_var, default))


def write_atomic(path, data: Any, *, file_mode: int = 0o644,
                 dir_mode: int | None = None, indent: int | None = 1,
                 sort_keys: bool = False, ensure_ascii: bool = True) -> None:
    """Serialise `data` to `path` as JSON, atomically and mode-set.

    tmp is `<name>.tmp` alongside the target (so a `.json` store's temp is the
    `.json.tmp` the voicemail sweep already skips); json.dump into it;
    best-effort chmod the parent (when dir_mode given — the mode-000 fix) then
    the tmp; then replace. chmod is best-effort on purpose: Windows ACLs don't
    map and the mode tightens, never gates.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, sort_keys=sort_keys,
                  ensure_ascii=ensure_ascii)
    targets = ([(path.parent, dir_mode)] if dir_mode is not None else [])
    targets.append((tmp, file_mode))
    for target, mode in targets:
        try:
            os.chmod(target, mode)
        except OSError:
            pass
    tmp.replace(path)


def read_or(path, seed: Any = None, *, warn: bool = False):
    """Parsed JSON, or `seed` when the file is absent or corrupt. When `seed` is
    a dict or list a parsed value of the WRONG type collapses to it too (the
    `data if isinstance(...) else {}` guard every store wrote by hand). A caller
    that must tell absent from corrupt — admin_auth's fail-closed gate — reads
    the file itself and does NOT use this.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return seed
    except (OSError, ValueError) as e:          # ValueError covers JSONDecodeError
        if warn:
            log.warning("unreadable JSON store %s (%s) — using the seed", path, e)
        return seed
    if isinstance(seed, (dict, list)) and not isinstance(data, type(seed)):
        return seed
    return data
