"""What the duck actually did, with timestamps, on the call record.

The on-air hold has been diagnosed three times now, and every time the method
was the same: watch data/hook-air.json at 200ms for five minutes and correlate
by hand. Nothing about a hold reached the call record — a transcript showed the
DJ going quiet and coming back with no way to tell whether it had been early,
late, or the right length in the wrong place. So the same call that says what
the DJ said now says when the line was held, why, and what the station was
doing at the time.

The distinction that matters, and the one no log line carried: **encoder time
is not caller time.** Every voice.* timestamp is stamped where the station
mixes, and the caller is `streamBufferSeconds` behind that — 22 seconds on the
operator's box, measured 2026-08-13. A hold placed on the encoder's clock is
placed 22 seconds before the caller hears anything, which is why the duck has
felt "right length, wrong moment" rather than simply too short.

Written for a person: the panel renders these rows as a strip under the
transcript, so "held 11s before the voice was audible" is something you read
rather than something you reconstruct.
"""

from __future__ import annotations

import time

# Nothing here may raise into a live call — a broken diagnostic that ends a
# call is worse than no diagnostic. Every entry point swallows.


class AirLog:
    """One call's ducking timeline."""

    # A call that ducks more than this is not going to be diagnosed by reading
    # further rows, and the record should not grow without bound.
    LIMIT = 60

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._open_at = 0.0

    def _add(self, what: str, at: float = 0.0, **fields) -> None:
        """`at` is when the thing HAPPENED, which for a replayed station push
        is not when we noticed it. Stamped ISO like every other row on the
        record, so the panel's one time formatter reads it."""
        if len(self.rows) >= self.LIMIT:
            return
        from call.record import _iso

        when = at or time.time()
        row = {"t": _iso(when), "at": round(when, 3), "what": what}
        row.update({k: v for k, v in fields.items() if v not in (None, "")})
        self.rows.append(row)

    # -- the hold ---------------------------------------------------------
    def opened(self, why: str, until: float = 0.0, buf: float = 0.0,
               text: str = "") -> None:
        """The line was held. `why` is the branch that decided it, which is
        the field that separates "our own action assumed it" from "the station
        told us" — they fail differently and used to look identical."""
        try:
            self._open_at = time.time()
            self._add("hold opened", why=why,
                      forSecs=round(max(0.0, until - time.time()), 1) or None,
                      bufSecs=round(buf, 1) or None,
                      text=(text or "")[:120] or None)
        except Exception:                                      # noqa: BLE001
            pass

    def closed(self, why: str) -> None:
        try:
            held = time.time() - self._open_at if self._open_at else 0.0
            self._open_at = 0.0
            self._add("hold closed", why=why,
                      heldSecs=round(held, 1) or None)
        except Exception:                                      # noqa: BLE001
            pass

    # -- what the station was doing meanwhile -----------------------------
    def station(self, entry: dict) -> None:
        """One push from the station, as the guard saw it.

        `audibleAt` is the number the whole problem turns on: when the CALLER
        hears this, which is the station's own timestamp plus the buffer it
        reports. A row where the hold opened well before audibleAt is a duck
        that started too early, stated rather than inferred.
        """
        try:
            if not isinstance(entry, dict):
                return
            at = float(entry.get("at") or 0)
            buf = float(entry.get("bufSecs") or 0)
            base = float(entry.get("airAt") or at)
            self._add("station " + str(entry.get("event") or "?"), at=at,
                      phase=entry.get("phase") or None,
                      voiceId=str(entry.get("voiceId") or "")[:12] or None,
                      durSecs=round(float(entry.get("durMs") or 0) / 1000.0, 1)
                      or None,
                      bufSecs=round(buf, 1) or None,
                      audibleIn=round(base + buf - time.time(), 1) or None,
                      ignored=entry.get("ignored") or None)
        except Exception:                                      # noqa: BLE001
            pass

    def replay(self, recent: list) -> None:
        """The pushes the station made that this call never evaluated.

        The guard only ever looks at the newest entry, so a queued/start/end
        sequence that completed between two polls left no trace at all. The
        receiver keeps a short history for exactly this; folding it in means
        the timeline is what the STATION did, not what we happened to notice.
        """
        try:
            seen = {(r.get("at"), r.get("what")) for r in self.rows}
            fresh = []
            for e in list(recent or [])[-24:]:
                if not isinstance(e, dict):
                    continue
                key = (round(float(e.get("at") or 0), 3),
                       "station " + str(e.get("event") or "?"))
                if key not in seen:
                    seen.add(key)
                    fresh.append(e)
            for e in fresh:
                self.station(e)
            # Replayed pushes are older than the rows already here, so the
            # timeline has to be re-sorted or it reads out of order — which is
            # the one thing a timeline may not do.
            self.rows.sort(key=lambda r: r.get("at") or 0)
        except Exception:                                      # noqa: BLE001
            pass

    # -- onto the record --------------------------------------------------
    def write(self, record) -> None:
        try:
            if record is not None and self.rows:
                record.data["air"] = self.rows[:self.LIMIT]
        except Exception:                                      # noqa: BLE001
            pass
