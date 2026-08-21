"""Whether the prompt can be MEASURED — not what it says.

Split from test_conduct at 0.10.152, when the tool-block split pushed that
file past the ceiling and made the seam obvious: everything there defends a
claim the prompt makes to the DJ, and everything here defends the instrument
that prices and ablates it. The two change for different reasons — one when a
call goes wrong, the other when the way we measure changes.

The instrument is load-bearing. The plan carried "~20k characters" as the
working figure for a year while the real assembled prompt reached 28,715, and
nobody was careless: there was simply no instrument, so each paragraph was
argued on its own merits — which is always "cheap" — and never against the
total.
"""

import inspect
import unittest


class TestThePromptBudgetIsMeasurable(unittest.TestCase):
    """Every section of the prompt has to be priceable, or the budget is an
    opinion.

    The plan carried "~20k characters" as the working figure for a year while
    the real assembled prompt reached 28,715 (measured on the live deployment,
    2026-08-14). Nobody was careless: there was simply no instrument, so each
    paragraph was argued on its own merits — which is always "cheap" — and
    never against the total. `tools/prompt_report.py` prices the sections, and
    it prices them from `blocks()`, which is only honest while `rules()` has no
    text of its own.

    So this is the invariant that keeps the instrument pointed at the real
    prompt: a section added straight into `rules()` would be sent to the model,
    paid for on every turn, and invisible to the report. It has to go through
    `blocks()` and get a name.
    """

    def _cfg(self):
        from call.tools.registry import TOOLS, NEVER

        return {t.gate: True for t in TOOLS if t.gate not in ("read", NEVER)}

    def test_neither_mouth_assembles_text_the_report_cannot_see(self):
        from brain import conduct, conduct_chat

        cfg = self._cfg()
        for mod in (conduct, conduct_chat):
            self.assertEqual(
                mod.rules(cfg),
                "\n\n".join(text for _name, text in mod.blocks(cfg)),
                f"{mod.__name__}.rules() is assembling text that is not in "
                "blocks(), so tools/prompt_report.py cannot price it and a "
                "section can grow unmeasured — the exact way this got to 28k",
            )

    def test_every_section_has_a_name_and_no_two_share_one(self):
        from brain import conduct, conduct_chat

        cfg = self._cfg()
        for mod in (conduct, conduct_chat):
            names = [name for name, _ in mod.blocks(cfg)]
            self.assertTrue(all(names), f"{mod.__name__} has an unnamed section")
            self.assertEqual(
                sorted(names), sorted(set(names)),
                f"{mod.__name__} has two sections with one name — the report "
                "would price them as one and an ablation would drop both")

    def test_a_section_can_be_dropped_for_measurement(self):
        # The ablation lever phase 3 runs on. If `drop` stops working, the
        # prompt stops being testable and the next cut is a matter of taste.
        from brain import conduct

        cfg = self._cfg()
        whole = conduct.rules(cfg)
        for name, text in conduct.blocks(cfg):
            without = conduct.rules(cfg, drop={name})
            self.assertLess(len(without), len(whole),
                            f"dropping {name} changed nothing")
            self.assertNotIn(text, without)

    def test_the_report_exists_and_reads_both_mouths(self):
        # Keeps the dev script named in the suite — the coverage floor every
        # module owes — and fails if it stops covering a mouth.
        from tests.support import REPO

        src = (REPO / "tools" / "prompt_report.py").read_text(encoding="utf-8")
        for needle in (".blocks(", "conduct_chat", "--live", "CHARS_PER_TOKEN"):
            self.assertIn(needle, src,
                          f"prompt_report.py no longer mentions {needle!r} — "
                          "the budget instrument has changed shape")



class TestTheToolBlockSplitChangedNoPromptByte(unittest.TestCase):
    """`tool_rules` became droppable in parts, and that had to cost nothing.

    The block is 11,613 characters on the live deployment — 39% of the whole
    prompt, four times any other section — and it had never been measured,
    because it could not be: `ABLATE=tool_rules` removes the tool surface's
    entire description and proves only that a DJ told nothing about its tools
    uses them badly. The question worth asking is whether the per-tool PROSE
    earns its place while the triage table stays, and that needs a finer knob.

    So `_tools` grew a `drop` argument, and the whole value of the change
    depends on it being invisible when nobody passes one. A refactor that
    quietly moves a character of the shipped prompt would put every earlier
    measurement on a different prompt from every later one.
    """

    def _configs(self):
        from call.tools.registry import TOOLS, NEVER

        every = {t.gate: True for t in TOOLS if t.gate not in ("read", NEVER)}
        return (("everything on", every), ("everything off", {}),
                ("requests only", {"allow_requests": True}),
                ("search but no requests", {"allow_library_search": True}))

    def test_the_block_is_unchanged_when_nothing_is_dropped(self):
        from brain import tool_rules

        for label, cfg in self._configs():
            self.assertEqual(tool_rules._tools(cfg),
                             tool_rules._tools(cfg, frozenset()),
                             f"{label}: passing an empty drop is not a no-op")

    def test_every_named_section_actually_removes_something(self):
        """A name in SECTIONS that drops no text is a knob wired to nothing.

        Borrowed from SUB/WAVE, whose instruction loader fails the build when a
        prompt section is authored but never rendered by any call site — dead
        prompt text reads as live instruction to whoever edits it next. The
        same hazard in reverse: `tool_floor` was listed here and its gate never
        applied, so an ablation arm naming it would have measured the full
        prompt against itself and reported "this section is free".
        """
        from brain import tool_rules

        # Across configs, not one: `tool_off` is the "not on this line
        # tonight" list, which exists only when a capability is switched OFF,
        # so it correctly removes nothing from a prompt with everything on.
        # A section only has to earn its name somewhere.
        for name in tool_rules.SECTIONS:
            bites = [
                len(tool_rules._tools(cfg, frozenset({name}))) < len(tool_rules._tools(cfg))
                for _label, cfg in self._configs()
            ]
            self.assertTrue(
                any(bites),
                f"ABLATE={name} removes nothing under any config — the name is "
                "in SECTIONS but no gate reads it, so an arm using it silently "
                "measures the control prompt twice",
            )

    def test_the_triage_table_is_droppable_but_flagged_as_a_keeper(self):
        # 30/30 on the deployed model, 2026-08-14. It is droppable so it can be
        # priced, not because cutting it is a live proposal, and the source has
        # to say which — a name in a drop list reads as a suggestion otherwise.
        from brain import tool_rules

        self.assertIn("tool_finding", tool_rules.SECTIONS)
        self.assertIn("MEASURED 30/30", inspect.getsource(tool_rules))


class TestTheTruthBlockSplitChangedNoPromptByte(unittest.TestCase):
    """`say_the_true_thing` became droppable in parts, and that had to cost
    nothing.

    At 4,049 characters it is 16% of the conduct and the second-largest block
    after `tool_rules`. The one ablation ever run on it — refusals set, 14/15
    with against 14/14 without — was RETRACTED by the session that ran it: one
    scenario in five never fired its fault, and with the section present the DJ
    still told a caller a refused request was "locked in to follow", two rounds
    of two. "The section is not inert — it is insufficient."

    So it is unmeasured, and dropping it whole would answer the wrong question
    anyway: it carries the FOURTH WALL rule, which is persona, beside three
    honesty rules that are not. Priced a clause at a time or not at all — and
    the whole value of that depends on the split being invisible when nobody
    passes a drop.
    """

    def _configs(self):
        return (("takeover on", {"allow_takeover": True}),
                ("takeover off", {}),
                ("requests only", {"allow_requests": True}),
                ("all on", {"allow_takeover": True, "allow_requests": True,
                            "allow_library_search": True}))

    def test_an_empty_drop_is_a_no_op(self):
        from brain import conduct

        for label, cfg in self._configs():
            self.assertEqual(conduct.say_the_true_thing(cfg),
                             conduct.say_the_true_thing(cfg, frozenset()),
                             f"{label}: passing an empty drop is not a no-op")

    def test_the_assembled_conduct_is_unchanged_on_both_mouths(self):
        # The section is shared, so a stray newline would move the shipped
        # prompt on the phone AND the text line at once, and put every earlier
        # measurement on a different prompt from every later one.
        from brain import conduct, conduct_chat

        for label, cfg in self._configs():
            for mod in (conduct, conduct_chat):
                with self.subTest(label=label, mod=mod.__name__):
                    self.assertEqual(mod.rules(cfg), mod.rules(cfg, drop=set()))

    def test_every_clause_actually_removes_something(self):
        """A name in TRUTH_CLAUSES that drops no text is a knob wired to
        nothing — an ablation arm naming it would measure the control prompt
        against itself and report the clause as free."""
        from brain import conduct

        for name in conduct.TRUTH_CLAUSES:
            bites = [
                len(conduct.say_the_true_thing(cfg, frozenset({name})))
                < len(conduct.say_the_true_thing(cfg))
                for _label, cfg in self._configs()
            ]
            self.assertTrue(any(bites), f"ABLATE={name} removes nothing")

    def test_the_heading_survives_every_clause_being_dropped(self):
        # Otherwise the section vanishes silently and an arm that dropped all
        # four would be measuring a prompt with no honesty section AND no
        # heading, which is two changes reported as one.
        from brain import conduct

        bare = conduct.say_the_true_thing({}, frozenset(conduct.TRUTH_CLAUSES))
        self.assertIn("Stay in character", bare)

    def test_the_clause_a_guard_now_covers_is_named(self):
        # call/stuck.py (0.98.22) mechanises part of "believe the caller",
        # which is exactly the position CLOSING_DOOR was in once door.py
        # existed. That one measured as KEEP the prose, so this is a real
        # question rather than a foregone one — and it is the clause to price
        # first.
        from brain import conduct

        self.assertIn("truth_believe_the_caller", conduct.TRUTH_CLAUSES)

    def test_the_ablation_harness_knows_these_names(self):
        # An ABLATE name the harness does not recognise is reported as unknown
        # and silently ignored, which measures the control prompt twice.
        import re

        from tests.support import AGENT_WORKER

        src = (AGENT_WORKER / "scripted_call.py").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"TRUTH_CLAUSES", src),
                        "scripted_call does not add TRUTH_CLAUSES to the known "
                        "ABLATE names, so an arm naming one measures nothing")
