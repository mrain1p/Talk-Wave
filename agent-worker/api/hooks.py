"""What the station pushes at us, and keeping it warm enough to answer.

The station can push track.play / dj.say / dj.link / request.received to a URL
instead of us polling for them. Receiving them lets the on-air card refresh the
moment something changes and gives later features a real event source.

Registration is a reconcile, not an append: read the station's list, find our
row, write the list back with ours in it. The station replaces the array
wholesale, so every other row has to be carried through untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import httpx
from aiohttp import web

import settings as settings_store
from api.auth import _write_allowed
from api.env import PORT
from api.wire import _cors
from log_setup import describe
from api.hook_receiver import (  # noqa: F401  (shared state, one-way; the
    # receiver's own faces are re-exported so its callers — diagnostics, the
    # tests — keep one import for the webhook subject)
    HOOK_ID,
    WANTED_EVENTS,
    _BUSTING_PREFIXES,
    _air_path,
    _hook_events,
    _hook_state,
    _load_hook_secret,
    _mint_hook_secret,
    _secret_path,
    _store_hook_secret,
    _unauthorised,
    handle_hooks_recent,
    handle_station_hook,
)
from station import StationClient

log = logging.getLogger("callin.token")

# Serialises registration: startup fires two register_station_webhook() calls
# (the warm loop's first tick and a standalone task), and without this they
# raced load->mint->store on the shared secret, leaving the station holding
# one secret and disk another until the next re-key (top-down review,
# 2026-08-28). Serialised, the second caller reads the first's stored secret
# and the settled short-circuit skips a redundant re-POST.
_register_lock = asyncio.Lock()


async def _register_once() -> None:
    """register_station_webhook under the serialising lock."""
    async with _register_lock:
        await register_station_webhook()


# The id this app registered under before the 0.10.52 rename, recognised as
# ours forever. The rename shipped claiming "nothing behaves differently" and
# this was the exception it missed: an upgraded deployment whose URL had also
# changed would register a fresh talk_wave row and leave the old wave_talk
# one behind, burning one of the station's sixteen webhook slots for good.
# Adopt it where it can be adopted; delete strays where it can't.
LEGACY_HOOK_IDS = ("wave_talk",)

# How long a transient failure is retried before standing down. Every attempt
# carries the admin credentials, and unbounded retries have been observed
# tripping a station's LOGIN rate limiter and locking the operator out of
# their own admin UI.
_MAX_ATTEMPTS = 5

# How long a test fire waits for the push to come back. The station awaits its
# own POST before answering us, so this is slack for the event loop rather
# than a network budget.
# How long to wait for the station's own POST to land after it says it sent
# one. Raised from 3.0 at 0.10.157: three seconds is generous against a healthy
# station and tight against a struggling one, and the operator's box was doing
# both on 2026-08-15 — the webhook row failed while the same station was timing
# out `/state` reads during a live call. A button that takes a few seconds
# longer is cheaper than a row that reports a network fault there isn't one.
_DELIVERY_WAIT = 8.0

def _lan_ip() -> str:
    """The address the NAS can reach this box on — NOT localhost."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _receiver_url() -> str:
    return os.environ.get("CALLIN_HOOK_URL") or f"http://{_lan_ip()}:{PORT}/hooks/station"


def _admin_client(user: str, password: str) -> httpx.AsyncClient:
    """A client for the station's admin API, pointed at the station we are
    configured for right now — settings can change while this runs."""
    return httpx.AsyncClient(
        base_url=settings_store.station_base_url(),
        timeout=10.0,
        auth=httpx.BasicAuth(user, password),
    )


def _station_said(r: httpx.Response) -> str:
    """The station's own words for a rejection.

    Since SUB/WAVE 1.6.0 the admin API validates at the route boundary and a
    400 carries `{error, fieldErrors}`: `error` is a sentence written for an
    operator ("URL must start with http:// or https://"), and fieldErrors maps
    a dotted path to the same. Older stations send a flat `{error}`, and a
    proxy in between may send neither, so take the first of those that is
    actually there.

    This used to be thrown away, and the panel reported "station did not
    accept either registration shape" for every possible cause — which is
    exactly the flat, unactionable error the field-level payload exists to
    replace.
    """
    try:
        body = r.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        if body.get("error"):
            return str(body["error"])[:200]
        fields = body.get("fieldErrors")
        if isinstance(fields, dict) and fields:
            path, msg = next(iter(fields.items()))
            if isinstance(msg, list):
                msg = msg[0] if msg else ""
            return f"{path}: {msg}"[:200]
    text = (r.text or "").strip()
    return text[:200] if text else f"HTTP {r.status_code}"


async def handle_hooks_test(request: web.Request) -> web.Response:
    """Prove a push can actually get from the station to us."""
    if not _write_allowed(request):
        return _unauthorised(request)
    return _cors(request, web.json_response(await fire_test_hook()))


def _our_row(rows: list[dict], url: str) -> dict | None:
    """Our row in the station's list, if it holds one.

    By id first (current, then the pre-rename legacy id), then by URL: a
    registration made before we started sending an id carries a station-minted
    one, and matching only on id would leave that row in place and add a
    second pointing at the same address.
    """
    for row in rows:
        if row.get("id") == HOOK_ID:
            return row
    for row in rows:
        if row.get("id") in LEGACY_HOOK_IDS:
            return row
    for row in rows:
        if row.get("url") == url:
            return row
    return None


def _registration_due() -> bool:
    """Whether the warm tick should try again.

    Not simply "have we registered": the station we registered AT is part of
    the answer. Pointing the panel at a different station used to leave
    `registered` true from the old one, so the new station never got a
    receiver and the card silently fell back to polling for good.
    """
    if not register_enabled():
        # No retry loop for a switch somebody set on purpose — the reason is
        # already standing in the hooks row.
        return False
    station = settings_store.station_base_url()
    if _hook_state.get("registered") and _hook_state.get("station") == station:
        # Our OWN callback address can move too — a non-container box whose
        # DHCP lease changes mid-process keeps the station pushing to the old,
        # now-dead URL, silently (received frozen, rejected 0, so _mis_keyed
        # stays False). Re-register to repoint it (top-down review,
        # 2026-08-28). The station-moved case is handled below; this is the
        # receiver-moved mirror it was missing.
        if _hook_state.get("url") and _hook_state["url"] != _receiver_url():
            return True
        # …unless the station is pushing with a header we cannot verify, which
        # is a registration that succeeded and achieved nothing. Being due is
        # what gets the row re-keyed; see _mis_keyed.
        return _mis_keyed()
    if _hook_state.get("station") not in ("", station):
        # A different station is a different question, so a previous refusal
        # does not carry over to it.
        _hook_state.update(registered=False, station="")
        _hook_state.pop("gave_up", None)
        _hook_state.pop("attempts", None)
    return not _hook_state.get("gave_up")


# A run of refusals this long, with nothing getting in between them, is a
# broken key rather than a stray probe. Only reached by a row that WAS working:
# the never-worked case is caught by `received == 0` on the first refusal.
_BREAK_RUN = 10
# …and no more than one re-key per this, so a device on the LAN poking the
# receiver cannot make us rewrite the station's row in a loop.
_REKEY_COOLDOWN = 600.0


def _mis_keyed() -> bool:
    """Whether the station is pushing with a header we cannot verify.

    Two shapes, and the second was missing until the audit that followed the
    first being fixed.

    NEVER WORKED: rejections while NOTHING has ever been accepted. That is the
    signature that separates "the row is wrong" from "the station is quiet" —
    a healthy row produces received > 0, a silent station produces neither.
    This is the one measured on the operator's box: 59 refusals, none accepted.

    WORKED, THEN BROKE: a run of refusals since the last accepted push. The
    first rule goes blind the moment one push lands, so a key that drifted
    later — the station's store rebuilt, a row edited by hand — would have sat
    broken for ever with `received` frozen at whatever it reached first. The
    run resets on every accepted push, so this can only fire when the traffic
    really has stopped getting in.
    """
    run = int(_hook_state.get("rejected") or 0)
    if not run:
        return False
    last = float(_hook_state.get("rekeyed_at") or 0)
    cooled = (time.time() - last) > _REKEY_COOLDOWN
    if not _hook_state.get("received"):
        # Never worked. The cooldown gates this too, or a proxy stripping the
        # Authorization header (received stays 0 for ever) makes us re-mint
        # and re-POST the row with admin creds every warm tick indefinitely —
        # the admin-lockout pattern this module guards its other paths against
        # (top-down review, 2026-08-28). rekeyed_at starts 0, so the FIRST
        # re-key still fires at once and fixes a genuine secret mismatch; only
        # the runaway loop is stopped.
        return cooled
    if run < _BREAK_RUN:
        return False
    return cooled


def _stand_down(detail: str, *, permanent: bool) -> None:
    """Record why registration failed, and whether to stop trying.

    A rejection is permanent for this station and this configuration: retrying
    it every warm tick only rate-limits us against the station. A network
    error might be a station that is merely down, so those get a few goes.
    Either way a settings save re-arms it — see `_registration_due` and
    api/settings.py.
    """
    _hook_state["detail"] = detail
    if permanent:
        _hook_state["gave_up"] = True
        log.warning("webhook registration failed: %s", detail)
        return
    _hook_state["attempts"] = _hook_state.get("attempts", 0) + 1
    log.warning("webhook registration failed: %s", detail)
    if _hook_state["attempts"] >= _MAX_ATTEMPTS:
        _hook_state["gave_up"] = True
        log.warning("webhook registration standing down after %d failed attempts",
                    _hook_state["attempts"])


def register_enabled() -> bool:
    """Whether THIS instance may claim the station's push address.

    The station keeps ONE row per webhook id and registration is an upsert —
    so a second Talk Wave instance pointed at the same station silently
    replaces the first's callback URL with its own. Measured, not theorized
    (2026-08-18): a local dev boot for a widget check re-pointed the live
    deployment's registration at the dev PC, and its ducking degraded to the
    4s poll with nothing logged anywhere — a victim that stops receiving
    pushes looks exactly like a quiet station, and a successfully-registered
    process never re-asserts.

    Default ON: a deployment must not need a flag to work. run-local.ps1 and
    the repo's preview launcher set it off, because a dev boot beside a real
    deployment is the one shape that meets two instances — flip it back on
    deliberately when webhook code itself is under test.
    """
    return os.environ.get("CALLIN_HOOK_REGISTER", "1").strip().lower() not in (
        "0", "false", "off", "no")


async def register_station_webhook() -> None:
    """Register our receiver with the station, idempotently.

    The write shape is `{"webhooks": [...]}` — the whole list, replaced
    atomically. It is the only shape there has ever been: the handler reads
    `req.body.webhooks` and nothing else, so the flat `{"url", "events"}` this
    used to try first was accepted with a 200 and quietly changed nothing.
    """
    from station_config import admin_credentials

    if not register_enabled():
        # Said in the panel's hooks row, not just skipped: an operator who
        # set the flag and forgot deserves the reason in front of them.
        _hook_state.update(registered=False, detail=(
            "registration is switched off on this instance "
            "(CALLIN_HOOK_REGISTER=0) — it will not claim the station's "
            "push address, and the air guard runs on the 4s poll"))
        return

    user, password = admin_credentials()
    if not (user and password):
        _hook_state["detail"] = "no admin credentials"
        return

    url = _receiver_url()
    station = settings_store.station_base_url()
    _hook_state.update(url=url, id=HOOK_ID)

    try:
        async with _admin_client(user, password) as c:
            read = await c.get("/webhooks")
            if read.status_code >= 400:
                # 401 is a credentials problem, which a settings save fixes;
                # anything else here is the station being unhappy rather than
                # us being wrong.
                _stand_down(f"cannot read the webhook list: {_station_said(read)}",
                            permanent=read.status_code not in (401, 403))
                return
            current = read.json()
            if not isinstance(current, dict):
                _stand_down("the station's webhook list came back in an unknown shape",
                            permanent=True)
                return

            rows = [r for r in (current.get("webhooks") or []) if isinstance(r, dict)]
            # The station advertises its own event vocabulary on the same read.
            # Trust that over our list where they disagree.
            known = [e for e in (current.get("events") or []) if isinstance(e, str)]
            events = [e for e in WANTED_EVENTS if e in known] or list(WANTED_EVENTS)

            mine = _our_row(rows, url)
            # Rows that are not ours but also point at a /hooks/station path
            # are almost always earlier deployments of this box: a
            # Docker-internal address computed before CALLIN_HOOK_URL existed,
            # a previous host IP, a pre-id duplicate. Observed live
            # (2026-08-11): four rows on one station, three of them stale,
            # every event costing three failed POSTs and the strays burning
            # station slots for good. Not ours to delete — a second Talk Wave
            # against the same station is legitimate — but surfaced so the
            # panel can say so instead of nobody ever noticing.
            _hook_state["lookalikes"] = sorted(
                str(r.get("url") or "") for r in rows
                if r is not mine
                and r.get("id") not in LEGACY_HOOK_IDS
                and str(r.get("url") or "").rstrip("/").endswith("/hooks/station")
            )
            # Everything we did not put there stays exactly as it was read,
            # including the sentinel the station substitutes for a stored auth
            # header — it resolves that back by row id, so an unchanged row
            # round-trips without losing anyone's credentials.
            desired = dict(mine or {})
            desired["url"] = url
            # Adopting a row means renaming it, and the station resolves the
            # redacted auth header BY id — so a row carrying one would lose
            # its credential in exchange for our tidier name. Not a trade
            # worth making: it keeps the id it has, and the URL match finds it
            # again next time.
            desired["id"] = (mine.get("id") or HOOK_ID) if (
                mine and mine.get("authHeader")) else HOOK_ID
            desired.setdefault("enabled", True)
            # Give our row an Authorization header the receiver can check. A
            # row that already holds one keeps it: ours round-trips as the
            # station's redaction sentinel (resolved back by row id), and one
            # the operator set by hand at the station is not ours to overwrite
            # — the receiver just can't verify that one, which is the open
            # behaviour it always had.
            minted = ""
            # Whether this write is a RE-key rather than a first registration.
            # The cooldown belongs only to the former: stamping it on every
            # minted header made a fresh registration look like a re-key that
            # had just happened, and the next genuine break was ignored for ten
            # minutes (caught by the test below, not on a box).
            rekeying = False
            if not desired.get("authHeader"):
                minted = _load_hook_secret() or _mint_hook_secret()
                desired["authHeader"] = minted
            elif desired["id"] == HOOK_ID and _mis_keyed():
                # THE ROW LOOKS PERFECT AND NOTHING GETS IN. The station
                # redacts the stored header on read, so a row whose secret has
                # drifted from ours is indistinguishable from a correct one —
                # every field matches, the settled check below returns
                # "registered", and every push is turned away for ever. Found
                # on the operator's box 2026-08-16: 59 rejections, zero
                # received, `registered: true`, and nothing anywhere able to
                # notice. The receiver's own counters are the only evidence
                # that exists, so they are what re-keys it.
                minted = _mint_hook_secret()
                desired["authHeader"] = minted
                rekeying = True
                log.warning("the station's pushes are being rejected (%d of "
                            "them, none accepted) — re-keying our webhook row",
                            _hook_state.get("rejected", 0))
            elif desired["id"] == HOOK_ID and not _load_hook_secret():
                # The row carries a header we no longer hold the secret for —
                # a data/ recreated without its volume. The row is ours (we
                # minted that header, under our id), so rotate it rather than
                # leave verification silently off for good.
                minted = _mint_hook_secret()
                desired["authHeader"] = minted
            # Union rather than replace: an operator who subscribed our row to
            # something extra in the station's own UI keeps it.
            desired["events"] = sorted(
                {e for e in desired.get("events") or [] if not known or e in known}
                | set(events)
            )

            # A stray legacy-id row that ISN'T mine is a duplicate the rename
            # left behind — it must be cleaned even when our own row is
            # settled, or it burns a station slot until someone notices.
            strays = [r for r in rows
                      if r is not mine and r.get("id") in LEGACY_HOOK_IDS]

            # Sorted on both sides: the station returns the events in whatever
            # order they were stored, and a write per boot to reorder a list
            # is a write for nothing.
            if not strays and mine is not None and desired == {
                **mine, "events": sorted(mine.get("events") or [])
            }:
                _hook_state.update(registered=True, station=station,
                                   events=desired["events"],
                                   detail=_settled_detail(desired))
                return
            if strays:
                log.info("dropping %d stale legacy webhook row(s) left by the "
                         "rename", len(strays))

            others = [r for r in rows if r is not mine and r not in strays]
            write = await c.post("/webhooks", json={"webhooks": others + [desired]})
            if write.status_code >= 400:
                _stand_down(f"the station refused the registration: {_station_said(write)}",
                            permanent=write.status_code not in (401, 403))
                return

            # Persisted only now: a refused write must not leave the receiver
            # demanding a header the station never agreed to send.
            if minted:
                _store_hook_secret(minted)
                # The rejection count belongs to the OLD key. Left standing it
                # would re-key the row on every warm tick for ever, since
                # `received` only moves when a push actually lands.
                _hook_state.pop("rejected", None)
                if rekeying:
                    _hook_state["rekeyed_at"] = time.time()
            _hook_state.update(registered=True, station=station,
                               events=desired["events"],
                               detail=_settled_detail(desired))
            _hook_state.pop("attempts", None)
            log.info("station webhook registered as %r -> %s", HOOK_ID, url)
    except Exception as e:
        _stand_down(str(e)[:120], permanent=False)


def _settled_detail(row: dict) -> str:
    """What to say once the station holds our row.

    A row can be present and switched off — the station keeps `enabled` per
    hook and the admin UI can toggle it. Reporting that as plain "registered"
    would have the panel claim push events work while nothing is ever sent.
    """
    if not row.get("enabled", True):
        return "registered, but disabled in the station's admin"
    return "registered"


async def fire_test_hook() -> dict:
    """Ask the station to push a test payload at our receiver, and watch for it.

    `registered: true` only ever proved the station accepted a row. It could
    not say whether a packet gets from the station back to us — which, with a
    receiver on a LAN address behind a NAS, is the failure that actually
    happens. The station's test endpoint fires at one hook by id and bypasses
    the event subscription, so this exercises the whole path in both
    directions.
    """
    from station_config import admin_credentials

    user, password = admin_credentials()
    if not (user and password):
        return {"ok": False, "fired": False, "detail": "no station admin credentials"}
    if not _hook_state.get("registered"):
        return {"ok": False, "fired": False,
                "detail": _hook_state.get("detail") or "not registered"}

    hook_id = _hook_state.get("id") or HOOK_ID
    url = _hook_state.get("url") or _receiver_url()
    before = _hook_state.get("received", 0)
    rejected_before = _hook_state.get("rejected", 0)

    try:
        async with _admin_client(user, password) as c:
            r = await c.post(f"/webhooks/{hook_id}/test")
    except Exception as e:
        return {"ok": False, "fired": False,
                "detail": f"could not reach the station: {str(e)[:120]}"}

    if r.status_code == 404:
        said = _station_said(r)
        # The station 404s with "webhook not found" when the id is unknown;
        # a station too old to have the endpoint 404s the path itself. Telling
        # those apart is the difference between "re-register" and "upgrade".
        if "not found" in said.lower():
            _hook_state.update(registered=False,
                               detail=f"the station no longer holds a webhook {hook_id!r}")
            return {"ok": False, "fired": False,
                    "detail": f"the station no longer holds a webhook {hook_id!r} — "
                              "it will re-register on the next warm tick"}
        return {"ok": False, "fired": False,
                "detail": "this station has no webhook test endpoint"}
    if r.status_code >= 400:
        return {"ok": False, "fired": False, "detail": _station_said(r)}

    # The station awaits its own POST before answering us, so the push has
    # usually landed already — but it arrives on a separate connection handled
    # by a separate task, so give the loop room to run it.
    deadline = time.monotonic() + _DELIVERY_WAIT
    while time.monotonic() < deadline:
        if _hook_state.get("received", 0) > before:
            return {"ok": True, "fired": True, "url": url,
                    "detail": f"the station's push reached {url}"}
        # A push that arrived and was turned away is a different failure from
        # one that never arrived: the network is fine, the header is not —
        # a hand-edited header on our row, or a secret that drifted. Both
        # sides of it are ours to fix: drop the local secret and re-arm, and
        # the next reconcile sees our row holding a header we no longer
        # recognise and rotates it (the id == HOOK_ID branch).
        if _hook_state.get("rejected", 0) > rejected_before:
            _store_hook_secret("")
            _hook_state.update(registered=False)
            return {"ok": False, "fired": True, "url": url,
                    "detail": "the push arrived but its Authorization header "
                              "didn't match — rotating it; this will "
                              "re-register with a fresh header on the next "
                              "warm tick"}
        await asyncio.sleep(0.05)

    # NOT "it cannot reach this address", which is what this said until
    # 0.10.157 and is a cause the probe never established. Checked on the
    # operator's box while the panel was showing exactly this row: a `wget
    # http://192.168.1.10:8100/health` from INSIDE the station's own
    # container answered 200. The address was reachable the whole time; the
    # station was simply slower than the window, on a night it was also timing
    # out `/state` reads mid-call.
    #
    # A probe that names a cause it cannot see sends the operator to check
    # firewalls and docker networks that were never wrong. Say what happened,
    # name the candidates, and say plainly that nothing is broken meanwhile.
    return {"ok": False, "fired": True, "url": url,
            "detail": f"the station accepted the test but nothing arrived at {url} "
                      f"within {_DELIVERY_WAIT:g}s. Either it cannot reach that "
                      "address, or it is answering slowly right now — this test "
                      "cannot tell which from here. The card falls back to 20s "
                      "polling either way, so the panel stays correct; it is the "
                      "instant updates that are missing."}


async def keep_station_warm(app: web.Application) -> None:
    """Poll /dj on a timer so a caller never pays for a cold read.

    The station caches persona state lazily: warm it answers in ~15ms, but the
    first read after a quiet spell was measured at 19.5 seconds. That delay
    would land squarely between pressing Call and the DJ speaking. Touching it
    periodically keeps it hot for whoever calls next.
    """
    interval = float(os.environ.get("STATION_WARM_INTERVAL", "45"))

    async def loop() -> None:
        while True:
            try:
                station = StationClient(timeout=25.0)
                try:
                    t0 = asyncio.get_running_loop().time()
                    dj = await station.live_dj()
                    took = asyncio.get_running_loop().time() - t0
                    # An empty read is a FAILED ping — logging "now warm" for
                    # it made an unreachable station look healthy in the logs.
                    if dj and took > 2:
                        log.info("station /dj was cold (%.1fs) — now warm", took)
                    elif not dj:
                        log.debug("warm ping got no answer from the station")
                finally:
                    await station.aclose()
            except Exception as e:
                log.debug("warm ping failed: %s", describe(e))
            # Registration was previously attempted exactly once at startup —
            # a station that was down at that moment, or admin credentials
            # added later, left it unregistered until a restart. Piggyback on
            # the warm tick until it sticks.
            #
            # OUTSIDE the ping's try, deliberately. It used to sit after
            # live_dj() inside it, so a station that did not answer the ping
            # took the retry down with it — and "the station was down a moment
            # ago" is the entire situation this retry exists for. Talk Wave
            # starting before SUB/WAVE does exactly that, every time they come
            # up together (2026-08-16, 01:32:54).
            try:
                if _registration_due():
                    await _register_once()
            except Exception as e:                            # noqa: BLE001
                log.debug("registration retry failed: %s", describe(e))
            await asyncio.sleep(interval)

    task = asyncio.create_task(loop())
    app["warm_task"] = task
    hook_task = asyncio.create_task(_register_once())
    app["hook_task"] = hook_task
    try:
        yield
    finally:
        task.cancel()
        # Was never cancelled — a fast restart abandoned an in-flight
        # registration, leaving a pending-task warning (top-down review).
        hook_task.cancel()
