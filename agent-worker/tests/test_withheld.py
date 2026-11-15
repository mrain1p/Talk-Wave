"""A caller asked for something the line withholds, and nobody invented a fault.

Every positive case here is the Mina call (2026-08-22): `allow_album_queue`
was `guest`, the caller was `open`, and the DJ — no tool, no sentence —
invented "it's been a bit stubborn with the queue". The mechanism under test
is the other half of 0.98.51's fix: the caller gets the denied card, and the
DJ gets the truth BEFORE it answers. See call/withheld.py.

The negative set matters more than the positive one. This watcher runs against
every caller turn on every call, and a "Not on this line tonight" card for
something the line actually offers is the widget itself lying — so the
patterns trade recall away for precision, and these tests pin that trade.
"""

import unittest

from call.withheld import ASKS_FOR, Withheld


class _Cards:
    """The denied-card sink, shaped like CallActions from where Withheld
    stands: `denied(kind, detail)` and nothing else."""

    def __init__(self):
        self.cards = []

    def denied(self, kind, detail=""):
        self.cards.append((kind, detail))


def _all_on():
    from brain.tool_rules import OFF_LIST
    return {gate: True for gate, _ in OFF_LIST} | {"allow_exact_queue": True}


class TestTheMinaCallGetsCardAndTruth(unittest.TestCase):
    def _mina_line(self):
        cfg = _all_on() | {"allow_album_queue": False}
        cards = _Cards()
        return Withheld(cfg, cards), cards

    def test_the_ask_is_carded_and_the_dj_is_told(self):
        w, cards = self._mina_line()
        note = w.hint_for("Create me a mix from the artist mina")
        self.assertIn("put a whole album or a mix in", note)
        self.assertIn("NOT ON THIS LINE TONIGHT", note)
        self.assertIn("do not claim you tried", note.lower())
        self.assertEqual(cards.cards,
                         [("not tonight", "put a whole album or a mix in")])

    def test_asking_again_notes_again_but_cards_once(self):
        # The note is free and every turn is a fresh chance to invent; the
        # card repeated is noise burying the receipts that matter.
        w, cards = self._mina_line()
        first = w.hint_for("Create me a mix from the artist mina")
        second = w.hint_for("come on, just make me a mix")
        self.assertTrue(first and second)
        self.assertEqual(len(cards.cards), 1)

    def test_the_other_mina_phrasings_are_heard(self):
        for said in ["Can you make me a mix of Sinatra songs?",
                     "spin me a mix all 90s rock",
                     "queue the whole album",
                     "play the full album for me"]:
            with self.subTest(said=said):
                w, cards = self._mina_line()
                self.assertTrue(w.hint_for(said), said)


class TestNothingOfferedIsNeverCarded(unittest.TestCase):
    def test_a_line_with_everything_on_stays_silent(self):
        w = Withheld(_all_on(), _Cards())
        for said in ["Create me a mix from the artist mina",
                     "Play diciembre first",
                     "skip this song",
                     "shoutout to my mum",
                     "never play this again"]:
            with self.subTest(said=said):
                self.assertEqual(w.hint_for(said), "")

    def test_play_x_is_not_withheld_while_either_path_serves_it(self):
        # "play X" is served by the request tool OR the exact-queue path.
        # Requests off with exact-queue on still gets the caller their song,
        # so carding "no requests tonight" would be false.
        cfg = _all_on() | {"allow_requests": False, "allow_exact_queue": True}
        w = Withheld(cfg, _Cards())
        self.assertEqual(w.hint_for("Play diciembre first"), "")

    def test_play_x_with_both_paths_off_is_carded(self):
        cfg = _all_on() | {"allow_requests": False, "allow_exact_queue": False}
        cards = _Cards()
        w = Withheld(cfg, cards)
        self.assertTrue(w.hint_for("Play diciembre first"))
        self.assertEqual(cards.cards[0][0], "not tonight")

    def test_chatter_is_never_carded_even_with_everything_off(self):
        # The precision bar: a line with every switch off must still not
        # card small talk. These are real archive lines.
        from brain.tool_rules import OFF_LIST
        cfg = {gate: False for gate, _ in OFF_LIST}
        w = Withheld(cfg, _Cards())
        for said in ["Can you hear me from over there?",
                     "What song is playing right now?",
                     "how long have you been on air?",
                     "i liked the song that was on before."]:
            with self.subTest(said=said):
                self.assertEqual(w.hint_for(said), "")


class TestRetuneFollowsTheSettings(unittest.TestCase):
    def test_a_switch_flipped_on_stops_the_carding(self):
        # The text line re-reads settings per message; the watcher must not
        # card a capability the operator just switched on.
        cfg = _all_on() | {"allow_album_queue": False}
        cards = _Cards()
        w = Withheld(cfg, cards)
        self.assertTrue(w.hint_for("make me a mix"))
        w.retune(_all_on())
        self.assertEqual(w.hint_for("make me another mix"), "")
        self.assertEqual(len(cards.cards), 1)


class TestThePatternsCannotDriftFromTheOffList(unittest.TestCase):
    def test_every_pattern_names_a_real_gate(self):
        # ASKS_FOR keyed by anything OFF_LIST doesn't name would watch for a
        # capability the prompt never mentions — the two lists must describe
        # the same world. (A gate with no pattern is fine: it is simply
        # never carded, and the prompt's sentence still covers it.)
        # ALSO_WITHHELD's two are the gates the prompt answers with a rule
        # of its own rather than an off-list sentence — see
        # TestTheStationWideAsksAreCardedToo for the guard on THAT list.
        from brain.tool_rules import OFF_LIST
        from call.withheld import ALSO_WITHHELD
        gates = {gate for gate, _ in OFF_LIST + ALSO_WITHHELD}
        self.assertEqual(sorted(set(ASKS_FOR) - gates), [])


def _only_off(gate):
    """Everything on except `gate` — so a card can only be for that one."""
    from call.withheld import ALSO_WITHHELD

    return (_all_on() | {g: True for g, _ in ALSO_WITHHELD}) | {gate: False}


class TestTheStationWideAsksAreCardedToo(unittest.TestCase):
    """Review, 2026-11-15. The three asks that reach every listener — steer
    the station, put another show on, pull a queued track — had no pattern
    here, so on a line with those switches off (the default for all three)
    the caller got neither the denied card nor the pre-answer note: the
    refusal existed only as the DJ's word for it, which is the Mina call
    again. Takeover and cancel were not even reachable — they are in
    OFF_LIST_EXEMPT, because the prompt covers them with its own rule, and
    the watcher only walked OFF_LIST."""

    HITS = {
        "allow_steer": (
            "more upbeat for the next hour",
            "keep it mellow for the rest of the night",
            "can you play jazz for the next couple of hours",
            "play only eighties from now on",
            "nothing but reggae all night",
            "steer the station towards soul",
        ),
        "allow_takeover": (
            "switch the show to Donovan's Pub",
            "change the DJ to Wade",
            "put someone else on",
            "can we get a different DJ",
            "put the Overlook show on",
        ),
        "allow_cancel_queue": (
            "cancel my request",
            "take that back out of the queue",
            "remove Dreams from the queue",
            "can you unqueue that",
            "cancel that song",
        ),
    }

    # What each must NOT hear. The first row of the steer set is the one the
    # review named: a single-song request is a request, never a steer.
    NEAR_MISSES = {
        "allow_steer": (
            "play something upbeat",
            "can I hear something more upbeat",
            "play Dreams by Fleetwood Mac",
            "one more upbeat song please",
            "I've been listening for a while",
            "keep it up for the rest of the night",
            "I'll be here for the next hour",
            "play that one for my mum, she's been up all night",
            "I need more coffee to stay up all night",
            "play some music for a while",
        ),
        "allow_takeover": (
            "put Dreams on",
            "put on some jazz",
            "I love this show",
            "what show is this",
            "who is the DJ tonight",
            "switch to something slower",
        ),
        "allow_cancel_queue": (
            "cancel the takeover",
            "take the heart back",
            "what's in the queue",
            "is my request in the queue",
            "queue that up",
            "never mind",
        ),
    }

    def test_each_ask_is_carded_and_the_dj_is_told_first(self):
        for gate, said in self.HITS.items():
            for line in said:
                with self.subTest(gate=gate, line=line):
                    cards = _Cards()
                    note = Withheld(_only_off(gate), cards).hint_for(line)
                    self.assertIn("does not offer that tonight", note)
                    self.assertEqual(len(cards.cards), 1)
                    self.assertEqual(cards.cards[0][0], "not tonight")

    def test_a_near_miss_is_never_carded(self):
        for gate, said in self.NEAR_MISSES.items():
            for line in said:
                with self.subTest(gate=gate, line=line):
                    cards = _Cards()
                    note = Withheld(_only_off(gate), cards).hint_for(line)
                    self.assertEqual(note, "")
                    self.assertEqual(cards.cards, [])

    def test_a_single_song_request_is_not_a_steer_even_with_everything_off(self):
        # With requests off too, "play something upbeat" is carded — as a
        # REQUEST. The steer's card must not be the one that goes up.
        from call.withheld import ALSO_WITHHELD

        cfg = ({gate: False for gate in _all_on()}
               | {g: False for g, _ in ALSO_WITHHELD})
        cards = _Cards()
        Withheld(cfg, cards).hint_for("play something upbeat")
        self.assertEqual(cards.cards, [("not tonight", "take song requests")])

    def test_a_line_that_offers_them_stays_silent(self):
        for gate, said in self.HITS.items():
            cards = _Cards()
            w = Withheld(_only_off(gate) | {gate: True}, cards)
            for line in said:
                self.assertEqual(w.hint_for(line), "", line)
            self.assertEqual(cards.cards, [])

    def test_the_card_says_what_was_asked_for(self):
        cards = _Cards()
        Withheld(_only_off("allow_takeover"), cards).hint_for(
            "change the DJ to Wade")
        self.assertEqual(cards.cards,
                         [("not tonight",
                           "put a different show or DJ on the air")])

    def test_the_two_extra_gates_are_ones_the_prompt_already_answers(self):
        # ALSO_WITHHELD may only name gates the prompt covers with a rule of
        # its own (OFF_LIST_EXEMPT) — never a gate OFF_LIST already carries,
        # which would card one ask under two phrases.
        from brain.tool_rules import OFF_LIST, OFF_LIST_EXEMPT
        from call.withheld import ALSO_WITHHELD

        extra = {gate for gate, _ in ALSO_WITHHELD}
        self.assertEqual(sorted(extra - set(OFF_LIST_EXEMPT)), [])
        self.assertEqual(sorted(extra & {g for g, _ in OFF_LIST}), [])
        for gate in extra:
            self.assertIn(gate, ASKS_FOR)
