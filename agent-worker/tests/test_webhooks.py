"""Registering for the station's pushes, and proving one arrived rather than assuming it.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest

from tests.support import _TempStores


class _FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


class _FakeStation:
    """The station's admin API, as much of it as registration touches.

    Not a network fake for its own sake: the write is a whole-list replace, so
    a test that only checked the response would miss the thing that matters,
    which is what ended up in the list.
    """

    EVENTS = ["track.play", "dj.say", "dj.link", "request.received"]

    def __init__(self, rows=None, events=None, refuse=None, on_test=None):
        self.rows = [dict(r) for r in (rows or [])]
        self.events = self.EVENTS if events is None else list(events)
        self.refuse = refuse            # a _FakeResponse to answer writes with
        self.on_test = on_test          # called when a test fire is requested
        self.writes = []
        self.tests = []

    def __call__(self, user, password):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, path):
        return _FakeResponse(200, {"events": self.events, "webhooks": self.rows})

    async def post(self, path, json=None):
        if path.endswith("/test"):
            self.tests.append(path)
            if self.on_test is None:
                return _FakeResponse(200, {"ok": True})
            # A test fire may want to push at the receiver first, the way the
            # real station does before it answers.
            answer = self.on_test()
            return await answer if hasattr(answer, "__await__") else answer
        self.writes.append(json)
        if self.refuse is not None:
            return self.refuse
        self.rows = [dict(r) for r in (json or {}).get("webhooks") or []]
        return _FakeResponse(200, {"webhooks": self.rows})


class _FakeHookRequest:
    """Enough of an aiohttp request for the receiver: a body and headers."""

    def __init__(self, body, headers=None):
        self._body = body
        self.headers = dict(headers or {})

    async def json(self):
        return self._body


class _StationWebhooks(_TempStores):
    """Registration against a fake station, with the module state restored."""

    def setUp(self):
        super().setUp()
        from api import hooks as api_hooks
        import station_config

        self.hooks = api_hooks
        self.station_config = station_config
        self._old_state = dict(api_hooks._hook_state)
        self._old_client = api_hooks._admin_client
        self._old_creds = station_config.admin_credentials
        api_hooks._hook_state.clear()
        api_hooks._hook_state.update(
            registered=False, url="", id=api_hooks.HOOK_ID, station="",
            events=[], received=0, detail="not attempted")
        station_config.admin_credentials = lambda: ("op", "pw")
        os.environ["CALLIN_HOOK_URL"] = "http://192.0.2.7:8100/hooks/station"

    def tearDown(self):
        self.hooks._admin_client = self._old_client
        self.hooks._hook_state.clear()
        self.hooks._hook_state.update(self._old_state)
        self.station_config.admin_credentials = self._old_creds
        os.environ.pop("CALLIN_HOOK_URL", None)
        super().tearDown()

    def register(self, station):
        self.hooks._admin_client = station
        asyncio.run(self.hooks.register_station_webhook())
        return station


class TestTheRenameDoesNotOrphanTheOldRow(_StationWebhooks):
    """The 0.10.52 rename changed HOOK_ID from wave_talk to talk_wave and
    shipped saying "nothing behaves differently" — the sprint review caught
    the exception: a deployment whose URL had also moved would register a
    fresh talk_wave row and leave the wave_talk one behind, burning one of
    the station's sixteen webhook slots for good. The legacy id is ours to
    adopt, and a stray legacy duplicate is ours to delete."""

    def test_a_wave_talk_row_at_an_old_address_is_adopted_not_duplicated(self):
        station = _FakeStation(rows=[{
            "id": "wave_talk",
            "url": "http://10.0.0.5:8100/hooks/station",   # the OLD address
            "events": ["track.play"], "enabled": True,
        }])
        self.register(station)
        self.assertEqual(len(station.rows), 1, station.rows)
        self.assertEqual(station.rows[0]["id"], self.hooks.HOOK_ID)
        self.assertEqual(station.rows[0]["url"],
                         "http://192.0.2.7:8100/hooks/station")

    def test_a_stray_legacy_duplicate_is_deleted_even_when_settled(self):
        # Both rows exist: ours (settled, current) and the rename's orphan.
        # The settled early-return must not spare the stray.
        station = self.register(_FakeStation())
        station.rows.append({"id": "wave_talk",
                             "url": "http://10.0.0.5:8100/hooks/station",
                             "events": ["track.play"], "enabled": True})
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        self.assertEqual([r["id"] for r in station.rows],
                         [self.hooks.HOOK_ID], station.rows)

    def test_someone_elses_rows_are_never_touched(self):
        station = _FakeStation(rows=[{
            "id": "their_bot", "url": "http://elsewhere:9/hook",
            "events": ["track.play"], "enabled": True,
        }])
        self.register(station)
        self.assertEqual(len(station.rows), 2, station.rows)
        self.assertIn("their_bot", [r["id"] for r in station.rows])


class TestOurWebhookRowKeepsItsIdentity(_StationWebhooks):
    """Registering sends a stable id, and that is the whole point of it.

    Without one the station mints a fresh id per registration, so this box
    moving to a new LAN address left its old row behind and added a second.
    The station caps the list at 16, after which registration fails for good —
    and the operator's only clue is a flat refusal.
    """

    def test_the_row_carries_our_id(self):
        station = self.register(_FakeStation())
        self.assertEqual([r["id"] for r in station.rows], [self.hooks.HOOK_ID])
        self.assertTrue(self.hooks._hook_state["registered"])

    def test_registering_again_does_not_add_a_second_row(self):
        station = self.register(_FakeStation())
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        self.assertEqual(len(station.rows), 1, station.rows)

    def test_an_unchanged_row_is_not_rewritten(self):
        station = self.register(_FakeStation())
        writes = len(station.writes)
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        self.assertEqual(len(station.writes), writes,
                         "a boot that changes nothing still wrote to the station")

    def test_a_new_address_moves_the_row_instead_of_adding_one(self):
        station = self.register(_FakeStation())
        os.environ["CALLIN_HOOK_URL"] = "http://192.0.2.9:8100/hooks/station"
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        self.assertEqual(len(station.rows), 1, station.rows)
        self.assertEqual(station.rows[0]["url"], "http://192.0.2.9:8100/hooks/station")

    def test_a_row_registered_before_we_sent_an_id_is_adopted(self):
        # What every existing deployment looks like on upgrade: our address,
        # an id the station chose. Matching on id alone would leave it there
        # and register a duplicate alongside it.
        station = _FakeStation(rows=[{
            "id": "wh_8f21", "url": "http://192.0.2.7:8100/hooks/station",
            "events": ["track.play"], "enabled": True,
        }])
        self.register(station)
        self.assertEqual(len(station.rows), 1, station.rows)
        self.assertEqual(station.rows[0]["id"], self.hooks.HOOK_ID)


class TestOtherWebhookRowsSurviveOurRegistration(_StationWebhooks):
    """The write replaces the whole array, so anything the operator wired up
    themselves is ours to carry through untouched.

    Including the sentinel the station substitutes for a stored auth header on
    read: it resolves that back by row id, so a row that round-trips unchanged
    keeps its credential — and one that loses its id does not.
    """

    def test_a_foreign_row_round_trips_byte_for_byte(self):
        other = {"id": "n8n_relay", "url": "https://example.invalid/hook",
                 "events": ["dj.say"], "enabled": True, "authHeader": "set"}
        station = self.register(_FakeStation(rows=[other]))
        kept = [r for r in station.rows if r["id"] == "n8n_relay"]
        self.assertEqual(kept, [other])

    def test_a_row_disabled_by_the_operator_is_not_switched_back_on(self):
        station = _FakeStation(rows=[{
            "id": self.hooks.HOOK_ID, "url": "http://192.0.2.7:8100/hooks/station",
            "events": list(self.hooks.WANTED_EVENTS), "enabled": False,
        }])
        self.register(station)
        self.assertFalse(station.rows[0]["enabled"], "we re-enabled our own row")
        # And the panel must not then claim push events are working.
        self.assertIn("disabled", self.hooks._hook_state["detail"])

    def test_adopting_a_row_never_costs_it_a_stored_credential(self):
        # The station resolves the redaction sentinel by row id, so renaming a
        # row to our preferred id would trade the operator's auth header for a
        # tidier name. The URL match finds it again either way.
        station = _FakeStation(rows=[{
            "id": "wh_8f21", "url": "http://192.0.2.7:8100/hooks/station",
            "events": ["track.play"], "enabled": True, "authHeader": "set",
        }])
        self.register(station)
        self.assertEqual(len(station.rows), 1, station.rows)
        self.assertEqual(station.rows[0]["id"], "wh_8f21")
        self.assertEqual(station.rows[0]["authHeader"], "set")

    def test_an_extra_subscription_on_our_row_is_kept(self):
        station = _FakeStation(rows=[{
            "id": self.hooks.HOOK_ID, "url": "http://192.0.2.7:8100/hooks/station",
            "events": ["request.received"], "enabled": True,
        }], events=_FakeStation.EVENTS + ["show.start"])
        station.rows[0]["events"].append("show.start")
        self.register(station)
        self.assertIn("show.start", station.rows[0]["events"])


class TestTheRegistrationShapeIsTheOneTheStationReads(_StationWebhooks):
    """`{"webhooks": [...]}` is the only shape there has ever been.

    This used to try a flat `{"url", "events"}` first. The handler reads
    `req.body.webhooks` and nothing else, and since SUB/WAVE 1.6.0 zod strips
    the unknown keys before it even gets there — so that attempt was answered
    200 and changed nothing, in both directions at once.
    """

    def test_every_write_is_the_whole_list(self):
        station = self.register(_FakeStation())
        self.assertTrue(station.writes)
        for body in station.writes:
            self.assertIn("webhooks", body, body)
            self.assertIsInstance(body["webhooks"], list)

    def test_the_gate_setting_is_never_touched(self):
        # trackPlayListenerGated saves independently, and sending it would
        # overwrite an operator's choice as a side effect of registering.
        station = self.register(_FakeStation())
        for body in station.writes:
            self.assertNotIn("trackPlayListenerGated", body)


class TestARefusedRegistrationSaysWhichFieldWasWrong(_StationWebhooks):
    """A refusal used to read "station did not accept either registration
    shape" whatever the cause — which is exactly the flat, unactionable error
    SUB/WAVE 1.6.0's field-level payload exists to replace."""

    def test_the_stations_own_sentence_reaches_the_panel(self):
        self.register(_FakeStation(refuse=_FakeResponse(
            400, {"error": "URL must start with http:// or https://",
                  "fieldErrors": {"webhooks.0.url": ["URL must start with http://"]}})))
        self.assertIn("URL must start with", self.hooks._hook_state["detail"])

    def test_a_field_error_alone_still_names_the_field(self):
        self.register(_FakeStation(refuse=_FakeResponse(
            400, {"fieldErrors": {"webhooks.0.id": ["id must be 3-32 characters"]}})))
        detail = self.hooks._hook_state["detail"]
        self.assertIn("webhooks.0.id", detail)
        self.assertIn("3-32", detail)

    def test_a_body_that_is_not_json_does_not_lose_the_status(self):
        self.register(_FakeStation(refuse=_FakeResponse(502, text="")))
        self.assertIn("502", self.hooks._hook_state["detail"])

    def test_a_refusal_stops_retrying_but_bad_credentials_do_not(self):
        self.register(_FakeStation(refuse=_FakeResponse(400, {"error": "no"})))
        self.assertTrue(self.hooks._hook_state.get("gave_up"))

        self.hooks._hook_state.pop("gave_up")
        self.register(_FakeStation(refuse=_FakeResponse(401, {"error": "nope"})))
        self.assertFalse(self.hooks._hook_state.get("gave_up"),
                         "a password the operator can fix is not a permanent no")


class TestWeOnlyAskForEventsTheStationKnows(_StationWebhooks):
    """The station validates the event list against an enum and refuses the
    WHOLE registration over one name it doesn't recognise. It advertises its
    own vocabulary on the same read we already make, so there is no reason to
    assert ours against it."""

    def test_an_event_the_station_dropped_is_not_sent(self):
        station = self.register(_FakeStation(events=["track.play", "dj.say"]))
        self.assertEqual(station.rows[0]["events"], ["dj.say", "track.play"])

    def test_a_station_that_advertises_nothing_still_gets_a_registration(self):
        station = self.register(_FakeStation(events=[]))
        self.assertEqual(sorted(station.rows[0]["events"]),
                         sorted(self.hooks.WANTED_EVENTS))

    def test_the_card_busts_for_exactly_what_we_subscribed_to(self):
        self.assertEqual(
            self.hooks._BUSTING_PREFIXES,
            frozenset(e.split(".")[0] for e in self.hooks.WANTED_EVENTS),
            "the events we ask for and the events that refresh the card drifted")


class TestPointingAtANewStationRegistersAgain(_StationWebhooks):
    """`registered` used to be true forever once one station had said yes, so
    changing the station address in the panel left the new one with no
    receiver and the card polling for good."""

    def test_a_changed_station_address_re_arms_registration(self):
        self.register(_FakeStation())
        self.assertFalse(self.hooks._registration_due())

        self.hooks._hook_state["station"] = "http://somewhere-else.invalid"
        self.assertTrue(self.hooks._registration_due())
        self.assertFalse(self.hooks._hook_state["registered"])

    def test_a_previous_refusal_does_not_follow_us_to_a_new_station(self):
        self.hooks._hook_state.update(station="http://old.invalid", gave_up=True)
        self.assertTrue(self.hooks._registration_due())
        self.assertNotIn("gave_up", self.hooks._hook_state)


class TestOurPushesCarryAnAuthHeader(_StationWebhooks):
    """The station echoes a per-hook Authorization header verbatim on every
    push — the only authentication an unsigned webhook can have. Registration
    mints one and the receiver turns away pushes that don't carry it back.
    Before this, anyone who could reach the port could drive the card's
    cache-bust loop; the station supported the header all along and we simply
    never asked for it."""

    def test_registration_writes_a_header_and_keeps_it_stable(self):
        station = self.register(_FakeStation())
        header = station.rows[0].get("authHeader")
        self.assertTrue(header, station.rows[0])
        self.assertTrue(header.startswith("Bearer "))
        self.assertEqual(self.hooks._load_hook_secret(), header)
        # Re-registering must not rotate it: the station would then hold a
        # header this receiver no longer recognises until the next write.
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        self.assertEqual(station.rows[0].get("authHeader"), header)

    def test_a_push_with_the_right_header_is_counted(self):
        self.register(_FakeStation())
        before = self.hooks._hook_state["received"]
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "track.play"},
            headers={"Authorization": self.hooks._load_hook_secret()})))
        self.assertEqual(self.hooks._hook_state["received"], before + 1)

    def test_a_push_without_the_header_is_turned_away(self):
        self.register(_FakeStation())
        before = self.hooks._hook_state["received"]
        resp = asyncio.run(self.hooks.handle_station_hook(
            _FakeHookRequest({"event": "track.play"})))
        self.assertEqual(resp.status, 401)
        self.assertEqual(self.hooks._hook_state["received"], before,
                         "a rejected push must not count as received")
        self.assertEqual(self.hooks._hook_state.get("rejected"), 1)

    def test_before_any_registration_the_receiver_stays_open(self):
        # No credentials, or an old station: no secret has ever been minted,
        # so pushes keep working exactly the way they always did.
        before = self.hooks._hook_state["received"]
        resp = asyncio.run(self.hooks.handle_station_hook(
            _FakeHookRequest({"event": "track.play"})))
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.hooks._hook_state["received"], before + 1)

    def test_an_operator_set_header_on_an_adopted_row_is_not_replaced(self):
        # A row we adopt by URL keeps its foreign id, and a header on it is
        # the operator's — not ours to overwrite with a minted one.
        station = _FakeStation(rows=[{
            "id": "wh_8f21", "url": "http://192.0.2.7:8100/hooks/station",
            "events": ["track.play"], "enabled": True, "authHeader": "set",
        }])
        self.register(station)
        self.assertEqual(station.rows[0]["authHeader"], "set")
        self.assertEqual(self.hooks._load_hook_secret(), "",
                         "no secret may be minted for a header we don't hold")

    def test_a_lost_secret_rotates_our_own_rows_header(self):
        # data/ recreated without its volume: the station still holds our row
        # with the old header and nothing local can verify it. The row is
        # ours — our id, so we minted that header — and rotating beats a
        # verification that is silently off forever.
        station = self.register(_FakeStation())
        old = station.rows[0]["authHeader"]
        self.hooks._store_hook_secret("")
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        new = station.rows[0]["authHeader"]
        self.assertNotEqual(new, old)
        self.assertEqual(self.hooks._load_hook_secret(), new)

    def test_a_rejected_test_fire_rotates_and_says_why(self):
        # The push ARRIVING with the wrong header is a different failure from
        # never arriving — the network is fine — and it is self-healing:
        # drop the secret, re-arm, and the next reconcile rotates the header.
        async def fire():
            await self.hooks.handle_station_hook(_FakeHookRequest(
                {"event": "test"}, headers={"Authorization": "Bearer wrong"}))
            return _FakeResponse(200, {"ok": True})

        station = self.register(_FakeStation(on_test=fire))
        self.hooks._admin_client = station
        result = asyncio.run(self.hooks.fire_test_hook())
        self.assertFalse(result["ok"], result)
        self.assertIn("Authorization", result["detail"])
        self.assertFalse(self.hooks._hook_state["registered"])
        self.assertEqual(self.hooks._load_hook_secret(), "")


class TestARowThatLooksRightAndLetsNothingInGetsReKeyed(_StationWebhooks):
    """A registration that succeeds and achieves nothing.

    The station REDACTS the stored header on read, so a row whose secret has
    drifted from ours reads back identical to a correct one — every field
    matches, registration reports "settled, registered", and every push is
    turned away for ever. Measured on the operator's deployment 2026-08-16:
    59 rejections, zero received, `registered: true`, and nothing in the
    system able to notice. The receiver's own counters are the only evidence
    there is, so they are what re-keys the row.
    """

    def _drift(self, station):
        """Register, then make the station's copy disagree with ours."""
        self.register(station)
        ours = self.hooks._load_hook_secret()
        station.rows[0]["authHeader"] = "Bearer something-else-entirely"
        return ours

    def test_rejections_with_nothing_received_rewrite_the_header(self):
        station = _FakeStation()
        ours = self._drift(station)
        # A push arrives carrying the station's key, not ours.
        resp = asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "track.play"},
            headers={"Authorization": "Bearer something-else-entirely"})))
        self.assertEqual(resp.status, 401)

        self.register(station)
        self.assertNotEqual(self.hooks._load_hook_secret(), ours,
                            "the row was left keyed to a secret we don't hold")
        self.assertEqual(station.rows[0].get("authHeader"),
                         self.hooks._load_hook_secret(),
                         "the station and this box still disagree")

    def test_the_rekey_is_due_even_though_it_says_registered(self):
        # The warm tick is what runs it, and that asks _registration_due —
        # which used to answer False the moment `registered` was true, which
        # is exactly the state this fault lives in.
        station = _FakeStation()
        self._drift(station)
        self.assertFalse(self.hooks._registration_due(),
                         "a settled row with no traffic is not due")
        asyncio.run(self.hooks.handle_station_hook(
            _FakeHookRequest({"event": "track.play"})))
        self.assertTrue(self.hooks._registration_due(),
                        "rejections with nothing received must re-arm it")

    def test_one_accepted_push_settles_it_for_good(self):
        # The counters are "has ANYTHING ever got in", not a rate: a row that
        # is working and then sees a stray bad push must not re-key itself.
        self.register(_FakeStation())
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "track.play"},
            headers={"Authorization": self.hooks._load_hook_secret()})))
        asyncio.run(self.hooks.handle_station_hook(
            _FakeHookRequest({"event": "track.play"})))     # rejected
        self.assertFalse(self.hooks._mis_keyed())
        self.assertFalse(self.hooks._registration_due())

    def test_a_key_that_breaks_AFTER_working_is_still_caught(self):
        """The half the first fix missed, found by auditing it.

        "Nothing has ever been accepted" goes blind the moment one push lands,
        so a key that drifted later — the station's store rebuilt, a row edited
        by hand — would sit broken for ever with `received` frozen at whatever
        it reached first. `rejected` is a RUN since the last accepted push, so
        a genuine break still shows.
        """
        station = _FakeStation()
        self.register(station)
        good = {"Authorization": self.hooks._load_hook_secret()}
        asyncio.run(self.hooks.handle_station_hook(
            _FakeHookRequest({"event": "track.play"}, headers=good)))
        self.assertFalse(self.hooks._mis_keyed(), "a working row is not broken")

        # …and now the station's copy drifts.
        station.rows[0]["authHeader"] = "Bearer no-longer-ours"
        for _ in range(self.hooks._BREAK_RUN):
            asyncio.run(self.hooks.handle_station_hook(
                _FakeHookRequest({"event": "track.play"})))
        self.assertTrue(self.hooks._mis_keyed(),
                        "a row that stopped letting anything in went unnoticed")

    def test_one_stray_probe_does_not_rewrite_the_row(self):
        # Anything on the LAN can POST to the receiver. A single refusal after
        # a working period must not make us rewrite the station's config.
        self.register(_FakeStation())
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "track.play"},
            headers={"Authorization": self.hooks._load_hook_secret()})))
        asyncio.run(self.hooks.handle_station_hook(
            _FakeHookRequest({"event": "track.play"})))
        self.assertFalse(self.hooks._mis_keyed())

    def test_a_successful_rekey_forgets_the_old_rejections(self):
        # Left standing, the count would re-key the row on every warm tick for
        # ever — `received` only moves when a push actually lands.
        station = _FakeStation()
        self._drift(station)
        asyncio.run(self.hooks.handle_station_hook(
            _FakeHookRequest({"event": "track.play"})))
        self.register(station)
        self.assertFalse(self.hooks._mis_keyed())
        self.assertFalse(self.hooks._hook_state.get("rejected"))


class TestStaleLookalikeRowsAreSurfacedNotDeleted(_StationWebhooks):
    """Observed live (2026-08-11): four rows on one station all pointing at a
    /hooks/station path — a Docker-internal address minted before
    CALLIN_HOOK_URL existed, a previous host, one duplicated outright — and
    only one of them real. Every event cost three failed POSTs and the strays
    burn station slots for good. They are not ours to delete (a second Talk
    Wave against the same station is legitimate), but the panel gets to say
    they exist."""

    def test_other_receivers_at_our_path_are_flagged_and_kept(self):
        station = _FakeStation(rows=[
            {"id": "wh_old1", "url": "http://172.20.0.13:8100/hooks/station",
             "events": ["track.play"], "enabled": True},
            {"id": "their_bot", "url": "https://example.invalid/hook",
             "events": ["dj.say"], "enabled": True},
        ])
        self.register(station)
        self.assertEqual(self.hooks._hook_state["lookalikes"],
                         ["http://172.20.0.13:8100/hooks/station"])
        # Flagged is ALL they are — both foreign rows are still there.
        self.assertEqual(len(station.rows), 3, station.rows)

    def test_our_own_settled_row_is_not_flagged(self):
        station = self.register(_FakeStation())
        self.hooks._hook_state.update(registered=False, station="")
        self.register(station)
        self.assertEqual(self.hooks._hook_state["lookalikes"], [])


class TestADeliveredPushIsProvedRatherThanAssumed(_StationWebhooks):
    """"Registered" only ever meant the station accepted a row.

    The receiver is a LAN address behind a NAS, so "the station cannot reach
    it" is the failure that actually happens — and it looks identical to
    working from the panel. The station's own test endpoint fires at one hook
    by id, which makes the whole path testable in both directions.
    """

    def _delivering(self):
        """A station that pushes at us before answering, as the real one does —
        including echoing back the Authorization header registration stored."""
        async def fire():
            await self.hooks.handle_station_hook(_FakeHookRequest(
                {"event": "test", "t": "now"},
                headers={"Authorization": self.hooks._load_hook_secret()}))
            return _FakeResponse(200, {"ok": True})

        return _FakeStation(on_test=fire)

    def test_a_push_that_lands_is_reported_as_delivered(self):
        station = self.register(self._delivering())
        self.hooks._admin_client = station
        result = asyncio.run(self.hooks.fire_test_hook())
        self.assertTrue(result["ok"], result)
        self.assertIn("192.0.2.7", result["detail"])

    def test_a_station_that_cannot_reach_us_is_not_reported_as_working(self):
        station = self.register(_FakeStation())     # accepts, never pushes
        self.hooks._admin_client = station
        # Restored to what it FOUND, not to a literal. Putting 3.0 back here
        # meant this test silently rewrote the shipped constant for every test
        # after it — so when the real value moved to 8.0, the sibling that
        # checks the shipped value read 3.0 and failed for a reason that had
        # nothing to do with the code it was testing.
        was, self.hooks._DELIVERY_WAIT = self.hooks._DELIVERY_WAIT, 0.05
        try:
            result = asyncio.run(self.hooks.fire_test_hook())
        finally:
            self.hooks._DELIVERY_WAIT = was
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["fired"])
        self.assertIn("192.0.2.7", result["detail"])

    def test_a_row_deleted_at_the_station_re_arms_registration(self):
        station = self.register(_FakeStation(
            on_test=lambda: _FakeResponse(404, {"error": "webhook not found"})))
        self.hooks._admin_client = station
        result = asyncio.run(self.hooks.fire_test_hook())
        self.assertFalse(result["ok"])
        self.assertFalse(self.hooks._hook_state["registered"])
        self.assertTrue(self.hooks._registration_due())

    def test_a_station_without_the_endpoint_says_so(self):
        station = self.register(_FakeStation(
            on_test=lambda: _FakeResponse(404, text="Cannot POST /webhooks/x/test")))
        self.hooks._admin_client = station
        result = asyncio.run(self.hooks.fire_test_hook())
        self.assertIn("no webhook test endpoint", result["detail"])
        self.assertTrue(self.hooks._hook_state["registered"],
                        "an old station is not a reason to forget our row")

    def test_pushes_are_counted_rather_than_read_off_the_capped_list(self):
        # The event list is a deque with a maxlen, so its length saturates —
        # counting from it would silently stop noticing arrivals.
        before = self.hooks._hook_state["received"]
        for _ in range(3):
            asyncio.run(self.hooks.handle_station_hook(
                _FakeHookRequest({"event": "track.play"})))
        self.assertEqual(self.hooks._hook_state["received"], before + 3)


class TestAVoicePushAnchorsTheAirGuard(_StationWebhooks):
    """The on-air guard anchors its hold on the push file (0.10.69): a
    verified dj.say/dj.link push lands at the station's handoff instant,
    seconds before the guard's 4s log poll would notice — which was most of
    why the hold ran early against the audible link. Only VERIFIED pushes may
    write the file: an open receiver steering the gate would let anyone on
    the LAN gag the call DJ at will."""

    def test_a_verified_voice_push_writes_the_air_file(self):
        import time

        self.register(_FakeStation())
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "dj.say", "text": "That was Riverside, by America."},
            headers={"Authorization": self.hooks._load_hook_secret()})))
        d = json.loads(self.hooks._air_path().read_text())
        self.assertEqual(d["event"], "dj.say")
        self.assertIn("Riverside", d["text"])
        self.assertLess(abs(time.time() - d["at"]), 5)

    def test_the_stream_buffer_survives_an_event_that_does_not_report_it(self):
        # Only some events carry streamBufferSeconds — voice.end reports 0 —
        # and the guard primes the caller's lag from whatever push happens to
        # be newest when a CALL starts. So a call beginning after a `clear`
        # believed the caller was 2 seconds behind the live edge rather than
        # 22, and sized the hold for its own announcement from that.
        #
        # Measured on air 2026-08-16: hold opened with callerLag=2.0 on a
        # station that had been reporting 22 all evening, and the DJ came back
        # three seconds before the caller had finished hearing the shoutout.
        # The buffer belongs to the station's Icecast config, not to one
        # event, so the last real reading is carried forward.
        self.register(_FakeStation())
        auth = self.hooks._load_hook_secret()
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "voice.start", "voiceId": "v1", "text": "a link",
             "durationMs": 3000, "streamBufferSeconds": 22},
            headers={"Authorization": auth})))
        self.assertEqual(22.0,
                         json.loads(self.hooks._air_path().read_text())["bufSecs"])

        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "voice.end", "voiceId": "v1"},
            headers={"Authorization": auth})))
        d = json.loads(self.hooks._air_path().read_text())
        self.assertEqual(d["phase"], "clear")
        self.assertEqual(22.0, d["bufSecs"],
                         "the clear threw the station's buffer away, and the "
                         "next call primed its lag from the 2s fallback")

    def test_a_real_reading_of_zero_is_not_overwritten_by_an_older_one(self):
        # Carrying forward must not invent a buffer for a station that really
        # reports none — only fill in where the EVENT is silent about it.
        self.register(_FakeStation())
        auth = self.hooks._load_hook_secret()
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "voice.start", "voiceId": "v1", "text": "a link",
             "streamBufferSeconds": 22}, headers={"Authorization": auth})))
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "voice.start", "voiceId": "v2", "text": "another",
             "streamBufferSeconds": 5}, headers={"Authorization": auth})))
        self.assertEqual(5.0,
                         json.loads(self.hooks._air_path().read_text())["bufSecs"])

    def test_an_unverified_push_never_steers_the_gate(self):
        # No secret minted: the receiver stays open for compatibility, but a
        # push nothing vouched for must not move the call DJ's gate.
        asyncio.run(self.hooks.handle_station_hook(
            _FakeHookRequest({"event": "dj.say", "text": "gag the DJ"})))
        self.assertFalse(self.hooks._air_path().exists())

    def test_a_track_push_is_not_a_voice_event(self):
        self.register(_FakeStation())
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "track.play", "text": "x"},
            headers={"Authorization": self.hooks._load_hook_secret()})))
        self.assertFalse(self.hooks._air_path().exists())

    def test_the_two_processes_agree_on_the_file_address(self):
        # call/air.py duplicates the path derivation rather than importing the
        # HTTP surface; if the two drift, the worker reads a file nobody
        # writes and the anchor silently stops working.
        from call import air as call_air

        self.assertEqual(str(self.hooks._air_path()),
                         str(call_air._air_path()))

    def test_the_voice_lifecycle_writes_phased_entries(self):
        # SUB/WAVE 1.8's voice.* events (our own issue #1382). queued carries
        # the forecast as an ABSOLUTE airAt so the guard never re-derives it;
        # start is the measurement; end deliberately writes empty text so a
        # version-skewed old worker reading it falls to its short fallback
        # hold instead of sizing a fresh one from words that just finished.
        import time

        self.register(_FakeStation())
        auth = {"Authorization": self.hooks._load_hook_secret()}
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "voice.queued", "voiceId": "9f3a", "text": "coming up",
             "durationMs": 6200, "estimatedAirInMs": 1300,
             "estimated": True}, headers=auth)))
        d = json.loads(self.hooks._air_path().read_text())
        self.assertEqual((d["v"], d["phase"], d["voiceId"]), (2, "queued", "9f3a"))
        self.assertAlmostEqual(d["airAt"], time.time() + 1.3, delta=2)
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "voice.start", "voiceId": "9f3a", "text": "coming up",
             "durationMs": 6200}, headers=auth)))
        d = json.loads(self.hooks._air_path().read_text())
        self.assertEqual((d["phase"], d["durMs"]), ("speaking", 6200))
        asyncio.run(self.hooks.handle_station_hook(_FakeHookRequest(
            {"event": "voice.end", "voiceId": "9f3a"}, headers=auth)))
        d = json.loads(self.hooks._air_path().read_text())
        self.assertEqual(d["phase"], "clear")
        self.assertEqual(d["text"], "")

    def test_the_voice_events_are_asked_for(self):
        # The registration intersects with the station's vocabulary, so a
        # pre-1.8 station simply never grants these — but they have to be in
        # the wanted list at all for a 1.8 station to send them.
        for e in ("voice.queued", "voice.start", "voice.end"):
            self.assertIn(e, self.hooks.WANTED_EVENTS)


class TestTheCallerHearsTheStreamLate(unittest.TestCase):
    """The ducking bug, and it was a constant offset rather than bad luck.

    Every voice.* timestamp the station sends is stamped at the ENCODER. The
    caller is listening to the stream, which runs `streamBufferSeconds`
    behind it — so "the DJ has stopped" reached us while the caller still had
    that many seconds of DJ left to hear, EVERY time, by the SAME amount. The
    call DJ came back mid-sentence on every hold. The station has measured
    and sent the offset since its #1114; we were dropping it on the floor.
    """

    def _verdict(self, entry, now_offset=0.0, buf_cfg=None):
        import time

        from call.air import DUCK_PAD_SECS, OnAirGuard

        g = OnAirGuard.__new__(OnAirGuard)
        g.quiet_secs = 30.0
        g.lag_secs = OnAirGuard.HANDOFF_LAG_SECS
        g.handover_secs = 0.0
        g.on_air = False
        g.duck_pad = DUCK_PAD_SECS
        g._last_buf = 0.0
        return g._push_verdict(entry, time.time() + now_offset)

    def test_the_hold_outlasts_voice_end_by_the_buffer(self):
        import time

        now = time.time()
        entry = {"v": 2, "phase": "clear", "at": now, "bufSecs": 8.0}
        # Straight after voice.end the caller is still hearing the DJ, so the
        # entry must prove nothing rather than prove quiet.
        # Since 0.10.129 the lag SHIFTS the window rather than serving as the
        # tail, so clear waits for the buffer to drain AND the pad to run.
        from call.air import DUCK_PAD_SECS

        self.assertIsNone(self._verdict(entry, 1.0))
        self.assertIsNone(self._verdict(entry, 9.0))
        self.assertEqual(self._verdict(entry, 8.0 + DUCK_PAD_SECS + 1),
                         ("clear", "", ""))

    def test_a_speaking_window_slides_by_the_buffer(self):
        import time

        now = time.time()
        entry = {"v": 2, "phase": "speaking", "at": now, "durMs": 5000,
                 "bufSecs": 6.0, "text": "hello"}
        # 5s of speech + 6s of buffer: still busy at 8s in, which is where the
        # old code had already let the call DJ back in.
        from call.air import DUCK_PAD_SECS

        self.assertEqual(self._verdict(entry, 8.0)[0], "busy")
        # 6s of buffer before they hear a word of it, then 5s of speech, then
        # the pad — 13s in they are still listening.
        self.assertEqual(self._verdict(entry, 13.0)[0], "busy")
        self.assertIsNone(self._verdict(entry, 6.0 + 5.0 + DUCK_PAD_SECS + 1))

    def test_a_station_too_old_to_send_one_still_gets_a_tail(self):
        # No bufSecs at all: fall back to the handoff lag rather than zero,
        # which is what "held on exactly, with no lag" used to mean.
        import time

        from call.air import OnAirGuard

        now = time.time()
        from call.air import DUCK_PAD_SECS

        # A station too old to send streamBufferSeconds still gets a tail: the
        # lag falls back to HANDOFF_LAG_SECS, and since 0.10.129 the lag SHIFTS
        # the window rather than padding the close, so "clear" waits for the
        # fallback lag AND the pad.
        entry = {"v": 2, "phase": "clear", "at": now}
        fallback = OnAirGuard.HANDOFF_LAG_SECS
        self.assertIsNone(self._verdict(entry, DUCK_PAD_SECS / 2))
        self.assertEqual(self._verdict(entry, DUCK_PAD_SECS + fallback + 1),
                         ("clear", "", ""))

    def test_the_receiver_records_what_the_station_measured(self):
        import json
        import tempfile
        import time
        from pathlib import Path
        from unittest import mock

        from api import hook_receiver

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hook-air.json"
            with mock.patch.object(hook_receiver, "_air_path", lambda: path):
                hook_receiver._remember_air(
                    "voice.start",
                    {"voiceId": "v1", "text": "hi", "durationMs": 4000,
                     "streamBufferSeconds": 7.5})
            d = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(d["bufSecs"], 7.5)
        self.assertEqual(d["phase"], "speaking")

    def test_a_nonsense_buffer_cannot_gag_the_call(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        from api import hook_receiver

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hook-air.json"
            with mock.patch.object(hook_receiver, "_air_path", lambda: path):
                hook_receiver._remember_air(
                    "voice.start",
                    {"voiceId": "v1", "durationMs": 1000,
                     "streamBufferSeconds": 99999})
            d = json.loads(path.read_text(encoding="utf-8"))
        self.assertLessEqual(d["bufSecs"], 30.0)

class TestTheAirFileRemembersWhatHappened(_TempStores):
    """The ducking has been diagnosed three times by watching hook-air.json at
    200ms for five minutes, because the file held ONE entry and each push
    overwrote the last. A queued -> start -> end sequence that completed
    between two polls left no trace at all. It keeps a short history now.
    """

    def _write(self, event, **body):
        from api import hook_receiver
        hook_receiver._remember_air(event, body)

    def _read(self):
        import json

        from api.hook_receiver import _air_path
        return json.loads(_air_path().read_text())

    def test_the_history_survives_the_next_push(self):
        self._write("voice.queued", voiceId="a1", text="hello",
                    durationMs=6000, estimatedAirInMs=1200,
                    streamBufferSeconds=22)
        self._write("voice.end", voiceId="a1", streamBufferSeconds=22)
        d = self._read()
        self.assertEqual(d["event"], "voice.end")
        events = [r["event"] for r in d["recent"]]
        self.assertEqual(events, ["voice.queued", "voice.end"])
        # And the numbers the diagnosis turns on are IN the history, not only
        # in the entry that happened to be last.
        self.assertEqual(d["recent"][0]["bufSecs"], 22.0)
        self.assertEqual(d["recent"][0]["durMs"], 6000)

    def test_the_history_is_bounded(self):
        from api.hook_receiver import AIR_HISTORY

        for i in range(AIR_HISTORY + 8):
            self._write("voice.end", voiceId=f"v{i}")
        self.assertEqual(len(self._read()["recent"]), AIR_HISTORY)


class TestTheHandoffEventDoesNotOutrankTheLifecycle(_TempStores):
    """Measured on air 2026-08-13: voice.queued arrives carrying durMs=17827
    and bufSecs=22, and 1.2 SECONDS LATER the legacy dj.say for the same
    utterance overwrites it carrying neither. The guard then sized the hold
    from a word count with no stream buffer and handed the caller back at the
    moment the DJ became audible to them — "returns before the on air DJ even
    says a word". The station emits both; they are the same speech, stamped at
    handoff and at air. When it speaks the lifecycle, the handoff event is a
    duplicate with a worse clock.
    """

    def _write(self, event, **body):
        from api import hook_receiver
        hook_receiver._remember_air(event, body)

    def _read(self):
        import json

        from api.hook_receiver import _air_path
        return json.loads(_air_path().read_text())

    def test_dj_say_does_not_replace_a_live_lifecycle_entry(self):
        self._write("voice.queued", voiceId="a1", text="the real one",
                    durationMs=17827, estimatedAirInMs=1200,
                    streamBufferSeconds=22)
        self._write("dj.say", text="the real one")
        d = self._read()
        self.assertEqual(d["event"], "voice.queued",
                         "the handoff event overwrote the measured one again")
        self.assertEqual(d["durMs"], 17827)
        self.assertEqual(d["bufSecs"], 22.0)

    def test_it_is_still_recorded_as_having_happened(self):
        # Demoted, not dropped: a timeline that hides events is worse than
        # none, and "we ignored this" is exactly what a diagnosis needs to see.
        self._write("voice.queued", voiceId="a1", text="x", durationMs=1000,
                    streamBufferSeconds=22)
        self._write("dj.say", text="x")
        rows = self._read()["recent"]
        self.assertEqual(rows[-1]["event"], "dj.say")
        self.assertIn("ignored", rows[-1])

    def test_a_station_with_no_lifecycle_still_works(self):
        # An older station sends only dj.say/dj.link, and that must keep
        # driving the hold exactly as it always has.
        self._write("dj.say", text="older station")
        self.assertEqual(self._read()["event"], "dj.say")

    def test_the_lifecycle_is_not_trusted_forever(self):
        # A station downgraded mid-run falls back within a track rather than
        # going deaf until the worker restarts.
        import time

        from api import hook_receiver
        self._write("voice.queued", voiceId="a1", text="x", durationMs=1000)
        old = hook_receiver.LIFECYCLE_TRUST_SECS
        hook_receiver.LIFECYCLE_TRUST_SECS = -1.0
        try:
            self._write("dj.say", text="later")
        finally:
            hook_receiver.LIFECYCLE_TRUST_SECS = old
        self.assertEqual(self._read()["event"], "dj.say")



class TestAProbeNamesOnlyWhatItSaw(_StationWebhooks):
    """The webhook row told the operator the station could not reach us.

    Checked on the live box while the panel was showing exactly that row: a
    `wget http://<sidecar>/health` from INSIDE the station's own container
    answered 200. The address was reachable the whole time — the station was
    slower than the three-second window, on a night it was also timing out
    `/state` reads mid-call.

    A probe that names a cause it never established sends the operator to check
    firewalls and docker networks that were never wrong, which is worse than
    reporting less.
    """

    def _timed_out_verdict(self):
        """Fire the test against a station that accepts it and pushes nothing."""
        import asyncio

        class _Accepts:
            def __call__(self, user, password):
                return self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, path):
                return type("R", (), {"status_code": 200, "text": "ok"})()

        self.hooks._admin_client = _Accepts()
        self.hooks._hook_state.update(registered=True, id=self.hooks.HOOK_ID,
                                      url="http://192.0.2.7:8100/hooks/station",
                                      received=0, rejected=0)
        # Shortened for the test and PUT BACK: leaving it patched leaked into
        # the sibling test that checks the shipped value, which then read
        # 0.05 and failed for a reason that had nothing to do with the code.
        was, self.hooks._DELIVERY_WAIT = self.hooks._DELIVERY_WAIT, 0.05
        try:
            return asyncio.run(self.hooks.fire_test_hook())
        finally:
            self.hooks._DELIVERY_WAIT = was

    def test_the_verdict_does_not_claim_the_station_cannot_reach_us(self):
        said = self._timed_out_verdict()["detail"]
        self.assertNotIn("it cannot reach this address", said,
                         "the verdict is asserting a cause the probe cannot "
                         "see — it knows only that nothing arrived in time")

    def test_the_verdict_offers_slowness_as_the_other_candidate(self):
        said = self._timed_out_verdict()["detail"].lower()
        self.assertIn("slowly", said)

    def test_the_verdict_says_what_still_works(self):
        # A row the operator reads as "calls are broken" when the card is
        # merely a little less live is a false alarm with a real cost.
        said = self._timed_out_verdict()["detail"].lower()
        self.assertIn("polling", said)

    def test_the_wait_is_long_enough_for_a_slow_station(self):
        from api import hooks

        self.assertGreaterEqual(
            hooks._DELIVERY_WAIT, 5.0,
            "three seconds was measured too tight against a station that was "
            "also timing out reads; a slower button beats a false fault")


class TestAFailedProbeQuotesTheProvider(unittest.TestCase):
    """"failed to generate LLM completion after 4 attempts" is the SDK's retry
    wrapper — true, and useless.

    Shown on the operator's panel 2026-08-15 as the one red row on the pipeline
    check, above the words "a call will not work until that is fixed". One link
    down the exception chain sat the answer: a 429 reading "Your prepayment
    credits are depleted." Nothing was wrong with the code, the config, or the
    network, and the panel could not say so.

    `_plain_error` already walked exception GROUPS for exactly this reason
    (0.10.82, "the real cause was one level down the whole time"). It did not
    walk a plain `raise ... from ...` chain, which is the shape every retrying
    client library produces.
    """

    def _chain(self):
        inner = RuntimeError(
            "message='gemini llm: client error', status_code=429, "
            'retryable=True, request_id=abc123, body={\n  "error": {\n'
            '    "code": 429,\n    "message": "Your prepayment credits are '
            'depleted. Please go to AI Studio to manage your billing.",\n'
            '    "status": "RESOURCE_EXHAUSTED"\n  }\n}')
        try:
            try:
                raise inner
            except Exception as cause:
                raise ConnectionError(
                    "failed to generate LLM completion after 4 attempts") from cause
        except Exception as e:
            return e

    def test_the_cause_reaches_the_operator(self):
        from api.diagnostics import _plain_error

        said = _plain_error(self._chain())
        self.assertIn("prepayment credits are depleted", said)

    def test_the_json_body_is_not_dumped_into_the_panel(self):
        from api.diagnostics import _plain_error

        said = _plain_error(self._chain())
        self.assertNotIn("RESOURCE_EXHAUSTED", said)
        self.assertNotIn("\n", said)

    def test_a_chain_that_loops_does_not_hang(self):
        from api.diagnostics import _plain_error

        a, b = ValueError("first"), ValueError("second")
        a.__cause__, b.__cause__ = b, a
        self.assertIn("first", _plain_error(a))

    def test_an_ordinary_error_is_unchanged(self):
        from api.diagnostics import _plain_error

        self.assertEqual("nope", _plain_error(ValueError("nope")))
