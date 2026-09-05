"""The operator's buttons and status read for Open Lines.

Admin-gated, with ONE deliberate exception. Opening a line writes to the
broadcast and closing one puts a sign-off on air, so neither is something a
guest code should reach by default — but the player's own ribbon carries a
segment button for signed-in listeners when the operator switches
`open_lines_guest_trigger` on. It ships off. Everything else here, including
the shelf and the close, stays admin only: the shelf is the operator's
writing and /live is cached across every caller, so it is never published.
"""

from __future__ import annotations

import logging

from aiohttp import web

import settings as settings_store
from api.auth import _write_allowed
from api.wire import _cors
from openlines import director, premises, state
from station import StationClient

log = logging.getLogger("callin.openlines")


def _refuse(request: web.Request) -> web.Response:
    return _cors(request, web.json_response(
        {"error": request.get("auth_error") or "not allowed",
         "authRequired": bool(request.get("auth_required"))}, status=401))


def _cfg() -> dict:
    return settings_store.permissions_for(settings_store.load(), "admin")


def status_payload() -> dict:
    """What the panel's card renders.

    `live` is the question the card actually asks, and it is not the same as
    "a record exists": an expired line, or one belonging to a DJ who has since
    gone off air, is on disk but is not open. The card must show what a caller
    would meet, not what was last written.
    """
    cfg = _cfg()
    record = state.read_raw()
    live = state.is_live(record)
    payload = {
        "enabled": bool(cfg.get("open_lines_enabled")),
        "live": live,
        "premise": str(record.get("premise") or ""),
        "spoken": str(record.get("spoken") or ""),
        "persona": str(record.get("persona_name") or ""),
        "openedAt": record.get("opened_at"),
        "expiresAt": record.get("expires_at"),
        "secondsLeft": int(state.seconds_left(record)) if live else 0,
        "remindersSent": int(record.get("reminders_sent") or 0),
        "reminderMax": int(record.get("reminder_max") or 0),
        "source": str(record.get("source") or ""),
        "openedBy": str(record.get("opened_by") or ""),
        "closedReason": str(record.get("closed_reason") or ""),
        "signOff": str(record.get("sign_off_spoken") or ""),
        "cutByShow": bool(record.get("cut_by_show")),
        # So the dashboard can grey "off the shelf" rather than offering a
        # press that can only ever answer "nothing on the shelf".
        "shelfCount": len(premises.read()),
    }
    return payload


def public_open_line(persona: dict, show: dict) -> dict:
    """The open line that is up right now, for the card's stage.

    PUBLIC, and deliberately only this much. What travels is the subject the
    DJ has already announced on air and the moment the line closes — two
    facts every listener has heard. What does not travel is the shelf: the
    operator's own writing, including premises that have never aired and may
    never. `state.current` returns {} unless a record is live FOR THIS DJ AND
    THIS SHOW, so a premise cannot outlive the show it was opened in and be
    read out by the next persona's card.

    Rides the cached payload rather than _for_this_caller: which line is open
    is a fact about the station, the same for everyone, and it changes on the
    scale of a segment rather than of a request.
    """
    from brain.briefing import demojibake
    from openlines import state as ol_state

    show_name = demojibake(str(show.get("name") or ""))
    record = ol_state.current(str(persona.get("id") or ""), show_name)
    if not record:
        return {"live": False}
    return {
        "live": True,
        # The premise is the QUESTION — "tell me about the record that raised
        # you". `spoken` is the whole announcement it arrived inside, which is
        # a paragraph and is not what a stage headline wants.
        "subject": demojibake(str(record.get("premise") or ""))[:220],
        "dj": str(record.get("persona_name") or ""),
        # When the line itself closes. The card shows the earlier of this and
        # the end of the show on the schedule, so it can never promise past
        # either one.
        "expiresAt": record.get("expires_at"),
    }


async def handle_open_lines_status(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    return _cors(request, web.json_response(status_payload()))


async def handle_open_lines_premises(request: web.Request) -> web.Response:
    """The shelf, plus the roster to aim entries at.

    The roster rides along so the panel can draw the per-premise DJ picker
    from one read — it is the same list the persona allowlist ticks, and two
    fetches for one screen is two ways for it to disagree with itself.
    """
    if not _write_allowed(request):
        return _refuse(request)

    import secrets_store

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        roster = await station.personas()
    except Exception as e:                                     # noqa: BLE001
        roster = []
        log.info("premise shelf could not read the roster: %s", e)
    if not roster:
        # /personas times out on a busy station — observed on the operator's,
        # where the panel showed "0 personas" and the DJ picker fell back to
        # raw ids like p_default0. /schedule carries the same roster and is a
        # different, cheaper read, so it answers when the other one will not.
        try:
            roster = (await station.schedule()).get("personas") or []
        except Exception as e:                                 # noqa: BLE001
            log.info("the schedule could not supply a roster either: %s", e)
    try:
        await station.aclose()
    except Exception:                                          # noqa: BLE001
        pass
    return _cors(request, web.json_response({
        "items": premises.read(),
        "personas": [{"id": str(p.get("id") or ""),
                      "name": str(p.get("name") or "")}
                     for p in roster if p.get("id")],
    }))


async def handle_open_lines_premise_add(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    body = await request.json() if request.can_read_body else {}
    item = premises.add(str(body.get("text") or ""),
                        list(body.get("personas") or []))
    if not item:
        return _cors(request, web.json_response(
            {"ok": False, "why": "A topic needs some words."}))
    return _cors(request, web.json_response({"ok": True, "item": item,
                                             "items": premises.read()}))


async def handle_open_lines_premise_edit(request: web.Request) -> web.Response:
    """Edit or delete one entry. DELETE removes; POST updates text and/or aim."""
    if not _write_allowed(request):
        return _refuse(request)
    pid = request.match_info.get("premise_id", "")
    if request.method == "DELETE":
        return _cors(request, web.json_response(
            {"ok": premises.remove(pid), "items": premises.read()}))
    body = await request.json() if request.can_read_body else {}
    item = premises.update(
        pid,
        text=body.get("text") if "text" in body else None,
        personas=list(body["personas"]) if "personas" in body else None,
        enabled=bool(body["enabled"]) if "enabled" in body else None)
    return _cors(request, web.json_response(
        {"ok": bool(item), "item": item, "items": premises.read()}))


async def handle_open_lines_open(request: web.Request) -> web.Response:
    """Put a subject up now, for one full duration.

    Refusals come back with `why` and a 200, not an error status: every one of
    them is a setting the operator can change (switched off, wrong DJ, nobody
    listening, empty list), and a red failure box for "nobody is listening" is
    a bug report waiting to be filed against a working feature.
    """
    # Admin always. A GUEST only when the operator has opted in, because
    # this is the one control on the player page that reaches the broadcast
    # and a guest code travels further than an admin password. Off by
    # default, so nothing changes for a deployment that never touches it.
    if not _write_allowed(request):
        from api.auth import caller_tier

        cfg = _cfg()
        if not (cfg.get("open_lines_guest_trigger")
                and cfg.get("open_lines_enabled")
                and caller_tier(request) in {"guest", "admin"}):
            return _refuse(request)
    body = await request.json() if request.can_read_body else {}
    # "dj" or "shelf", for THIS press only. Absent = whatever the settings
    # page says, which is what the section's own button sends.
    source = str(body.get("source") or "").strip() or None
    # Typed beats picked: somebody who typed a subject meant that one,
    # whatever the dropdown happened to be showing when they hit the button.
    try:
        # The player's ribbon picks a length per press. Clamped, because this
        # arrives from a page a guest can reach.
        minutes = max(0, min(240, int(body.get("minutes") or 0))) or None
    except (TypeError, ValueError):
        minutes = None
    result = await director.open_now(
        reason="operator", source=source, minutes=minutes,
        premise=str(body.get("premise") or "").strip() or None,
        premise_id=str(body.get("premise_id") or "").strip() or None)
    return _cors(request, web.json_response(
        {**result, "status": status_payload()}))


async def handle_open_lines_close(request: web.Request) -> web.Response:
    """Close the line by hand. The sign-off airs on the director's next tick,
    so the operator's press returns immediately rather than waiting on the
    station's TTS — and a stack restarted in between still airs it exactly
    once, because `signed_off` is the latch, not this request."""
    if not _write_allowed(request):
        return _refuse(request)
    closed = state.close(reason="operator")
    return _cors(request, web.json_response(
        {"ok": bool(closed), "status": status_payload()}))
