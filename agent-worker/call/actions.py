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
        # Its own kind for the same reason "cancel" is not "request": undoing a
        # takeover was noted AS a takeover, so calling one off put up a card
        # reading SHOW TAKEOVER SET — the opposite of what happened, on the one
        # action the caller cannot hear land.
        "takeover lifted": ("📅", "Takeover cancelled"),
        # The two POWERS, and the two that went longest with no label of their
        # own: both shipped noting a kind that was never in this table, so a
        # station-wide lock and a permanent ban each showed the caller a bare
        # "Action completed". The guard that should have caught it scraped
        # kinds with \w+, which matches neither the space in "genre lock" nor
        # the hyphen in "never-play" — see test_tools_surface.
        "genre lock": ("🔒", "Station locked to a genre"),
        "genre lock lifted": ("🔓", "Genre lock lifted"),
        # No expiry at all, which is what makes the receipt matter: nothing
        # later un-does this on its own the way a window lapsing does.
        "never-play": ("❌", "Banned from the station"),
        "never-play lifted": ("↩️", "Back in rotation"),
        # Bulk out, mirroring the bulk in: one card for a whole clear-out.
        "clear": ("🧹", "Queued tracks cleared"),
        # Not an action at all — the card that says NO MORE will happen.
        # Announced once per call by refusal(); never counted. The operator's
        # ask (2026-08-19): on the chat that hit the cap, the only voice
        # saying so was a DJ who dressed it as the scheduler fighting him.
        "limit": ("⛔", "Call limit reached"),
        # The refusal receipts — the denied-card family, none of them actions
        # and none of them counted. Same reasoning as "limit": the card is
        # the half the persona cannot spin, and every one of these situations
        # has been narrated as something else on a real call. See denied().
        #
        # The station said no and gave its reason (a never-play rule, the
        # request rate gate, requests closed). Detail carries the station's
        # own words — invented as "the queue's jammed" on 2026-08-13.
        "refused": ("🛑", "The station refused that"),
        # A capability this line's settings withhold — the caller asked, no
        # tool exists tonight, and the DJ invented "it's been a bit stubborn
        # with the queue" (the Mina call, 2026-08-22). See call/withheld.py.
        "not tonight": ("📵", "Not on this line tonight"),
        # A feature the station itself does not have — told to a caller as
        # a fact about the MUSIC eleven times on 2026-08-20 (lyrics).
        "unavailable": ("🚧", "Not available on this station"),
    }

    def __init__(self, limit: int, room=None, mode: str = "before",
                 tier: str = "") -> None:
        # Which door this caller came through — attribution for the day-log
        # (call/daylog.py): a tier, never an identity.
        self.tier = str(tier or "")
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
        # Label -> the track ids that went in under it, for the batches this
        # call queued. `subwave_queue_mix` takes a label ("90s alt rock mix"),
        # says it back on the receipt, and used to drop it there: the station
        # never hears it and no queue row carries it, so the name the caller
        # was GIVEN was the one name that could not be used to take the batch
        # out again. Observed 2026-08-19 — "cancel the 90s alt rock mix i
        # queued" was answered "nothing matching that is waiting", and all
        # five tracks aired over the following ten minutes. Kept here because
        # it is per-call state that both tool modules already share, and it
        # dies with the call the way the ledger above does.
        self.batches: list[tuple[str, list[str]]] = []
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
        # The id of the last request THIS call submitted, so the local
        # subwave_request_status twin (call/tools/reads.py) can answer "did
        # my request go in?" without the model ever having been shown an id
        # — the request wrapper keeps ids out of its return on purpose.
        self.last_request_id = ""
        # Whether the "call limit reached" card has gone out. Once per call:
        # the 2026-08-19 chat hit the cap four times in twenty seconds, and
        # four identical warnings would bury the one that matters.
        self._limit_announced = False
        # Denied cards already shown, keyed (kind, detail) — the same
        # once-per-call rule as the limit card, per distinct refusal: the
        # station repeating one rate-limit answer four times in a burst is
        # one fact, and four identical cards would bury it.
        self._denied: set[tuple[str, str]] = set()

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
        # The cap as a CARD, once, before the model gets a word in: on the
        # 2026-08-19 chat the ledger refused four cancels and the DJ described
        # every one as the scheduler fighting him, claiming pulls that never
        # ran. The card is the half the persona cannot spin. Not an action —
        # it never touches count or taken.
        if not self._limit_announced:
            self._limit_announced = True
            icon, label = self.LABELS["limit"]
            self._deliver({
                "kind": "limit", "icon": icon, "label": label,
                "detail": (f"{self.count} action(s) used — this call takes "
                           "no more; ring back for another round"),
            })
        return (
            f"You've already put {self.count} things through for this caller, which "
            "is the limit for one call. Don't do any more of those — say warmly that "
            "you'll have to leave it there for this call and they're welcome to ring "
            "back. Do not blame the station or invent a technical reason. The caller "
            "has been shown an official CALL LIMIT REACHED card, so the cap is "
            "already public — a story that contradicts it will be caught."
        )

    def denied(self, kind: str, detail: str = "") -> None:
        """A card for something that will NOT happen, and why.

        The receipt channel's other half. `note()` tells the caller what
        landed; this tells them what was refused and by whom — the fact the
        DJ's prose keeps dressing up as a station fault, a jammed queue, or
        an attempt that never ran. Never counts against the limit, never
        touches `taken`: a refusal costs the caller nothing.

        Once per distinct (kind, detail) per call. A different refusal still
        cards; the same one repeated does not.
        """
        key = (str(kind), str(detail or "")[:120])
        if key in self._denied:
            return
        self._denied.add(key)
        icon, label = self.LABELS.get(kind, ("🛑", "That can't happen tonight"))
        log.info("caller denied card: %s — %s", kind, detail)
        self._deliver({"kind": kind, "icon": icon,
                       "label": label, "detail": str(detail or "")[:200]})

    def station_refused(self, result: dict, said: str) -> str:
        """Card a station refusal and return the model-facing prose, once.

        The refusal-card idiom, collapsed (Batch 4). Fourteen call/tools sites
        read the station's reason TWICE — once for the denied() card, once
        inside the return string — and drifted on the tail ("don't" vs "do
        not"). Here the reason is read once, the card goes up once, and the
        prose is built from the site's own `said` lead ("That didn't go out",
        "That segment didn't run"). Everything after the reason is the pinned
        house phrasing the refusal graders read (spoken_rules.reads_as_a_refusal
        and the refusals ablation), so it is fixed here and cannot drift per
        call site.
        """
        err = result.get("error") or "the station refused it"
        self.denied("refused", err)
        return (f"{said}: {err}. Tell the caller plainly — "
                "do not claim it worked.")

    def note(self, kind: str, detail: str = "") -> None:
        # Something landed, so whatever was promised is no longer outstanding.
        self.promised_at = 0.0
        self.count += 1
        icon, label = self.LABELS.get(kind, ("✅", "Action completed"))
        log.info("caller action %d/%s: %s — %s", self.count, self.limit or "∞", kind, detail)
        self.taken.append((kind, detail))
        self.taken_at.append(time.time())
        # The cross-call ledger — station-changing kinds only, filtered
        # there, and never allowed to cost the action its receipt.
        from . import daylog
        daylog.note(kind, detail, tier=self.tier)
        self._deliver({"kind": kind, "icon": icon,
                       "label": label, "detail": detail})

    def note_batch(self, label: str, ids: list[str]) -> None:
        """Remember which tracks went in under a caller-facing label.

        Only called when something actually queued, so a label the caller was
        never told about never becomes a name they can ask us to undo."""
        label = str(label or "").strip()
        keep = [str(i).strip() for i in (ids or []) if str(i).strip()]
        if label and keep:
            self.batches.append((label, keep))

    def batch_ids(self, wanted: str) -> list[str]:
        """The ids queued under a label this call used, or [].

        Matched loosely on purpose. The label is something the DJ wrote and
        the caller then paraphrased back — "the 90s alt rock mix", "that 90s
        mix", "the alt rock one" — and the model retypes it a third time.
        Newest first, so re-using a label within one call undoes the most
        recent batch rather than the oldest."""
        want = " ".join(str(wanted or "").lower().split())
        if not want:
            return []
        for label, ids in reversed(self.batches):
            have = " ".join(label.lower().split())
            if want == have or want in have or have in want:
                return list(ids)
        return []

    def _deliver(self, card: dict) -> None:
        """One card to whoever is listening — the hook, then the room."""
        if self.on_note is not None:
            try:
                self.on_note(card)
            except Exception as e:                             # noqa: BLE001
                log.debug("action note hook failed (harmless): %s", e)
        if self._room is None or self.mode == "off":
            # "off" withholds only the caller-facing card: the count, the
            # taken list and the record's tools entry all still happen.
            return
        payload = json.dumps({"type": "action", **card}).encode()
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
