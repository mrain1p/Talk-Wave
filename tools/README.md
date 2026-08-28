# The instruments

Four of these place or simulate a call and it has never been written down which one to reach for, so the answer has lived in whoever last used one. That cost real time: `tools/tool_eval.py` was maintained for months alongside a harness that already subsumed it, and was only retired at 0.10.146 once somebody compared them.

Pick by what you are trying to find out.

## Is the DJ making the right decisions?

**[`agent-worker/scripted_call.py`](../agent-worker/scripted_call.py)** — not in this directory, and still the one you want most of the time. Typed caller turns against the REAL brain: the live prompt, the production tool objects, the operator's own model and settings, with every station write swapped for a recorder. It grades itself, repeats for a rate, and can drop a named prompt section to measure what that section is worth. No audio, no LiveKit, no room. Read its docstring for the scenario sets and the levers; run it inside the deployed worker so you are measuring the DJ the operator actually has.

**[`prompt_report.py`](prompt_report.py)** — what that prompt costs, section by section, priced from the same list `rules()` assembles from. Run it before and after any conduct edit; it is the only thing that can say a rewrite made the prompt bigger.

## Does a real call work?

These three all place genuine calls, and they differ in what they bring with them.

**[`call_harness.py`](call_harness.py)** — ONE call against whatever stack is already running, with a stopwatch on every leg. The quickest way to answer "did that change break answering the phone", and the smallest thing that would have caught the endpointing gap that reached a tester's fork.

**[`call_scenarios.py`](call_scenarios.py)** — boots its OWN stack on scratch settings and walks the line through every mode it has: live calls, voicemail, the fallback between them, push to talk, the timeouts, a read-only tool call. Reach for it when the question spans modes, or before a release, rather than when you are iterating on one.

**[`livecall/`](livecall/)** — everything real except the mouth, which is a WAV file Chrome is pretending is a microphone. Real STT, real TTS, real station, real air. It exists because timing questions cannot be answered any other way and a human on the line changes what is being measured — every ducking fix from 0.10.121 to 0.10.124 was sized from a run of it. Slowest to set up; the only one that can see the on-air hold.

## What actually happened on the line?

**[`fetch_records.py`](fetch_records.py)** — the finished-call records, fetched over the panel's own HTTP auth and printed as conversations: problems up top, turns and tool runs merged in time order. Built after the 2026-08-27 text-exchange review cost an evening of hand-SSH and raw JSON for a question the records already answered. `save` archives them into `livecall/records-archive/` (gitignored — caller words never reach the public repo) before the server's 20-record window rotates them away. The `talkwave-records` skill carries the reading guide.

## Everything else

**[`panel_dev_server.py`](panel_dev_server.py)** — the widget and panel against a fake backend, for driving them in a browser without a stack. Dev only, and it lives here so the Dockerfile cannot ship it.

**[`make_library_sounds.py`](make_library_sounds.py)** — run once, commit the WAVs. Synthesizes the bundled sound packs from pure maths, which is a licensing decision before it is an aesthetic one.
