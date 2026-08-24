# What happens between a caller speaking and the station acting

Every other page here describes a part: [the settings](settings.md), [the models](models.md), [the network](networking.md), [voicemail](VOICEMAIL.md), [live on air](on-air.md). This one describes the path a single sentence takes — from the caller's mouth, through the prompt, into a tool, onto the air, and into the record — because until 2026-08-14 nothing did, and a system nobody has read end to end is one where each piece is right and the whole is not.

[← back to the README](../README.md)

---

**On this page**

1. [The prompt, and what it costs](#1-the-prompt-and-what-it-costs) — the two halves, and the ~7,200 tokens paid every turn
2. [The tool surface](#2-the-tool-surface) — one table, three ways a tool is served
3. [Request triage](#3-request-triage--the-five-ways-into-a-library) — the five ways into a library
4. [What happens after a tool runs](#4-what-happens-after-a-tool-runs) — the ledger, the cap, the receipts
5. [Who may speak](#5-who-may-speak) — ten things can start a DJ turn
6. [Four mouths, not two](#6-four-mouths-not-two)
7. [What a record does NOT contain](#7-what-a-record-does-not-contain)

The back half is measurement: [what the scenario sets hold](#measured-not-argued), and [how the briefing follows the room](#the-briefing-follows-the-room).

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

The ringing is shorter than it looks on paper, because most of its questions are answered before the worker asks them:

- the token server **prefetches the station snapshot** the moment it mints the room (`station_prefetch.py`) — adopted only while fresh, and refused otherwise;
- the room join, the TTS voice list and the station's MCP handshake all ride **`prepare()`'s one concurrent wait**, instead of queuing behind it.

The call record's `setup` block writes down what each leg took (`preparedSecs`, `onLineSecs`, `greetingSecs`) and whether the snapshot was `prefetched` or `fetched` — so **"calls feel slow to connect" is readable off one record**, instead of an evening of probes.

## 1. The prompt, and what it costs

`brain/assemble.py` joins two halves that change for two different reasons:

| Half | Module | What it is | Changes when |
|---|---|---|---|
| the briefing | `brain/briefing.py` | what is true on the station right now | the station does |
| the conduct | `brain/conduct.py` + `brain/tool_rules.py` | how to behave, and what may be done | a call goes wrong |

`tool_rules` is split from `conduct` on a hard rule: **a rule written FROM a tool lives beside the switch that builds it.** Every sentence there appears only when its tool does, and says the opposite thing when it doesn't.

> **The prompt has lied in both directions before.** It promised a takeover on a deployment with takeovers off — the DJ faked one with a song request — and it claimed queued requests could never be cancelled, months after the station gained the endpoint.

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
- **Two capabilities still cost nothing when ON.** `allow_favorite` and `allow_unfavorite` add no prompt text at all, and that is deliberate: a heart is the lowest-harm action on the line, and the tool's own description is the whole instruction.

Sections are named (`conduct.blocks`), the report prices them from that list, and `rules(cfg, drop={...})` drops one by name so a sweep can measure whether it changes behaviour. `TestThePromptBudgetIsMeasurable` fails the build if a section is ever assembled outside that list.

**`tool_rules` is a special case, because being the biggest made it the least measurable.**

At 42.6% of the conduct it is four times the next section, and dropping it whole removes the tool surface's entire description — an ablation that proves only that a DJ told nothing about its tools uses them badly.

It has **seven named parts of its own** (`tool_rules.SECTIONS`), each droppable while the rest stays — so the question actually worth asking can be run: *does the per-tool prose earn its place while the triage table remains?*

The shipped prompt is byte-identical when nothing is dropped, which `TestTheToolBlockSplitChangedNoPromptByte` holds to the character. Without that, every measurement taken before the split would be against a different prompt from every one taken after.

## 2. The tool surface

`call/tools/registry.py` is the single table. The allowlists and the panel's tool reference are both derived from it, so they cannot disagree.

Each tool declares a **gate** (`read`, `never`, or the settings field that unlocks it) and how it is **served**:

- **MCP** — the station's own MCP server, filtered by an allowlist. A caller is an untrusted stranger driving a live broadcast by voice, so the destructive tools (`skip_track`, `play_sfx`, `queue_track`, `dj_segment`, `refresh_playlist` on the station's side) are never on the list. An empty allowlist fails **closed** with a sentinel, because the SDK reads empty as "expose everything".
- **LOCAL** — our own wrapper. A wrapper exists for one of three reasons, and it is worth knowing which:
  1. it adds **retries and guards** the model cannot be trusted to do in prose (`subwave_search_library`);
  2. it **counts against the per-call action ledger**, which an MCP-served tool bypasses entirely (every action);
  3. **the station serves no MCP endpoint at all**, and the wrapper is the only way to have the tool (`subwave_current_lyrics`, `subwave_recent_tracks`, the takeover pair).
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

Two rules sit under the table, both bought with real calls:

- **A name search missing is not proof the library hasn't got it.** One wrong letter finds nothing, so search the artist alone before ever telling a caller a record isn't there — a caller asked for "Firestorm by Kygo", and the library holds *Firestone*.
- **Once you have found it, queue that recording by id** rather than re-requesting the title, because a request re-matches the words and can come back with a different record. This happened three times in one conversation.

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
| the promise nudge | `call/promise_guard.py` | yes |
| the time-limit sign-off | `call/clocks.py` | yes |
| the on-air wrap cue | `call/clocks.py` | n/a — only fires on a live phone-in, where the ducking is off because the broadcast IS the call |
| the provider apology | `call/lifecycle.py` | no — 20s cooldown only, and deliberately: it exists because the line has already gone silent |

The promise nudge is one entry and **three** conditions, which is worth knowing before reading it as one rule:

1. the DJ promised and called nothing;
2. the DJ said it was already done and nothing ran;
3. the DJ told the caller it landed after a tool came back **refused**.

It is one guard because **the repair is the same in all three** — make it true, or say plainly that it isn't. The nudge is the one most likely to be heard: it fires a second or so after the DJ says "let me have a dig", which is exactly when a queued link lands.

**And the air is only half the question — the other half is each other.** Reading them against each other showed that most already cannot collide:

- the greeting is a one-shot at pickup;
- the late match and the idle ladder both wait for `agent_state == "listening"`, which is false while anything is generating;
- the hand-over line *is* the air.

The ones left — the promise nudge, the come-back, the time-limit sign-off, and the on-air wrap cue — each fire on their own clock and know nothing of the others.

[`call/floor.py`](../agent-worker/call/floor.py) is a lock and deliberately nothing more: **it cannot decide who *should* speak, only stop two turns starting at once** — and it counts collisions into the record, so the next reader can tell whether it was ever needed.

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

- **Tool entries carry their arguments and their failed marker** on both the voice and text paths — "search_library returned nothing" comes with the words it searched for, and a refused tool reads as refused.
- **Every tool-shaped ask is paired to an outcome.** [`call/asks.py`](../agent-worker/call/asks.py) notes a caller line that asks for something a TOOL would have to do, and the record says so when no action landed afterwards. Detection only — the DJ is never told and no turn is generated.
- **Held receipt cards drop at hang-up.** The record has them; the caller's screen does not.

## Measured, not argued

The prompt's sections are priceable: each block is named, droppable by name, and graded by scenario sets on the deployed model (`SCENARIO_SET=closing / mimicry / banter / refusals`). What the measurements hold:

- **The smallest block is the safety-critical one.** `LANGUAGE_AND_MIMICRY` (4% of the conduct) is what stops a caller driving station-wide actions by quoting fake instructions at the DJ — ablated, the DJ skipped the track and put "the station is closing down" on air.
- **`HOW_TO_TALK` buys about fifteen words a turn, on every turn** — dropping it moves the median DJ turn from 39 words to 54.
- **Letting a caller go is a mechanism, not prose.** [`call/door.py`](../agent-worker/call/door.py) holds the door open after a landed request; measured against its own absence it takes the scenario from mostly-failing to passing, and it costs nothing on turns where the DJ behaved.
- **Honesty is judged on belief, not phrases.** The refusal set carries a belief judge — one extra model call over the DJ's own lines, asking whether an ordinary caller walks away believing something false — because an invented outcome is not on any phrase list.
- **Size predicts none of it**, which is the case against ever trimming the prompt by eye.

## The briefing follows the room

The briefing's volatile lines say **when** they were true — *"Playing when this call connected: …"* — and a mid-call track change stages one sentence that is injected as a system note on the next caller turn, the same Gemini-safe insertion point the door hint uses. Staged rather than pushed, deliberately: a context note that generated a turn would perturb the turn-taking, and that is worse than a stale fact.

- The first sighting stages nothing — the briefing covers the pickup track.
- A second change overwrites the first — the caller only needs the newest truth.
- The injection is written into the ducking timeline, so a record shows the correction beside the moment it landed.

It rides the overlap guard's poll, so **a deployment with the overlap guard off keeps the frozen briefing** — written down because that coupling is a choice, not an accident.
