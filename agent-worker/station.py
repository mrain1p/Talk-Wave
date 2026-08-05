"""
Slim read-only client for the SUB/WAVE controller REST API.

Scope note: the agent's *actions* during a call go through the station's MCP
server (see main.py) rather than through here — MCP already exposes those as
tool-calling-ready definitions with descriptions and schemas, so hand-rolling
them again would be duplicate surface. What's left for this module is the
handful of reads used to assemble the system prompt before the call starts.

All endpoints used here are public reads; no auth needed.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

import settings as settings_store

log = logging.getLogger("callin.station")

# Consecutive failed reads across every client in this process, so the token
# server can tell the card "the station is struggling" instead of silently
# serving thin prompts. Any successful read resets it.
_read_stats = {"consecutive_failures": 0}
_DEGRADED_AFTER = 3


def degraded() -> bool:
    return _read_stats["consecutive_failures"] >= _DEGRADED_AFTER


# Reads are quick or they're broken. ACTIONS are not: /dj/skill runs a whole
# segment (script, then speech) before it answers, and /dj/say re-voices a line
# through the station's own TTS. Both routinely take longer than the read
# timeout — and a timeout was being reported to the caller as "that didn't
# work" while the segment was audibly going out on air.
ACTION_TIMEOUT = 45.0


def _body(r: httpx.Response) -> dict:
    """A 2xx is the success signal, not the body's shape. Some station
    endpoints answer with an empty body, plain text, or a bare list — calling
    .json() blind turned those into exceptions and, one layer up, into the DJ
    telling the caller an action had failed."""
    try:
        data = r.json() if r.content else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"result": data}


def _sent_but_unconfirmed(e: Exception) -> bool:
    """True when the request reached the station but the answer didn't come
    back in time. The action has almost certainly run, so this must NOT be
    reported as a failure — the honest line is "it's gone through"."""
    return isinstance(e, httpx.TimeoutException) and not isinstance(
        e, httpx.ConnectTimeout
    )

# Last-known-good persona, shared across calls in this process.
#
# Why: GET /dj is lazily cached on the station side. Warm it answers in ~15ms,
# but the first read after a quiet spell has been measured at 19.5s — which
# would otherwise sit in front of every call as dead air while the caller
# listens to ringing. A slightly stale persona is far better than a caller
# waiting, so a slow read falls back to the last one that worked.
_persona_cache: dict = {"value": None, "at": 0.0}
_PERSONA_TTL = 300.0


class StationClient:
    def __init__(self, base_url: str | None = None, timeout: float = 8.0) -> None:
        # Which station this points at is a setting, so it can be re-homed
        # from the settings page without a restart.
        self._client = httpx.AsyncClient(
            base_url=base_url or settings_store.station_base_url(), timeout=timeout
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, retries: int = 0) -> dict:
        """Reads are best-effort — a call should still connect if the station
        is mid-restart, just with a thinner prompt."""
        for attempt in range(retries + 1):
            try:
                r = await self._client.get(path)
                r.raise_for_status()
                _read_stats["consecutive_failures"] = 0
                return r.json()
            except Exception as e:
                if attempt < retries:
                    log.info("station read %s failed (%s) — retrying", path, e)
                    continue
                _read_stats["consecutive_failures"] += 1
                log.warning("station read %s failed: %s", path, e)
                return {}
        return {}

    async def health(self) -> dict:
        return await self._get("/health")

    async def now_playing(self) -> dict:
        return await self._get("/now-playing")

    async def live_dj(self) -> dict:
        """Who is on air right now — name, tagline, and `soul` (the DJ Card).

        The one known-slow endpoint (lazy cache on the station side), so it
        gets the one retry. Everything else answers in ~20ms or is down.
        """
        return await self._get("/dj", retries=1)

    async def personas(self) -> list[dict]:
        return (await self._get("/personas")).get("personas", [])

    async def schedule(self) -> dict:
        return await self._get("/schedule")

    async def state(self) -> dict:
        return await self._get("/state")

    async def session(self) -> dict:
        return await self._get("/session")

    async def snapshot(self, with_skills: bool = False) -> dict:
        """Everything the prompt needs, fetched concurrently.

        Serially these cost ~1s on a good day and far more when the station's
        persona cache is cold; the caller hears all of it as ringing. The
        skill catalogue joins the same gather when segments are enabled, so
        knowing the show costs no extra ringing time.
        """
        async def _skills() -> list[dict]:
            return await self.list_skills() if with_skills else []

        dj, personas, now, state, session, schedule, skills = await asyncio.gather(
            self.live_dj(), self.personas(), self.now_playing(),
            self.state(), self.session(), self.schedule(), _skills(),
        )
        return {
            "dj": dj, "personas": personas, "now_playing": now,
            "state": state, "session": session, "schedule": schedule,
            "skills": skills,
        }

    def persona_from(self, dj: dict, personas: list[dict]) -> dict:
        """Resolve the on-air persona from an already-fetched snapshot."""
        name = dj.get("name")
        persona_id = dj.get("id") or dj.get("personaId")

        if not persona_id and name:
            for p in personas:
                if p.get("name") == name:
                    persona_id = p.get("id")
                    break

        resolved = {
            "id": persona_id or "default",
            "name": name or "the DJ",
            "soul": dj.get("soul", ""),
            "tagline": dj.get("tagline", ""),
        }

        if name:
            _persona_cache["value"] = resolved
            _persona_cache["at"] = time.time()
        elif _persona_cache["value"] and (time.time() - _persona_cache["at"]) < _PERSONA_TTL:
            log.warning("station /dj was slow or empty — using the last known persona")
            return _persona_cache["value"]

        return resolved

    async def resolve_live_persona(self) -> dict:
        """Resolve the on-air persona to {id, name, soul, tagline}.

        `GET /dj` returns the live persona but not always its id, so match it
        back against `GET /personas` by name to recover the id that
        persona-voices.json is keyed on.
        """
        dj = await self.live_dj()
        personas = await self.personas() if not (dj.get("id") or dj.get("personaId")) else []
        return self.persona_from(dj, personas)

    # djLog kinds that mean the on-air DJ is actually making sound. Everything
    # else in that log (picker, queued, scheduler, mix…) is bookkeeping.
    ON_AIR_SPEECH_KINDS = {
        "link", "dj-speak", "station-id", "sfx", "hourly", "segment", "skill",
    }

    async def seconds_since_on_air_speech(self, state: dict | None = None) -> float | None:
        """How long since the on-air DJ last said something, in seconds.

        Used to avoid the same persona talking on air and on the call at the
        same time. Returns None if it can't tell.
        """
        from datetime import datetime, timezone

        st = state if state is not None else await self.state()
        newest = None
        for entry in (st.get("djLog") or []):
            kind = str(entry.get("kind") or "")
            # Skill segments are logged under their own name, so treat anything
            # that isn't recognisably bookkeeping as speech.
            speechy = kind in self.ON_AIR_SPEECH_KINDS or kind.startswith(("dj", "seg"))
            if not speechy:
                continue
            raw = entry.get("t")
            if not raw:
                continue
            try:
                when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if newest is None or when > newest:
                newest = when

        if newest is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - newest).total_seconds())

    async def dj_say(self, text: str, mode: str = "styled", kind: str = "callin") -> dict:
        """Hand a line to the on-air DJ. `styled` lets the station rewrite it in
        the persona's own voice before speaking it, so the call-in agent's
        phrasing doesn't have to match the broadcast voice exactly.

        Admin-only (the endpoint 401s without credentials).
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            log.info("skipping on-air handoff — no station admin credentials")
            return {"ok": False, "error": "no station admin credentials"}

        try:
            r = await self._client.post(
                "/dj/say",
                json={"text": text, "mode": mode, "kind": kind},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("on-air line slow to confirm (%s) — treating as sent", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("on-air handoff failed: %s", e)
            return {"ok": False, "error": str(e)[:140]}

    async def search_library(self, q: str) -> list[dict]:
        """Term search over the library. Admin-gated; empty list on failure."""
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return []
        try:
            r = await self._client.get(
                "/dj/search", params={"q": q}, auth=httpx.BasicAuth(user, password)
            )
            r.raise_for_status()
            d = r.json()
            items = d if isinstance(d, list) else (d.get("results") or d.get("tracks") or [])
            return items if isinstance(items, list) else []
        except Exception as e:
            log.warning("library search failed: %s", e)
            return []

    async def queue_track(self, track: dict) -> dict:
        """Push an exact track from a search result onto the queue.

        Different from submit_request in ways that matter on a call: it takes
        the id the search returned, so the station plays THAT track rather than
        resolving the words again and possibly landing on something else — and
        it isn't subject to the request endpoint's 1-per-20s gate. No DJ intro
        is generated; the caller's DJ is already talking about it.

        Admin-only.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        if not (track.get("id") and track.get("title")):
            return {"ok": False, "error": "need the track id and title from a search"}
        try:
            r = await self._client.post(
                "/dj/queue-track",
                json={k: v for k, v in track.items() if v not in (None, "")},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("queue-track slow to confirm (%s) — treating as queued", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("queue-track failed: %s", e)
            return {"ok": False, "error": str(e)[:140]}

    async def submit_request(self, text: str, name: str = "") -> dict:
        """Public request endpoint — the same path the station's own request
        slip uses.

        Retries once on a 5xx. Observed on a real call: the caller asked for a
        song eight seconds after pickup and the station answered 503 — it
        pauses requests while nobody is listening, and the caller's own tune-in
        had not shown up in its listener count yet. Their request was lost to a
        few seconds of timing, and the DJ told them so. A single retry after a
        short wait costs nothing and covers the window.
        """
        payload: dict = {"text": text}
        if name:
            payload["name"] = name

        last: Exception | None = None
        for attempt in (0, 1):
            if attempt:
                await asyncio.sleep(4.0)
            try:
                r = await self._client.post(
                    "/request", json=payload, timeout=ACTION_TIMEOUT
                )
                r.raise_for_status()
                return _body(r)
            except httpx.HTTPStatusError as e:
                last = e
                if e.response.status_code < 500:
                    break        # a real refusal — retrying would just repeat it
                log.info("station 5xx on request (%s) — retrying once",
                         e.response.status_code)
            except Exception as e:
                last = e
                if _sent_but_unconfirmed(e):
                    log.warning("request slow to confirm (%s) — treating as submitted", e)
                    return {"unconfirmed": True}
                break

        log.warning("request submit failed: %s", last)
        return {"error": str(last)[:140]}

    async def request_status(self, request_id: str) -> dict:
        try:
            r = await self._client.get(f"/request/{request_id}")
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    async def list_skills(self) -> list[dict]:
        """The station's segment catalogue — kind, label and cooldown.

        Read up front rather than left to a mid-call tool round-trip: a DJ
        knows its own show. Without this the agent either guesses at segment
        names or spends a turn asking the station what it can do, and a caller
        hears the pause.

        Admin-gated; empty list when unavailable, which simply means the
        prompt doesn't mention segments.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return []
        try:
            r = await self._client.get("/dj/skills", auth=httpx.BasicAuth(user, password))
            r.raise_for_status()
            d = _body(r)
            items = d.get("skills") or d.get("result") or []
            return items if isinstance(items, list) else []
        except Exception as e:
            log.info("skill catalogue unavailable: %s", e)
            return []

    async def run_skill(self, name: str) -> dict:
        """Fire one of the station's own segments. Admin-only."""
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        try:
            r = await self._client.post(
                "/dj/skill",
                json={"name": name},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            # `ok` first so a station payload carrying its own verdict wins.
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("skill %s slow to confirm (%s) — treating as running", name, e)
                return {"ok": True, "unconfirmed": True}
            log.warning("skill %s failed: %s", name, e)
            return {"ok": False, "error": str(e)[:120]}

    async def skip_track(self) -> dict:
        """Force-end whatever is playing. Admin-only, and station-wide.

        The station's own API calls this an operator override and notes there
        is deliberately no listener-facing skip. Exposing it to a caller is
        therefore a decision the operator makes about their own station, which
        is why the setting is off by default and capped per call.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        try:
            r = await self._client.post(
                "/dj/skip",
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("skip slow to confirm (%s) — treating as done", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("skip failed: %s", e)
            return {"ok": False, "error": str(e)[:120]}

    async def dj_segment(self, kind: str) -> dict:
        """Fire one of the station's scripted beats — station id, the hour, a
        link, banter. Admin-only.

        Distinct from `run_skill`: these are programme furniture rather than
        content segments, and the station documents that an explicit press
        bypasses its own frequency and budget gates. So the only thing pacing
        them on a call is our per-call action cap.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        try:
            r = await self._client.post(
                "/dj/segment",
                json={"type": kind},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("segment %s slow to confirm (%s) — treating as running", kind, e)
                return {"ok": True, "unconfirmed": True}
            log.warning("segment %s failed: %s", kind, e)
            return {"ok": False, "error": str(e)[:120]}

    async def active_show(self, now_playing: dict | None = None) -> dict:
        """The show currently on air, with its `topic` (the Show Card).

        Accepts an already-fetched /now-playing payload so prompt assembly
        doesn't request it twice per call.
        """
        np = now_playing if now_playing is not None else await self.now_playing()
        active = (np.get("context") or {}).get("activeShow") or {}
        show_id = active.get("id")
        if not show_id:
            return active

        for show in (await self.schedule()).get("shows", []):
            if show.get("id") == show_id:
                return show
        return active
