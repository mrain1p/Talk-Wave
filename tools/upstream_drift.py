#!/usr/bin/env python3
"""Diff the vocabularies Talk Wave hand-mirrors against SUB/WAVE's own.

Three alignment passes have found the same disease: a constant copied out of
the station's source, and then the station moved. The 2026-08-14 pass found
`energy` being tested as a number when the station had always sent a word. The
2026-09-07 pass took seven agents and ~27 minutes to re-derive, by hand, what
this script prints in one call — and got the DeepSeek default wrong until the
seventh file came back.

    python tools/upstream_drift.py            # diff against the station's HEAD
    python tools/upstream_drift.py --record   # …and write the SHA it checked

Exit code is 1 when anything drifted, so CI or a cron can run it. It is NOT
part of the test suite: the suite is network-free by house rule, and a test
that fails because GitHub is slow teaches people to ignore failures.

Needs `gh` on PATH and read access to perminder-klair/subwave.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = "perminder-klair/subwave"
ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "agent-worker"
STAMP = ROOT / "tools" / "upstream-drift.json"

# Station files this reads, and why each one is here. Keeping the list beside
# the checks means a file that stops existing upstream is a loud failure rather
# than a silently empty comparison.
STATION_FILES = {
    "routes/dj.ts": "SAY_KINDS, the segment table, the skill catalogue",
    "routes/webhooks.ts": "the documented webhook payloads",
    "schemas/webhook.ts": "WEBHOOK_EVENTS",
    "schemas/schedule.ts": "OVERRIDE_MIN/MAX_MINUTES",
    "schemas/persona.ts": "TTS_ENGINES, the inherit sentinel",
    "settings/vocab.ts": "LLM_PROVIDERS",
    "mcp/tools.ts": "the MCP tool surface",
    "llm/internal/provider/registry.ts": "per-provider default model ids",
}


def sh(*args: str) -> str:
    out = subprocess.run(args, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}…: {out.stderr.strip()[:200]}")
    return out.stdout


def station_head() -> str:
    """The SHA this run compared against, so a later run can say what moved."""
    return json.loads(sh("gh", "api", f"repos/{REPO}/commits/main",
                         "--jq", "{sha: .sha, date: .commit.committer.date}"))


def fetch(path: str) -> str:
    """One station file at HEAD. Windows note: the content comes back base64
    precisely so the shell never has to carry UTF-8 through cp1252."""
    import base64

    raw = sh("gh", "api", f"repos/{REPO}/contents/controller/src/{path}",
             "--jq", ".content")
    return base64.b64decode(raw).decode("utf-8", "replace")


def worker(rel: str) -> str:
    return (WORKER / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------- extractors
# Each returns (station_value, talkwave_value). They are deliberately dumb
# regexes over source rather than a parse: the point is to notice a change, and
# a reader who sees "these two lists differ" goes and looks. A parser that
# silently returned [] on a syntax it did not expect would report "no drift".

def _strip_comments(text: str) -> str:
    """Line and block comments out. The station documents these lists inline
    and the prose is full of apostrophes, so a scan for quoted values reads
    "the station committed to speaking" as an event name."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in text.splitlines())


def _list_after(text: str, after: str) -> list[str]:
    """The quoted values of the array literal that follows `after`.

    Bounded by the closing bracket rather than by a character count — a window
    is a guess that goes wrong the moment somebody adds a line.
    """
    body = _strip_comments(text)
    i = body.find(after)
    if i < 0:
        return []
    j = body.find("]", i)
    return re.findall(r"'([^']+)'", body[i:j if j > i else i + 900])


def check_webhook_events(src: dict) -> tuple[list, list]:
    station = _list_after(src["schemas/webhook.ts"], "WEBHOOK_EVENTS = [")
    ours = re.search(r"WANTED_EVENTS\s*=\s*\((.*?)\)",
                     worker("api/hook_receiver.py"), re.S)
    mine = re.findall(r'"([a-z.]+)"', ours.group(1)) if ours else []
    return sorted(station), sorted(mine)


def check_say_kinds(src: dict) -> tuple[list, list]:
    station = _list_after(src["routes/dj.ts"], "const SAY_KINDS = [")
    # Ours is prose in the dj_say docstring, not a constant — so this looks for
    # the kinds being NAMED there. A kind the station drops would leave a
    # docstring promising something that is now silently coerced.
    doc = worker("station.py")
    mine = [k for k in station if f"'{k}'" in doc or f'"{k}"' in doc]
    return sorted(station), sorted(mine)


def check_takeover_bounds(src: dict) -> tuple[list, list]:
    s = src["schemas/schedule.ts"]
    station = [re.search(r"OVERRIDE_MIN_MINUTES\s*=\s*(\d+)", s),
               re.search(r"OVERRIDE_MAX_MINUTES\s*=\s*(\d+)", s)]
    w = worker("station.py")
    mine = [re.search(r"TAKEOVER_MIN_MINUTES\s*=\s*(\d+)", w),
            re.search(r"TAKEOVER_MAX_MINUTES\s*=\s*(\d+)", w)]
    return ([m.group(1) for m in station if m],
            [m.group(1) for m in mine if m])


def check_llm_providers(src: dict) -> tuple[list, list]:
    station = _list_after(src["settings/vocab.ts"], "LLM_PROVIDERS")
    body = worker("settings_schema.py")
    i = body.find("MODEL_CHOICES = {")
    mine = re.findall(r'^\s{4}"([a-z0-9-]+)"\s*:', body[i:i + 4000], re.M) if i >= 0 else []
    # A provider the STATION offers and we cannot name is the drift that
    # matters — the operator picks it there and the sidecar cannot follow. The
    # reverse is not: our list is also the one the panel offers on its own.
    return sorted(set(station)), sorted(set(station) & set(mine))


def check_tts_engines(src: dict) -> tuple[list, list]:
    # The mirrored fact is not the whole engine list — it is WHICH engines
    # share the seed roster's id-space, so an inherit slot keeps the persona's
    # own voice. Comparing against TTS_ENGINES reported drift for four engines
    # we were never mirroring, which is a tool teaching people to skim it.
    station = _list_after(src["schemas/persona.ts"],
                          "TTS_INHERITABLE_VOICE_ENGINES = [")
    w = worker("station_config.py")
    inherit = re.search(r"_INHERIT_CARRIES_VOICE\s*=\s*\((.*?)\)", w, re.S)
    mine = re.findall(r'"([a-z0-9-]+)"', inherit.group(1)) if inherit else []
    return sorted(station), sorted(mine)


def check_mcp_tools(src: dict) -> tuple[list, list]:
    station = sorted(set(re.findall(r"subwave_[a-z_]+", src["mcp/tools.ts"])))
    mine = sorted(set(re.findall(r"subwave_[a-z_]+",
                                 worker("call/tools/registry.py"))))
    # Ours is a superset by design: most of the DJ's actions are admin REST
    # wrapped here, not MCP. What matters is a station tool we do not name.
    return station, [t for t in station if t in mine]


def check_default_models(src: dict) -> tuple[list, list]:
    s = src["llm/internal/provider/registry.ts"]
    station = re.findall(r"cfg\.provider === '([a-z0-9-]+)'\)\s*return '([^']+)'", s)
    w = worker("settings_schema.py")
    mine = []
    for prov, _model in station:
        m = re.search(r'"' + re.escape(prov) + r'":\s*\("[^"]*",\s*"([^"]+)"\)', w)
        mine.append((prov, m.group(1) if m else "(none)"))
    return [f"{p}={m}" for p, m in station], [f"{p}={m}" for p, m in mine]


CHECKS = {
    "webhook events": check_webhook_events,
    "say kinds": check_say_kinds,
    "takeover bounds": check_takeover_bounds,
    "llm providers": check_llm_providers,
    "tts engines (inheritable)": check_tts_engines,
    "mcp tools": check_mcp_tools,
    "provider default models": check_default_models,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", action="store_true",
                    help="write the SHA checked to tools/upstream-drift.json")
    args = ap.parse_args()

    head = station_head()
    print(f"station {REPO}@{head['sha'][:12]}  ({head['date'][:10]})")
    was = {}
    if STAMP.exists():
        was = json.loads(STAMP.read_text(encoding="utf-8"))
        if was.get("sha"):
            print(f"last recorded  {was['sha'][:12]}  ({was.get('date','?')[:10]})")
            if was["sha"] == head["sha"]:
                print("  (unchanged since that pass)")
    print()

    src = {}
    for path in STATION_FILES:
        try:
            src[path] = fetch(path)
        except Exception as e:                                  # noqa: BLE001
            print(f"!! could not read controller/src/{path}: {e}")
            print(f"   ({STATION_FILES[path]}) — a move upstream, or gh is "
                  "not authenticated. Nothing below that reads it is trustworthy.")
            src[path] = ""

    drifted = 0
    for name, fn in CHECKS.items():
        try:
            station, mine = fn(src)
        except Exception as e:                                  # noqa: BLE001
            print(f"?? {name}: check itself failed ({e})")
            drifted += 1
            continue
        if station == mine:
            print(f"ok {name}: {len(station)} matched")
            continue
        drifted += 1
        only_station = [x for x in station if x not in mine]
        only_mine = [x for x in mine if x not in station]
        print(f"DRIFT {name}")
        if only_station:
            print(f"      station only : {only_station}")
        if only_mine:
            print(f"      talk wave only: {only_mine}")
        if not (only_station or only_mine):
            print(f"      station : {station}")
            print(f"      talk wave: {mine}")

    print()
    if args.record:
        STAMP.write_text(json.dumps(
            {"sha": head["sha"], "date": head["date"], "drifted": drifted},
            indent=2) + "\n", encoding="utf-8")
        print(f"recorded {head['sha'][:12]} in {STAMP.relative_to(ROOT)}")
    print(f"{drifted} of {len(CHECKS)} checks drifted"
          if drifted else "nothing drifted")
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
