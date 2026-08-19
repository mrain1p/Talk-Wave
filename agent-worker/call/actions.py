"""Per-call record of what the caller actually made happen."""

from __future__ import annotations

import json
import logging
import time

from .background import spawn

log = logging.getLogger("callin.agent")


class CallActions:
    """Per-call record of what the caller actually made happen.

    Two jobs, both of which need the same ledger:

      * a ceiling on how much one call can set in motion, so a single caller
        can't fill the request queue or fire segment after segment;
      * a signal to the widget when an action really lands, so the caller sees
        "Song scheduled" as its own line in the transcript instead of having to
        take the DJ's word for it.

    Only SUCCESSFUL actions are counted and announced. An attempt the station
    refused costs the caller nothing and shows nothing.
    """

    # What the widget renders for each kind. Kept here rather than in the page
    # so a new action type can't ship with no label.
    LABELS = {
        "request": ("🎵", "Song request scheduled"),
        # Its own row rather than folding into "request": a caller watching the
        # card needs to see the undo land, and "Song request scheduled" for a
        # cancellation is the exact confusion the receipts exist to prevent.
        "cancel": ("🚫", "Queued track pulled"),
        # Bulk queueing: the whole batch is one action and one receipt — a
        # caller who asked for an album in one sentence should not watch
        # thirty cards land for it.
        "album": ("💿", "Album queued"),
        "mix": ("🎶", "Mix queued"),
        "like": ("❤️", "Liked the track on air"),
        "unlike": ("🤍", "Removed the like"),
        "announcement": ("📢", "Message sent to air"),
        "skill": ("🎙", "Station segment running"),
        # Both reach every listener rather than the caller, so the receipt
        # matters more here than anywhere else: these shipped without labels
        # and showed the caller a bare "Action completed" for cutting off the
        # record the rest of the audience was listening to.
        "skip": ("⏭", "Current track cut short"),
        "segment": ("📻", "Station beat on air"),
        # The one action that outlives the call. The receipt matters most here
        # for the same reason: the caller cannot hear it land (it takes over at
        # the next track boundary), so the DJ saying it worked is all they have.
        "takeover": ("🔀", "Show takeover set"),
    }

    def __init__(self, limit: int, room=None, mode: str = "before") -> None:
        self.limit = max(0, int(limit or 0))
        self.count = 0
        self._room = room
        # The action_cards setting, for the room-publish path only: "before"
        # publishes the instant a tool lands, "after" holds the card until the
        # DJ's line commits (lifecycle.attach_card_flush releases it), "off"
        # publishes nothing. A consumer that routes cards through on_note (the
        # chat's WebSocket) does its own routing and ignores this.
        self.mode = (mode or "before").strip().lower()
        self.held: list[bytes] = []
        # What actually happened, for consumers with no room to publish to:
        # the chat line writes its record from this, the way the call's
        # record hears the room. And an optional hook for delivering the
        # caption card somewhere other than a LiveKit data channel — the
        # chat's WebSocket sets it; a call leaves it alone.
        self.taken: list[tuple[str, str]] = []
        # WHEN each of those landed. `taken` alone cannot answer "did
        # anything happen AFTER the caller asked", which is the whole
        # question call/asks.py exists to record.
        self.taken_at: list[float] = []
        self.on_note = None
        # When the DJ is mid-task ON THE CALLER'S BEHALF — a search running, a
        # request still resolving in the background — the ball is in the DJ's
        # court, not the caller's. The idle watcher reads this so it never asks
        # "still there?" of a caller who is only waiting on us (the Zeppelin
        # call, 2026-08-10: a long resolve, then "are you still there?").
        self.working_until = 0.0
        # And when the DJ SAID it was about to do something. `working_until`
        # only covers a tool actually in flight, which turned out to be the
        # narrower half of "the caller is waiting on us": on 2026-08-16 the DJ
        # said "I'm digging through the crates now", ran nothing at all, and
        # asked "Still with me?" eighteen seconds later. Twice on one evening,
        # and the second time twenty seconds after four searches had already
        # come back with no answer spoken. Nothing was in flight either time,
        # so is_working() was correctly False and the check-in was still wrong.
        self.promised_at = 0.0
        # Track ids this call has already put in the queue, so the same record
        # cannot take two slots. See subwave_queue_track.
        self.queued_ids: set[str] = set()
        # (song id, track) of the last thing this call LIKED, so "actually,
        # un-like that" still works once the record has moved on — which is
        # when a caller usually changes their mind. See _target_to_unlike.
        self.last_liked: tuple[str, dict] | None = None

    # How long a promise keeps the check-in quiet. Capped, because a DJ that
    # promises and never delivers would otherwise buy silence for the rest of
    # the call — and a caller sitting in nothing is worse off than one asked a
    # slightly rude question.
    PROMISE_PATIENCE_SECS = 45.0

    def promise_made(self) -> None:
        """The DJ told the caller it was about to do something."""
        self.promised_at = time.time()

    def caller_is_waiting_on_us(self) -> bool:
        """Is the ball in the DJ's court rather than the caller's?

        Either a tool is genuinely running, or the DJ has said it is going to
        do something and nothing has landed since. Asking "still there?" in
        that window blames the caller for a pause the DJ created.
        """
        if self.is_working():
            return True
        return (self.promised_at > 0.0
                and time.time() - self.promised_at < self.PROMISE_PATIENCE_SECS)

    def mark_working(self, secs: float = 8.0) -> None:
        """Hold the 'DJ is working' flag for `secs` from now. Called repeatedly
        while a background task is still running, so the window follows the work
        rather than guessing its length up front."""
        self.working_until = max(self.working_until, time.time() + secs)

    def is_working(self) -> bool:
        return time.time() < self.working_until

    def at_limit(self) -> bool:
        return self.limit > 0 and self.count >= self.limit

    def refusal(self) -> str:
        """In-world, and explicit that this is the line's rule rather than the
        station refusing — otherwise the DJ invents a reason."""
        return (
            f"You've already put {self.count} things through for this caller, which "
            "is the limit for one call. Don't do any more of those — say warmly that "
            "you'll have to leave it there for this call and they're welcome to ring "
            "back. Do not blame the station or invent a technical reason."
        )

    def note(self, kind: str, detail: str = "") -> None:
        # Something landed, so whatever was promised is no longer outstanding.
        self.promised_at = 0.0
        self.count += 1
        icon, label = self.LABELS.get(kind, ("✅", "Action completed"))
        log.info("caller action %d/%s: %s — %s", self.count, self.limit or "∞", kind, detail)
        self.taken.append((kind, detail))
        self.taken_at.append(time.time())
        if self.on_note is not None:
            try:
                self.on_note({"kind": kind, "icon": icon,
                              "label": label, "detail": detail})
            except Exception as e:                             # noqa: BLE001
                log.debug("action note hook failed (harmless): %s", e)
        if self._room is None or self.mode == "off":
            # "off" withholds only the caller-facing card: the count, the
            # taken list and the record's tools entry all still happen.
            return
        payload = json.dumps({
            "type": "action", "kind": kind, "icon": icon,
            "label": label, "detail": detail,
        }).encode()
        if self.mode == "after":
            self.held.append(payload)
            return
        self._publish(payload)

    def _publish(self, payload: bytes) -> None:
        try:
            # Fire-and-forget: a caption card is never worth delaying a tool
            # return (and so never worth failing the action over).
            spawn(
                self._room.local_participant.publish_data(
                    payload, reliable=True, topic="talkwave.action"
                )
            )
        except Exception as e:
            log.debug("action card publish failed (harmless): %s", e)

    def flush_cards(self) -> None:
        """Release everything the "after" mode held. Called each time a DJ
        line commits, so the widget paints the words first and the card lands
        under them. Under "before"/"off" the holder is empty and this is a
        no-op."""
        held, self.held = self.held, []
        for payload in held:
            self._publish(payload)
