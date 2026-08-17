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


async def _triage(station, cfg: dict, text: str) -> tuple[str, str]:
    """One bounded model read of the message, picking ONE action.

    The operator's design: the machine should be able to tell a song request
    from a shoutout from "do the weather", instead of pushing everything down
    the request pipe. One completion, JSON out, one action in — and anything
    the model gets wrong falls through to hold, which loses nothing.

    Bounded by the caller permissions the way a live call is: an action the
    tiers would not grant an open caller is not available to an anonymous
    message either.
    """
    import json as _json

    from call.providers import build_llm
    from call.tools.registry import mcp_allowlist  # noqa: F401  (documented gate)

    # Requests ride allow_requests, the same master switch the live line and
    # the soundbite studio gate on — a line that takes no requests holds the
    # message rather than pushing it down the request pipe regardless. Default
    # "open" (the shipped default), so only an operator who set it "off" loses
    # the request path here.
    allowed = []
    if str(cfg.get("allow_requests") or "open").lower() != "off":
        allowed.append("request")
    if str(cfg.get("allow_announcements") or "off") != "off":
        allowed.append("air")
    if str(cfg.get("allow_skills") or "off") != "off":
        try:
            skills = [str(s.get("kind") or s.get("name") or "")
                      for s in await station.list_skills()]
            skills = [s for s in skills if s]
        except Exception:                                     # noqa: BLE001
            skills = []
        if skills:
            allowed.append("skill")
    else:
        skills = []

    llm = build_llm(cfg)
    prompt = (
        "A radio station's answering machine took this message:\n"
        f"  {text[:800]}\n\n"
        "Pick ONE action. Answer with bare JSON only:\n"
        + ('  {"action": "request", "text": "<what to ask the station to '
           'play>"}\n' if "request" in allowed else "")
        + ('  {"action": "air", "text": "<one line for the DJ to read>"}\n'
           if "air" in allowed else "")
        + (('  {"action": "skill", "name": "<one of: '
            + ", ".join(skills[:12]) + '>"}\n') if "skill" in allowed else "")
        + '  {"action": "hold"}  when none of those fit.'
    )
    try:
        chunks = []
        from livekit.agents.llm import ChatContext

        chat_ctx = ChatContext()
        chat_ctx.add_message(role="user", content=prompt)
        async with llm.chat(chat_ctx=chat_ctx) as st:
            async for chunk in st:
                delta = getattr(chunk, "delta", None)
                if delta and getattr(delta, "content", None):
                    chunks.append(delta.content)
        raw = "".join(chunks).strip()
        raw = raw[raw.index("{"):raw.rindex("}") + 1]
        verdict = _json.loads(raw)
    except Exception as e:                                    # noqa: BLE001
        log.warning("voicemail triage failed (%s) — holding the message", e)
        return "hold", ""
    finally:
        try:
            await llm.aclose()
        except Exception:                                     # noqa: BLE001
            pass

    action = str(verdict.get("action") or "hold").lower()
    if action == "request" and "request" in allowed:
        return "request", str(verdict.get("text") or text)
    if action == "air" and "air" in allowed:
        return "air", str(verdict.get("text") or text)
    if action == "skill" and "skill" in allowed:
        name = str(verdict.get("name") or "")
        if name in skills:
            return "skill", name
    return "hold", ""


async def deliver(station, cfg: dict, text: str, persona_name: str) -> str:
    """Send one message where the operator chose. Returns a one-line receipt
    for the log and the panel entry."""
    mode = str(cfg.get("voicemail_destination") or "hold").lower()
    text = str(text or "").strip()
    if not text:
        return "empty message — nothing delivered"

    if mode == "triage":
        action, payload = await _triage(station, cfg, text)
        if action == "request":
            mode = "request"
            text = payload or text
        elif action == "air":
            mode = "air"
        elif action == "skill":
            try:
                await station.run_skill(payload)
                hold(text, persona_name, delivered="skill",
                     note=f"ran the {payload} segment")
                return f"triaged — ran the {payload} segment"
            except Exception as e:                            # noqa: BLE001
                hold(text, persona_name, note=f"skill {payload} failed: {e}")
                return "triage picked a segment the station refused — held"
        else:
            hold(text, persona_name, note="triage: held")
            return "triaged — held for the operator"

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
