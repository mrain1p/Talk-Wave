"""Putting a reviewed soundbite on the air: intro, the caller, the close.

Two backends behind one call, because the operator's deployments differ in one
capability and nothing else:

- ``dj-reads``  — the on-air DJ reads the caller's words. Plain admin API
  (/dj/say), works on any deployment, and was clearly audible on every test.
- ``caller-voice`` — the caller's own recording plays on the station's VOICE
  channel: heavy duck, mic_chain, full voice level. Proven on air 2026-08-17
  (RID 409, heard by the operator). Needs two things the plain API does not
  give: the mixer's telnet port (so Talk Wave must share the station's docker
  network) and a URL the mixer can fetch — it curls the clip from us the same
  way it curls every music track from Navidrome, so nothing is ever written
  to the station's disk and there is nothing on their side to clean up.

Why not the sfx door: the first caller clip aired through it was decoded end
to end and heard by nobody (amplify(0.7) against a 3 dB duck), and the
station's segment director reaches anything in the sfx catalogue — it aired an
uploaded caller clip under an invented caller the same night. The voice
channel has neither problem.

Ordering note, because it is the whole trick: /dj/say returns after the
station has HANDED its clip to the mixer, and voice_queue is FIFO — so intro
→ push → action → close needs no sleeps and no duration arithmetic. Each
piece queues behind the last and the duck holds across the joins.

The receipts discipline is deliver.py's: the station's answer is what
happened, and the close the DJ speaks is chosen AFTER the action's real
result — never a promise made before the station answered.
"""

from __future__ import annotations

import logging

# The transport lives in onair.transport since the live-call relay became its
# second consumer; these names are re-exported because this module IS the
# studio's air interface and its tests read them here. `deliver` below is the
# studio's orchestration of that shared transport, nothing more.
from onair.transport import (  # noqa: F401  (air_base_url et al are our API)
    DEFAULT_MIXER,
    air_base_url,
    mixer_address,
    mixer_reachable,
    telnet_push,
)
from voicemail import review

log = logging.getLogger("callin.voicemail")


def _quote(text: str, limit: int = 420) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


async def _run_staged_tool(station, cfg: dict, action: dict) -> tuple[str, bool]:
    """Replay a staged tool call through the LIVE wrapper — the exact code a
    call runs — re-gated for the caller's tier at send (cfg is already
    permissions_for-resolved by deliver). A permission the operator switched
    off since the preview is refused here, like the takeover re-check above.
    The wrapper notes a clean (kind, detail) to the ledger on success; a
    refusal leaves it empty and its returned sentence carries the reason."""
    from voicemail.preview import build_action_tools

    name = str(action.get("name") or "")
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    tools, actions = await build_action_tools(cfg, station)
    tool = next((t for t in tools if t.info.name == name), None)
    if tool is None:
        return f"'{name}' is not an action this line allows any more", False
    try:
        out = await tool(**args)
    except Exception as e:                                     # noqa: BLE001
        return f"that action didn't run ({type(e).__name__})", False
    if actions.taken:
        act_kind, detail = actions.taken[-1]
        return (f"{act_kind}: {detail}" if detail else act_kind), True
    # No ledger note = the wrapper refused (the action cap, or the station said
    # no); its sentence carries the reason.
    return str(out).strip().split(".")[0][:160], False


async def _run_action(station, cfg: dict, action: dict) -> tuple[str, bool]:
    """Execute the ONE action the caller approved, exactly as resolved at
    review time. Returns (receipt line, ok)."""
    kind = str((action or {}).get("kind") or "none")
    if kind == "none" or not action:
        return "no action asked for", True
    if kind == "tool":
        return await _run_staged_tool(station, cfg, action)
    if kind == "queue":
        track = dict(action.get("track") or {})
        result = await station.queue_track(track)
        title = str(track.get("title") or "the track")
        artist = str(track.get("artist") or "")
        name = f"{title} — {artist}" if artist else title
        if result.get("ok"):
            return f"queued {name}", True
        return (f"the station refused {name}: "
                f"{result.get('error') or 'no reason given'}"), False
    if kind == "request":
        result = await station.submit_request(str(action.get("text") or ""))
        if result.get("ok", True) and not result.get("error"):
            return "sent as a request", True
        return f"request failed: {result.get('error')}", False
    if kind == "takeover":
        # The switch is read AGAIN at send: settings are re-read per action
        # everywhere else, and a preview minted while takeover was allowed
        # must not execute after the operator turned it off.
        if not cfg.get("allow_takeover"):
            return "takeovers are switched off on this line", False
        result = await station.pin_show(str(action.get("showId") or ""), 60)
        who = str(action.get("who") or action.get("show") or "that show")
        if result.get("ok"):
            return (f"put {who}'s show on air ({action.get('show')}) for the "
                    "next hour — it starts at the end of the current record"),\
                   True
        return (f"the station refused the takeover: "
                f"{result.get('error') or 'no reason given'}"), False
    return f"unknown action '{kind}' — did nothing", False


async def deliver(station, cfg: dict, draft: dict) -> dict:
    """One reviewed draft, on air, with the DJ around it.

    Returns {ok, backend, receipt, lines} — receipt for the operator's
    records, lines being what the DJ was told to do (not what it said; the
    station's styled mode owns the wording).
    """
    transcript = _quote(draft.get("transcript"))
    action = dict(draft.get("action") or {})
    # Re-gate for the caller's tier at send — the same defence-in-depth the
    # takeover branch already ran, now for every staged tool: a permission the
    # operator switched off since the preview is refused here, not aired. Only
    # the tiered permissions collapse; the backend/mixer settings below are
    # untouched.
    import settings as settings_store
    cfg = settings_store.permissions_for(cfg, str(draft.get("tier") or "open"))
    wanted = str(cfg.get("vm_air_backend") or "dj-reads")
    backend = wanted
    note = ""

    base = air_base_url(cfg)
    if backend == "caller-voice" and (not base or not mixer_reachable(cfg)):
        # Fail soft, and SAY so — silently downgrading is how an operator
        # spends a week not noticing the network stanza never landed.
        backend = "dj-reads"
        note = ("caller-voice unavailable ("
                + ("no reachable mixer" if base else "no air base URL")
                + ") — the DJ read it instead")
        log.warning("vm air: %s", note)

    lines: list[str] = []

    async def say(instruction: str) -> bool:
        lines.append(instruction)
        result = await station.dj_say(instruction, mode="styled",
                                      kind="callin")
        if not result.get("ok"):
            log.warning("vm air say failed: %s", result.get("error"))
        return bool(result.get("ok"))

    if backend == "caller-voice":
        ok_intro = await say(
            "A listener called in and their recorded message is about to "
            "play on air. In one short sentence, hand over to the caller — "
            "do not summarise what they say.")
        if ok_intro:
            # /dj/say's 200 means the intro is WRITTEN to the mixer's handoff
            # file, not read from it — the mixer polls say.txt every 0.5s,
            # while the telnet push lands in voice_queue instantly. Pushing
            # inside that window queued the caller's clip AHEAD of the intro
            # and the operator heard their own voice land before either DJ
            # line (2026-08-17). Out-wait the poll; a clip cannot beat an
            # intro that is already in the queue.
            import asyncio

            await asyncio.sleep(0.8)
        token = review.mint_air_token(str(draft.get("id")))
        rid = telnet_push(cfg, f"{base}/vm-air/{token}") if token else None
        if rid is None:
            # The intro may already be airing; the honest recovery is the
            # DJ reading the message, not dead air after a hand-over.
            backend = "dj-reads"
            note = "the mixer refused the push — the DJ read it instead"
            log.warning("vm air: %s", note)
        else:
            if not ok_intro:
                note = "the intro line failed; the clip aired without one"
            act_receipt, act_ok = await _run_action(station, cfg, action)
            closing = (
                f"A caller's message just played on air, saying: "
                f"\"{transcript}\". "
                + (f"You have {act_receipt}. React to the caller warmly and "
                   "say what you did, in your own words."
                   if action and act_ok else
                   f"IMPORTANT: {act_receipt} — do NOT claim it worked. "
                   "React to the caller and be honest about that."
                   if action else
                   "React to the caller warmly, in your own words.")
                + " Two short sentences.")
            ok_close = await say(closing)
            receipt = f"aired the caller's own voice (RID {rid}); {act_receipt}"
            if not ok_close:
                receipt += "; the close failed to air"
            return {"ok": act_ok, "backend": "caller-voice",
                    "receipt": receipt + (f" [{note}]" if note else ""),
                    "lines": lines}

    # dj-reads — by choice, or as the fallback either failure above took.
    act_receipt, act_ok = await _run_action(station, cfg, action)
    read = (
        f"A listener called in with this message: \"{transcript}\". "
        "Tell the listeners a caller rang in and read the message to them "
        "in your own voice, keeping the caller's meaning exact. "
        + (f"You have {act_receipt} — say what you did."
           if action and act_ok else
           f"IMPORTANT: {act_receipt} — do NOT claim it worked; be honest."
           if action else "")
        + " Keep the whole thing to a few sentences.")
    ok_read = await say(read)
    receipt = ("the DJ read the caller's message; " + act_receipt
               + (f" [{note}]" if note else ""))
    if not ok_read:
        receipt = "the read failed to air; " + receipt
    return {"ok": act_ok and ok_read, "backend": "dj-reads",
            "receipt": receipt, "lines": lines}
