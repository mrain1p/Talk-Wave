"""Reading back what happened: the /calls record store and the /logs viewer.

Split out of api/diagnostics.py (the maintainability plan, Batch 2). Diagnostics
is "does the configuration work" (the /test/* probes and the prompt preview);
this is "what already happened" — the operator reading transcripts and log
lines after the fact. They share only the admin gate and the CORS edge, so the
seam is clean: nothing here probes anything, and nothing in diagnostics reads a
record.

The worker writes call records (call/record.py); this process only reads them,
and attaches the mint-time caller context it holds in memory (api.tokens's
_mint_info) that the worker never saw.
"""

from __future__ import annotations

import logging

from aiohttp import web

from api.auth import _write_allowed
from api.tokens import _mint_info
from api.wire import _cors

log = logging.getLogger("callin.token")


async def handle_calls(request: web.Request) -> web.Response:
    """Recent calls, both sides of each conversation.

    The worker writes these; this process only reads them. Operator-only —
    it's a transcript of what callers said.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    from call.record import recent

    # The worker writes the record and never sees the browser that called, so
    # what we knew at mint time is attached here rather than stored with it.
    calls = recent(20)
    for c in calls:
        known = _mint_info.get(c.get("room") or "")
        if known:
            c["caller"] = known
    return _cors(request, web.json_response({"calls": calls}))


async def handle_clear_calls(request: web.Request) -> web.Response:
    """Throw away every stored call record.

    `record_keep` only trims as new calls arrive, so a deployment that has gone
    quiet keeps whatever it last had indefinitely — and after a run of test
    calls the panel is mostly stale conversations you have already read. This
    is the operator saying so.

    The mint-time caller context goes with them. It lives in memory here rather
    than in the record, so clearing the records alone would leave the panel
    able to say which browser and which network rang for a call whose
    transcript no longer exists.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    from call.record import clear

    gone = clear()
    _mint_info.clear()
    log.info("call records cleared by the operator (%d removed)", gone)
    return _cors(request, web.json_response({"ok": True, "removed": gone}))


async def handle_delete_call(request: web.Request) -> web.Response:
    """Delete ONE stored record.

    Clear-all was the only option, so removing a single bad test call meant
    throwing away every conversation on the box — including the ones you were
    about to read back.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    from call.record import delete_one

    rid = request.match_info.get("rid", "")
    if not delete_one(rid):
        return _cors(request, web.json_response(
            {"error": "no such call record"}, status=404))
    # The mint-time context is keyed the same way and must go with it, or the
    # panel could still say which browser rang for a transcript that no
    # longer exists — the reason clear-all drops it too.
    _mint_info.pop(rid, None)
    log.info("call record %s deleted by the operator", rid)
    return _cors(request, web.json_response({"ok": True}))


async def handle_mark_call(request: web.Request) -> web.Response:
    """The operator's own verdict on ONE record.

    The caller's thumbs were the only verdict a call could carry, and they
    arrive from the one person who cannot hear how the call sounded from the
    outside — most calls carry no rating at all, and a run of test calls placed
    by the operator carries none by definition. This is the operator marking
    what they heard, stored beside the caller's rather than over it.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    from call.record import mark_one

    try:
        body = await request.json()
    except Exception:
        body = {}
    mark = str((body or {}).get("mark") or "").strip().lower()
    if mark not in ("up", "down", ""):
        return _cors(request, web.json_response(
            {"error": "mark must be up, down, or empty to clear"}, status=400))
    rid = request.match_info.get("rid", "")
    if not mark_one(rid, mark):
        return _cors(request, web.json_response(
            {"error": "no such call record"}, status=404))
    return _cors(request, web.json_response({"ok": True, "mark": mark}))


async def handle_clear_logs(request: web.Request) -> web.Response:
    """Empty the log viewer's buffer.

    In memory only — docker still holds its own copy of this process's stdout,
    so this clears what the panel shows rather than destroying the record.
    """
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    import log_setup

    gone = log_setup.clear()
    log.info("log buffer cleared by the operator (%d lines removed)", gone)
    return _cors(request, web.json_response({"ok": True, "removed": gone}))


async def handle_logs(request: web.Request) -> web.Response:
    """The web service's recent log lines, for the panel's log viewer —
    settings changes, tokens minted, station reads, webhook events. The
    call agent runs in its own container; its logs need
    `docker logs <worker container>`."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))},
            status=401,
        ))
    import log_setup

    records = log_setup.recent_records(300)
    return _cors(request, web.json_response({
        "records": records,
        # The flattened form stays, so an older widget cached in a browser
        # keeps working rather than showing an empty box after an upgrade.
        "lines": log_setup.recent_lines(300),
        # What is actually present, so the filter offers real choices rather
        # than a fixed list of levels that may match nothing.
        "levels": sorted({r["level"] for r in records}),
        "sources": sorted({r["logger"] for r in records}),
    }))
