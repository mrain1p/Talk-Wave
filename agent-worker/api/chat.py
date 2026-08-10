"""The text line's door: a WebSocket per chat, gated like the phone.

Browsers cannot set headers on a WebSocket, so the credentials the phone
sends as X-Call-Key arrive here in the FIRST message ({"type": "hello"})
rather than on the request — the gate is the same one, checked one frame
later. Everything else mirrors /token deliberately: The Line outranks the
mode, the guest ladder decides who may use it, and ceilings exist because a
text endpoint that spends LLM money is scriptable with curl in a way a
WebRTC call never was. The station's own text surface took a real raid
(2026-07-28, request-guard.ts) — these gates are that lesson, worn locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from aiohttp import WSMsgType, web

import settings as settings_store
from api.auth import _guest_check, caller_tier
from api.wire import _caller_key
from chat.session import SHELF

log = logging.getLogger("callin.chat")

# Fresh chats opened, as timestamps — the global ceiling's ledger, exactly
# _recent_mints' shape in tokens.py: a full day is kept so the daily wallet
# cap has something to count, and the hourly figure is a slice of it.
_recent_opens: list[float] = []
# caller key -> when they last opened a chat. The PER-IP brake the phone has
# (caller_cooldown_secs) and chat lacked: the global ceilings stop a runaway,
# but without this one abuser is never singled out from everyone else. Same
# shape as tokens._caller_last.
_chat_caller_last: dict[str, float] = {}


def _open_refusal(cfg: dict, request: web.Request) -> str | None:
    """Ceilings that apply only to OPENING a fresh chat, not to resuming or
    to sending within one. Kept apart from _refusal (which answers "may this
    caller use the line at all") because a resume must skip these — the cost
    is a new conversation, and resuming spends nothing new here.

    Mirrors _check_usage in tokens.py: per-IP cooldown, global daily wallet,
    global hourly. In-world wording, because a stranger opening a chat should
    read the booth as busy, not be shown a rate-limit code."""
    import time as _time

    now = _time.time()
    _recent_opens[:] = [t for t in _recent_opens if t > now - 86400]
    for k, at in list(_chat_caller_last.items()):
        if now - at > 86400:
            _chat_caller_last.pop(k, None)

    cooldown = int(cfg.get("chat_caller_cooldown_secs") or 0)
    if cooldown > 0:
        last = _chat_caller_last.get(_caller_key(request))
        if last and (now - last) < cooldown:
            wait = int(cooldown - (now - last))
            return f"You've only just been in — give it {wait}s before opening another."

    per_day = int(cfg.get("chats_per_day") or 0)
    if per_day > 0 and len(_recent_opens) >= per_day:
        return ("The text line has had a lot of attention today. "
                "Try again tomorrow.")

    per_hour = int(cfg.get("chats_per_hour") or 0)
    this_hour = sum(1 for t in _recent_opens if t > now - 3600)
    if per_hour > 0 and this_hour >= per_hour:
        return "The text line has been busy this hour — try again a little later."
    return None


def _refusal(cfg: dict, request: web.Request, key: str) -> str | None:
    """A caller-facing reason the text line is closed, or None to allow.
    Same in-world register as _check_usage: nobody is told about rate
    limits, they are told the booth is busy."""
    if not cfg.get("chat_enabled"):
        return "The booth doesn't have a text line."
    if cfg.get("calls_paused"):
        # The kill switch is the LINE; the modes hang off it. A paused line
        # that still chatted would make the dashboard's one big switch a lie
        # — the same hierarchy voicemail learned.
        return "The booth isn't taking anything at the moment — the line's closed for now."
    reason = _guest_check(key, _caller_key(request))
    if reason:
        return reason
    if not settings_store.tier_reaches(cfg.get("allow_chat"), _tier_for(key)):
        return "The booth doesn't take texts on this line."
    return None


def _tier_for(key: str) -> str:
    """caller_tier reads headers; the WS key arrives in-band, so the same
    question is asked of the key directly."""
    import admin_auth
    from api.auth import _key_valid

    if key and _key_valid(key):
        return "admin"
    if key and admin_auth.verify_guest(key):
        return "guest"
    return "open"


async def _relay(ws: web.WebSocketResponse, chat, make_coro) -> None:
    """Run one DJ turn under the chat's lock, relaying its streamed events to
    the socket. `make_coro(put)` is the coroutine that produces the turn
    (a reply or the opening greeting); it pushes {"type": ...} dicts through
    `put`, and finishing with the queue drained is the end of the turn.

    The turn's task is cancelled on any exit — including a wait_for timeout one
    level up — so a stalled model does not keep typing into a transcript after
    the caller has been told the line gave up.
    """
    events: asyncio.Queue = asyncio.Queue()
    async with chat.lock:
        task = asyncio.create_task(make_coro(events.put_nowait))
        try:
            while True:
                getter = asyncio.create_task(events.get())
                await asyncio.wait({task, getter},
                                   return_when=asyncio.FIRST_COMPLETED)
                if getter.done():
                    await ws.send_json(getter.result())
                else:
                    getter.cancel()
                if task.done() and events.empty():
                    err = task.exception()
                    if err:
                        # The same promise the phone makes: a model outage is
                        # never silence.
                        log.warning("chat %s turn failed: %s", chat.id, err)
                        await ws.send_json(
                            {"type": "done",
                             "text": "Line dropped a beat there — say that "
                                     "again for me?"})
                    break
        finally:
            if not task.done():
                task.cancel()


async def _run_turn(ws: web.WebSocketResponse, chat, make_coro, cfg: dict) -> None:
    """One DJ turn with the caller-facing dressing: a typing cue first so the
    booth is visibly composing, and a hard reply timeout so a model that never
    answers cannot leave the caller watching a dot spin forever."""
    await ws.send_json({"type": "typing"})
    timeout = float(cfg.get("chat_reply_timeout_secs") or 0)
    try:
        if timeout > 0:
            await asyncio.wait_for(_relay(ws, chat, make_coro), timeout)
        else:
            await _relay(ws, chat, make_coro)
    except asyncio.TimeoutError:
        log.warning("chat %s turn timed out after %ss", chat.id, timeout)
        await ws.send_json(
            {"type": "done",
             "text": "Still digging for that one — give me another go in a "
                     "moment?"})


async def handle_chat_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    cfg = settings_store.load()
    SHELF.sweep(cfg)

    chat = None
    msg_times: list[float] = []
    nudged = False
    try:
        while True:
            # When the ball is in the CALLER's court, wait only so long before
            # the DJ nudges once — a text line that sits silent after its own
            # last message feels dead and turn-based (operator's ask). On by
            # default; the switch and interval are settings. Never repeated
            # (reset when they type) and never while a turn is mid-flight, so it
            # can't fire while the DJ is the one still owing a reply.
            reprompt = (
                float(cfg.get("chat_reprompt_secs") or 0)
                if cfg.get("chat_reprompt", True) and chat is not None
                and not nudged and not chat.lock.locked()
                else 0
            )
            try:
                msg = (await ws.receive(timeout=reprompt) if reprompt > 0
                       else await ws.receive())
            except asyncio.TimeoutError:
                nudged = True
                if chat is not None and not chat.lock.locked():
                    await _run_turn(ws, chat, lambda put: chat.nudge(cfg, put), cfg)
                continue
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING,
                            WSMsgType.CLOSED, WSMsgType.ERROR):
                break
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                body = json.loads(msg.data)
            except Exception:                                  # noqa: BLE001
                continue
            kind = str(body.get("type") or "")

            if kind == "hello":
                cfg = settings_store.load()
                key = str(body.get("key") or "")
                refusal = _refusal(cfg, request, key)
                if refusal:
                    await ws.send_json({"type": "refused", "error": refusal})
                    break
                wanted = str(body.get("chat") or "")
                is_fresh = wanted not in SHELF.chats
                # Only a FRESH open faces the ceilings — resuming an existing
                # conversation costs nothing new and must not be rate-limited
                # out of the caller's own chat.
                if is_fresh:
                    over = _open_refusal(cfg, request)
                    if over:
                        await ws.send_json({"type": "refused", "error": over})
                        break
                chat = SHELF.get_or_open(wanted, _tier_for(key), cfg)
                if chat is None:
                    await ws.send_json({"type": "refused", "error":
                                        "The booth's text lines are all "
                                        "tied up — give it a minute."})
                    break
                if is_fresh:
                    now = time.time()
                    _recent_opens.append(now)
                    _chat_caller_last[_caller_key(request)] = now
                    log.info("chat %s opened (%s, %d open)", chat.id,
                             chat.tier, len(SHELF.chats))
                # The transcript rides back so a resumed chat repaints what
                # was already said instead of starting visually blank.
                await ws.send_json({"type": "ready", "chat": chat.id,
                                    "dj": chat.persona_name,
                                    "turns": [{"who": w, "text": t}
                                              for w, t in chat.turns[-40:]]})
                # A FRESH chat gets the booth's opening line — the text line
                # answering with silence until the caller types reads as
                # broken. A resumed chat does not: its opener is already in the
                # replayed transcript above.
                if is_fresh and str(cfg.get("chat_greeting_mode") or "off") != "off":
                    await _run_turn(ws, chat,
                                    lambda put: chat.greet(cfg, put), cfg)
                continue

            if kind == "bye" and chat is not None:
                # The caller ended it deliberately — write the record now and
                # drop the chat, rather than leaving it for the idle sweep.
                # The id stops resuming, which is what the widget's End does:
                # a fresh open after this is a new conversation.
                chat.write_record("the caller ended the chat")
                SHELF.chats.pop(chat.id, None)
                log.info("chat %s ended by the caller (%d msgs)", chat.id,
                         chat.messages)
                await ws.send_json({"type": "ended"})
                break

            if kind == "msg" and chat is not None:
                text = str(body.get("text") or "").strip()[:2000]
                if not text:
                    continue
                # They typed — the ball is back with the DJ, so the next silence
                # earns a fresh nudge.
                nudged = False
                # The flood brake, per chat: a human types a handful of
                # messages a minute; a script does not.
                now = time.time()
                msg_times[:] = [t for t in msg_times if t > now - 60]
                if len(msg_times) >= int(cfg.get("chat_msgs_per_minute") or 10):
                    await ws.send_json({"type": "refused", "error":
                                        "Easy on the keys — give it a "
                                        "breath and try again."})
                    continue
                msg_times.append(now)
                if chat.lock.locked():
                    await ws.send_json({"type": "refused", "error":
                                        "One at a time — the DJ is still "
                                        "answering the last one."})
                    continue
                # Stream the reply as it happens, wrapped in the typing cue and
                # the reply timeout — see _run_turn.
                await _run_turn(ws, chat,
                                lambda put: chat.ask(text, put), cfg)
    finally:
        SHELF.sweep(settings_store.load())
    return ws
