---
name: talkwave-records
description: Fetch and read call/chat records off the deployed Talk Wave stack — transcripts, problems and tool runs as one conversation. Use when the operator says a call or text exchange had a problem, when reviewing what the DJ actually did on air, or before diagnosing any "the DJ said/did X" report. HTTP only; never needs SSH.
---

# Read back what happened on the line

Every finished call, text exchange and voicemail is one JSON record. Reviewing the
2026-08-27 text exchange took hand-SSH, docker exec and raw JSON parsing — this path
replaces that. The token server's `GET /calls` returns the newest 20 FULL records
(turns, tools with arguments folded in, problems, the air timeline, plus the
mint-time caller context that never reaches the on-disk file), behind the same
`X-Admin-Key` header the panel uses.

## Fetch and read

From the repo root — base URL and the admin key are in `.claude/OPERATOR.local.md`
(gitignored; never copy the values into this file or any committed one):

```bash
TALKWAVE_ADMIN_KEY=<key> python tools/fetch_records.py --base http://$NAS:8100 list
```

- `list` — one line per record: id, kind (call/chat/voicemail), length, turns, problem count.
- `show` — the newest record as a conversation: problems up top, then turns and tool
  runs merged in time order, failed tools marked `TOOL!`. Add `--id <id>` for a specific one.
- `save` — archive all 20 to `tools/livecall/records-archive/` (gitignored — records
  hold caller words and must NEVER reach the public repo), skipping ids already there.
- `corr` — what a caller's thumb travels with: up- vs down-rated calls compared on
  average problems, duration, and whether a station action was refused, reading the
  archive alongside the live window. The weekly-check-in read for "did anything the
  caller disliked share a cause". Run `save` first so rated calls survive the window.

**One 401 means stop.** A wrong key counts toward the server's 5-strike per-IP
lockout (5 minutes, then banned until restart); an absent key is refused for free.
The script refuses to retry auth — do the same by hand.

## Reading a record

- `problems` is where to start: the guards write one line per thing that went wrong
  ("The DJ told the caller it was about to do something and ran no tool…"), and the
  worst kinds name themselves.
- `tools` rows carry `(args) -> result`; a `failed: true` row is a refusal or error —
  check whether the DJ's next spoken line respected it.
- `setup` carries the pickup timings (`preparedSecs`, `greetingSecs`) and, since
  0.99.1, `stationTools`: `mcp`, `local-fallback` (the MCP handshake failed and the
  local read twins stood in), or `absent` (failed with no credentials — a blind call).
- `kind: "chat"` marks a text exchange; calls carry no `kind`.

The panel renders the same records under Diagnostics → recent calls (the viewer in
`web-widget/panel-viewers.js`) — this skill is for getting them into a terminal or a
session where they can be diffed, archived and quoted.

## The 20-record window

The server returns the newest 20; disk keeps ~40 (`record_keep`). Records older than
the window need `save` to have run while they were still inside it — run it after any
session the operator flags, so the evidence survives the rotation.
