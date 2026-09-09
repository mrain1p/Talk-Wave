"""The dashboard's station-override card: what stands, and the one clear.

A takeover or a genre lock outlives the call that set it — a caller with the
permission can point the station somewhere and hang up. Until this card,
neither panel showed one standing: this dashboard had only the permission
row, and the station's own quick control is still an open PR upstream. The
read is the station's public /schedule (the override plus the shows list to
name it); the clear is the same admin DELETE the DJ's cancel tool uses, so
the operator can lift what a caller set without hunting the transcript.
"""

from __future__ import annotations

import logging

from aiohttp import web

import settings as settings_store
from api.auth import _guest_ok, _write_allowed, caller_tier
from api.wire import _cors
from station import StationClient

log = logging.getLogger("callin.override")

# The reserved show id the genre lock pins — the same mirror
# call/tools/broadcast.GENRE_LOCK_SHOW_ID keeps, for the same reason: the
# card names the two differently, because "pinned to The Graveyard Shift"
# and "locked to a genre" are different sentences to an operator.
GENRE_LOCK_SHOW_ID = "genre_lock"


def _refuse(request: web.Request) -> web.Response:
    return _cors(request, web.json_response(
        {"error": request.get("auth_error") or "not allowed",
         "authRequired": bool(request.get("auth_required"))}, status=401))


async def override_payload() -> dict:
    """The pin currently in force, named, or {"active": False}.

    /schedule reports an expired or dangling override as null already, so
    active here means the station will actually honour it. The show name is
    resolved from the same payload's shows list — one read, no second fetch
    to disagree with the first.
    """
    station = StationClient()
    try:
        schedule = await station.schedule()
    finally:
        try:
            await station.aclose()
        except Exception:                                      # noqa: BLE001
            pass
    ov = schedule.get("override") if isinstance(schedule, dict) else None
    if not isinstance(ov, dict):
        return {"active": False}
    # `showId: null` INSIDE an override object is Default programming — the
    # operator pinning the station's own mix over the grid (upstream #1543,
    # schemas/schedule.ts:229: "showId: null means Default programming; an
    # outer scheduleOverride: null means there is no takeover at all"). Read
    # as falsy it came back as "nothing is pinned", which is the one state it
    # is not (upstream pass, 2026-09-07).
    if "showId" not in ov:
        return {"active": False}
    if ov.get("showId") is None:
        return {
            "active": True,
            "kind": "default-programming",
            "showId": None,
            "show": "Default programming",
            "startedAt": ov.get("startedAt"),
            "expiresAt": ov.get("expiresAt"),
        }
    show_id = str(ov.get("showId") or "")
    if not show_id:
        return {"active": False}
    name = ""
    for s in (schedule.get("shows") or []):
        if isinstance(s, dict) and str(s.get("id") or "") == show_id:
            name = str(s.get("name") or "")
            break
    return {
        "active": True,
        "kind": "genre-lock" if show_id == GENRE_LOCK_SHOW_ID else "takeover",
        "showId": show_id,
        "show": name,
        "startedAt": ov.get("startedAt"),
        "expiresAt": ov.get("expiresAt"),
    }


async def handle_override_status(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _refuse(request)
    try:
        payload = await override_payload()
    except Exception as e:                                     # noqa: BLE001
        # A station that will not answer is not "no override" — the card
        # hides on active:False, and hiding a pin that may still stand
        # because one read timed out would be the dashboard lying.
        log.info("override read failed: %s", e)
        payload = {"active": False, "unreachable": True}
    return _cors(request, web.json_response(payload))


def _takeover_allowed(request: web.Request) -> bool:
    """Whether THIS caller may pin a show, by the same rule the call line
    uses for the DJ's own takeover tool.

    The operator's ask (2026-09-08) was exactly this: "if a usertype has
    permission to change the dj/takeover, then they should have permission to
    do this from this page". So the gate is `allow_takeover` against the
    caller's tier — not the panel's admin write check — and the guide's
    button is offered on the same answer, sent as `takeoverMine` on /live.
    An operator whose password the browser holds clears it either way, since
    admin is the top of the ladder.

    Still behind the phone's own door: a code-gated line wants the code
    before it will even say who is asking.
    """
    if not _guest_ok(request):
        return False
    return settings_store.tier_reaches(
        settings_store.load().get("allow_takeover"), caller_tier(request))


def _tier_refuse(request: web.Request) -> web.Response:
    return _cors(request, web.json_response(
        {"error": "your access level does not include changing the DJ"},
        status=403))


async def handle_override_set(request: web.Request) -> web.Response:
    """POST /station/override {showId, minutes} — put a show on air now.

    The guide's own takeover button. `showId: null` is Default programming,
    the station's own mix pinned over the grid (#1543) — the same shape the
    read above already understands, so the card can offer "hand it back to
    the station" as a pin rather than as a clear.

    The window is clamped to the station's own bounds here rather than being
    posted and refused with a 400, exactly as the DJ's tool does it.
    """
    if not _takeover_allowed(request):
        return _tier_refuse(request)
    import secrets_store

    try:
        body = await request.json()
    except Exception:                                          # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    if "showId" not in body:
        return _cors(request, web.json_response(
            {"error": "showId is needed - which show?"}, status=400))
    # None is DEFAULT PROGRAMMING, not "no show" — the station pins its own
    # mix over the grid and reports it back as an override with a null
    # showId (#1543). Absent is a caller's mistake and was refused above;
    # the two must not read the same.
    raw = body.get("showId")
    show_id = None if raw is None else str(raw).strip()
    if show_id == "":
        return _cors(request, web.json_response(
            {"error": "showId is needed - which show?"}, status=400))
    until = "schedule-change" if body.get("until") == "schedule-change" else "fixed"
    try:
        asked = int(body.get("minutes") or 0)
    except (TypeError, ValueError):
        asked = 0
    window = max(StationClient.TAKEOVER_MIN_MINUTES,
                 min(StationClient.TAKEOVER_MAX_MINUTES, asked or 60))

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        res = await station.pin_show(show_id, window, until=until)
    finally:
        try:
            await station.aclose()
        except Exception:                                      # noqa: BLE001
            pass
    if not (isinstance(res, dict) and res.get("ok")):
        return _cors(request, web.json_response(
            res if isinstance(res, dict) else {"ok": False}, status=502))
    from api import guide
    from call import daylog

    # The week the card re-reads must show the pin that was just made.
    guide.forget()
    daylog.note("takeover", (show_id or "Default programming")
                + (" until the schedule changes" if until == "schedule-change"
                   else f" for {window} min"),
                tier=caller_tier(request))
    return _cors(request, web.json_response(
        {"ok": True, "showId": show_id, "minutes": window, "until": until}))


async def handle_override_clear(request: web.Request) -> web.Response:
    # Whoever may SET a takeover may lift one. The dashboard's own admin
    # check still passes (admin is the top of the ladder); what this adds is
    # the tier the operator granted `allow_takeover` to — which can already
    # cancel one by asking the DJ (subwave_cancel_takeover is gated on the
    # same setting), so the guide is not a new power, only a shorter route
    # to it. Without this the guide's button could pin a show and offer no
    # way back from the page that pinned it.
    if not (_write_allowed(request) or _takeover_allowed(request)):
        return _refuse(request)
    import secrets_store

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        res = await station.clear_pinned_show()
    finally:
        try:
            await station.aclose()
        except Exception:                                      # noqa: BLE001
            pass
    from api import guide

    guide.forget()
    return _cors(request, web.json_response(
        res if isinstance(res, dict) else {"ok": False}))
