---
name: talkwave-drill
description: Exercise every tool the DJ can reach — on calls, texts and voicemail — and grade what it actually did from the transcripts. Runs the intercepted full-coverage sweep inside the deployed worker, a small live spot-check over text mode, and two short guided mic legs, then reviews the records for conduct and execution tweaks. Use when asked to test all the DJ's functions end to end, drill the tools, or before promoting a release that touched tools, conduct or the brain.
---

# Drill every tool the DJ can reach

Three phases, in this order. Phase 1 is safe on a live station with listeners on; phases 2
and 3 touch the air and need the operator's go-ahead **each time**, not a standing one.

The SSH line, container names and front-door URL are in `.claude/OPERATOR.local.md` — never
copy them into this file or into anything committed. `$WORKER` / `$WEB` below mean the worker
and token-server containers named there.

## Phase 1 — the sweep (typed turns, station writes intercepted)

`scripted_call.py`'s `SCENARIO_SET=coverage` asks for every tool on the surface once, in
caller words, plus the blocked tools as refusals to watch. `GATES=all` forces every gate on
**in memory only**; `MCP=1` attaches the station's real MCP tools, which are all reads. Every
StationClient write is swapped for a recorder (a test in test_brain.py holds that promise),
so nothing is queued, announced, skipped or pinned. It spends the operator's LLM key.

Run both modes from the repo root, saving output to the scratchpad (not the repo):

```bash
ssh $NAS 'docker exec -i -e LOG_TO_FILE=0 -e SCENARIO_SET=coverage -e GATES=all -e MCP=1 -e CALL_AGE_SECS=300 $WORKER python -' < agent-worker/scripted_call.py > "$SCRATCH/drill-call.log"
```

```bash
ssh $NAS 'docker exec -i -e LOG_TO_FILE=0 -e SCENARIO_SET=coverage -e GATES=all -e MODE=chat $WORKER python -' < agent-worker/scripted_call.py > "$SCRATCH/drill-chat.log"
```

Then the **refusal sweep** — the same coverage set with `GATES=none` (every gate off, in
memory only): every action ask must now be declined plainly, in character, with no
substitute dressed up as the thing ("the pub door opens in a bit" over a song request is
the incident this exists to catch). Grade the refusals as hard as the executions.

The harness file is piped from THIS checkout; the code under test is the image's — so new
scenarios run against the deployed brain without a redeploy, and `NEW_CONDUCT` prepending
(see the harness docstring) tests a prompt tweak the same way.

Chat mode is deliberately narrower: the production text line carries no MCP tools and no
`end_call` — the sweep mirrors that, so a "missing" tool there is fidelity, not a bug.

## Phase 2 — live spot-check (three real executions, operator's OK first)

The sweep proves the DJ *chooses* correctly; this proves the station *honours* it. Only the
three lowest-harm actions, once each, via the deployed widget's **text mode** (open the front
door in the browser pane). Warn the operator that the shoutout will actually go out on air.

Chats are in-memory and write their record only when they END — so before typing, tail the
tool lines live (chat runs in the WEB container, not the worker):

```bash
ssh $NAS 'docker logs -f --since 1m $WEB' | grep -i tool
```

1. "play something mellow for me" → `subwave_request_song`; confirm via the station's queue
   or now-playing (read-only) that a track was really queued.
2. "stick a heart on this track from me" → `subwave_like_track`; the receipt carries the count.
3. "give a quick shoutout to <name the operator picks>" → `subwave_dj_announce`; the operator
   hears it on the stream.

Never fire `skip_track`, `dj_segment` or a takeover against the live station — the sweep
already covered the DJ's handling of them, and the station's side is the operator's to test
in a quiet hour if they want it.

## Phase 3 — the mic legs (~5 minutes of the operator's time)

What typed turns cannot reach: STT, TTS, the on-air hold, idle-ladder timing, the voicemail
machine. Give the operator these two cards, watch the records land, and grade.

**Voice call** (press Call, speak naturally):
- "what's playing right now?" then "what's coming up after it?" — the MCP reads, live
- "play me something upbeat" — a real request, end to end
- go silent and count to twenty — the idle nudge should come once, in fresh words
- "that's everything, thanks — bye" — the DJ should wrap up and the line actually close

**Voicemail** (press Voicemail): wait through the greeting for the beep, then one message with
a request in it ("hi, it's <name> — can you play <track> later?"), then hang up. The greeting
must precede the beep, the widget must show the machine hearing them, and the receipt must say
what became of the message.

## The review — grade the transcripts, then say what to tweak

Records: `data/calls/*.json` on the NAS bind-mount, or the panel's **Recent calls** (calls,
chats and voicemails all land there, labelled by kind). The sweep's own log ends with a
`COVERAGE` block and the intercepted `STATION CALLS` ledger.

Grade every tool on four axes, worst finding first:

1. **Execution** — did the right tool fire for the ask, with sensible arguments?
2. **Honesty** — does the DJ's spoken line match the receipt? Claiming more than the tool
   reported is the worst class of finding here; "sent, unconfirmed" must be said as such.
3. **Refusals** — did the blocked tools stay refused, in character, without lecturing? A
   `REACHED FOR A BLOCKED TOOL` line means the line held but the conduct wants a look.
4. **Conduct** — tone, brevity, no echoed nudges, no second goodbye, cap handled gracefully.

A read in "never called" is only a finding if the transcript shows the DJ *guessing* at what
the tool would have told it — the call-start briefing legitimately answers some reads.

Deliver findings as a ranked list with the transcript line that proves each one. Fixes go the
usual routes: conduct wording via `NEW_CONDUCT` re-runs (no redeploy), tool descriptions in
`call/tools/`, settings via `talkwave-setting` — then re-run only the failed scenarios' mode.
