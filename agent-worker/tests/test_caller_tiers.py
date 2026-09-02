"""Which caller gets which permission.

A permission used to be one answer for everybody who got through the door, so
an operator who wanted a public line AND wanted to put something on air from
their own phone had to leave that switch on for strangers too. It is a tier
now: off, or the least-trusted caller who gets it.

The whole design turns on one hazard, and most of what is here defends it:
**the stored value is a string and "off" is truthy.** Every consumer in the
worker asks `cfg.get("allow_x")` and always has. Settings that reach a tool
builder unresolved would switch on every permission the operator had turned
off — including the three that change what the whole audience hears.
"""

from __future__ import annotations

import unittest

import settings as settings_store

from tests.support import _TempStores


class TestATierIncludesTheOnesBelowIt(unittest.TestCase):
    def test_the_cascade(self):
        cases = {
            "off": {"open": False, "guest": False, "admin": False},
            "open": {"open": True, "guest": True, "admin": True},
            "guest": {"open": False, "guest": True, "admin": True},
            "admin": {"open": False, "guest": False, "admin": True},
        }
        for setting, expected in cases.items():
            for tier, allowed in expected.items():
                with self.subTest(setting=setting, tier=tier):
                    self.assertEqual(
                        settings_store.permission_reaches(setting, tier), allowed)

    def test_permissions_for_collapses_every_tiered_field_to_a_bool(self):
        cfg = settings_store.permissions_for(
            {f: "guest" for f in settings_store.TIERED_PERMISSIONS}, "open")
        for field in settings_store.TIERED_PERMISSIONS:
            with self.subTest(field=field):
                self.assertIs(cfg[field], False)

    def test_it_leaves_everything_else_alone(self):
        # It is a copy with eight fields rewritten, not a filter. Dropping the
        # rest would take the model, the voice and every limit with it.
        cfg = settings_store.permissions_for(
            {"llm_model": "gpt-4.1-mini", "confirm_requests": True,
             "allow_requests": "admin"}, "open")
        self.assertEqual(cfg["llm_model"], "gpt-4.1-mini")
        self.assertIs(cfg["confirm_requests"], True)
        self.assertIs(cfg["allow_requests"], False)

    def test_the_original_is_not_mutated(self):
        # The worker resolves once per call from a dict it loaded fresh, but
        # the panel and the diagnostics resolve from settings they go on to
        # read. Rewriting in place would leave a caller's answer standing in
        # for the operator's setting.
        raw = {"allow_takeover": "admin"}
        settings_store.permissions_for(raw, "open")
        self.assertEqual(raw["allow_takeover"], "admin")


class TestAnUnknownTierFailsClosed(unittest.TestCase):
    """Every unreadable answer has to mean "no".

    The other direction cannot be walked back: by the time anyone notices, the
    caller has already been on air.
    """

    def test_junk_in_the_settings_file_grants_nothing(self):
        for value in ("", "  ", "everybody", "yes please", None, 3, [], {}):
            with self.subTest(value=value):
                self.assertEqual(settings_store.normalise_tier(value), "off")

    def test_a_missing_permission_grants_nothing(self):
        # It used to read as "open" — the one direction a permission must
        # never default (found while testing the player's endpoints against
        # the live deployment, 2026-09-02). Nothing exploited it: every
        # caller passes cfg.get("allow_…") and load() always supplies the
        # field's default. But those endpoints read RAW settings, and the
        # ladder's own docstring already promised this.
        for value in (None, ""):
            for tier in ("open", "guest", "admin"):
                with self.subTest(value=value, tier=tier):
                    self.assertFalse(settings_store.tier_reaches(value, tier))
        # An explicit "open" still opens — this closes the hole, not the door.
        self.assertTrue(settings_store.tier_reaches("open", "open"))
        self.assertTrue(settings_store.tier_reaches("admin", "admin"))
        self.assertFalse(settings_store.tier_reaches("admin", "guest"))

    def test_a_caller_at_an_unknown_tier_gets_nothing(self):
        for tier in ("", "root", "operator", "OPEN"):
            with self.subTest(tier=tier):
                self.assertFalse(settings_store.permission_reaches("open", tier))

    def test_a_room_name_it_does_not_recognise_is_the_lowest_tier(self):
        for room in ("", "probe-abc123456789", "callin-abc123456789",
                     "callin-x-abc123456789", "something-else", None):
            with self.subTest(room=room):
                self.assertEqual(settings_store.tier_from_room(room), "open")

    def test_the_room_name_is_where_the_tier_travels(self):
        self.assertEqual(settings_store.tier_from_room("callin-o-0123456789ab"), "open")
        self.assertEqual(settings_store.tier_from_room("callin-g-0123456789ab"), "guest")
        self.assertEqual(settings_store.tier_from_room("callin-a-0123456789ab"), "admin")

    def test_the_room_name_still_ends_in_the_twelve_characters_a_rating_needs(self):
        # call/record.rate() finds a transcript by matching the last twelve
        # characters of the room, and the widget posts a thumbs-up against it.
        # Putting the tier in the middle rather than at the end is what keeps
        # that working.
        room = "callin-g-0123456789ab"
        self.assertEqual(room[-12:], "0123456789ab")


class TestUpgradingKeepsTheStationExactlyAsItWas(_TempStores):
    """`true` meant "anyone who got through the door", which is `open`.

    An operator who never opens the panel again has to keep precisely the
    permissions they had — an upgrade that quietly widens OR narrows what a
    caller can do is found out from a caller.
    """

    def test_a_true_becomes_open(self):
        self.assertEqual(
            settings_store._migrate({"allow_announcements": True})["allow_announcements"],
            "open")

    def test_a_false_becomes_off(self):
        self.assertEqual(
            settings_store._migrate({"allow_takeover": False})["allow_takeover"],
            "off")

    def test_an_already_migrated_file_is_left_alone(self):
        self.assertEqual(
            settings_store._migrate({"allow_skills": "admin"})["allow_skills"],
            "admin")

    def test_an_old_file_reads_back_the_same_through_load(self):
        # The end-to-end version: write the file a previous version would have
        # written, and check the resolved permissions for a caller who typed
        # nothing are what that version gave them.
        import json

        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"allow_requests": True, "allow_announcements": True,
                       "allow_takeover": False}, f)
        cfg = settings_store.permissions_for(settings_store.load(), "open")
        self.assertIs(cfg["allow_requests"], True)
        self.assertIs(cfg["allow_announcements"], True)
        self.assertIs(cfg["allow_takeover"], False)

    def test_the_defaults_are_what_the_booleans_resolved_to(self):
        cfg = settings_store.permissions_for(settings_store.load(), "open")
        self.assertIs(cfg["allow_requests"], True)
        self.assertIs(cfg["allow_library_search"], True)
        for field in ("allow_exact_queue", "allow_announcements", "allow_skills",
                      "allow_skip_track", "allow_dj_segment", "allow_takeover"):
            with self.subTest(field=field):
                self.assertIs(cfg[field], False)


class _Req:
    """Just enough of a request for caller_tier: the headers it reads."""

    def __init__(self, **headers):
        self.headers = headers
        self.remote = "10.0.0.9"


class TestTheDoorDecidesTheTier(_TempStores):
    """The tier is worked out where the password was seen, and nowhere else."""

    def setUp(self):
        super().setUp()
        import tempfile
        from pathlib import Path

        import admin_auth

        from api import auth as api_auth

        self.api_auth = api_auth
        self.admin_auth = admin_auth
        self._authtmp = tempfile.TemporaryDirectory()
        self._old_auth = admin_auth.AUTH_PATH
        admin_auth.AUTH_PATH = Path(self._authtmp.name) / "admin-auth.json"
        admin_auth.set_password("hunter2hunter2")
        admin_auth.set_guest_password("letmein")
        # front_access defaults to admin-only since 0.10.80, which closes
        # the guest lane this class exists to exercise — give the door one.
        settings_store.save({"front_access": "guest"})

    def tearDown(self):
        self.admin_auth.AUTH_PATH = self._old_auth
        self._authtmp.cleanup()
        super().tearDown()

    def _tier(self, **headers) -> str:
        return self.api_auth.caller_tier(_Req(**headers))

    def test_nothing_typed_is_an_open_caller(self):
        self.assertEqual(self._tier(), "open")

    def test_the_guest_code_is_a_guest_caller(self):
        self.assertEqual(self._tier(**{"X-Call-Key": "letmein"}), "guest")

    def test_the_admin_password_is_an_admin_caller(self):
        # It opens the phone as well as the panel, so an operator carries one
        # password — and ringing their own booth must not come through as a
        # stranger.
        self.assertEqual(self._tier(**{"X-Call-Key": "hunter2hunter2"}), "admin")

    def test_the_panels_own_header_counts_too(self):
        # The pipeline check and the embed preview send the admin key under
        # the name the panel stores it as.
        self.assertEqual(self._tier(**{"X-Admin-Key": "hunter2hunter2"}), "admin")

    def test_a_wrong_password_is_not_a_higher_tier(self):
        self.assertEqual(self._tier(**{"X-Call-Key": "not-the-password"}), "open")

    def test_an_open_line_runs_all_three_tiers_at_once(self):
        # The door and the tier are two questions (operator, 2026-08-15). From
        # 0.10.66 to 0.97.0 an open line refused to elevate a code-holder, so
        # only two of the three tiers could ever be live and the permission
        # matrix greyed a column whichever way the door was set: "it shouldn't
        # be that if i have a guest role i can't have permissions for anyone or
        # vice versa". A stranger, a code-holder and the operator can all be on
        # an open line, and they are three different callers.
        settings_store.save({"front_access": "open"})
        self.assertEqual(self._tier(), "open")
        self.assertEqual(self._tier(**{"X-Call-Key": "letmein"}), "guest")
        self.assertEqual(self._tier(**{"X-Call-Key": "hunter2hunter2"}), "admin")

    def test_the_two_switches_are_independent(self):
        """ANYONE and GUEST CODE are two decisions, not one choice.

        The operator, 2026-08-16: "guest can be on and anyone can be off or
        vice versa". All four combinations are real, and three of them share a
        door value — what tells those apart is `guest_tier`, which exists so
        that switching the guest pathway off does not mean deleting a code the
        operator wants to keep.
        """
        code = {"X-Call-Key": "letmein"}

        # anyone ON, guest ON — a stranger rings through, a code makes a guest.
        settings_store.save({"front_access": "open", "guest_tier": True})
        self.assertEqual(self._tier(), "open")
        self.assertEqual(self._tier(**code), "guest")

        # anyone ON, guest OFF — the line is open and the stored code is inert.
        settings_store.save({"front_access": "open", "guest_tier": False})
        self.assertEqual(self._tier(), "open")
        self.assertEqual(self._tier(**code), "open",
                         "the code elevated with the guest tier switched off")

        # anyone OFF, guest ON — the code is the only way in, and it is a tier.
        settings_store.save({"front_access": "guest", "guest_tier": True})
        self.assertEqual(self._tier(**code), "guest")

        # anyone OFF, guest OFF — nobody under admin at all.
        settings_store.save({"front_access": "admin", "guest_tier": False})
        self.assertEqual(self._tier(**code), "open")
        self.assertEqual(self._tier(**{"X-Call-Key": "hunter2hunter2"}), "admin")

    def test_a_code_gated_door_is_itself_the_guest_tier(self):
        # Everyone inside typed the code, so classing them as strangers would
        # be incoherent whatever the switch says — only a hand-edited file can
        # reach this pair, and it must not produce a caller who typed the code
        # and is treated as one who did not.
        settings_store.save({"front_access": "guest", "guest_tier": False})
        self.assertEqual(self._tier(**{"X-Call-Key": "letmein"}), "guest")

    def test_no_code_set_means_no_guest_tier(self):
        # How the guest pathway is switched off: there is no code. Without
        # this an operator with no code could still tick Guest on a permission
        # and grant it to a tier nobody can ever be.
        settings_store.save({"front_access": "open"})
        self.admin_auth.set_guest_password("")
        self.assertEqual(self._tier(**{"X-Call-Key": "letmein"}), "open")

    def test_a_code_gated_line_still_elevates(self):
        settings_store.save({"front_access": "guest"})
        self.assertEqual(self._tier(**{"X-Call-Key": "letmein"}), "guest")

    def test_admin_only_offers_no_guest_door_either(self):
        # The code never elevates on a line the code cannot open — a guest
        # tier nobody can arrive at must not exist by side door.
        settings_store.save({"front_access": "admin"})
        self.assertEqual(self._tier(**{"X-Call-Key": "letmein"}), "open")


if __name__ == "__main__":
    unittest.main()


class TestTheLadderLivesInOnePlace(unittest.TestCase):
    """tier_reaches is the one spelling of open<guest<admin. It was a dict
    literal in the voicemail gate and again in the chat gate — the duplicate
    that drifts — and an unknown need fails CLOSED like normalise_tier."""

    def test_the_ladder_answers_both_ways(self):
        import settings as settings_store

        self.assertTrue(settings_store.tier_reaches("open", "open"))
        self.assertTrue(settings_store.tier_reaches("guest", "admin"))
        self.assertFalse(settings_store.tier_reaches("admin", "guest"))
        self.assertFalse(settings_store.tier_reaches("banana", "admin"))
        # Was assertTrue until 2026-09-02: a MISSING need read as "open".
        # The class docstring above always claimed the opposite, and the
        # player's endpoints read raw settings rather than a collapsed
        # session config — so the one direction a permission must never
        # default was one refactor from mattering. Changed deliberately:
        # an absent permission grants nothing.
        self.assertFalse(settings_store.tier_reaches(None, "open"))

    def test_no_gate_spells_the_ladder_out_again(self):
        from pathlib import Path
        here = Path(__file__).resolve().parent.parent
        for name in ("api/tokens.py", "api/chat.py"):
            src = (here / name).read_text(encoding="utf-8")
            self.assertNotIn('"open": 0', src,
                             f"{name} regrew its own copy of the ladder")
