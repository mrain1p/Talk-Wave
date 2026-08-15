"""What the system prompt COSTS, section by section.

Every character here is re-sent on every turn of every call — and on every
turn the machinery generates on its own (the greeting, the promise nudge, the
idle check-in, the come-back, the late-match line). So a paragraph added to
the conduct is not paid once, it is paid per turn, per call, forever, in
time-to-first-token the caller hears.

Nothing could say how much. The master plan carried "~20k characters" as the
working figure from 0.9.47 onwards and it was never re-measured; the real
assembled prompt on the operator's own deployment is **28,715 characters**
(measured 2026-08-14, all gates on, live station). That is not a rebuke of any
one paragraph — every section here was added for a call that went wrong — it
is the reason a budget needs an instrument rather than an intuition.

This is the instrument. It reads the SAME named block lists the prompt is
assembled from (`conduct.blocks` / `conduct_chat.blocks`), so it cannot
describe a prompt that is no longer being sent, and a section added without
appearing here fails `TestThePromptBudgetIsMeasurable`.

    python tools/prompt_report.py               # sections, offline, no station
    python tools/prompt_report.py --live        # + the real assembled prompt

Offline is the default on purpose: the section costs are pure functions of the
settings, so the number that matters most needs no network, no key and no
station — it can be run in a container, in CI, or on a plane. `--live` adds
what the STATION contributes (the DJ card, the show card, the facts), which is
the half that varies per deployment.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_aw = os.path.join(os.path.dirname(_here), "agent-worker")
# Local: agent-worker/ is a sibling of tools/. In the deployed container the
# app IS the module root (run with PYTHONPATH=/app), so fall back to that.
sys.path.insert(0, _aw if os.path.isdir(_aw) else os.path.dirname(_here))

import settings as settings_store  # noqa: E402
from brain import conduct, conduct_chat  # noqa: E402

# Roughly four characters to a token across these models. Deliberately crude:
# the point is the ORDER of the number and the ratio between sections, and a
# real tokenizer would add a dependency to a script whose whole value is that
# it runs anywhere.
CHARS_PER_TOKEN = 4


def gate_sets() -> list[tuple[str, dict]]:
    """The configurations worth pricing, and why each one.

    A prompt is not one size. The operator's own tiers differ, and "everything
    on" is what the drill sweeps — so a budget quoted as a single number is
    quoting one of these and not saying which.
    """
    stored = settings_store.load()
    every_gate = settings_store.permissions_for(stored, "admin")
    from call.tools import registry as tool_registry

    every_gate = dict(every_gate)
    for tool in tool_registry.TOOLS:
        if tool.gate not in (tool_registry.READ, tool_registry.NEVER):
            every_gate[tool.gate] = True
    return [
        ("open tier", settings_store.permissions_for(stored, "open")),
        ("guest tier", settings_store.permissions_for(stored, "guest")),
        ("admin tier", settings_store.permissions_for(stored, "admin")),
        ("every gate on", every_gate),
    ]


def price(blocks: list[tuple[str, str]]) -> list[tuple[str, int, float]]:
    total = sum(len(text) for _, text in blocks) or 1
    return [(name, len(text), 100.0 * len(text) / total) for name, text in blocks]


def report_sections(label: str, cfg: dict) -> None:
    for mouth, mod in (("call", conduct), ("chat", conduct_chat)):
        rows = price(mod.blocks(cfg))
        total = sum(n for _, n, _ in rows)
        print(f"\n  {mouth.upper()}  ({label})   "
              f"{total:,} chars  ~{total // CHARS_PER_TOKEN:,} tokens")
        for name, chars, share in rows:
            bar = "#" * max(1, round(share / 2))
            print(f"    {chars:6,}  {share:4.1f}%  {name:<22} {bar}")


def report_switch_cost(cfg: dict) -> None:
    """What turning one thing off actually does to the size.

    Worth its own section because the answer is counter-intuitive and an
    operator has reasonably assumed otherwise: switching a capability OFF can
    make the prompt BIGGER. The "Not on this line tonight" list in
    `tool_rules._tools` exists because absence alone was not enough — a DJ with
    no announce tool still told a caller the shoutout was in the air — so a
    disabled capability buys a sentence saying so.
    """
    from call.tools import registry as tool_registry

    base = len(conduct.rules(cfg))
    print(f"\n  WHAT ONE SWITCH IS WORTH  (from {base:,} chars)")
    gates = sorted({t.gate for t in tool_registry.TOOLS
                    if t.gate not in (tool_registry.READ, tool_registry.NEVER)})
    for gate in gates:
        if not cfg.get(gate):
            continue
        flipped = dict(cfg)
        flipped[gate] = False
        delta = len(conduct.rules(flipped)) - base
        print(f"    {delta:+6,}  turning off {gate}")


def report_ablations(cfg: dict) -> None:
    """What each section would save if it were dropped — the shortlist for a
    measured cut. Saving is not a reason to cut; it is the size of the prize
    if a sweep shows the section changes nothing."""
    base = len(conduct.rules(cfg))
    print(f"\n  IF DROPPED  (call mouth, from {base:,} chars)")
    for name, _text in conduct.blocks(cfg):
        saved = base - len(conduct.rules(cfg, drop={name}))
        print(f"    -{saved:6,}  ({100.0 * saved / base:4.1f}%)  {name}")

    # tool_rules is 39% of the prompt and `blocks()` returns it whole, so the
    # line above prices it as one indivisible lump — which is exactly why it
    # went a year unmeasured. These are its parts, indented under it because
    # they are not peers of the sections above: they nest inside the largest
    # one. `tool_finding` is the triage table and is here to be priced, not
    # proposed — it measured 30/30 on the deployed model.
    from brain import tool_rules

    print(f"\n    within tool_rules")
    for name in getattr(tool_rules, "SECTIONS", ()):
        saved = base - len(conduct.rules(cfg, drop={name}))
        print(f"      -{saved:6,}  ({100.0 * saved / base:4.1f}%)  {name}")


async def report_live() -> None:
    """The whole assembled prompt against the real station, so the identity
    and the facts — the half this script cannot compute — are priced too."""
    import secrets_store
    from station import StationClient

    import brain

    secrets_store.apply_to_env()
    cfg = settings_store.permissions_for(settings_store.load(), "admin")
    station = StationClient()
    try:
        snap = await station.snapshot(with_skills=bool(cfg.get("allow_skills")))
        persona = station.persona_from(snap["dj"], snap["personas"])
        whole = await brain.build_system_prompt(station, persona, snapshot=snap,
                                                cfg=cfg, mode="call")
    finally:
        await station.aclose()

    rules = len(conduct.rules(cfg))
    print(f"\n  LIVE  (persona {persona.get('name')!r})")
    print(f"    {len(whole):6,}  the whole assembled prompt "
          f"(~{len(whole) // CHARS_PER_TOKEN:,} tokens, every turn)")
    print(f"    {rules:6,}  of which conduct + tool rules")
    print(f"    {len(whole) - rules:6,}  of which identity, cards and station "
          "facts")


def main() -> None:
    print("PROMPT BUDGET")
    for label, cfg in gate_sets():
        report_sections(label, cfg)
    everything = gate_sets()[-1][1]
    report_switch_cost(everything)
    report_ablations(everything)
    if "--live" in sys.argv:
        import asyncio

        asyncio.run(report_live())


if __name__ == "__main__":
    main()
