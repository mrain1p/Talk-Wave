"""What the caller waited for, and what they talked over.

A new subject at 0.97.65 rather than a corner of an existing module: the two
numbers here belong to neither "what is written down about a call"
(test_call_record) nor "a call while it runs" (test_call_flow), and both of
those are already at their ceiling.

These are the settings the panel has always been able to turn and no
instrument here has ever measured — the endpointing delays and the
interruption dial.
"""

from __future__ import annotations

import time
import unittest

from call.heard import MAX_REPLY_GAP_SECS, MIN_CUT_SECS, HeardMeter, _percentile


class TestTheWaitIsMeasuredFromWhenTheCallerStopped(unittest.TestCase):
    """Not from when the model started, which is what every existing
    instrument measures.

    `ThinkMeter` times the model's first token and `firstWordAt` stamps the
    first audio of the whole call, so both begin AFTER endpointing has had its
    effect. The one dial whose entire job is trading responsiveness against
    talking over people has therefore never appeared in a number taken here.
    """

    def test_the_gap_spans_endpointing_not_just_the_model(self):
        m = HeardMeter()
        m.caller_stopped()
        time.sleep(0.05)
        m.dj_speaking()
        self.assertEqual(len(m.replies), 1)
        # Loose on purpose: Windows' sleep granularity returns a hair under
        # what you asked for (0.047 for a 0.05 sleep), and the claim here is
        # that real elapsed time is spanned, not that the clock is a stopwatch.
        self.assertGreater(m.replies[0], 0.02)

    def test_the_greeting_is_not_a_reply_to_anything(self):
        # Nobody had spoken, so there is no wait to attribute. A greeting
        # counted as a reply would report the ring as latency.
        m = HeardMeter()
        m.dj_speaking()
        self.assertEqual(m.replies, [])

    def test_a_caller_who_wandered_off_is_not_a_slow_reply(self):
        m = HeardMeter()
        m.caller_stopped()
        # monotonic, like the meter: an epoch timestamp here would make the
        # gap hugely negative and the test would pass on the lower bound
        # instead of on the ceiling it is meant to defend.
        m._waiting_since = time.monotonic() - (MAX_REPLY_GAP_SECS + 5)
        m.dj_speaking()
        self.assertEqual(m.replies, [],
                         "a minute of silence in the same average as a 900ms "
                         "reply makes the whole measurement useless")

    def test_a_turn_held_for_the_broadcast_is_dropped_not_counted(self):
        # The duck holds the call DJ on purpose, sometimes for tens of
        # seconds. Counted, the ducking would read as latency and the fix
        # would be to break the ducking.
        m = HeardMeter()
        m.caller_stopped()
        m.held_for_air()
        m.dj_speaking()
        self.assertEqual(m.replies, [])


class TestTheBargeInIsMeasuredAndAlwaysReported(unittest.TestCase):
    def test_time_to_silence_runs_from_the_callers_first_word_over_the_top(self):
        m = HeardMeter()
        m.dj_speaking()
        m.caller_started()
        time.sleep(0.05)
        m.playback_finished(played=0.4, interrupted=True)
        self.assertEqual(len(m.barge_ins), 1)
        self.assertGreater(m.barge_ins[0], 0.02)   # see the note above on sleep granularity

    def test_an_uninterrupted_line_is_not_a_barge_in(self):
        m = HeardMeter()
        m.dj_speaking()
        m.playback_finished(played=2.0, interrupted=False)
        self.assertEqual(m.barge_ins, [])
        self.assertEqual(m.cut_off, [])

    def test_the_pair_is_reported_together_even_when_nobody_interrupted(self):
        """The reason this module exists in one piece.

        A line that answers faster by cutting people off is not a better line,
        so a latency number published without its interruption number beside
        it is the half-truth worth guarding against. Zero interruptions is a
        RESULT and gets said out loud.
        """
        m = HeardMeter()
        m.caller_stopped()
        m.dj_speaking()
        out = m.summary()
        self.assertIn("replyGap", out)
        self.assertIn("bargeIn", out, "the pair is never reported by halves")
        self.assertEqual(out["bargeIn"]["n"], 0)


class TestWhatTheCallerActuallyHeardIsKept(unittest.TestCase):
    def test_a_cut_line_records_both_versions(self):
        # The transcript in the record is what the DJ SAID. Where a line was
        # cut those two disagree, and every scenario set and postmortem grades
        # the said one — so the grader reads a call that did not happen.
        m = HeardMeter()
        m.dj_speaking()
        m.caller_started()
        m.playback_finished(played=0.6, interrupted=True,
                            heard_text="right, let me have a dig",
                            said_text="right, let me have a dig through the racks")
        self.assertEqual(len(m.cut_off), 1)
        entry = m.cut_off[0]
        self.assertEqual(entry["playedSecs"], 0.6)
        self.assertEqual(entry["heard"], "right, let me have a dig")
        self.assertIn("through the racks", entry["said"])

    def test_a_call_where_nothing_happened_writes_nothing(self):
        # Zeroes in a record read as a measurement that was taken. A call that
        # never got going did not take one.
        self.assertEqual(HeardMeter().summary(), {})


class TestThePercentileDoesNotInventNumbers(unittest.TestCase):
    def test_nearest_rank_returns_a_wait_somebody_actually_had(self):
        # On the handful of turns a call contains, interpolating between two
        # samples produces a number no caller ever waited.
        self.assertEqual(_percentile([1.0, 2.0, 9.0], 50), 2.0)
        self.assertEqual(_percentile([1.0, 2.0, 9.0], 90), 9.0)
        self.assertEqual(_percentile([], 50), 0.0)



class TestAClearedSynthesisIsNotSomebodyTalkingOver(unittest.TestCase):
    """The first deployed call wrote two cut-offs and zero barge-ins.

    Both were interrupted playbacks of 0.05s and 0.02s — a synthesis cleared
    before it started, not a sentence anybody talked over. Read back, that
    record said the caller cut the DJ off twice, which is simply not what
    happened, and the whole point of this block is to be the honest account of
    what reached their ears.

    Same call call/tee.py makes with MIN_CLIP_SECS, for the same reason.
    """

    def test_a_blip_is_not_recorded_as_something_they_heard(self):
        m = HeardMeter()
        m.dj_speaking()
        m.playback_finished(played=0.02, interrupted=True)
        self.assertEqual(m.cut_off, [])

    def test_a_real_cut_line_is_still_kept(self):
        m = HeardMeter()
        m.dj_speaking()
        m.playback_finished(played=1.4, interrupted=True, heard_text="right, let me")
        self.assertEqual(len(m.cut_off), 1)
        self.assertEqual(m.cut_off[0]["playedSecs"], 1.4)

    def test_a_blip_still_counts_as_a_barge_in_when_the_caller_caused_it(self):
        # The two halves answer different questions: "did they interrupt" is
        # about the caller, "what did they hear" is about the audio. A blip is
        # a real interruption and an empty thing to have heard.
        m = HeardMeter()
        m.dj_speaking()
        m.caller_started()
        m.playback_finished(played=0.02, interrupted=True)
        self.assertEqual(len(m.barge_ins), 1)
        self.assertEqual(m.cut_off, [])

if __name__ == "__main__":
    unittest.main()
