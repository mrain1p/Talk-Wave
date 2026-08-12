"""What the prompt is allowed to promise the DJ can do.

The lesson these defend, learned live on 2026-08-12: a capability the prompt
teaches but the tool list doesn't carry gets MIMED, not refused. Two real
calls answered "switch the show to Donovan's Pub" (allow_takeover off) by
queueing a SONG through request_song and telling the caller "the pub door
opens in a bit"; the drill's refusal sweep then caught the same shape for
shoutouts and hearts. Every action the conduct teaches must ride its own
switch, and the floor (reads, the no-miming rule, the stranger rule) must
survive every switch being off.

Prompt-side only — the tool SURFACE for each switch is test_tools_surface's
subject.
"""

import unittest


class TestThePromptNeverPromisesATakeoverItCannotDo(unittest.TestCase):
    """Two real calls, 2026-08-12 (records ...163605 and ...164707): the
    caller asked to "switch the show to Donovan's Pub" on a deployment with
    allow_takeover OFF. The conduct told the DJ a takeover "is a thing you
    can do" unconditionally — so, with no takeover tool on the surface, it
    reached for the nearest one and queued a SONG through request_song,
    telling the caller "the pub door opens in a bit". The takeover guidance
    now flips with the switch, in both mouths."""

    def _both(self, cfg):
        from brain import conduct, conduct_chat

        return conduct.rules(cfg), conduct_chat.rules(cfg)

    def test_with_the_switch_off_the_prompt_teaches_the_refusal(self):
        for text in self._both({}):
            self.assertNotIn("a thing you can do", text)
            self.assertIn("NEVER put in a song request",
                          " ".join(text.split()))
            # The worked example from the real call, so the model sees the
            # exact shape of the lie it must not tell.
            self.assertIn("the pub door opens in a bit", text)

    def test_with_the_switch_on_the_prompt_still_says_do_it(self):
        for text in self._both({"allow_takeover": True}):
            self.assertIn("a thing you can do", text)
            self.assertNotIn("the pub door opens in a bit", text)

    def test_the_request_tool_says_music_only(self):
        # The tool description carries the boundary too — the model reads it
        # at the moment of choice, which the prompt may be 18k chars behind.
        import inspect

        from call.tools import music

        self.assertIn("MUSIC ONLY", inspect.getsource(music))


class TestADoubtedActionIsCheckedNotExplainedAway(unittest.TestCase):
    """A real chat, 2026-08-12 (record ...195347): a dedication was promised
    with no tool behind it, then claimed as done ("passed it right on to
    Séamus... playing out over the airwaves as we speak"), then explained
    away when the caller said they couldn't hear it ("the sound's got to
    travel from the back corner of Donovan's out through the old masts") —
    and only actually sent on the caller's third push. Claiming an outcome
    is one failure; inventing physics to cover it is the one that costs the
    caller their trust in everything else."""

    def test_both_mouths_are_told_to_go_and_look(self):
        from brain import conduct, conduct_chat

        for text in (conduct.rules({}), conduct_chat.rules({})):
            self.assertIn("BELIEVE THEM", text)
            self.assertIn("Check whether a tool really ran", text)

    def test_the_invented_cover_story_is_shown_as_the_wrong_answer(self):
        from brain import conduct

        text = conduct.rules({})
        self.assertIn("old\n         masts", text)      # the NO example
        self.assertIn("that never went out", text)      # the YES example


class TestActionBulletsRideTheirOwnSwitch(unittest.TestCase):
    """The generalisation of the takeover lesson, caught by the drill's
    refusal sweep the same day: with no announce tool the DJ "passed on" a
    shoutout that went nowhere, because the prompt taught shoutouts
    unconditionally."""

    def test_each_bullet_appears_only_with_its_switch(self):
        from brain import conduct, conduct_chat

        for rules in (conduct.rules, conduct_chat.rules):
            for cfg, marker in [
                ({"allow_requests": True}, "**Requests.**"),
                ({"allow_library_search": True}, "**Search the library**"),
                ({"allow_announcements": True}, "**Put things on air**"),
            ]:
                self.assertIn(marker, rules(cfg))
                self.assertNotIn(marker, rules({}))

    def test_the_floor_survives_every_switch_being_off(self):
        # Reads, the no-miming rule, and the stranger rule are not actions —
        # they must be there whatever the toggles say.
        from brain import conduct

        bare = conduct.rules({})
        self.assertIn("Check what's playing", bare)
        self.assertIn("never mime the action", bare)
        self.assertIn("stranger", bare)

    def test_whats_off_is_said_out_loud_not_just_omitted(self):
        # Absence wasn't enough: with the shoutout bullet merely missing, the
        # DJ still said "that shoutout's in the air now" (refusal sweep,
        # 2026-08-12). The off-list must name what the line can't do, and
        # vanish when everything is on.
        from brain import conduct, conduct_chat

        for rules in (conduct.rules, conduct_chat.rules):
            bare = rules({})
            self.assertIn("Not on this line tonight", bare)
            self.assertIn("shoutouts", bare)
            everything = rules({g: True for g in (
                "allow_requests", "allow_announcements", "allow_favorite",
                "allow_skip_track", "allow_skills")})
            self.assertNotIn("Not on this line tonight", everything)


if __name__ == "__main__":
    unittest.main()
