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

import inspect
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
        self.assertIn("What's on right now", bare)
        self.assertIn("never mime the action", bare)
        self.assertIn("stranger", bare)

    def test_the_reads_row_names_the_tool_for_what_is_on_air(self):
        # Missing on 2026-08-20: `tool_reads` was one line naming no tool, and
        # the table beside it covers only finding a record to PLAY — so "what
        # song is this" had no row, and the DJ answered it eleven times with
        # current_lyrics and never once with now_playing. The last assertion
        # is the other half: the caller said "it does have lyrics" and was
        # told "my ears aren't playing tricks on me".
        from brain import conduct

        bare = conduct.rules({})
        self.assertIn("subwave_now_playing", bare)
        self.assertIn("subwave_current_lyrics is for the WORDS", bare)
        self.assertIn("never the answer to what the song IS", bare)
        self.assertIn("they are the one hearing it", bare)

    def test_the_briefing_is_only_called_live_when_it_refreshes(self):
        # The "briefing is LIVE, refreshed for you" promise is only true under
        # avoid_on_air_overlap (the watch loop is what re-stages the track).
        # With the toggle off the briefing is frozen at pickup, so the prompt
        # must not claim it stays live, or the DJ reads a stale track from
        # memory (top-down review, 2026-08-28).
        from brain import conduct

        live = conduct.rules({"avoid_on_air_overlap": True})
        self.assertIn("is LIVE", live)
        frozen = conduct.rules({"avoid_on_air_overlap": False})
        self.assertNotIn("is LIVE", frozen)
        self.assertIn("when this call CONNECTED", frozen)
        # Either way, now_playing is the certain read.
        self.assertIn("subwave_now_playing", frozen)

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
            # Read from OFF_LIST rather than repeated here: this list was
            # hardcoded in two places and they drifted, which is how
            # allow_album_queue ended up with a tool and no sentence.
            from brain.tool_rules import OFF_LIST

            everything = rules({g: True for g, _ in OFF_LIST})
            self.assertNotIn("Not on this line tonight", everything)

    def test_nothing_askable_goes_unsaid(self):
        """A gate with a tool is named when it is off, or exempt with a reason.

        The bug this exists for, in the operator's own chat (2026-08-22):
        `allow_album_queue` is `guest`, an OPEN-tier caller asked for a mix,
        the tool was never built, the off-list never mentioned it — and the DJ,
        with no tool and no sentence, invented a station fault ("it's been a
        bit stubborn with the queue"). Nothing was stubborn. Landing in
        neither list must be impossible to do by accident.
        """
        from brain.tool_rules import OFF_LIST, OFF_LIST_EXEMPT
        from call.tools.registry import TOOLS, NEVER, READ

        named = {g for g, _ in OFF_LIST}
        gates = {t.gate for t in TOOLS if t.gate not in (NEVER, READ)}
        unaccounted = sorted(gates - named - set(OFF_LIST_EXEMPT))
        self.assertEqual(
            unaccounted, [],
            "these gates build a tool a caller can ask for, and when the gate "
            "is OFF the DJ is told nothing about it — so it has no tool and no "
            "sentence, which is when it invents one. Add to OFF_LIST, or to "
            f"OFF_LIST_EXEMPT with the reason: {unaccounted}")

    def test_no_gate_is_in_both_lists(self):
        from brain.tool_rules import OFF_LIST, OFF_LIST_EXEMPT

        both = sorted({g for g, _ in OFF_LIST} & set(OFF_LIST_EXEMPT))
        self.assertEqual(both, [], f"named and excused at once: {both}")

    def test_every_exemption_names_a_real_gate(self):
        # An exemption for a gate that no longer exists is a stale excuse that
        # would silently cover a future gate of the same name.
        from brain.tool_rules import OFF_LIST_EXEMPT
        from call.tools.registry import TOOLS

        real = {t.gate for t in TOOLS} | {"single_lookup_tool"}
        stale = sorted(set(OFF_LIST_EXEMPT) - real)
        self.assertEqual(stale, [], f"exemption for a gate nothing builds: {stale}")


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

    def test_an_earlier_call_is_answered_from_the_station_not_from_memory(self):
        # The Casino night's opening line: "did you recently cancel my
        # queue?" — answered "I haven't cleared anything since we started
        # chatting", which is per-call true and globally evasive: the DJ's
        # memory starts at pickup and the caller's question did not. The
        # queue and the play log are readable; the rule points there.
        for rules in self._both(self.ON):
            flat = " ".join(rules.split())
            self.assertIn("your memory starts at pickup", flat)
            self.assertIn("memory resets between calls", flat)
            # The day-log closed the loop: the rule now points at the booth's
            # own cross-call ledger, not just the queue snapshot.
            self.assertIn("subwave_booth_log", flat)
            self.assertIn("the log and the queue are the answer", flat)

    def test_a_soundtrack_is_knowledge_the_dj_may_not_disown(self):
        # The Casino calls (2026-08-26, three thumbs-down): asked for songs
        # from the film, the DJ searched the film's NAME, then said "I don't
        # have a way to pull a soundtrack" — a false claim of incapacity from
        # a model that knew the tracklist and later named it. The honesty
        # rules against inventing LIBRARY facts had over-generalised into
        # denying its own knowledge; this line draws the boundary where it
        # belongs, on both mouths.
        for rules in self._both(self.ON):
            # Flattened: the rule wraps mid-sentence and the claim is about
            # the words, not the line breaks.
            flat = " ".join(rules.split())
            self.assertIn("a LIST you already know", flat)
            self.assertIn("YOU are the authority", flat)
            self.assertIn(
                "Never tell a caller you have no way to know a soundtrack",
                flat)
            # The substitute-by-offer rule — the "inspired mix" was queued
            # unasked on the real call.
            self.assertIn("never a silent swap", flat)

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
        self.assertIn("the tools refuse", rules)
        self.assertIn("a DIFFERENT caller", rules)

    def test_with_the_tool_off_the_old_truth_comes_back(self):
        from brain import conduct

        rules = conduct.rules({"allow_requests": "open"})
        self.assertIn("CANNOT be cancelled", rules)
        self.assertNotIn("subwave_cancel_queued_track", rules)


class TestTriageIsStatedInOnePlace(unittest.TestCase):
    """Three statements of the same decision, and they disagreed.

    Where does "something dreamy" go? `tool_rules.finding_rule` said the sound
    search; the "Search the library" bullet, two paragraphs above it in the SAME
    file, said "straight to a REQUEST"; and the search wrapper's runtime refusal
    named the request tool as well. The bullet and the wrapper both predate the
    discovery tools (0.10.104) and neither was reconciled with them, so the DJ
    was given contradictory instructions and then graded on which one it picked.

    The table in finding_rule is the single source now. The bullet says a
    description is not a search and points AT the table rather than answering
    for it; the wrapper's refusal is checked in test_music_tools.
    """

    def _rules(self, **cfg):
        from brain import tool_rules

        base = {"allow_library_search": True, "allow_requests": True,
                "allow_sound_search": True}
        base.update(cfg)
        return tool_rules._tools(base)

    def test_the_search_bullet_does_not_answer_for_the_table(self):
        text = self._rules()
        bullet = text.split("**Search the library**")[1].split("- **")[0]
        self.assertIn("A description is not a search", bullet)
        self.assertNotIn("go straight to a REQUEST", bullet,
                         "the bullet is prescribing a destination again, and "
                         "the table below it prescribes a different one")

    def test_the_table_is_where_a_description_is_routed(self):
        text = self._rules()
        self.assertIn("Finding the record", text)
        self.assertIn("subwave_search_by_sound", text)
        # And the pointer actually points somewhere: the table comes after.
        self.assertLess(text.index("A description is not a search"),
                        text.index("**Finding the record.**"))

    def test_with_no_sound_search_the_table_still_answers(self):
        # The bullet defers unconditionally, so the table has to carry the
        # fallback on a station with no analyser — otherwise deferring would
        # send the DJ to a paragraph that says nothing about descriptions.
        text = self._rules(allow_sound_search=False)
        self.assertIn("Finding the record", text)
        self.assertIn("subwave_request_song", text)


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
    #
    # Two categories, and the split is not bookkeeping. "BULLET" claims the
    # tool has prose of its own that appears and disappears with its switch;
    # "SCHEMA" claims the tool's own description is the whole instruction and
    # the prompt deliberately says nothing. The first claim is CHECKABLE and is
    # checked below — and when it was written down as one undifferentiated set,
    # four entries claimed a bullet they did not have.
    #
    # Found 2026-08-14 with tools/prompt_report.py: flipping allow_skip_track,
    # allow_dj_segment, allow_favorite or allow_unfavorite changed the assembled
    # prompt by ZERO characters. The exemption list said all four "carry a named
    # bullet in _tools()". They carried nothing, and this test — the one written
    # to close the 0.10.104 bug class for good — was passing on a sentence that
    # had stopped being true. Skip and the programme beat now have real bullets
    # (they reach every listener and the prompt owed them a consequence); the
    # hearts keep the exemption, honestly labelled.
    BULLET = {
        "subwave_dj_announce": "Put things on air",
        "subwave_skip_track": "Skip what's playing",
        "subwave_dj_segment": "Fire a programme beat",
        "subwave_takeover_show": "the takeover bullet",
        "subwave_cancel_takeover": "the takeover bullet",
        "subwave_never_play_track": "Ban a record for good",
        "subwave_allow_track_again": "Ban a record for good",
        "subwave_genre_lock": "Hold the station to a genre",
        "subwave_clear_genre_lock": "Hold the station to a genre",
    }
    SCHEMA = {
        # Segments are named by the ALWAYS-ON line in running_the_call ("A
        # segment — run it by name, only from the list you've been given"), and
        # the catalogue of real segment names rides allow_skills in the
        # BRIEFING rather than in the conduct. So there is no switch-riding
        # bullet to find here and there should not be one: the rule holds
        # whether or not this station has segments, and the list is a fact.
        "subwave_list_skills": "named by the always-on segment line",
        "subwave_run_skill": "named by the always-on segment line",
        # The five station reads are covered by "Check what's playing /
        # coming up rather than guessing" and the briefing's own facts. They
        # are also always on, so there is no switch to be out of step with.
        "subwave_health": "always-on read",
        "subwave_now_playing": "always-on read",
        "subwave_station_state": "always-on read",
        "subwave_schedule": "always-on read",
        "subwave_session": "always-on read",
        "subwave_current_lyrics": "always-on read",
        # Follow-ups to a tool the prompt does name, reached from that tool's
        # own result text rather than from the conduct.
        "subwave_request_status": "named by request_song's own result",
        "subwave_recent_tracks": "named by the finding table's neighbours",
        # The lowest-harm actions on the line: a like changes nobody's audio
        # and is exactly what a listener does from the app. The tool's own
        # description is the whole instruction, and a bullet would spend prompt
        # on every call to teach a heart.
        "subwave_like_track": "lowest-harm action, schema is enough",
        "subwave_unlike_track": "lowest-harm action, schema is enough",
        # A MODE, not a capability: its switch REPLACES the six finders, so
        # it is never in the same prompt as the table and cannot be checked in
        # the same pass. Pinned by test_the_finder_mode_names_the_one_tool.
        "subwave_find_music": "a mode; named only when its switch is on",
    }
    NAMED_ELSEWHERE = set(BULLET) | set(SCHEMA)

    def test_a_tool_claiming_a_bullet_actually_has_one(self):
        """The exemption that has to be earned rather than asserted.

        A BULLET entry says: this tool's prose rides its own switch. That is a
        measurement, not an opinion — flip the switch and text APPEARS.

        Note the question is what the switch ADDS, not whether the prompt gets
        shorter. Turning a capability off replaces its bullet with a line in
        the "Not on this line tonight" list, and for four of these the refusal
        is the LONGER of the two — so a size comparison would report a bullet
        missing that is plainly there.
        """
        import difflib

        from brain import conduct
        from call.tools.registry import BY_NAME, NEVER, READ, TOOLS

        allon = {t.gate: True for t in TOOLS if t.gate not in (READ, NEVER)}
        base = conduct.rules(allon).splitlines()
        empty = []
        for name in sorted(self.BULLET):
            gate = BY_NAME[name].gate
            off = dict(allon)
            off[gate] = False
            added = [
                line[1:].strip() for line in difflib.unified_diff(
                    conduct.rules(off).splitlines(), base, lineterm="", n=0)
                if line.startswith("+") and not line.startswith("+++")
            ]
            if not "".join(added).strip():
                empty.append(f"{name} (gate {gate})")
        self.assertEqual(
            empty, [],
            "these are exempted from the naming rule because they 'carry a "
            "bullet', and turning them off does not shrink the prompt — so "
            f"there is no bullet: {empty}. Either write one, or move the entry "
            "to SCHEMA and say why the tool's own description is enough.")

    def test_the_two_exemption_kinds_do_not_overlap(self):
        both = sorted(set(self.BULLET) & set(self.SCHEMA))
        self.assertEqual(both, [], f"claimed twice, two ways: {both}")

    def test_every_unlocked_tool_is_named_or_deliberately_not(self):
        from brain import conduct, conduct_chat
        from call.tools.registry import TOOLS, NEVER

        # Everything on at once: the question is whether the prompt CAN name
        # a tool, not whether this operator switched it on.
        # `single_lookup_tool` excluded: it REPLACES the six finders rather
        # than adding to them, so turning it on here would ask the prompt to
        # name seven tools of which six are not offered. Own test below.
        cfg = {t.gate: "open" for t in TOOLS
               if t.gate not in ("read", NEVER, "single_lookup_tool")}
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

    def test_the_finder_mode_names_the_one_tool(self):
        # With the dispatcher on the prompt teaches ONE tool, not both:
        # teaching the table too would leave neither arm of the A/B an
        # arrangement anyone would ship.
        from brain import conduct
        from call.tools.finding import ROUTES

        on = {"allow_library_search": "open", "allow_sound_search": "open",
              "single_lookup_tool": True}
        text = conduct.rules(on)
        self.assertIn("subwave_find_music", text)
        for name in ROUTES.values():
            with self.subTest(name=name):
                self.assertNotIn(name, text)
        # And the judgement rules that are NOT about routing survive the swap:
        # a miss is not proof, whichever way the looking is arranged.
        self.assertIn("NOT proof", text)

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


class TestTheStationsLanguageIsNotTheDJsLanguage(unittest.TestCase):
    """Brock answered a caller in Mandarin on 2026-08-18.

    Brock's own persona is English and carries no CJK at all. The Mandarin came
    from the BRIEFING: the station was rotating Mandarin-titled tracks, one of
    the schedule's shows is named in Chinese, and the previous presenter (Rosie,
    who does work in Mandarin) had her on-air line quoted verbatim into Brock's
    context. The caller spoke English throughout.

    The rule already said "answer in the language the caller is using" and was
    not wrong — it just could not apply at PICKUP, which is where this went
    wrong. The greeting is generated before the caller has said a word, and the
    greeting instruction actively invites the DJ to let the broadcast colour it,
    so the only language in front of the model was the station's. Once the
    opening line was Mandarin the whole call followed it.
    """

    def test_the_rule_says_station_material_is_not_a_cue(self):
        from brain.conduct import LANGUAGE_AND_MIMICRY

        text = LANGUAGE_AND_MIMICRY.lower()
        self.assertIn("not a language cue", text)
        for needle in ("pickup", "who you are"):
            self.assertIn(needle, text,
                          f"the pickup case is the one that failed: {needle!r} "
                          "missing from the language rule. The rule points at "
                          "the persona block because that is where the "
                          "station's own per-DJ language lands — mirrored, "
                          "not inferred (see station.persona_from).")

    def test_the_rule_still_rides_its_own_section(self):
        # It is a named, priced, ablatable block and measured 11/11 against
        # 5/11 without. Growing it must not move it out of the list the budget
        # report and the ablation switch both read.
        from brain import conduct

        names = [name for name, _ in conduct.blocks({})]
        self.assertIn("LANGUAGE_AND_MIMICRY", names)

    def test_a_foreign_name_is_spoken_in_its_latin_form(self):
        # The mirror of upstream #1455's spoken-proper-noun directive, for
        # the phone voice. The live library's title sort ends in two hundred
        # Korean rows (SHINee, NELL, SUPER JUNIOR) — the DJ WILL meet a
        # Hangul title on a search row, and saying that name must neither
        # become a language switch nor a spelling bee through characters the
        # voice cannot carry. The station's booth solves this at two
        # boundaries (prompt directive + TTS scrub); the phone keeps the
        # prompt half only, because a scrub here would delete a
        # Korean-speaking caller's own language from the DJ's mouth.
        from brain.conduct import LANGUAGE_AND_MIMICRY

        text = LANGUAGE_AND_MIMICRY.lower()
        self.assertIn("latin name", text)
        self.assertIn("romanisation", text)
        self.assertIn("saying a name is not switching language", text)


class TestBulkQueueingIsActedOnNotSoldOn(unittest.TestCase):
    """The operator's ask, near verbatim (2026-08-18): no need to offer the
    full album by default, but if it sounds like that's what they want, do
    it. So the rule exists only when the switch does, and the restraint —
    a question about the shelf is not an order for thirty tracks — is in
    the words."""

    ON = {"allow_requests": "open", "allow_library_search": "open",
          "allow_album_queue": "open"}

    def _both(self, cfg):
        from brain import conduct, conduct_chat

        return conduct.rules(cfg), conduct_chat.rules(cfg)

    def test_the_rule_rides_the_switch(self):
        for rules in self._both(self.ON):
            self.assertIn("subwave_queue_album", rules)
            self.assertIn("subwave_queue_mix", rules)
        off = {k: v for k, v in self.ON.items() if k != "allow_album_queue"}
        for rules in self._both(off):
            self.assertNotIn("subwave_queue_album", rules)
            self.assertNotIn("subwave_queue_mix", rules)

    def test_an_album_is_never_offered_unprompted(self):
        for rules in self._both(self.ON):
            self.assertIn("offer a whole album unprompted", rules)
            self.assertIn("question about", rules)
            self.assertIn("a caller asking for a song gets a song", rules)

    def test_the_mix_names_only_finders_that_exist(self):
        # A taught tool that was never built gets MIMED, not refused — the
        # exact failure the per-switch action bullets exist to stop. The mix
        # bullet lists its finders, so each mention rides that finder's own
        # switch.
        for rules in self._both(self.ON):
            self.assertIn("subwave_browse_library", rules)
        with_sound = dict(self.ON, allow_sound_search="open")
        for rules in self._both(with_sound):
            self.assertIn("subwave_search_by_sound", rules)


class TestASpokenMixIsNotAMix(unittest.TestCase):
    """The Wade chat, 2026-08-19: the DJ ran the right searches, announced a
    Soundgarden/Alice in Chains/Pixies run, and queue_mix never ran — the
    caller got one track and, later, an apology. The rule and its worked
    example ride the bulk switch with the rest of the mix teaching."""

    ON = {"allow_requests": "open", "allow_library_search": "open",
          "allow_album_queue": "open"}

    def test_the_receipt_rule_rides_the_switch(self):
        from brain import conduct, conduct_chat

        for rules in (conduct.rules(self.ON), conduct_chat.rules(self.ON)):
            self.assertIn("A mix you have SPOKEN is not a mix", rules)
            self.assertIn("queue_mix never did", rules)
            self.assertIn("receipt", rules)
        off = {k: v for k, v in self.ON.items() if k != "allow_album_queue"}
        for rules in (conduct.rules(off), conduct_chat.rules(off)):
            self.assertNotIn("A mix you have SPOKEN is not a mix", rules)
