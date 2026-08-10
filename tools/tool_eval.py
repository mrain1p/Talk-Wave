"""Tool-adherence eval: does the DJ FIRE the right tool when a caller asks?

The gaps in the 2026-08-10 calls (a takeover refused on the first ask, a
specific track submitted without the confirm the setting requires) were not
prompt or settings bugs — those were right. They were the MODEL not routing to
the tool. Prompt-poking is guesswork against that; this measures it.

It runs the REAL brain (the conduct prompt + `build_llm`) against scripted
caller lines, with FAKE tools that only RECORD what the model tried to call and
return a canned result — so it never touches the station. Point it at a model
and it reports, per scenario, which tool fired (or that the DJ asked first).

    cd agent-worker
    LLM_PROVIDER=google LLM_MODEL=gemini-3.1-flash-lite \\
      python tools/tool_eval.py

Compare two models by running it twice. Needs a provider key in the environment
(GOOGLE_API_KEY / OPENAI_API_KEY), the same one a call uses.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_aw = os.path.join(os.path.dirname(_here), "agent-worker")
# Local: agent-worker/ is a sibling of tools/. In the deployed container the
# app IS the module root (run with PYTHONPATH=/app), so fall back to that.
sys.path.insert(0, _aw if os.path.isdir(_aw) else os.path.dirname(_here))

from livekit.agents import llm as lk_llm  # noqa: E402

import secrets_store  # noqa: E402
import settings as settings_store  # noqa: E402
from brain import conduct  # noqa: E402
from call.providers import build_llm  # noqa: E402

secrets_store.apply_to_env()

# A tiny station context so the model has a DJ to be and a roster to resolve a
# name against — the takeover matching itself is not under test here, the
# ROUTING to it is.
STATION_CONTEXT = """\
You are Danny Boy, live on air on Yosemite FM, hosting "Donovan's Pub · Irish
Folk & Trad". Other shows on the station and who hosts them: "Up Stream · Deep
Cuts" (Wade), "The Trail Ahead · Morning Show" (Dawn), "The Indigo Mile" (Ash).
Now playing: "Segundo" by Pink Martini."""

FIRED: list[tuple[str, dict]] = []


def _fake_tools():
    @lk_llm.function_tool(name="subwave_takeover_show")
    async def takeover_show(show: str, minutes: int = 60) -> str:
        """Put a different show on the air (a takeover). `show` is the show or
        the DJ's name."""
        FIRED.append(("subwave_takeover_show", {"show": show, "minutes": minutes}))
        return f"Done — {show} is pinned for {minutes} minutes."

    @lk_llm.function_tool(name="subwave_request_song")
    async def request_song(query: str) -> str:
        """Request a specific track by name."""
        FIRED.append(("subwave_request_song", {"query": query}))
        return "Request is in — the station is lining it up."

    @lk_llm.function_tool(name="subwave_search_library")
    async def search_library(query: str) -> str:
        """Search the music library for a track before requesting it."""
        FIRED.append(("subwave_search_library", {"query": query}))
        return "Found it in the racks."

    @lk_llm.function_tool(name="subwave_dj_announce")
    async def announce(message: str) -> str:
        """Put a message on the air."""
        FIRED.append(("subwave_dj_announce", {"message": message}))
        return "That's going out on air now."

    return [takeover_show, request_song, search_library, announce]


async def _run_turn(model, tools, system: str, user: str) -> tuple[list, str]:
    """One caller line -> (tools fired, final text)."""
    FIRED.clear()
    by_name = {t.info.name: t for t in tools}
    ctx = lk_llm.ChatContext.empty()
    ctx.add_message(role="system", content=system)
    ctx.add_message(role="user", content=user)
    reply = ""
    for _ in range(4):
        calls = []
        stream = model.chat(chat_ctx=ctx, tools=tools)
        async for chunk in stream:
            d = getattr(chunk, "delta", None)
            if not d:
                continue
            if d.content:
                reply += d.content
            if d.tool_calls:
                calls.extend(d.tool_calls)
        await stream.aclose()
        if not calls:
            break
        for c in calls:
            ctx.insert(lk_llm.FunctionCall(call_id=c.call_id, name=c.name,
                                           arguments=c.arguments or "{}"))
            tool = by_name.get(c.name)
            out = "no such tool"
            if tool is not None:
                try:
                    out = str(await tool(**json.loads(c.arguments or "{}")))
                except Exception as e:                             # noqa: BLE001
                    out = f"error: {e}"
            ctx.insert(lk_llm.FunctionCallOutput(call_id=c.call_id, name=c.name,
                                                 output=out, is_error=False))
    try:
        await model.aclose()
    except Exception:                                             # noqa: BLE001
        pass
    return list(FIRED), reply.strip()


# (label, caller line, what SHOULD happen)
SCENARIOS = [
    ("takeover on first ask", "Can you change the DJ to Wade?",
     "fires subwave_takeover_show"),
    ("specific track waits for a yes", "Play Let It Be by John Legend.",
     "asks to confirm BEFORE firing subwave_request_song"),
    ("close is the caller's button", "Close the chat for me.",
     "no tool; points at the Close button, doesn't loop farewells"),
]


async def main():
    cfg = settings_store.permissions_for(settings_store.load(), "admin")
    cfg = dict(cfg)
    cfg.update({"confirm_requests": True, "allow_takeover": "open",
                "allow_requests": "open", "allow_announcements": "open",
                "allow_library_search": "open"})
    if os.environ.get("EVAL_PROVIDER"):
        cfg["llm_provider"] = os.environ["EVAL_PROVIDER"]
    if os.environ.get("EVAL_MODEL"):
        cfg["llm_model"] = os.environ["EVAL_MODEL"]
    system = STATION_CONTEXT + "\n\n" + conduct.rules(cfg)
    print(f"model = {cfg['llm_provider']}/{cfg['llm_model']}\n")
    for label, line, want in SCENARIOS:
        model = build_llm(cfg)
        tools = _fake_tools()
        fired, reply = await _run_turn(model, tools, system, line)
        names = [f for f, _ in fired]
        print(f"[{label}]")
        print(f"  caller: {line}")
        print(f"  want:   {want}")
        print(f"  fired:  {names or '(none)'}")
        print(f"  said:   {reply[:160]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
