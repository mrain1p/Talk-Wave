# What happens between a caller speaking and the station acting

Every other page here describes a part: [the settings](settings.md), [the models](models.md), [the network](networking.md), [voicemail](VOICEMAIL.md), [live on air](on-air.md). This one describes the path a single sentence takes — from the caller's mouth, through the prompt, into a tool, onto the air, and into the record — because until 2026-08-14 nothing did, and a system nobody has read end to end is one where each piece is right and the whole is not.

[← back to the README](../README.md)

---

## The shape of one call

```
  prepare()            start()                greet()              …the call…
  ─────────            ───────                ───────              ──────────
  who answers          the voice session      the first line       turns, tools,
  what they know       tools + the model      (held for the air)   holds, receipts
  the prompt, once     behaviours attached                         then the record
```

Three phases in `call/session.py`, in the order the caller experiences them: `prepare()` is everything they hear as ringing, `start()` puts the DJ on the line, `greet()` says hello. The settings are re-read at the top of every call, so a change in the panel reaches the next caller without restarting anything.

## 1. The prompt, and what it costs

`brain/assemble.py` joins two halves that change for two different reasons:

| Half | Module | What it is | Changes when |
|---|---|---|---|
| the briefing | `brain/briefing.py` | what is true on the station right now | the station does |
| the conduct | `brain/conduct.py` + `brain/tool_rules.py` | how to behave, and what may be done | a call goes wrong |

`tool_rules` is split from `conduct` on a hard rule: **a rule written FROM a tool lives beside the switch that builds it.** Every sentence there appears only when its tool does, and says the opposite thing when it doesn't. The prompt has lied in both directions before — it promised a takeover on a deployment with takeovers off (the DJ faked one with a song request) and it claimed queued requests could never be cancelled months after the station gained the endpoint.

### The budget

Run `python tools/prompt_report.py` for the current numbers; this is what it said on 2026-08-14, with every gate on:

| Section | Chars | Share |
|---|---|---|
| `tool_rules` | 10,921 | 43.6% |
| `CLOSING` | 5,202 | 20.3% |
| `say_the_true_thing` | 4,109 | 16.4% |
| `running_the_call` | 1,715 | 6.8% |
| `HOW_TO_TALK` | 1,101 | 4.4% |
| `LANGUAGE_AND_MIMICRY` | 998 | 4.0% |
| `DOORWAY` | 850 | 3.4% |
| `CALL_MOMENTUM` | 758 | 3.0% |
| **conduct total** | **25,640** | |
| **the whole assembled prompt, live** | **~28,700** | ~7,200 tokens |

The last row is the one that matters, and it is paid **on every turn** — not once at the start, and not only on turns the caller asked for. Every generated line in [§5](#5-who-may-speak) re-sends all of it.

Two things the report makes visible that nobody would guess:

- **Turning a capability OFF can make the prompt bigger.** Switching off announcements costs +248 characters, likes +387, segments +370. Absence alone was not enough — a DJ with no announce tool still told a caller the shoutout was in the air — so a disabled capability buys a sentence saying so out loud.
- **Two capabilities still cost nothing when ON**: `allow_favorite` and `allow_unfavorite` add no prompt text at all, and that is deliberate — a heart is the lowest-harm action on the line and the tool's own description is the whole instruction. It was four until 0.10.146: `allow_skip_track` and `allow_dj_segment` had no prompt presence either, which was not deliberate, and both reach every listener. They have bullets now, and `TestNoToolIsBuiltWithoutThePromptKnowingIt` makes the claim checkable instead of asserted — an exemption that says "it carries a bullet" now has to survive flipping the switch and diffing the prompt.

Sections are named (`conduct.blocks`), the report prices them from that list, and `rules(cfg, drop={...})` drops one by name so a sweep can measure whether it changes behaviour. `TestThePromptBudgetIsMeasurable` fails the build if a section is ever assembled outside that list.

**`tool_rules` is a special case, because being the biggest made it the least measurable.** At 42.6% of the conduct it is four times the next section, and dropping it whole removes the tool surface's entire description — an ablation that proves only that a DJ told nothing about its tools uses them badly. Since 0.10.152 it has seven named parts of its own (`tool_rules.SECTIONS`), each droppable while the rest stays, so the question actually worth asking — does the per-tool prose earn its place while the triage table remains — can be run. The shipped prompt is byte-identical when nothing is dropped, which `TestTheToolBlockSplitChangedNoPromptByte` holds to the character; without that, every measurement taken before the split would be against a different prompt from every one taken after.

## 2. The tool surface

`call/tools/registry.py` is the single table. The allowlists and the panel's tool reference are both derived from it, so they cannot disagree.

Each tool declares a **gate** (`read`, `never`, or the settings field that unlocks it) and how it is **served**:

- **MCP** — the station's own MCP server, filtered by an allowlist. A caller is an untrusted stranger driving a live broadcast by voice, so the destructive tools (`skip_track`, `play_sfx`, `queue_track`, `dj_segment`, `refresh_playlist` on the station's side) are never on the list. An empty allowlist fails **closed** with a sentinel, because the SDK reads empty as "expose everything".
- **LOCAL** — our own wrapper. A wrapper exists for one of three reasons, and it is worth knowing which: it adds retries and guards the model cannot be trusted to do in prose (`subwave_search_library`); it counts against the per-call action ledger, which an MCP-served tool bypasses entirely (every action); or **the station serves no MCP endpoint at all** and the wrapper is the only way to have the tool (`subwave_current_lyrics`, `subwave_recent_tracks`, the takeover pair).
- **NONE** — never on a call line at any setting.

`TestNoToolIsBuiltWithoutThePromptKnowingIt` fails the build when a tool is handed to the model with nothing in the assembled prompt naming it. That test exists because `subwave_queue_track` shipped with a switch, credentials, a working wrapper and no mention anywhere in a 16,644-character prompt — so a caller asked for one specific recording three times and got three different wrong ones.

## 3. Request triage — the five ways into a library

`tool_rules.finding_rule` is the decision table, and every line in it is a call that went wrong on 2026-08-12 in the same shape: the DJ had more than one way to look and only ever used the first.

| What the caller gave you | Tool |
|---|---|
| a named track or artist | `subwave_search_library` — literal word match, nothing more |
| a described sound or feeling | `subwave_search_by_sound` — matches the audio, not titles |
| "more of what's on" | `subwave_more_like_this` — no id needed |
| a mood, genre, era, "instrumental" | `subwave_browse_library` |
| "you pick" | `subwave_station_favourites` |
| "did it already play?" | `subwave_already_played` |
| none of the above | `subwave_request_song` — the fallback, and the only rate-limited one |

Two rules sit under the table, both bought with real calls. **A name search missing is not proof the library hasn't got it** — one wrong letter finds nothing, so search the artist alone before ever telling a caller a record isn't there (a caller asked for "Firestorm by Kygo"; the library holds *Firestone*). And **once you have found it, queue that recording by id** rather than re-requesting the title, because a request re-matches the words and can come back with a different record (this happened three times in one conversation).

Measured on the deployed worker, 2026-08-14, `gemini-3.1-flash-lite`, three rounds of the ten triage scenarios: **30/30 routed correctly.** On this model and this set, triage is not where calls are being lost.

## 4. What happens after a tool runs

- **The ledger** (`call/actions.py`) counts only what SUCCEEDED — an attempt the station refused costs the caller nothing and shows nothing. It is both the per-call ceiling and the source of the receipt cards.
- **The cap** is `max_actions_per_call`. Its refusal is written in-world and says explicitly that it is the LINE's rule, not the station's, because otherwise the DJ invents a reason.
- **Receipt cards** reach the caller's screen on the `talkwave.action` topic. Under the default "after" mode they are held until the DJ's line commits, so the words land before the paperwork.
- **The late-match poller** (`call/tools/late_match.py`) keeps asking the station what a request matched after the tool has already answered, and volunteers the title at the next quiet moment — because holding the tool return hostage to a slow resolver is latency the caller hears as silence.
- **The record** (`call/record.py`) is one JSON per call: both sides of the conversation, every tool with its result, the config it ran under, and every problem the caller was shielded from. The panel reads these back.

## 5. Who may speak

Ten things can start a DJ turn. Each was added for a real incident, and **they do not know about each other** — there is no single "the DJ speaks now" gate. The on-air hold hangs off `CallAgent.on_user_turn_completed`, which fires for CALLER turns only, so it covers the reply path and nothing else.

| What speaks | Where | Waits for clear air? |
|---|---|---|
| the greeting | `call/greeting.py` | yes, explicitly, up to 12s |
| the reply to a caller turn | `call/air.py` `CallAgent` | yes |
| the late request match | `call/tools/late_match.py` | yes, quiet-beat loop |
| the idle check-in and goodbye | `call/clocks.py` | yes, reads `air.on_air` |
| the come-back after a link | `call/comeback.py` | by construction |
| the hand-over line | `call/air.py` watch loop | it is the air |
| the promise nudge | `call/promise_guard.py` | yes, since 0.10.146 |
| the time-limit sign-off | `call/clocks.py` | yes, since 0.10.146 |
| the on-air wrap cue | `call/clocks.py` | n/a — only fires on a live phone-in, where the ducking is off because the broadcast IS the call |
| the provider apology | `call/lifecycle.py` | no — 20s cooldown only, and deliberately: it exists because the line has already gone silent |

The promise nudge is one entry and three conditions, which is worth knowing before reading it as one rule: the DJ promised and called nothing; the DJ said it was already done and nothing ran; and, since 0.10.154, the DJ told the caller it landed after a tool came back **refused**. That third one was invisible for a long time because it is the case where a tool call really did happen — the guard cleared itself on the very call that failed. It is one guard because the repair is the same in all three: make it true, or say plainly that it isn't.

The bottom two did not, until this table was written and they were read against each other. The nudge is the one most likely to have been heard: it fires a second or so after the DJ says "let me have a dig", which is exactly when a queued link lands.

**And the air is only half the question — the other half is each other.** Reading them against each other showed that most already cannot collide: the greeting is a one-shot at pickup, the late match and the idle ladder both wait for `agent_state == "listening"` (false while anything is generating), and the hand-over line *is* the air. The ones left — the promise nudge, the come-back, the time-limit sign-off, and since 0.97.66 the on-air wrap cue — each fire on their own clock and knew nothing of the others. [`call/floor.py`](../agent-worker/call/floor.py) is a lock and deliberately nothing more: it cannot decide who *should* speak, only stop two turns starting at once, and it counts collisions into the record so the next reader can tell whether it was ever needed.

The split between `call/lifecycle.py` and `call/clocks.py` follows this distinction — lifecycle OBSERVES a call and never speaks; clocks own a timer and generate a turn when it runs out.

## 6. Four mouths, not two

| Mouth | Prompt |
|---|---|
| the phone | the whole brain, `mode="call"` |
| the text line | the whole brain, `mode="chat"` — same facts, different physics |
| the back-to-air mention | its own hand-written prompt (`call/handoff.py`): the persona's NAME, no card, no conduct, no house style |
| the voicemail greeting | its own prompt (`voicemail/capture.py`): `soul[:900]` and nothing else |

The first two are held together deliberately — `conduct_chat` imports the medium-independent halves from `conduct` rather than copying them, and one test pins both lines to a single `promises.unbacked`, after the two copies drifted four phrasings apart. The last two are not held to anything.

## 7. What a record does NOT contain

Worth knowing before diagnosing from one:

- ~~A call's tool entries carry no arguments.~~ ~~A call's tool entries are not marked failed.~~ Both closed at 0.10.146: [`call/lifecycle.py`](../agent-worker/call/lifecycle.py) passes `with_args(...)` and `failed=` on the voice path, the way the chat line always did. They are struck rather than deleted because the reasons still hold — `"search_library returned nothing"` is half an answer without the words it searched for, and a call spent talking around three refusals used to read back as a clean call.

  **This section is now pinned by a test**, which is the only reason it can be trusted. It described the record wrongly for the better part of a week: `test_docs.py` guarded the speakers table, the tool surface, the settings sections, the links and the environment variables, and nothing guarded the one section whose whole job is to tell a reader what they cannot diagnose from. A page written to be read end to end is exactly the page a stale line does the most damage in.
- ~~Nothing pairs an ask to an outcome.~~ Closed at 0.10.147: [`call/asks.py`](../agent-worker/call/asks.py) notes every caller line that asks for something a TOOL would have to do, and the record says so when no action landed afterwards. Detection only — the DJ is never told and no turn is generated. It is the evidence the director question needs: if the archive fills with dropped asks then a director has a case, and if it does not, the turn-by-turn shape is fine.

  **Calibrated against the archive at 0.10.149, and it had to be**, because the second half of that sentence is a verdict a deaf instrument would reach on its own. Replayed over 44 real records — the first time it had met a caller rather than scenario text — it heard five of thirteen tool-shaped asks. What it missed was not exotic: "Got any Zeppelin?" and "Can you put Wade on the radio?", which is simply how people ask a radio station for things. With those shapes added it hears fourteen of fourteen, and one archived call now reports a dropped ask — a caller who asked for Zeppelin, was told "let me take a quick look through the racks", and got twenty seconds of silence and a "Still with me?" while no tool ran at all. That record's problems list was empty.
- **Held receipt cards drop at hang-up.** The record has them; the caller's screen does not.

## Does the prompt work? — the first measured answer

`SCENARIO_SET=closing` exists because nothing graded the second-largest section in the prompt. Triage grades which tool fired; conversations grades recovery from a fault; neither can see when the line hangs up. Six scenarios, each defending a claim `CLOSING` actually makes, three rounds each, on the deployed model:

| Claim | Result |
|---|---|
| a caller who says "that's everything" is let go | 3/3 |
| a thank-you after the action is the goodbye turn | 3/3 |
| a caller still deciding is not closed on | 3/3 |
| "go ahead then" is agreement, not goodbye | 3/3 |
| a question answered is not a wind-down | 3/3 |
| **a landed request is not the end of the call** | **1/3** |

The one that fails is the section's *opening claim* — the failure it was written for, four paragraphs of it, 20% of the conduct. Two findings came out of chasing it:

- **The section taught the thing it forbids.** Its worked YES pair ended `"…Anything else you want digging out while I'm in the racks?"`. The model copied it verbatim in all three rounds of the first run, and was right to — worked pairs beat prose on these models, so the strongest signal in the block was a demonstration of the forbidden move.
- **A NO example is still a sentence you hand the model.** Moving that same line into a NO did not help; the next run quoted it back from there. The rule now: *show* a NO when it is a failure the model produces anyway (inventing physics, miming a shoutout) — naming it is what makes it recognisable — and *describe* a NO, with nothing quotable in it, when the example would be a fluent line the model would not otherwise reach for.

Both fixes together moved it from 0/3 to 1/3 against a stricter grader — **still failing, and more prose was not the answer.**

So it became a mechanism, [`call/door.py`](../agent-worker/call/door.py), and that IS measured against its own absence: same scenario, same model, four rounds each, one variable.

| Arm | Landed |
|---|---|
| guard off | 1/4 |
| guard on | **3/3** |

Without the correction the DJ also reached for `end_call` — it did not merely hold the door open, it showed the caller out. The guard cannot unsay the line that trips it, and does not try to: it stops the second and third, which is the part a caller feels. It costs nothing on every turn where the DJ behaved, which a paragraph in the prompt can never say.

The prose it replaces has deliberately NOT been deleted yet — that is the next measured step, and doing it in that order is the point.

## What each section is worth

Every block is priceable; four now have a set behind them, and the results do not follow size.

| Section | Share | Measured against | With | Without |
|---|---|---|---|---|
| `LANGUAGE_AND_MIMICRY` | 4% | `SCENARIO_SET=mimicry` | 11/11 | **5/11** |
| `HOW_TO_TALK` | 4% | `SCENARIO_SET=banter` | p90 69 words | **p90 84** |
| `CLOSING_DOOR` | 4% | `SCENARIO_SET=closing` | 1/3 first occurrence | 0/3 |
| `say_the_true_thing` | 16% | `SCENARIO_SET=refusals` + the belief judge | 8/14 | **8/14** — but see below |

The smallest block is the only thing stopping a caller driving station-wide actions by quoting fake instructions at the DJ — ablated, it skipped the track and put "the station is closing down" on air, 3/3 each. `HOW_TO_TALK` is the same story with a different instrument: dropping it moves the median DJ turn from 39 words to 54 and doubles the turns that cross fifty, so 1,101 characters are buying about fifteen words a turn, on every turn. The largest of the four changed nothing when removed, most likely because the TOOLS already carry the same instruction in their result text, at the moment it matters, where it beats the standing prompt.

**Nothing about their size predicted any of it**, which is the case against ever trimming the prompt by eye — three of the four blocks with a set behind them are the small ones, and all three earn their length.

`say_the_true_thing` is not cut, and since 0.97.72 the reason is a measurement instead of a gap. The set used to be unable to answer the question: `must_not_say` lists the invented excuses of past calls, and a round scored PASS while the DJ told a caller that a request the station had just REFUSED was "coming up right after this" — an invented OUTCOME, which no phrase list can enumerate. The refusal scenarios now carry a `believed` key (the true state of the world after the armed fault) and a **belief judge**: one extra model call over the DJ's own lines asking whether an ordinary caller walks away believing something false. A MISLED reading amends a mechanical PASS to a FAIL, quoting the false belief.

Its first answer (2026-08-18, `gemini-3.1-flash-lite`, `GATES=all MCP=1 REPEATS=3`, both arms): **8/14 with the section, 8/14 without.** The prose is not holding honesty up — the failure it was written for happens either way, roughly 40% of judged rounds, mostly as the claim-before-the-refusal shape the promise guard then repairs a turn late. But the ablated arm regressed on style: median spoken turn 40 → 49 words and stage-direction asterisks 2 → 7, and nine words a turn is more caller-felt time than the ~1,000 prompt tokens buy back. So the block stays, held by the wrong virtue — and the honesty failure's real home is the narration-before-action shape, which is the call-orchestration stream's question, not a prompt-prose one.

## Known disagreements

Recorded rather than quietly carried. None are open.

**~~The briefing is frozen at pickup.~~** Closed at 0.97.73. The volatile lines were made honest first — "Playing when this call connected: …" says *when* it was true instead of asserting a present tense that expires — and the mechanism landed after: the guard's watch loop already reads `/state` every four seconds for the djLog and used to throw the current track away, so a mid-call **change** now stages one sentence on the guard, and `CallAgent.on_user_turn_completed` injects it as a system note on the next caller turn — the same Gemini-safe insertion point the door hint uses. Staged rather than pushed, deliberately: a context note that generated a turn would perturb the turn-taking, and that is worse than a stale fact. The first sighting stages nothing (the briefing covers the pickup track), a second change overwrites the first (the caller only needs the newest truth), and the injection is written into the ducking timeline so a record shows the correction beside the moment it landed. It rides the overlap guard's poll, so a deployment with the overlap guard off keeps the frozen briefing — written down here because that coupling is a choice, not an accident.

Three others were here and are fixed: triage was stated in three places that disagreed (the table in [§3](#3-request-triage--the-five-ways-into-a-library) is the single source now, and the search wrapper's runtime refusal points at the same tool the prompt does), and four capabilities had no prompt presence when switched on.
