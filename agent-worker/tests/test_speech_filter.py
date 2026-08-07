"""The speech filter: what is allowed to reach the caller's ears.

The last thing between the model and the speaker, so everything here is about
something a real caller heard, or nearly did. Split out of test_brain.py, which
had grown to hold two unrelated subjects — prompt assembly and this.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import unittest

import speech_filter


class TestSpeechFilter(unittest.TestCase):
    def test_strips_asterisk_stage_directions(self):
        out = speech_filter.strip_stage_directions(
            "*shuffles through records* Here's one for you."
        )
        self.assertEqual(out, "Here's one for you.")

    def test_strips_bracketed_and_paren_actions(self):
        out = speech_filter.strip_stage_directions("[pause] Right. (laughs) Where were we?")
        self.assertNotIn("[pause]", out)
        self.assertNotIn("(laughs)", out)
        self.assertIn("Where were we?", out)

    def test_strips_stage_directions_that_do_not_start_on_the_verb(self):
        # Went out on a real call: "(Phone rings) Yeah, Cliff here." The old
        # rule only matched a parenthetical whose FIRST word was a verb.
        out = speech_filter.strip_stage_directions(
            "(Phone rings) Yeah, Cliff here. We're letting the last track settle."
        )
        self.assertNotIn("Phone rings", out)
        self.assertTrue(out.startswith("Yeah, Cliff here."))
        for direction in ("(the receiver clicks)", "(static crackles)",
                          "(sound of vinyl scratches)"):
            self.assertNotIn(
                direction, speech_filter.strip_stage_directions(direction + " right then")
            )

    def test_keeps_ordinary_parenthetical_speech(self):
        text = "the set (which runs till two) is all vinyl"
        self.assertEqual(speech_filter.strip_stage_directions(text), text)

    def test_keeps_parentheticals_that_merely_end_in_s(self):
        # The permissive "any word ending in -s" version of the verb-last rule
        # ate ordinary speech like this.
        for text in ("back in (about three minutes)",
                     "that one's from (one of my favourite albums)"):
            self.assertEqual(speech_filter.strip_stage_directions(text), text)

    def test_strips_the_djs_own_name_used_as_a_script_label(self):
        # Went out on a real call: the model slipped into screenplay format and
        # the voice read the DJ's own name aloud at the top of every turn.
        speech_filter.set_speaker("Francesca Hale")
        try:
            self.assertEqual(
                speech_filter.strip_speaker_labels(
                    "Francesca: Hey there, thanks for holding on."),
                "Hey there, thanks for holding on.",
            )
            for variant in ("**Francesca:** right then", "Francesca Hale: right then",
                            "DJ: right then", "HOST: right then"):
                self.assertEqual(
                    speech_filter.strip_speaker_labels(variant), "right then", variant)
        finally:
            speech_filter.set_speaker("")

    def test_label_strip_leaves_a_following_stage_direction_intact(self):
        # A greedy bold matcher ate the opening asterisk of what came next,
        # so the direction no longer looked like one and went out on air.
        speech_filter.set_speaker("Francesca")
        try:
            self.assertEqual(
                speech_filter.clean_for_speech(
                    "Francesca: *adjusts headphones* Loud and clear now.",
                    profanity_mode="off"),
                "Loud and clear now.",
            )
            self.assertEqual(
                speech_filter.clean_for_speech(
                    "**Francesca:** (Phone rings) Yeah, Cliff here.",
                    profanity_mode="off"),
                "Yeah, Cliff here.",
            )
        finally:
            speech_filter.set_speaker("")

    def test_the_dj_can_still_say_its_own_name_out_loud(self):
        # Only the SCRIPT LABEL form is a problem. Introducing yourself is
        # what a DJ does — the fix must not cost that.
        speech_filter.set_speaker("Wade")
        try:
            for kept in ("This is Wade, you're through to the booth.",
                         "Wade here, what can I do for you?",
                         "You're on with Wade on the late shift.",
                         "Wade's the name, records are the game."):
                self.assertEqual(speech_filter.strip_speaker_labels(kept), kept)
            # …but the label form still goes.
            self.assertEqual(
                speech_filter.strip_speaker_labels("Wade: You're through to the booth."),
                "You're through to the booth.")
        finally:
            speech_filter.set_speaker("")

    def test_never_eats_ordinary_speech_that_contains_a_colon(self):
        speech_filter.set_speaker("Francesca")
        try:
            for text in ("Listen: this one's a classic.",
                         "Here's the deal: we're out of time.",
                         "One thing: it's not on the album."):
                self.assertEqual(speech_filter.strip_speaker_labels(text), text)
            # Another person's name is dialogue, not a label for OUR voice.
            self.assertEqual(
                speech_filter.strip_speaker_labels("Bowie: an underrated run"),
                "Bowie: an underrated run",
            )
        finally:
            speech_filter.set_speaker("")

    def test_label_stripping_is_inert_before_a_persona_is_known(self):
        speech_filter.set_speaker("")
        self.assertEqual(
            speech_filter.strip_speaker_labels("Francesca: hello"), "Francesca: hello")

    def test_profanity_mask_and_drop_and_off(self):
        words = ["fuck", "shit"]
        self.assertEqual(
            speech_filter.filter_profanity("well fuck that", words, "mask"),
            "well f— that",
        )
        self.assertEqual(
            speech_filter.filter_profanity("well fuck that", words, "drop"),
            "well that",
        )
        self.assertEqual(
            speech_filter.filter_profanity("well fuck that", words, "off"),
            "well fuck that",
        )

    def test_profanity_respects_word_boundaries(self):
        # "Scunthorpe problem": substrings must survive.
        text = "let me assess the Scunthorpe situation"
        self.assertEqual(
            speech_filter.filter_profanity(text, ["cunt", "ass"], "drop"), text
        )

    def test_clean_for_speech_combined(self):
        out = speech_filter.clean_for_speech(
            "*sighs* That's some shit, huh?",
            strip_directions=True, profanity_mode="mask", profanity_words=["shit"],
        )
        self.assertEqual(out, "That's some s—, huh?")


class TestATypedToolCallNeverReachesTheSpeaker(unittest.TestCase):
    """A model that writes a function call instead of making one.

    Observed on a real call on gemini-2.5-flash-lite, as the whole of the DJ's
    last turn: the caller had asked for something relaxing, the record shows no
    tool ran, and the text below went to the TTS. What the caller heard was
    Python.
    """

    LEAK = ("tool_code\n"
            "print(default_api.subwave_request_song(request='Something relaxing'))")

    def test_the_observed_leak_is_not_spoken(self):
        self.assertEqual(speech_filter.clean_for_speech(self.LEAK), "")

    def test_the_observed_leak_is_recognised(self):
        self.assertTrue(speech_filter.looks_like_tool_code(self.LEAK))

    def test_a_fenced_block_goes_too(self):
        out = speech_filter.clean_for_speech(
            "Sure thing.\n```python\nprint(default_api.subwave_skip_track())\n```")
        self.assertEqual(out, "Sure thing.")

    def test_a_bare_call_without_the_marker_goes(self):
        out = speech_filter.clean_for_speech(
            "One moment.\ndefault_api.subwave_search_library(query='rain')")
        self.assertEqual(out, "One moment.")

    def test_it_is_stripped_even_with_stage_directions_left_on(self):
        # The stage-direction toggle is a matter of taste — a theatrical
        # persona may legitimately want "(laughs)" spoken. There is no setting
        # under which reading out a function call is wanted, so this must not
        # ride on that switch.
        out = speech_filter.clean_for_speech(
            self.LEAK, strip_directions=False, profanity_mode="off")
        self.assertEqual(out, "")

    def test_ordinary_speech_with_brackets_survives(self):
        # The rule is anchored on the providers' namespace precisely so that a
        # DJ talking about anything parenthetical is untouched.
        for line in (
            "I can print that out for you if you like.",
            "Give me a second (about three minutes) and I'll have it.",
            "The tool I use for that is the request line, believe it or not.",
        ):
            with self.subTest(line=line):
                self.assertEqual(speech_filter.clean_for_speech(line), line)
                self.assertFalse(speech_filter.looks_like_tool_code(line))


class TestPunctuationIsSpokenNotSpelled(unittest.TestCase):
    """Operator-reported: some voices read "&" as the word ampersand, and an
    em dash read literally lands as a hard stop where the writer meant a
    breath. Both become what a person would say."""

    def test_ampersand_becomes_and(self):
        from speech_filter import clean_for_speech

        self.assertEqual("Fish and Chips tonight",
                         clean_for_speech("Fish & Chips tonight"))

    def test_dashes_become_a_breath(self):
        from speech_filter import clean_for_speech

        self.assertEqual("one thing, and another",
                         clean_for_speech("one thing — and another"))
        self.assertEqual("three to four, ish",
                         clean_for_speech("three to four–ish"))
