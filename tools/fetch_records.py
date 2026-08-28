"""Pull call/chat records off a deployment and read them as conversations.

Reviewing the 2026-08-27 text exchange took hand-SSH, docker exec, and raw
JSON parsing — for a question ("what went wrong on my last chat?") the
records already answer. This asks the token server instead: GET /calls
returns the 20 newest FULL records, including the mint-time caller context
that never reaches the on-disk JSON, over the same X-Admin-Key auth the
panel uses. HTTP, not SSH, on purpose: it works from any machine the panel
works from, including when the NAS's SSH service has switched itself off
again (it does).

Usage, from the repo root:

    python tools/fetch_records.py --base http://192.168.1.245:8100 list
    python tools/fetch_records.py --base ... show            # newest record
    python tools/fetch_records.py --base ... show --id 20260827-174809-803c3322ffa0
    python tools/fetch_records.py --base ... save            # archive all 20

The admin key comes from TALKWAVE_ADMIN_KEY in the environment, or --key.
Never put it in a file in this repo — the repo is public; the real values
live in .claude/OPERATOR.local.md, which is gitignored.

A WRONG key counts toward the server's 5-strike per-IP lockout (5 minutes,
then banned until restart) — an ABSENT key is refused for free. So this
script never retries auth: one 401 and it stops and says why.

`save` writes each record to tools/livecall/records-archive/<id>.json
(gitignored — records hold caller words and must never reach the public
repo) and skips ids already archived.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ARCHIVE = Path(__file__).parent / "livecall" / "records-archive"


def fetch(base: str, key: str) -> list[dict]:
    req = urllib.request.Request(
        base.rstrip("/") + "/calls",
        headers={"X-Admin-Key": key} if key else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8")).get("calls") or []
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit(
                "401 — the server wants the admin key. Set TALKWAVE_ADMIN_KEY "
                "or pass --key. NOT retrying: wrong keys count toward the "
                "server's lockout, absent ones don't."
            )
        raise


def one_line(text: str, width: int = 110) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def show(rec: dict) -> None:
    kind = rec.get("kind") or "call"
    print(f"=== {rec.get('id', '?')}  [{kind}]  {rec.get('startedAt', '?')}")
    cfg = rec.get("config") or {}
    print(f"    {cfg.get('llm', '?')} | tier {cfg.get('callerTier', '?')} | "
          f"{rec.get('durationSecs', '?')}s | ended: "
          f"{rec.get('endedBecause', '?')}")
    for extra in ("rating", "opRating"):
        if rec.get(extra):
            print(f"    {extra}: {rec[extra]}")
    problems = rec.get("problems") or []
    if problems:
        print("  WHAT WENT WRONG")
        for p in problems:
            print(f"    !! {one_line(p.get('what'), 200)}")
    # The conversation as it happened: turns and tools merged on time,
    # the same shape the panel's viewer renders.
    events = [(t.get("t", ""), t.get("who", "?"), t.get("text", ""))
              for t in rec.get("turns") or []]
    events += [(t.get("t", ""),
                "TOOL!" if t.get("failed") else "TOOL",
                f"{t.get('name')}: {t.get('result', '')}")
               for t in rec.get("tools") or []]
    print("  THE CONVERSATION")
    for _t, who, text in sorted(events, key=lambda e: e[0]):
        print(f"    {who:>7}: {one_line(text)}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True,
                    help="token server origin, e.g. http://192.168.1.245:8100")
    ap.add_argument("--key", default=os.environ.get("TALKWAVE_ADMIN_KEY", ""))
    ap.add_argument("--id", default="", help="one record id for `show`")
    ap.add_argument("command", choices=["list", "show", "save"],
                    nargs="?", default="list")
    args = ap.parse_args()

    calls = fetch(args.base, args.key)
    if not calls:
        print("No records on the server (it returns the newest 20).")
        return

    if args.command == "list":
        for rec in calls:
            marks = (len(rec.get("problems") or []))
            print(f"{rec.get('id', '?')}  {rec.get('kind') or 'call':<9}"
                  f"{str(rec.get('durationSecs', '?')):>6}s  "
                  f"{rec.get('callerTurns', '?'):>3} turns  "
                  + (f"{marks} problem(s)" if marks else ""))
        print("\npython tools/fetch_records.py --base ... show   # newest in full")
        return

    if args.command == "show":
        if args.id:
            wanted = [r for r in calls if r.get("id") == args.id]
            if not wanted:
                sys.exit(f"{args.id} is not among the newest 20 the server "
                         "returns — `list` shows what is.")
            show(wanted[0])
        else:
            show(calls[0])
        return

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    saved = 0
    for rec in calls:
        rid = rec.get("id") or "unknown"
        path = ARCHIVE / f"{rid}.json"
        if path.exists():
            continue
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        saved += 1
    print(f"archived {saved} new record(s) to {ARCHIVE} "
          f"({len(calls) - saved} already there)")


if __name__ == "__main__":
    main()
