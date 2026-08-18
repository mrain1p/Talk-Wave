# Live on air

[← back to the README](../README.md)

A caller's conversation with the DJ, going out on the station's broadcast while it happens. This page is the whole feature: what listeners actually hear, the three consents that all have to agree before anyone airs, the operator's pull, what happens when the plumbing is missing, the one compose stanza the wiring needs, and the terms on which a caller's voice touches a disk. The switches live on [the dashboard](dashboard.md); the delivery cousin for recorded messages — the soundbite studio — is on [the voicemail page](VOICEMAIL.md).

## What listeners actually hear

The station has no live input — everything that airs is a file its mixer fetches. So a "live" phone-in is a relay: each finished utterance, the caller's and the DJ's, becomes a short clip pushed onto the station's voice queue in conversation order. The queue is first-in-first-out and the music duck holds across back-to-back items, so what listeners hear is the conversation, tightened — the model's thinking gaps don't air — running about one exchange behind the room, on a stream every listener hears a couple of seconds late.

That last number is worth being precise about, because the obvious one is wrong. The station reports `streamBufferSeconds` as 22 and Icecast really does burst 22 seconds of audio on connect — but a browser throws most of that away, and a plain `<audio>` element was measured sitting a rock-steady **2.3 seconds** behind the newest buffered byte for a whole run (2026-08-13, against the operator's own station; the working is in [`call/air_log.py`](../agent-worker/call/air_log.py)). 22 describes the burst SIZE, not the playhead. Anywhere a number is needed, `audibleIn` is recorded per push so the gap can be read off a real call rather than argued from a config value that turns out to describe something else.

The lag is not an apology, it is the dump button. Real phone-in radio runs on a deliberate delay for exactly this reason: a turn that has not aired yet can still be killed. The relay holds exactly one finished clip back — a turn airs when the one after it completes, **or after six seconds, whichever comes first**. The cap matters: without it the hold lasted whatever the next turn happened to take — the model thinking, then the DJ's whole answer playing out in the room — so a caller's turn could sit unaired for twenty seconds and the broadcast filled the gap with music swells. Six seconds is a promise instead of an accident: every finished turn stays killable for exactly that long, the air runs about that far behind the room, and the mixer's queue stays fed so the duck never lifts mid-conversation.

The brackets around the segment are lazy on purpose. The DJ's on-air intro — a caller is coming on the air — airs at the **first clip**, not when the call connects, and the thank-you outro airs only if something actually aired. The first deployed test did it the other way and aired both brackets around a call whose media never arrived: listeners got the announcement, a minute of nothing, and a thank-you to nobody.

One phone-in holds the air at a time — while one is live, the ON AIR route refuses the next caller until it ends. And the **On-air window** setting (default 240 seconds) bounds how long one caller may hold the broadcast: when it closes, the relay signs them off air and the call itself carries on privately. The station's own segments queue behind a live call, so shorter is kinder to the programme.

## Three switches have to agree

Nobody airs by accident. Three separate consents stand between a caller and the broadcast, and any one of them alone says no:

- **Go live on the station** — the tier row under Caller permissions: off, anyone, guest code, or admin. Off means nobody, the admin included. The route is minted into the signed room name at pickup, so a caller cannot put themselves on air mid-call.
- **The dashboard's two doors** — the Live on air cluster's quick kills, [documented with the dashboard](dashboard.md): **Live Call** and **Aired Voicemails**, one per door, so you can close one without touching the other. Live calls need the mixer's telnet below; aired voicemails don't — the studio's DJ-reads backend airs over the plain admin API. A closed door just means private: callers still talk to the DJ, they just don't reach the broadcast.
- **The caller's own switch** — the ON AIR | OFF AIR route on the card's top edge. It defaults to OFF AIR, is never remembered between visits, and locks the moment anything is running. One switch arms both doors: with it on, a call relays live, and a recorded message goes to [the soundbite studio](VOICEMAIL.md) for air instead of the private machine.

Settings are re-read before **every push** — tighter than the per-call re-read the rest of the system promises — so flipping a door shut mid-broadcast stops the next clip, not the next caller.

## The pull

**PULL OFF AIR**, on the dashboard's On-air transmission row, is the operator's dump: the turn in hand and the one arriving both die unaired, the DJ signs the segment off, and the call itself carries on — the caller may not even notice. The press itself asks the server whether a phone-in is actually live, so pressed on a quiet line it says so and kills nothing, and a leftover press can never behead the next caller's first turn.

**What it protects depends on where you are listening**, and that is worth knowing before you rely on it. The dump kills what has not been pushed yet — it cannot reach back for something already in the mixer's queue. So the question is how far behind the room you are when you decide to press it. Monitoring the **panel**, you are watching the conversation as the worker sees it, and every finished turn is yours to kill for a guaranteed six seconds. Monitoring the **stream**, you are a turn plus a couple of seconds behind, so what made you reach for the button has already gone out and you are killing what comes after it. Neither is wrong — a phone-in you are half-watching is more realistically monitored on the stream — but the panel is the surface where the dump does what the word implies. Anything that must never air is a caller who should not have been given the ON AIR route in the first place; the three consents above are the real control, and the dump is the one you keep for the thing nobody predicted.

## When the plumbing is missing

A caller who pressed the button in good faith is never punished for the mixer being down — every failure lands on the broadcast side, out loud, and the call survives it:

- At pickup the relay preflights the mixer. Unreachable, the call goes ahead **off air** and the transcript says why — "no air base URL", or "no reachable mixer" — because the studio once spent a week not noticing a missing network stanza, and a silently absent broadcast is that all over.
- Two pushes failing back to back means the transport is gone, not unlucky: the relay takes the segment off air out loud rather than airing a conversation with holes in it, and the call continues privately.
- The dashboard's Live Call door answers with the worker's own last word — the process that actually pushes, its verdict written at worker start and at every phone-in — so a half-joined deployment shows the door shut rather than open over calls that quietly fall back.

## The wiring

The transport is the one proven on a live station: Talk Wave serves each clip at an unguessable URL on its published `:8100`, then pushes `voice_queue.push <url>` over the station mixer's telnet — Liquidsoap fetches the audio itself, exactly the way it fetches every music track, so nothing is ever written to the station's disk. The voice channel is the right one on purpose: it is the only channel that carries speech at broadcast level, music ducked beneath it.

Two facts about Docker decide the setup:

- Compose gives every project its own private network, so the station's stack and Talk Wave's cannot see each other's service names — not a configuration anyone made, just Compose's default. The mixer's telnet (`broadcast:1234`) only resolves on the station's network.
- Host-published ports are reachable from everywhere, which is why the clip-fetch half needs no changes at all.

So the deployment step is joining **both** Talk Wave containers to the station's network:

```yaml
  talkwave-worker:
    networks:
      - default                                       # keep — Talk Wave's own services find each other here
      - <station-project>_default                     # the station's network, so broadcast:1234 resolves

  talkwave-web:
    networks:
      - default
      - <station-project>_default

networks:
  <station-project>_default:
    external: true                                    # created by the station's own compose — joined, not owned
```

Both, because the pushing is split across the two processes and reachability is answered per container: **live calls push from the worker** (the relay runs inside the call), and **the studio's caller-voice sends push from the web half** (the panel's API lives there). The dashboard's Live Call door reads the worker's own verdict, so a half-join is at least visible — join only the web and the door honestly shows shut — but only joining both gives you both features.

Keep `default` in each list: naming any network on a service drops Compose's implicit one, and Talk Wave's own services stop finding each other by name.

The mixer fetches clips at `http://HOST_IP:8100` by default — the published port your `.env` already names for LiveKit. If your compose sets no `HOST_IP`, give **both** services `VM_AIR_BASE_URL` explicitly, or the relay falls back private with "no air base URL" in the record; once joined to the station's network, `VM_AIR_BASE_URL=http://talkwave-web:8100` also works and never goes stale when the LAN address changes. The two transport settings (`VM_MIXER_TELNET`, `VM_AIR_BASE_URL`) deliberately have no panel row — they are deployment facts, set in the environment or `data/settings.json`, and they keep their historical `vm_` prefix because the studio configured them first and a rename would silently disconnect every station that already has.

Deliberately **not** the alternative of publishing the mixer's telnet port on the host: that port takes unauthenticated commands — push audio on air, skip the record — and publishing it offers that to the whole LAN. The network join offers it to exactly one stack, and assumes the station and Talk Wave share a Docker host.

## What touches a disk, and for how long

The worker writes each finished turn as a clip into the shared `data/onair/`; the web half serves it to the mixer once. The URL's token is the credential — unguessable, and the mixer's HEAD probe peeks without spending it, so the probe cannot rob the download that follows. A clip in a caller's voice is deleted three ways: discarded the moment its one fetch is served (read into memory and gone before the response goes out), swept by a three-minute TTL if the fetch never came, and cleared wholesale when the call's relay closes. What remains afterwards is the transcript — under the Transcripts switch and retention like every other conversation — with a line per aired turn naming the request id the mixer minted for it. The broadcast itself is the station's: what aired, aired, and the held turn is the only take-back there is.
