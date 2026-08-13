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


class TestThePromptTeachesTheDJHowToActuallyFindARecord(unittest.TestCase):
    """The night of 2026-08-12/13, in prompt form.

    Three separate callers, three shapes of the same failure, and in every
    one the DJ had a better tool built and switched on that the prompt had
    never mentioned. `tool_rules.finding_rule` is the fix; these hold it in
    place, including the part where each clause disappears with its switch.
    """

    ON = {"allow_requests": "open", "allow_library_search": "open",
          "allow_sound_search": "open", "allow_exact_queue": "open",
          "allow_cancel_queue": "open"}

    def _both(self, cfg):
        from brain import conduct, conduct_chat

        return conduct.rules(cfg), conduct_chat.rules(cfg)

    def test_a_name_search_missing_is_not_proof_the_track_is_absent(self):
        # Caller asked for "Firestorm by Kygo". The track is called Firestone
        # and the library holds it; /dj/search is a literal match, so one
        # letter was the whole difference and the caller was told their song
        # did not exist.
        for rules in self._both(self.ON):
            self.assertIn("NOT proof", rules)
            self.assertIn("Firestone", rules)
            # The two recoveries it never tried.
            self.assertIn("Search the ARTIST on their own", rules)
            self.assertIn("Use what you know", rules)

    def test_a_described_vibe_is_routed_to_the_sound_search(self):
        for rules in self._both(self.ON):
            self.assertIn("subwave_search_by_sound", rules)
            self.assertIn("subwave_more_like_this", rules)

    def test_a_picked_search_result_is_queued_not_re_requested(self):
        # Caller picked "On the Nature of Daylight" out of the results; the
        # DJ submitted the TITLE as a request three times and got three
        # different wrong records while the right one sat in the list. The
        # exact-queue tool was on the whole time and the prompt had never
        # named it — the 16,644-character conduct did not contain the string
        # "queue_track".
        for rules in self._both(self.ON):
            self.assertIn("subwave_queue_track", rules)
            self.assertIn("Dinah Washington", rules)

    def test_every_finding_clause_rides_its_own_switch(self):
        from brain import conduct

        names_only = conduct.rules({"allow_requests": "open",
                                    "allow_library_search": "open"})
        self.assertIn("subwave_search_library", names_only)
        self.assertNotIn("subwave_search_by_sound", names_only)
        self.assertNotIn("subwave_queue_track", names_only)

        nothing = conduct.rules({"allow_requests": "open"})
        self.assertNotIn("subwave_browse_library", nothing)
        self.assertNotIn("Firestone", nothing)


class TestThePromptStopsClaimingRequestsCannotBeCancelled(unittest.TestCase):
    """It said so for months after the station gained DELETE /dj/queue/:id.

    A caller asked for the track he had just been given to be taken back out;
    the DJ said "can't pull a track back once it's rolling down the wire",
    which was the prompt talking, and the track had not started. Both halves
    are written from the tool now.
    """

    def test_with_the_tool_on_the_dj_is_told_it_can_pull_a_waiting_track(self):
        from brain import conduct

        rules = conduct.rules({"allow_requests": "open",
                               "allow_cancel_queue": "open"})
        self.assertIn("subwave_cancel_queued_track", rules)
        self.assertNotIn("CANNOT be cancelled", rules)
        # The two limits that make it honest rather than a new way to lie.
        self.assertIn("the tool refuses", rules)
        self.assertIn("a DIFFERENT caller", rules)

    def test_with_the_tool_off_the_old_truth_comes_back(self):
        from brain import conduct

        rules = conduct.rules({"allow_requests": "open"})
        self.assertIn("CANNOT be cancelled", rules)
        self.assertNotIn("subwave_cancel_queued_track", rules)


class TestNoToolIsBuiltWithoutThePromptKnowingIt(unittest.TestCase):
    """The general shape of the 0.10.104 bug, caught once and for all.

    `subwave_queue_track` had a switch, a permission row, station credentials
    and a working wrapper — and the 16,644-character conduct never contained
    the string "queue_track". So the model was handed a tool it was never told
    to use, and reached for the wrong one instead; a caller asked for a
    specific recording three times and got three different wrong ones.

    Absence is invisible: nothing failed, no test went red, and the only
    symptom was a caller being annoyed. This is the test that would have
    caught it on the day it shipped — every tool the caller's DJ is handed
    must be NAMED somewhere in the prompt that comes with it.

    Deliberately a weak claim (named at all, not named well). A strong one
    would be unmaintainable prose-matching; this one only has to be true.
    """

    # Tools the model is not steered to by name because the prompt describes
    # WHEN to use them in words instead. Each needs a reason, and the reason
    # has to survive being read out loud.
    NAMED_ELSEWHERE = {
        # The five station reads are covered by "Check what's playing /
        # coming up rather than guessing" and the briefing's own facts. They
        # are also always on, so there is no switch to be out of step with.
        "subwave_health", "subwave_now_playing", "subwave_station_state",
        "subwave_schedule", "subwave_session",
        # A read with no switch, same as above.
        "subwave_current_lyrics",
        # Follow-ups to a tool the prompt does name, reached from that tool's
        # own result text rather than from the conduct.
        "subwave_request_status", "subwave_recent_tracks",
        # These carry a named bullet in _tools() rather than a bare tool name
        # ("Put things on air", "Offering a segment", the takeover bullet).
        "subwave_dj_announce", "subwave_list_skills", "subwave_run_skill",
        "subwave_skip_track", "subwave_dj_segment",
        "subwave_takeover_show", "subwave_cancel_takeover",
        "subwave_like_track", "subwave_unlike_track",
    }

    def test_every_unlocked_tool_is_named_or_deliberately_not(self):
        from brain import conduct, conduct_chat
        from call.tools.registry import TOOLS, NEVER

        # Everything on at once: the question is whether the prompt CAN name
        # a tool, not whether this operator switched it on.
        cfg = {t.gate: "open" for t in TOOLS if t.gate not in ("read", NEVER)}
        for rules in (conduct.rules, conduct_chat.rules):
            text = rules(cfg)
            missing = [
                t.name for t in TOOLS
                if t.gate != NEVER
                and t.name not in self.NAMED_ELSEWHERE
                and t.name not in text
            ]
            self.assertEqual(
                missing, [],
                "these tools are handed to the model with nothing in the "
                "prompt telling it they exist, which is how queue_track went "
                "unused for months: " + ", ".join(missing))

    def test_a_blocked_tool_is_never_named_as_available(self):
        # The mirror image, and the worse direction: naming a tool the line
        # does not carry is what taught the DJ to MIME an action.
        from brain import conduct
        from call.tools.registry import TOOLS, NEVER

        cfg = {t.gate: "open" for t in TOOLS if t.gate not in ("read", NEVER)}
        text = conduct.rules(cfg)
        for tool in TOOLS:
            if tool.gate == NEVER:
                self.assertNotIn(tool.name, text)

    def test_the_exemption_list_cannot_outlive_its_tools(self):
        # A retired tool left in the exemption list would silently excuse a
        # DIFFERENT tool later if the name were ever reused.
        from call.tools.registry import BY_NAME

        stale = sorted(n for n in self.NAMED_ELSEWHERE if n not in BY_NAME)
        self.assertEqual(stale, [], f"no such tool any more: {stale}")


class TestARefusalIsPassedOnNotNarrated(unittest.TestCase):
    """The station said one specific thing and the caller heard three
    inventions of it: "the queue's jammed solid", "the decks won't clear",
    "requests open back up in a few minutes". What it actually said was
    "your last request is still queued — it airs first"."""

    def test_the_reason_the_tool_gave_is_the_thing_to_say(self):
        from brain import conduct, conduct_chat

        for rules in (conduct.rules({}), conduct_chat.rules({})):
            self.assertIn("pass on the REASON IT GAVE", rules)
            self.assertIn("queue's jammed solid", rules)   # the NO example


if __name__ == "__main__":
    unittest.main()


class TestTheDJSpeaksAsItselfNotAboutItself(unittest.TestCase):
    """"Duke reached across the console and yanked the lever."

    A caller asked, in as many words, "why are you talking about yourself in
    the third person? it's weird" (2026-08-13). It is the stage-direction ban
    wearing better clothes — the persona narrating its own actions as prose
    instead of speaking them — and the existing rule did not name it, so the
    model kept doing it in a voice the operator had written to be atmospheric.
    """

    def test_both_mouths_are_told_to_speak_in_the_first_person(self):
        from brain import conduct, conduct_chat

        for text in (conduct.rules({}), conduct_chat.rules({})):
            self.assertIn("not narrating a novel", text)
            self.assertIn("third person", text)

    def test_the_real_line_is_the_worked_example(self):
        from brain import conduct

        text = conduct.rules({})
        self.assertIn("Duke reached across the console", text)   # the NO
        self.assertIn("needle's off the groove", text)           # the YES
