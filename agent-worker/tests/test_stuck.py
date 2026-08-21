"""The caller asked again, and the DJ was the last to know.

Every case here is taken from `chat-16e0dffa11e4` (2026-08-20), the chat that
prompted the mechanism: seven asks, one wrong answer repeated, and nothing
anywhere that counted. The negative set matters as much as the positive one —
a hint on every turn is the prescriptive noise this is supposed to reduce, not
add to.
"""

import unittest

from call.stuck import Stuck, contradicts, same_ask


# The caller's turns, in order, exactly as the record has them.
THE_CHAT = [
    "what song is it",
    "it does have lyrics can you search the web",
    "what about the current song",
    "what song is this",
    "what song is this",
    "does Gala have lyrics?",
    "what song are you playing right now and does it have lyrics?",
]


class TestTheSameAskIsRecognised(unittest.TestCase):
    """Word overlap against the shorter turn, floored at two shared words.

    The floor is the whole design. Overlap alone treats any two turns sharing
    one word as the same question, and a caller who says "song" twice about
    two different things would be told to stop repeating themselves.
    """

    def test_the_same_question_reworded_is_the_same_question(self):
        self.assertTrue(same_ask("what song is it", "what song is this"))

    def test_a_caller_who_spells_it_out_is_still_repeating(self):
        # The seventh ask against the first. Jaccard scores this 0.29 — it
        # punished the caller for being more explicit after being ignored,
        # which is exactly backwards.
        self.assertTrue(same_ask(
            "what song is it",
            "what song are you playing right now and does it have lyrics?"))

    def test_two_different_requests_in_one_sentence_frame_are_not(self):
        for a, b in [("play some jazz", "play some rock"),
                     ("put on the beatles", "put on the stones"),
                     ("play anything by eminem", "play anything by drake"),
                     ("can you play some jazz", "can you play some blues")]:
            with self.subTest(a=a, b=b):
                self.assertFalse(same_ask(a, b))

    def test_one_shared_word_is_never_enough(self):
        self.assertFalse(same_ask("what song is this", "queue that song"))

    def test_an_empty_turn_matches_nothing(self):
        self.assertFalse(same_ask("", "what song is this"))
        self.assertFalse(same_ask("what song is this", "   "))


class TestBeingToldYouAreWrong(unittest.TestCase):
    """The caller is the one hearing the record. On anything they can perceive
    directly they are the better witness, and nothing in the conduct said so.
    """

    def test_the_line_that_started_all_of_this(self):
        self.assertTrue(contradicts("it does have lyrics can you search the web"))

    def test_the_ordinary_shapes_of_being_corrected(self):
        for said in ["no it doesn't", "that's wrong", "you're wrong",
                     "but it does", "i can hear the words",
                     "it has vocals", "that's not right"]:
            with self.subTest(said=said):
                self.assertTrue(contradicts(said))

    def test_a_hedge_on_its_own_is_not_a_contradiction(self):
        # "actually" is ordinary speech. Matching it would fire on half of
        # every call, which is how a mechanism becomes noise.
        for said in ["actually can you play something else",
                     "i'm not sure what it is",
                     "no thanks, that's everything"]:
            with self.subTest(said=said):
                self.assertFalse(contradicts(said))


class TestTheRealChatIsCaught(unittest.TestCase):
    """Replayed end to end. This is the regression: if a future change stops
    this conversation raising anything, the mechanism has stopped working on
    the only case anyone has actually reported."""

    def _run(self):
        s = Stuck()
        return s, [s.hint_for(t) for t in THE_CHAT]

    def test_the_first_ask_is_never_flagged(self):
        _, hints = self._run()
        self.assertEqual(hints[0], "")

    def test_being_contradicted_lands_on_the_turn_it_happens(self):
        _, hints = self._run()
        self.assertIn("better witness", hints[1])

    def test_the_repeats_are_caught_and_they_escalate(self):
        s, hints = self._run()
        self.assertIn("asked you this twice", hints[2])
        # By the fourth ask the instruction changes: restating has already
        # been tried and has already failed.
        self.assertIn("STOP repeating it", hints[3])
        self.assertGreaterEqual(s.repeats, 4)

    def test_the_escalated_note_forbids_the_exact_line_the_dj_used(self):
        # "I just double-checked the feed directly, and it's confirmed" —
        # said to a caller who was right and had been right for two minutes.
        _, hints = self._run()
        self.assertIn("do not tell them you have double-checked", hints[3])

    def test_the_count_is_spelled_like_a_word_not_a_bug(self):
        # "the 3th time" is the kind of malformed token a model happily
        # copies back into the DJ's own voice.
        s = Stuck()
        for _ in range(4):
            note = s.hint_for("what song is this")
        self.assertIn("4th time", note)
        self.assertNotIn("3th", note)

    def test_the_operator_can_see_it_afterwards(self):
        s, _ = self._run()
        self.assertGreater(s.repeats, 0)
        self.assertEqual(s.contradictions, 1)


class TestItCostsNothingWhenTheCallerWasHeard(unittest.TestCase):
    """The argument for a mechanism over a paragraph is that it is silent on
    every turn where nobody needed it. If that stops being true it is just
    prompt text with extra steps."""

    def test_a_conversation_that_moves_forward_raises_nothing(self):
        s = Stuck()
        turns = ["what's playing", "got any zeppelin",
                 "queue whole lotta love", "who's on after you",
                 "thanks, that's everything"]
        self.assertEqual([s.hint_for(t) for t in turns], [""] * len(turns))
        self.assertEqual(s.repeats, 0)
        self.assertEqual(s.contradictions, 0)

    def test_an_ask_from_long_ago_is_a_new_ask(self):
        # A long call legitimately circles back, and the window is what stops
        # "what's playing" at minute one making "what's playing" at minute
        # twenty a scolding.
        s = Stuck()
        s.hint_for("what song is this")
        for i in range(Stuck.WINDOW):
            s.hint_for(f"queue track number {i} for me please")
        self.assertEqual(s.hint_for("what song is this"), "")

    def test_a_blank_turn_is_not_a_repeat_of_a_blank_turn(self):
        s = Stuck()
        self.assertEqual(s.hint_for(""), "")
        self.assertEqual(s.hint_for("   "), "")
        self.assertEqual(s.repeats, 0)


if __name__ == "__main__":
    unittest.main()
