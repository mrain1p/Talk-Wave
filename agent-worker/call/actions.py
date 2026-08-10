"""Per-call record of what the caller actually made happen."""

from __future__ import annotations

import json
import logging

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

    def __init__(self, limit: int, room=None) -> None:
        self.limit = max(0, int(limit or 0))
        self.count = 0
        self._room = room
        # What actually happened, for consumers with no room to publish to:
        # the chat line writes its record from this, the way the call's
        # record hears the room. And an optional hook for delivering the
        # caption card somewhere other than a LiveKit data channel — the
        # chat's WebSocket sets it; a call leaves it alone.
        self.taken: list[tuple[str, str]] = []
        self.on_note = None

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
        self.count += 1
        icon, label = self.LABELS.get(kind, ("✅", "Action completed"))
        log.info("caller action %d/%s: %s — %s", self.count, self.limit or "∞", kind, detail)
        self.taken.append((kind, detail))
        if self.on_note is not None:
            try:
                self.on_note({"kind": kind, "icon": icon,
                              "label": label, "detail": detail})
            except Exception as e:                             # noqa: BLE001
                log.debug("action note hook failed (harmless): %s", e)
        if self._room is None:
            return
        try:
            payload = json.dumps({
                "type": "action", "kind": kind, "icon": icon,
                "label": label, "detail": detail,
            }).encode()
            # Fire-and-forget: a caption card is never worth delaying a tool
            # return (and so never worth failing the action over).
            spawn(
                self._room.local_participant.publish_data(
                    payload, reliable=True, topic="wavetalk.action"
                )
            )
        except Exception as e:
            log.debug("action card publish failed (harmless): %s", e)
