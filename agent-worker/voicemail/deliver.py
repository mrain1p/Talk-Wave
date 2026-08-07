"""Where a voicemail message goes, and the proof it went.

One message is one action. The same receipts discipline as a live call: the
station's answer is what happened, never the intention — a delivery that
claims success it cannot show is exactly the failure the tool registry
exists to prevent.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("callin.voicemail")

MESSAGES_PATH = Path(
    os.environ.get("VOICEMAIL_MESSAGES_PATH",
                   Path(__file__).parent.parent.parent / "data" / "voicemail"
                   / "messages.json")
)
# Enough to read back a busy night, small enough that a robot dialling the
# machine all day cannot fill the volume the settings live on.
MAX_MESSAGES = 200


def _read() -> list:
    try:
        with open(MESSAGES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def held_messages() -> list:
    return _read()


def clear_messages() -> None:
    MESSAGES_PATH.unlink(missing_ok=True)


def hold(text: str, persona_name: str, delivered: str = "hold",
         note: str = "") -> None:
    """Every message lands here, whatever else happened to it — the panel's
    list is the one place the operator can always read the night back, and a
    request that the station then lost would otherwise be gone entirely."""
    entry = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "text": str(text)[:2000],
        "dj": str(persona_name or ""),
        "delivered": delivered,
    }
    if note:
        entry["note"] = str(note)[:300]
    messages = _read()
    messages.append(entry)
    messages = messages[-MAX_MESSAGES:]
    MESSAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MESSAGES_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=1)
    for path, mode in ((MESSAGES_PATH.parent, 0o755), (tmp, 0o644)):
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    tmp.replace(MESSAGES_PATH)


async def deliver(station, cfg: dict, text: str, persona_name: str) -> str:
    """Send one message where the operator chose. Returns a one-line receipt
    for the log and the panel entry."""
    mode = str(cfg.get("voicemail_destination") or "hold").lower()
    text = str(text or "").strip()
    if not text:
        return "empty message — nothing delivered"

    if mode == "request":
        try:
            result = await station.submit_request(text)
            note = str(result.get("message") or result.get("status") or "sent")
            hold(text, persona_name, delivered="request", note=note)
            return f"sent as a request — {note}"
        except Exception as e:                                # noqa: BLE001
            # The station refusing is a real outcome, not a crash: the
            # message is still held so the operator can act on it by hand.
            hold(text, persona_name, delivered="hold",
                 note=f"request failed: {e}")
            log.warning("voicemail request delivery failed: %s", e)
            return "the station refused the request — held for the operator"

    if mode == "air":
        try:
            line = (f"A caller left a message for the booth: {text}"
                    if len(text) < 400 else
                    f"A caller left a message for the booth: {text[:400]}…")
            await station.dj_say(line, mode="styled", kind="voicemail")
            hold(text, persona_name, delivered="air")
            return "handed to the on-air DJ"
        except Exception as e:                                # noqa: BLE001
            hold(text, persona_name, delivered="hold",
                 note=f"air delivery failed: {e}")
            log.warning("voicemail air delivery failed: %s", e)
            return "the station would not take it — held for the operator"

    hold(text, persona_name)
    return "held for the operator"
