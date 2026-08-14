"""The browser half, guarded from here because it has no toolchain of its own and no runner to add one to.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import types
import unittest
from pathlib import Path
import settings as settings_store
from tests.support import AGENT_WORKER, REPO, _TempStores, widget_js


class TestAssetVersioning(unittest.TestCase):
    """The html must point at versioned asset URLs.

    This is the silent kind of failure: if index.html's script or link tag is
    ever reformatted, the rewrite quietly matches nothing, the browser asks for
    the bare /call.js, and the middleware correctly answers `no-cache` — so
    every visitor silently goes back to re-downloading 150KB on every load with
    nothing broken enough to notice.
    """

    def test_the_served_html_versions_its_own_assets(self):
        from api import widget as api_widget

        api_widget._page_cache.clear()
        html = api_widget._versioned_page("index.html")
        self.assertIn(f'src="/call.js?v={api_widget.asset_tag("call.js")}"', html)
        self.assertIn(
            f'href="/style.css?v={api_widget.asset_tag("style.css")}"', html)
        self.assertNotIn('src="/call.js"', html)
        self.assertNotIn('href="/style.css"', html)

    def test_both_pages_version_their_own_scripts(self):
        # The two pages load different scripts. A cache keyed on one name, or a
        # hardcoded asset list, would leave the other page's scripts untagged
        # and therefore uncached — silently, which is this test's whole subject.
        from api import widget as api_widget

        api_widget._page_cache.clear()
        for page, script in (("index.html", "call.js"), ("panel.html", "panel.js")):
            with self.subTest(page=page):
                html = api_widget._versioned_page(page)
                self.assertIn(
                    f'src="/{script}?v={api_widget.asset_tag(script)}"', html)
                self.assertIn(
                    f'src="/shared.js?v={api_widget.asset_tag("shared.js")}"', html)
                self.assertNotIn(f'src="/{script}"', html)

    def test_the_tag_changes_when_the_file_does(self):
        # The bug this prevents: assets are served `immutable` for a year, so
        # keying the URL on APP_VERSION meant any change to call.js without a
        # version bump left every browser pinned to the old copy.
        #
        # Tested by actually changing a file. An earlier version of this
        # asserted that two different assets had different tags, which passed
        # locally and failed in CI — a fresh checkout stamps every file with
        # the same mtime, and sharing a tag was never the property that
        # mattered anyway.
        import os
        import time

        from api import widget as api_widget

        original = api_widget.WIDGET_DIR
        tmp = Path(tempfile.mkdtemp())
        try:
            api_widget.WIDGET_DIR = tmp
            asset = tmp / "call.js"
            asset.write_text("// one", encoding="utf-8")
            before = api_widget.asset_tag("call.js")

            asset.write_text("// two", encoding="utf-8")
            os.utime(asset, (time.time() + 5, time.time() + 5))
            self.assertNotEqual(
                api_widget.asset_tag("call.js"), before,
                "editing the file left the cache key unchanged")

            # A missing file must not crash the page; it falls back.
            from version import APP_VERSION

            self.assertEqual(api_widget.asset_tag("nope.js"), APP_VERSION)
        finally:
            api_widget.WIDGET_DIR = original
            shutil.rmtree(tmp, ignore_errors=True)

    def test_it_is_the_real_widget_html(self):
        # Guards against the rewrite silently operating on an empty string.
        from api import widget as api_widget

        html = api_widget._versioned_page("index.html")
        self.assertIn("<html", html.lower())
        self.assertGreater(len(html), 2000)


class TestThumbsArePerDoor(_TempStores):
    """Feedback is offered per door (operator's ask, 0.10.48): the call, the
    text line and the machine each read their own switch, so switching one on
    must not light the other two."""

    DOORS = (("ask_call_feedback", "askFeedback"),
             ("ask_chat_feedback", "askChatFeedback"),
             ("ask_vm_feedback", "askVmFeedback"))

    def test_each_door_reads_its_own_switch(self):
        import settings as settings_store
        from api.live import look_payload

        # All three default ON since 0.10.80, so per-door independence is
        # proven by switching one OFF and watching the other two stay lit.
        base = settings_store.load()
        on = look_payload(dict(base), "Rosie")
        for _, flag in self.DOORS:
            self.assertTrue(on[flag], flag)
        for field, flag in self.DOORS:
            off = look_payload({**base, field: False}, "Rosie")
            for _, other in self.DOORS:
                self.assertEqual(off[other], other != flag,
                                 f"{field} doused {other}")


class TestTheEmbedSitsFlushByDefault(_TempStores):
    """An embed displays in whatever area its host gives it — no border, no
    sheet — unless the operator ticks the outline back on (0.10.51). The
    main page always keeps its card; only the frame reads the flag."""

    def test_the_flag_defaults_off_and_reads_its_setting(self):
        import settings as settings_store
        from api.live import look_payload

        base = settings_store.load()
        self.assertFalse(look_payload(dict(base), "Rosie")["embedOutline"])
        self.assertTrue(look_payload(
            {**base, "embed_card_outline": True}, "Rosie")["embedOutline"])

    def test_the_widget_gates_bare_on_framed(self):
        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("classList.toggle('bare'", js)
        bare_at = js.index("classList.toggle('bare'")
        # `compact`, deliberately not `framed`: the panel's Page-tab preview
        # is framed too, and gating on framed stripped the card there.
        self.assertIn("compact &&", js[bare_at:bare_at + 160],
                      "bare must never strip the main page's card")
        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".card.bare", css)


class TestDiagnosticsResultsKeepTheirScrollSkin(unittest.TestCase):
    """The viewers' result boxes must keep the 'scrolly' class on rewrite.

    className assignment is a full replacement, and a rewrite in
    panel-viewers.js once dropped 'scrolly' from the class the markup ships:
    max-height still applied (a later `.result.logs` rule carries its own),
    overflow did not, and fifty wrapped log lines poured straight through the
    box's border and over the page footer. The operator sent the screenshot.
    """

    def test_every_result_class_rewrite_keeps_scrolly(self):
        js = (REPO / "web-widget" / "panel-viewers.js").read_text(encoding="utf-8")
        rewrites = re.findall(r"className\s*=\s*'([^']*\bresult\b[^']*)'", js)
        self.assertTrue(rewrites, "expected className rewrites in panel-viewers.js")
        missing = [c for c in rewrites if "scrolly" not in c]
        self.assertEqual(
            missing, [],
            "a viewer result box loses its scroll skin on rewrite: %r" % missing)


class TestPanelMarkup(unittest.TestCase):
    """The panel builds itself from the schema, but it can only fill in a
    control the markup actually contains — `byKind` skips any field with no
    matching element id. So a setting declared in settings.py with no input in
    panel.html is simply unreachable, with nothing to say so. That shipped
    twice (avoid_on_air_overlap, on_air_quiet_secs)."""

    def setUp(self):
        import re

        html = (REPO / "web-widget" / "panel.html").read_text(
            encoding="utf-8"
        )
        self.ids = set(re.findall(r'id="([^"]+)"', html))
        self.groups = set(re.findall(r'data-group="([^"]+)"', html))

    def test_every_schema_field_has_a_control(self):
        missing = sorted(f for f in settings_store.SCHEMA if f not in self.ids)
        self.assertFalse(
            missing,
            "settings with no input in panel.html — they cannot be changed from "
            f"the panel: {missing}",
        )

    def test_every_schema_group_has_a_section(self):
        missing = sorted(g for g, *_ in settings_store.GROUPS if g not in self.groups)
        self.assertFalse(missing, f"schema groups with no section: {missing}")

    def test_every_field_belongs_to_a_real_group(self):
        known = {g for g, *_ in settings_store.GROUPS}
        strays = sorted(
            f for f, meta in settings_store.SCHEMA.items() if meta["group"] not in known
        )
        self.assertFalse(strays, f"settings in an unknown group: {strays}")


class TestPanelLoadsOnOpen(unittest.TestCase):
    """Opening the panel must actually fetch the settings.

    0.9.61 shipped with an admin panel that had nothing in it: every dropdown
    empty, no values, no section headers, and no login prompt either. The cause
    was one word. The gear's "have I loaded already?" guard was
    `if (... || options || loading) return;`, written when `options` started as
    null — then 0.9.58 changed it to `{}` so the panel could paint before the
    slow provider lists arrived. `{}` is truthy, so from that commit on the
    guard fired on the very first open and `loadSettings()` was never called.

    Nothing failed loudly: no request, no console error, no 401. There is no JS
    test runner here, so this reads the source — in the same spirit as
    TestPanelMarkup above. It is deliberately narrow: the loaded-flag must be
    its own boolean, never a data container tested for truthiness.

    Since 0.9.105 the panel is its own page, so the trigger is arriving rather
    than clicking a gear — the guard moved into `open_()` and is still exactly
    as capable of being written the wrong way round."""

    @classmethod
    def setUpClass(cls):
        cls.js = (REPO / "web-widget" / "panel.js").read_text(
            encoding="utf-8"
        )

    def _open_handler(self) -> str:
        start = self.js.index("async function open_()")
        return self.js[start : self.js.index("\n  }", start)]

    def test_the_open_guard_uses_a_dedicated_flag(self):
        guard = self._open_handler()
        self.assertIn(
            "loaded ||",
            guard,
            "the skip-the-fetch guard must test the `loaded` flag",
        )

    def test_the_open_guard_never_tests_a_data_container(self):
        # The actual bug: any of these is an object that is truthy while empty,
        # so using one as "already loaded" skips the fetch on the first open.
        guard = self._open_handler()
        for name in ("options", "overrides", "resolved", "secrets", "SCHEMA"):
            self.assertNotIn(
                f"|| {name} ||",
                guard,
                f"`{name}` is truthy when empty — it cannot stand in for `loaded`",
            )

    def test_arriving_on_the_page_actually_loads_it(self):
        # The panel used to be opened by a click. Now nothing clicks anything,
        # so if this call is ever dropped the page renders an empty form with
        # no error — the same silent failure as 0.9.61, by a different route.
        self.assertRegex(
            self.js, r"\n  open_\(\);",
            "panel.js defines open_() but never calls it, so the page loads "
            "nothing")

    def test_loading_the_settings_sets_the_flag(self):
        start = self.js.index("async function loadSettings()")
        body = self.js[start : self.js.index("\n  }", start)]
        self.assertIn(
            "loaded = true",
            body,
            "loadSettings must record that the panel is filled, or arriving "
            "refetches everything every time",
        )

    def test_the_flag_starts_false(self):
        self.assertIn("let loaded = false;", self.js)

    def test_every_group_belongs_to_a_real_supergroup(self):
        known = {s for s, *_ in settings_store.SUPERGROUPS}
        strays = sorted(
            g for g, sup, *_ in settings_store.GROUPS if sup not in known
        )
        self.assertFalse(strays, f"groups under an unknown supergroup: {strays}")

    def test_the_channel_pages_own_their_doors(self):
        # 0.10.62 cut the panel into pages by door (the operator's ask): the
        # sections that ARE a door live on that door's page, and the shared
        # brain stays on The DJ — filed under Calls it would be a lie the
        # moment a text chat used it. A new section drifting onto the wrong
        # page ships a panel whose page names stop meaning anything.
        supers = {g: sup for g, sup, *_ in settings_store.GROUPS}
        self.assertEqual(supers["voicemail"], "voicemail")
        self.assertEqual(supers["chat"], "texts")
        for g in ("call", "turns", "closing", "onair", "tunein",
                  "callback", "sounds", "effects"):
            self.assertEqual(supers[g], "calls", f"{g} left the Calls page")
        # Transcripts moved to the booth page at 0.10.64 (operator's call):
        # the records cover calls, chats and voicemails alike.
        for g in ("context", "style", "record"):
            self.assertEqual(supers[g], "dj", f"{g} is shared by every door")

    def test_the_page_ids_the_picker_reserves_stay_reserved(self):
        # panel.js gives the dashboard and Diagnostics the page ids "dash"
        # and "diag". A super-group minted with either id would collide with
        # them in the picker and the hash router, silently.
        taken = {s for s, *_ in settings_store.SUPERGROUPS}
        self.assertFalse(taken & {"dash", "diag"},
                         "supergroup ids 'dash' and 'diag' belong to panel.js")

    def test_every_declared_field_is_storable(self):
        # A SCHEMA entry with no FIELDS entry renders a control that silently
        # discards whatever you type into it (save() drops unknown keys).
        strays = sorted(f for f in settings_store.SCHEMA if f not in settings_store.FIELDS)
        self.assertFalse(strays, f"settings that cannot be saved: {strays}")


class TestWidgetServerContract(unittest.TestCase):
    """The widget is plain browser JS with no toolchain and no test harness of
    its own, so this is what guards it.

    Two ways it has broken before: a route renamed on the server while the
    widget kept calling the old path, and a DOM id changed in the markup while
    the script kept reaching for the old one. Both leave a green suite and a
    widget that silently does nothing — the exact failure mode this project
    treats as a bug rather than a nitpick.

    Checked PER PAGE since the panel moved to its own page. That is stricter than
    the old whole-widget check, not looser: panel.js reaching for an id that
    only exists on the call page used to pass, because both surfaces were one
    document and every id was in scope. Now it fails, which is right — those
    two pages never load each other's script.
    """

    # page -> the scripts that page loads, in load order.
    PAGES = {
        "index.html": ("shared.js", "call.js"),
        # panel-sounds.js, panel-viewers.js and panel-charts.js read
        # window.Panel, which panel.js publishes, so the order is
        # load-bearing rather than cosmetic. panel-sounds.js additionally
        # publishes Panel.sounds back for panel.js's call sites.
        "panel.html": ("shared.js", "panel.js", "panel-sounds.js",
                       "panel-viewers.js", "panel-charts.js"),
    }

    @classmethod
    def setUpClass(cls):
        import re

        root = REPO
        cls.sources = widget_js()
        cls.js = "\n".join(cls.sources.values())
        cls.pages = {
            name: (root / "web-widget" / name).read_text(encoding="utf-8")
            for name in cls.PAGES
        }
        server = (AGENT_WORKER / "token_server.py").read_text(encoding="utf-8")

        cls.routes = set(re.findall(
            r'router\.add_(?:get|post|put|delete)\(\s*"([^"]+)"', server))
        cls.fetched = set(re.findall(r"""fetch\(\s*['"`](/[^'"`?${]*)""", cls.js))

    @staticmethod
    def _ids_wanted(src):
        import re

        return set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", src)) | set(
            re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", src))

    def test_the_scan_found_something_to_check(self):
        # A silently-empty scan would make every assertion below pass forever.
        self.assertGreater(len(self.routes), 10)
        self.assertGreater(len(self.fetched), 10)
        self.assertEqual(
            set(self.sources),
            {"shared.js", "call.js", "panel.js", "panel-sounds.js",
             "panel-viewers.js", "panel-charts.js"})

    def test_every_script_belongs_to_a_page_and_every_page_loads_its_own(self):
        # A file that exists but nothing loads is the split's own failure mode:
        # the code is right, the tests still read it, and the browser never
        # sees it. The reverse matters just as much now — a page that started
        # loading the other surface's script would undo the whole point of
        # giving the panel its own URL.
        import re

        for page, expected in self.PAGES.items():
            with self.subTest(page=page):
                loaded = re.findall(r'<script src="/([\w.-]+\.js)"', self.pages[page])
                self.assertEqual(
                    tuple(loaded), expected,
                    f"{page} loads {loaded}, expected {list(expected)}")

        covered = {s for scripts in self.PAGES.values() for s in scripts}
        orphans = sorted(set(self.sources) - covered)
        self.assertEqual(
            orphans, [],
            f"these ship in web-widget/ but no page loads them: {orphans}")

    def test_the_call_page_does_not_ship_the_operator_interface(self):
        # The reason the panel got its own page. index.html carried the entire
        # settings form until 0.9.105, so every anonymous caller downloaded it.
        html = self.pages["index.html"]
        for marker in ('id="panel"', "Call-in settings", 'id="saveBtn"'):
            with self.subTest(marker=marker):
                self.assertNotIn(
                    marker, html,
                    "the operator interface is back on the caller's page")

    def test_every_path_the_widget_calls_is_a_route_the_server_serves(self):
        static = {r.rstrip("/") for r in self.routes if "{" not in r}
        prefixes = [r.split("{")[0] for r in self.routes if "{" in r]
        missing = sorted(
            path for path in self.fetched
            if path.rstrip("/") not in static
            and not any(path.startswith(p) for p in prefixes)
        )
        self.assertEqual(
            missing, [],
            "the widget calls paths token_server.py does not serve — it will "
            f"404 with nothing to say so: {missing}",
        )

    def test_every_element_a_page_reaches_for_exists_on_that_page(self):
        import re

        for page, scripts in self.PAGES.items():
            src = "\n".join(self.sources[s] for s in scripts)
            declared = set(re.findall(r'id="([A-Za-z0-9_-]+)"', self.pages[page]))
            # Some elements are built in JS when first needed rather than
            # sitting in the markup (the first-run banner, the password nudge).
            built = set(re.findall(r"\.id\s*=\s*['\"]([A-Za-z0-9_-]+)['\"]", src))
            missing = sorted(self._ids_wanted(src) - declared - built)
            with self.subTest(page=page):
                self.assertEqual(
                    missing, [],
                    f"{'/'.join(scripts)} reads element ids that {page} does not "
                    f"declare and never creates — those controls are dead: "
                    f"{missing}")

    def test_the_widget_is_still_dependency_free(self):
        # No build step, no bundler, no node_modules. The moment the widget
        # needs one, everything above stops being enough and the deploy story
        # changes. The split into three files is script tags, not modules,
        # precisely so this stays true.
        root = REPO
        self.assertFalse(
            list(root.glob("package.json")) + list((root / "web-widget").glob("package.json")),
            "a package.json appeared — the widget is meant to stay toolchain-free",
        )
        for name, src in self.sources.items():
            with self.subTest(file=name):
                self.assertNotIn("require(", src)
                self.assertNotIn("import ", src.split("//")[0][:200])


class TestEachSurfaceIsAnsweredDeliberately(unittest.TestCase):
    """The call card's corner controls are the server's decision, not the
    stylesheet's — and from 0.9.111 the server is asked twice.

    Before 0.9.95 they were three unrelated mechanisms in the widget, and the
    settings gear was hidden by a rule that existed only for embeds — so the
    call page and an embed offered different controls, which nobody had
    decided. They may differ now, which is the opposite of an accident: the
    operator answers a two-column matrix. What has not changed is that the
    widget may only ever SUBTRACT from what it is sent, for reasons this side
    cannot see (a host page that pinned a theme, an embed with no panel).
    """

    def test_the_help_button_follows_its_setting(self):
        from api import live as api_live

        self.assertTrue(api_live.corner_controls(
            {"show_caller_help": True})["help"])
        self.assertFalse(api_live.corner_controls(
            {"show_caller_help": False})["help"])

    def test_the_defaults_leave_an_existing_deployment_alone(self):
        # Thirteen new switches arrived at once. Every one of them defaults
        # on, because turning something off has to be somebody's decision and
        # not an upgrade's — an operator who pulls this image and reads
        # nothing must see the card they saw yesterday.
        import settings as settings_store
        from api import live as api_live

        cfg = {k: v for k, (_env, v) in settings_store.FIELDS.items()}
        for embed in (False, True):
            with self.subTest(embed=embed):
                controls = api_live.corner_controls(cfg, embed=embed)
                self.assertTrue(controls["help"])
                self.assertTrue(controls["theme"])
                self.assertEqual(controls["settings"], not embed)
                self.assertEqual(
                    set(api_live.card_identity(cfg, embed=embed).values()),
                    {True})
        self.assertEqual(api_live.call_button_label(cfg, "Francesca"),
                         "Call the DJ")

    def test_each_surface_reads_its_own_answer(self):
        from api import live as api_live

        cfg = {
            "show_caller_help": True, "embed_caller_help": False,
            "show_theme_toggle": True, "embed_theme_toggle": False,
        }
        page = api_live.corner_controls(cfg)
        embed = api_live.corner_controls(cfg, embed=True)
        self.assertTrue(page["help"])
        self.assertFalse(embed["help"])
        self.assertTrue(page["theme"])
        self.assertFalse(embed["theme"])

    def test_an_embed_never_gets_a_gear(self):
        # Not a setting, and deliberately so: an embed does not load the
        # panel's code, so the gear would open nothing whichever way an
        # operator set it. The panel shows a dash in that cell for the same
        # reason.
        from api import live as api_live

        self.assertFalse(api_live.corner_controls(
            {"show_settings_gear": True}, embed=True)["settings"])
        self.assertTrue(api_live.corner_controls(
            {"show_settings_gear": True})["settings"])

    def test_pinning_a_theme_takes_the_toggle_away(self):
        from api import live as api_live

        for pinned in ("light", "dark"):
            with self.subTest(theme=pinned):
                self.assertFalse(api_live.corner_controls(
                    {"widget_theme": pinned, "show_theme_toggle": True})["theme"],
                    "a pinned theme leaves nothing to toggle")

    def test_auto_and_inherit_keep_the_toggle(self):
        from api import live as api_live

        # "inherit" is not a pinned theme: on the standalone page, where
        # there is no host to inherit from, it behaves as auto. Both gates
        # have to pass — the operator's switch AND there being two themes to
        # switch between.
        for choice in ("auto", "inherit", "", None):
            with self.subTest(theme=choice):
                self.assertTrue(api_live.corner_controls(
                    {"widget_theme": choice, "show_theme_toggle": True})["theme"])

    def test_the_widget_reads_the_keys_the_server_writes(self):
        # The widget subtracts from these by name. A rename on one side only
        # would silently hide a control rather than raising anything.
        from api import live as api_live

        call_js = (REPO / "web-widget" / "call.js"
                   ).read_text(encoding="utf-8")
        for key in api_live.corner_controls({}):
            with self.subTest(key=key):
                self.assertIn(f"c.{key} !== false", call_js,
                              f"call.js never reads controls.{key}")

    def test_the_widget_reads_the_card_keys_too(self):
        # Same trap, one payload along: a renamed card key would blank a line
        # of the card rather than raising.
        from api import live as api_live

        call_js = (REPO / "web-widget" / "call.js"
                   ).read_text(encoding="utf-8")
        for key in api_live.card_identity({}):
            with self.subTest(key=key):
                self.assertIn(f"parts.{key} === false", call_js,
                              f"call.js never reads card.{key}")


class TestTheCardIsOneHeightAndStaysThere(unittest.TestCase):
    """The card does not change size. Not at pickup, not per line of speech.

    This widget's main home is an embed in a station page's player column,
    and a card that grows there shoves the host's own layout around. 0.9.117
    briefly traded the reservation away — small idle, one growth at pickup —
    on the reading that §5 only forbids resizing per LINE. It moved a real
    station page, which settles the question: the rule here is one height,
    full stop.

    The thing that was actually wrong was the SIZE of what got reserved, not
    the reserving. So what is pinned here is both halves: everything holds
    its space, and the caption box holds TWO lines rather than the fourteen
    it once did.
    """

    @classmethod
    def setUpClass(cls):
        cls.css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")

    def test_the_in_call_chrome_holds_its_space(self):
        # `visibility`, never `display` — the second one collapses the box and
        # the card changes height the moment a call starts. (The rig also
        # carries display:flex now, to flex the transcript to one footprint —
        # but the space-reserving visibility:hidden is the part under test.)
        self.assertRegex(
            self.css, r'\.rig \{\s+visibility: hidden;',
            "the rig must reserve its space with visibility:hidden")
        # The TICKER no longer reserves, and that is the point of the fixed
        # card: it used to hold two lines of empty grid so the frame could not
        # jump when the first caption arrived, and inside a line box that
        # scrolls that reservation was 36px of invisible content — an idle
        # card with nothing in it grew a scrollbar (measured: scrollHeight 255
        # against a 219px box). The card's own fixed height keeps the promise
        # now, which is what the rig assertion above pins.
        self.assertIn(".ticker[hidden] { display: none; }", self.css)
        self.assertIn(".pill[hidden] { display: none; }", self.css)
        # The chips collapse rather than reserve, wherever they live — and
        # since 0.10.136 they live on the track's rail, not in the identity
        # row, so the row that must always be able to say who answers is not
        # sharing its width with them.
        self.assertIn(".nprail .chips", self.css)
        self.assertNotIn(".who-row .chips", self.css)

    def test_the_line_area_is_the_only_thing_that_flexes(self):
        # The invariant has never been the number, it is that the frame does
        # not resize as speech arrives. Until the player redesign that was
        # bought with a RESERVED height (--lines-h) that three separate rules
        # had to agree on; it is bought now with a fixed card and one elastic
        # band, which is stronger — a nine-line read-back and a full text
        # thread scroll in here instead of growing anything.
        # Anchor on the BASE rule (2-space indent, its own line), not a
        # descendant selector that merely ends in ".linebox {" — the chat mode
        # legitimately overrides this to grow with the conversation, and that
        # override must not be mistaken for the base reservation.
        block = self.css.split("\n  .linebox {")[1].split("}")[0]
        self.assertIn("flex: 1", block)
        self.assertIn("min-height: 0", block)
        self.assertIn("overflow-y: auto", block)
        self.assertNotIn("height: auto", block)
        # The reserved-height machinery is gone, not merely unused: a stale
        # --lines-h would be a second answer to the same question.
        self.assertNotIn("--lines-h:", self.css)
        self.assertNotIn("--action-h:", self.css)

    def test_reading_back_a_finished_call_scrolls_rather_than_grows(self):
        # It used to open the box to 200px, which was safe only because there
        # was no call left for the resize to interrupt. The box scrolls now, so
        # the drawer costs the card nothing and the rule is gone.
        self.assertNotIn(".linebox.open { height", self.css)

    def test_the_action_row_is_the_last_row_of_the_card(self):
        # Bottom-up: the Call button (and in-call, the state it becomes) is
        # the card's last row on every surface — so an embed's button lines
        # up with the host page's own bottom row — with the talk bar above
        # it, the meters band (which carries the volume since 0.9.134 — a
        # row of its own was ~41px of air on every embed) above that, and
        # the words on top.
        html = (REPO / "web-widget" / "index.html").read_text(encoding="utf-8")
        order = [html.index(m) for m in ('id="stateRow"', 'id="lineBox"',
                                         'class="meters"',
                                         'class="talkrow"', 'class="actionrow"')]
        self.assertEqual(order, sorted(order),
                         "card order must be state, words, meters+volume, "
                         "talk row, action row")
        self.assertNotIn('class="callrow"', html,
                         "the volume's own row is dead; it lives in .meters")
        self.assertIn('id="volSlider"',
                      html.split('class="meters"')[1].split("talkrow")[0],
                      "the volume control must sit inside the meters band")
        self.assertIn(".rig > .linebox, .rig > .meters, .rig > .actionrow "
                      "{ visibility: visible; }", self.css)

    def test_the_compact_card_fits_a_station_page_column(self):
        # The real page this embeds in gives its player column 400px and
        # stretches its own marquee to match the frame — every pixel this
        # card reported over ~356 (400 minus the host's caption row) came
        # back as BLANK SPACE between the sleeve and "Up next" on a live
        # station page, twice.
        #
        # That used to be defended by a budget of individual savings — a 30px
        # eyebrow, a hidden tagline, 14px bars — each of which a future band
        # could quietly spend. The embed is a FIXED 320 now, so the budget is
        # the height itself and there is nothing left to overspend.
        self.assertIn("THE HEIGHT BUDGET", self.css)
        card = self.css.split("body.compact .card {")[1].split("}")[0]
        # A DECLARED fixed height, and one a host column can live with. The
        # exact number is not the invariant and has moved once already: 320
        # was the spec's, and the operator called the resulting embed too
        # short after seeing it — at 320 the conversation box is 111px.
        #
        # The old ~356 ceiling came from a station page that gave its player
        # column 400px; 380 spends 24 of the 44 that were spare. Past ~400
        # that page starts showing blank space again, so this is the ceiling
        # the number may not cross without the operator seeing it first.
        import re
        m = re.search(r"height: (\d+)px", card)
        self.assertTrue(m, "the embed must declare a fixed height")
        self.assertLessEqual(int(m.group(1)), 400,
                             "past 400 the station page shows blank space "
                             "under the card again")
        self.assertGreaterEqual(int(m.group(1)), 320)
        # flex:none, or the frame's own column stretches it back out and the
        # fixed height means nothing.
        self.assertIn("flex: none", card)
        self.assertIn("body.compact .bars { height: 14px; }", self.css)

    def test_the_post_call_chrome_lives_inside_the_line_area(self):
        # The transcript drawer and the how-was-it buttons used to be bands
        # below the box: every call's end grew the card and moved the host
        # page — the exact thing the reserved line area exists to prevent.
        html = (REPO / "web-widget" / "index.html").read_text(encoding="utf-8")
        box = html.split('id="lineBox"')[1].split('class="meters"')[0]
        for inside in ('id="endedBar"', 'id="rateBar"', 'id="guestGate"'):
            with self.subTest(element=inside):
                self.assertIn(inside, box,
                              f"{inside} must sit inside the line area, not "
                              "grow the card as a band of its own")

    def test_an_idle_card_says_nothing(self):
        # "Not connected" sat on every idle card — a permanent grey sentence
        # restating what the eyebrow and the Call button already say, and on
        # a host page it read as something being wrong. The element stays for
        # transient messages; the idle filler is gone.
        html = (REPO / "web-widget" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("Not connected", html)
        self.assertIn("#status:has(#statusText:empty) { display: none; }",
                      self.css)

    def test_the_volume_slider_is_not_the_browsers_own(self):
        # `accent-color` on a native range is a fat rounded bar with a big
        # round knob: at full volume, a bright solid stripe across the widest
        # row of the card, out-shouting the Call button next to it. It is a
        # setting nobody changes. It takes the same 3px trough every other
        # level in this card uses.
        self.assertNotIn("accent-color: var(--coral)", self.css)
        self.assertIn("::-webkit-slider-runnable-track", self.css)
        self.assertIn("::-moz-range-progress", self.css)
        # webkit cannot style the filled half of a range, so the fill is a
        # gradient stop fed from JS. Without this the trough is always empty.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("setProperty('--vol'", call_js)
        self.assertIn("var(--vol, 100%)", self.css)

    def test_no_rule_grows_the_card_when_a_call_starts(self):
        # The 0.9.117 shape, named so it cannot come back by accident: bands
        # that appear on `.rig.on` are bands that were not there before it.
        for gone in (".card:has(.rig.on) .linebox",
                     ".card:has(.rig.on) .ticker[hidden]",
                     ".rig { display: none; }"):
            with self.subTest(rule=gone):
                self.assertNotIn(gone, self.css)


class TestTheServiceWorkerStaysOutOfTheWay(unittest.TestCase):
    """A phone-in answered from a cache is not a phone-in.

    sw.js is the one file here that can break the app without breaking the
    page: it sits between the widget and the server and can go on doing so
    long after anyone remembers installing it. A cached /live paints a DJ who
    went off air hours ago; a cached /token mints nothing. So the list of
    paths it refuses to touch is a contract, not a preference — it is checked
    here because the widget has no runner of its own to check it.
    """

    @classmethod
    def setUpClass(cls):
        cls.sw = (REPO / "web-widget" / "sw.js").read_text(encoding="utf-8")
        cls.index = (REPO / "web-widget" / "index.html").read_text(encoding="utf-8")
        cls.call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")

    def test_nothing_live_is_ever_cached(self):
        # /live and /token are the two that would be actively wrong. /panel is
        # here because the operator's surface is not part of the installed app
        # and has no business in its cache.
        for path in ("/live", "/token", "/call-ended", "/panel", "/settings"):
            with self.subTest(path=path):
                self.assertIn(
                    f"'{path}'", self.sw,
                    f"{path} is not in the worker's never-touch list — it "
                    "would be answered from a cache")

    def test_the_worker_only_installs_on_the_real_page(self):
        # An embed on somebody else's site installing a worker for this origin
        # is a surprise nobody asked for, and it outlives the frame.
        self.assertIn("!framed", self.call_js.split("serviceWorker'")[1][:400])

    def test_the_page_is_actually_installable(self):
        # Each of these is individually load-bearing: no manifest and the
        # browser never offers to install; no apple-touch-icon and iOS puts a
        # screenshot on the home screen instead of the icon.
        for needle in ('rel="manifest"', "apple-touch-icon",
                       'name="theme-color"', "viewport-fit=cover"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.index)

    def test_the_manifest_is_valid_json_and_points_at_real_icons(self):
        import json

        m = json.loads((REPO / "web-widget" / "manifest.webmanifest").read_text(
            encoding="utf-8"))
        self.assertEqual(m["start_url"], "/")
        self.assertTrue(m["icons"], "a manifest with no icons is not installable")
        for icon in m["icons"]:
            with self.subTest(icon=icon["src"]):
                self.assertTrue(
                    (REPO / "web-widget" / icon["src"].lstrip("/")).is_file(),
                    f"{icon['src']} is in the manifest but not in web-widget/")
        # Maskable is the one that stops Android drawing a white ring around
        # the icon, and it is easy to drop in a refactor because nothing
        # visibly breaks on any other platform.
        self.assertIn("maskable", [i.get("purpose") for i in m["icons"]])


class TestThePreviewCannotDisagreeWithTheCard(unittest.TestCase):
    """The panel's preview resolves the look through the same code a caller
    does.

    The alternative was the panel working out in JavaScript which corner
    controls appear, which lines each surface paints and what the Call button
    says — three rules that already exist in api/live.py. Two copies agree
    until one of them changes, and a preview that is quietly wrong about the
    thing you opened it to check is worse than no preview at all.
    """

    def test_live_and_the_preview_answer_from_one_function(self):
        from api import live as api_live

        cfg = dict(settings_store.load())
        look = api_live.look_payload(cfg, "Francesca")
        for key in ("controls", "embedControls", "card", "embedCard",
                    "avatarStyle", "speakerDefault", "callLabel", "theme"):
            with self.subTest(key=key):
                self.assertIn(key, look)

    def test_the_real_live_payload_is_built_from_it(self):
        # Spread into the payload rather than restated. If someone ever
        # re-inlines these keys, /live and the preview can drift apart again.
        src = (AGENT_WORKER / "api" / "live.py").read_text(encoding="utf-8")
        self.assertIn("**look_payload(cfg", src)

    def test_a_patch_changes_the_answer_without_saving_anything(self):
        from api import live as api_live

        cfg = dict(settings_store.load())
        cfg["avatar_style"] = "square"
        self.assertEqual(api_live.look_payload(cfg)["avatarStyle"], "square")
        # And the stored config is untouched — the handler copies before it
        # updates, so a preview can never become a save by accident.
        self.assertNotEqual(settings_store.load().get("avatar_style"), "square")

    def test_the_preview_frame_is_inert(self):
        # It is a real call card inside the settings page. Pressing Call in it
        # would ring the actual DJ from inside the settings form.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("!room && !previewMode", call_js)

    def test_a_preview_message_is_same_origin_only(self):
        # It can change what the card OFFERS, unlike swtv:theme which is
        # colour. A station page embedding the widget must never be able to
        # send it.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        block = call_js.split("swtv:preview")[1][:400]
        self.assertIn("e.origin !== location.origin", block)
        self.assertIn("!previewMode", block)


class TestTheCallerCanChooseWhichWayOut(unittest.TestCase):
    """A live microphone puts the phone into its voice-call audio session,
    which routes to the earpiece — so music playing out loud goes private the
    moment the DJ answers. Wrong for a radio phone-in someone is listening to
    in a car.

    There is no one API for this and iOS Safari publishes none at all, so the
    button is the half that always works. What is guarded here is that the
    default is a setting, that it reaches the widget, and that the button is
    hidden rather than dead where nothing can move the audio.
    """

    def test_the_default_reaches_the_widget(self):
        from api import live as api_live

        self.assertTrue(api_live.look_payload({"default_to_speaker": True})
                        ["speakerDefault"])
        self.assertFalse(api_live.look_payload({"default_to_speaker": False})
                         ["speakerDefault"])

    def test_loudspeaker_is_the_default(self):
        # A phone-in is not a private call. Changing this changes what every
        # existing deployment does on the next call, so it is pinned.
        self.assertTrue(settings_store.FIELDS["default_to_speaker"][1])

    def test_the_widget_reads_it(self):
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("d.speakerDefault !== false", call_js)

    def test_the_button_is_offered_on_a_phone_and_nowhere_else(self):
        # A laptop has no earpiece for the audio to be moved to, and in an
        # embed a row of call-handling buttons is furniture the host page did
        # not ask for. A control that provably cannot do anything is also
        # worse than no control: the caller presses it, nothing happens, and
        # they conclude the call is broken rather than that their browser is
        # old.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("b.hidden = !offerSpeakerButton()", call_js)
        block = call_js.split("function offerSpeakerButton()")[1][:220]
        self.assertIn("!framed", block)
        self.assertIn("pointer: coarse", block)

    def test_an_embed_on_a_phone_still_gets_the_loudspeaker(self):
        # Whether the platform can be ASKED and whether this surface shows a
        # BUTTON are two questions. An embed on a phone has exactly the same
        # earpiece problem — it just does not get a control for it.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        route = call_js.split("async function routeAudio(")[1][:700]
        self.assertNotIn("offerSpeakerButton", route)
        self.assertNotIn("framed", route)

    def test_wanting_the_loudspeaker_does_not_ask_for_play_and_record(self):
        # The Audio Session spec says play-and-record is the type that may be
        # routed to the receiver. Asking for it while wanting the speaker is
        # the exact bug this whole thing exists to fix, and it reads as
        # correct — it is the "I am on a call" type.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        block = call_js.split("navigator.audioSession.type =")[1][:120]
        self.assertIn("'playback' : 'play-and-record'", block)


class TestTheStatusChipDescribesTheCallNotTheSDK(unittest.TestCase):
    """What the chip says is for the caller, not for whoever is debugging.

    "On air" sat on a card whose header already reads ON AIR NOW, so it read
    as the station's state — which does not change during a call — rather than
    as the reason the DJ has stopped talking. And before the DJ has said a
    word, "Thinking" and "Connecting" describe machinery: from the caller's
    end everything up to first speech is one thing, a phone ringing somewhere.

    There is no JS test harness in this repo (see web-widget/CLAUDE.md), so
    this is a text check. It cannot prove the logic runs; it can stop the
    wording being reverted by accident, which is what it is for.
    """

    def setUp(self):
        self.js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")

    def test_the_on_air_hold_says_working_the_booth(self):
        self.assertIn("onair: 'Working the booth'", self.js)

    def test_everything_before_first_speech_says_reaching_the_booth(self):
        self.assertIn("Reaching the booth", self.js)
        self.assertIn("djHasSpoken", self.js)

    def test_the_ringing_latch_is_cleared_at_both_ends_of_a_call(self):
        # Three writes: the declaration, the reset when a call starts, and the
        # reset in endCall. Missing the second means a second call never
        # rings; missing the third leaves the chip mid-call after the line has
        # dropped. Counted rather than parsed — there is no JS harness here —
        # so the number is stated with what it is made of.
        self.assertEqual(self.js.count("djHasSpoken = false"), 3)
        self.assertIn("let djOnAir = false, lastAgentState = 'idle', djHasSpoken = false;",
                      self.js)
        self.assertIn("djOnAir = false; djHasSpoken = false;", self.js)   # endCall


class TestTheCallButtonSaysWhatTheOperatorChose(unittest.TestCase):
    """The label is resolved server-side so the two surfaces cannot drift, and
    so "use the DJ's name" follows the live roster without the widget knowing
    the rule."""

    def test_the_default_is_unchanged(self):
        from api import live as api_live

        self.assertEqual(api_live.call_button_label({}, "Francesca"),
                         "Call the DJ")

    def test_a_typed_label_wins_over_the_default(self):
        from api import live as api_live

        self.assertEqual(
            api_live.call_button_label({"call_button_mode": "custom",
                                        "call_button_label": "Ring the booth"},
                                       "Francesca"),
            "Ring the booth")

    def test_the_name_replaces_the_typed_label(self):
        # They are alternatives, not layers. They used to be a checkbox beside
        # a text box, where ticking the box left whatever you had typed sitting
        # in an enabled field that no longer did anything. One picker now, so
        # there is nothing left to quietly combine.
        from api import live as api_live

        self.assertEqual(
            api_live.call_button_label(
                {"call_button_mode": "name",
                 "call_button_label": "Ring the booth"},
                "Francesca"),
            "Call Francesca")

    def test_a_missing_name_falls_back_rather_than_saying_call(self):
        # "Call " with nothing after it is worse than the generic label, and
        # the persona name is genuinely absent when nobody is on air.
        from api import live as api_live

        self.assertEqual(
            api_live.call_button_label({"call_button_mode": "name"}, ""),
            "Call the DJ")

    def test_a_typed_label_does_nothing_unless_it_was_chosen(self):
        # The text survives switching the picker back to the default, so it is
        # still there if you switch back. It must not paint the button while
        # something else is selected.
        from api import live as api_live

        self.assertEqual(
            api_live.call_button_label({"call_button_mode": "default",
                                        "call_button_label": "Ring the booth"},
                                       "Francesca"),
            "Call the DJ")


class TestTheCallButtonSurvivesTheUpgrade(unittest.TestCase):
    """An upgrade may never change what a caller sees.

    `call_button_uses_name` became one option of `call_button_mode` in
    0.9.115. Without a migration every operator who had set either half of the
    old pair would have silently reverted to "Call the DJ" — and would have
    found out from somebody looking at the card, which is the same class of
    failure as a setting that does nothing.
    """

    def test_the_old_checkbox_becomes_the_name_option(self):
        import settings as settings_store

        self.assertEqual(
            settings_store._migrate({"call_button_uses_name": True})
            .get("call_button_mode"), "name")

    def test_an_old_typed_label_becomes_the_custom_option(self):
        import settings as settings_store

        self.assertEqual(
            settings_store._migrate({"call_button_label": "Ring the booth"})
            .get("call_button_mode"), "custom")

    def test_an_old_file_with_neither_set_stays_on_the_default(self):
        import settings as settings_store

        self.assertNotIn(
            "call_button_mode",
            settings_store._migrate({"call_button_uses_name": False,
                                     "call_button_label": ""}))

    def test_a_new_file_is_left_alone(self):
        # The legacy keys can survive in a file written before the upgrade.
        # Once the operator has chosen explicitly, that choice wins.
        import settings as settings_store

        self.assertEqual(
            settings_store._migrate({"call_button_mode": "default",
                                     "call_button_uses_name": True})
            .get("call_button_mode"), "default")


class TestTheStationsOwnColoursReachTheCard(unittest.TestCase):
    """"The station's own colours" is a translation, and it happens here.

    The station names its palette --bg / --ink / --accent; the widget names it
    --pine / --alpenglow / --coral, and neither is going to change — the
    station's names are what its own player is written against, and the
    widget's are what HOST-STYLE-GUIDE publishes for host pages. So there is
    one map, in one direction, and this is what stops it going stale.
    """

    THEMES = {
        "effective": "vinyl",
        "active": "signal",
        "themes": [
            {"id": "signal", "name": "Signal", "mode": "dark",
             "tokens": {"--bg": "#000000", "--accent": "#00ff00"}},
            {"id": "vinyl", "name": "Vinyl", "mode": "light",
             "tokens": {
                 "--bg": "#efe4cf", "--surface": "#f7efdf", "--ink": "#2a1a10",
                 "--muted": "#8a6f55", "--accent": "oklch(0.62 0.16 70)",
                 "--display-font": "instrument-serif",
             }},
        ],
    }

    def test_the_on_air_show_beats_the_station_default(self):
        # `effective` is the station's own answer to "what should a client
        # paint right now", and a show's own themeId outranks the station
        # picker while it is on air. Painting `active` would leave the call
        # card in the station's colours next to a player that had moved.
        from api import live as api_live

        self.assertEqual(api_live.station_palette(self.THEMES)["id"], "vinyl")

    def test_tokens_arrive_under_this_widgets_names(self):
        from api import live as api_live

        tokens = api_live.station_palette(self.THEMES)["tokens"]
        self.assertEqual(tokens["--pine"], "#efe4cf")
        self.assertEqual(tokens["--granite"], "#f7efdf")
        self.assertEqual(tokens["--alpenglow"], "#2a1a10")
        self.assertEqual(tokens["--sage"], "#8a6f55")
        self.assertEqual(tokens["--coral"], "oklch(0.62 0.16 70)")

    def test_nothing_the_widget_does_not_name_comes_through(self):
        # The station's set includes fonts. This widget ships no font files
        # and makes no third-party request for one, so a --display-font
        # arriving from a theme must not become a font-family the browser
        # then goes looking for.
        from api import live as api_live

        tokens = api_live.station_palette(self.THEMES)["tokens"]
        self.assertNotIn("--display-font", tokens)
        for name in tokens:
            self.assertIn(name, set(api_live._STATION_TOKENS.values()))

    def test_the_mode_comes_across(self):
        # It decides the handful of tokens the station has no counterpart for
        # — the green that means the line is open, the shadow — and what the
        # browser paints its own scrollbars in.
        from api import live as api_live

        self.assertEqual(api_live.station_palette(self.THEMES)["mode"], "light")

    def test_a_value_that_is_not_a_colour_is_dropped(self):
        # These are written straight into inline style on :root. A station is
        # trusted and can still be misconfigured, and a token that poisons
        # every embed's stylesheet is not a failure anyone traces to a theme.
        from api import live as api_live

        out = api_live.station_palette({
            "effective": "x",
            "themes": [{"id": "x", "mode": "dark", "tokens": {
                "--bg": "#101010",
                "--ink": "red; } body { display: none } .x {",
                "--accent": "<script>",
            }}],
        })
        self.assertEqual(out["tokens"], {"--pine": "#101010"})

    def test_a_station_that_says_nothing_useful_gets_no_palette(self):
        # The card still has to paint. Falling back to the neutral base is the
        # honest answer for a station that will not say what colour it is.
        from api import live as api_live

        self.assertIsNone(api_live.station_palette({}))
        self.assertIsNone(api_live.station_palette({"effective": "gone", "themes": []}))
        self.assertIsNone(api_live.station_palette(
            {"effective": "x", "themes": [{"id": "x", "tokens": {}}]}))

    def test_the_toggle_stays_while_the_station_is_painting(self):
        # It used to be dropped, because the palette's inline tokens outrank
        # every data-theme rule and the toggle would visibly do nothing. The
        # operator who chose station colours reported the toggle "not
        # surfacing" as a bug — so the widget now clears the inline tokens on
        # toggle (shared.js) and the control works instead of being hidden.
        from api import live as api_live

        cfg = {"show_theme_toggle": True, "embed_theme_toggle": True,
               "widget_theme": "station"}
        self.assertTrue(api_live.corner_controls(cfg)["theme"])
        self.assertTrue(api_live.corner_controls(cfg, embed=True)["theme"])
        # The widget's half of the bargain: the toggle must clear the inline
        # tokens, or it is back to flipping an attribute nothing responds to.
        shared = (REPO / "web-widget" / "shared.js").read_text(encoding="utf-8")
        self.assertIn("removeProperty", shared)
        # And the poll must not re-apply the palette over an explicit
        # choice — the viewer's stored pick (including 'station' itself, a
        # viewer option since the cycle) is applied first and returns.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        fn = call_js.split("function applyConfiguredTheme")[1][:700]
        self.assertIn("localStorage.getItem('callinTheme')", fn)
        self.assertIn("applyThemeChoice(stored)", fn)


class TestSoundPacks(unittest.TestCase):
    """Bundled sound assets: a pack is a folder, not a code change.

    The tier that did not exist before — uploads worked and synthesis worked,
    so shipping a default ring meant writing oscillator code in the widget.
    """

    def setUp(self):
        import sounds

        self.sounds = sounds
        self._real_dir = sounds.ASSETS_DIR
        self.tmp = Path(tempfile.mkdtemp())
        sounds.ASSETS_DIR = self.tmp

    def tearDown(self):
        self.sounds.ASSETS_DIR = self._real_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pack(self, name: str, files=(), label: str | None = None) -> Path:
        folder = self.tmp / name
        folder.mkdir(parents=True, exist_ok=True)
        for f in files:
            (folder / f).write_bytes(b"not really audio")
        if label:
            (folder / "label.txt").write_text(label, encoding="utf-8")
        return folder

    def test_with_no_assets_at_all_nothing_changes(self):
        # The product has always worked with zero audio files and must keep
        # doing so: every sound resolves to "", which the widget synthesizes.
        self.assertEqual(self.sounds.asset_url("classic", "ring"), "")
        self.assertEqual(self.sounds.assets_for("classic"), {})
        self.assertEqual(
            sorted(p for p, _ in self.sounds.packs()), ["classic", "phone"])

    def test_a_new_folder_becomes_a_new_pack(self):
        self._pack("vintage", ["ring.mp3"])
        self.assertIn(("vintage", "Vintage"), self.sounds.packs())
        self.assertEqual(
            self.sounds.asset_url("vintage", "ring"), "/pack-sounds/vintage/ring.mp3")

    def test_a_pack_can_name_itself(self):
        self._pack("old-bell", ["ring.mp3"], label="Old Bell — 1950s exchange")
        self.assertIn(("old-bell", "Old Bell — 1950s exchange"), self.sounds.packs())

    def test_a_folder_name_without_a_label_is_tidied(self):
        self._pack("old-bell", ["ring.mp3"])
        self.assertIn(("old-bell", "Old Bell"), self.sounds.packs())

    def test_a_partial_pack_only_covers_what_it_ships(self):
        # One file is a valid pack; everything else stays synthesized.
        self._pack("sparse", ["ring.mp3"])
        self.assertEqual(self.sounds.assets_for("sparse"), {"ring": "/pack-sounds/sparse/ring.mp3"})
        self.assertEqual(self.sounds.asset_url("sparse", "hangup"), "")

    def test_a_folder_named_after_a_builtin_supplies_files_for_it(self):
        # Not a new pack — the curated label is kept and only the one sound
        # is replaced.
        self._pack("classic", ["ring.mp3"])
        ids = [p for p, _ in self.sounds.packs()]
        self.assertEqual(ids.count("classic"), 1)
        self.assertIn(("classic", self.sounds.SYNTHESIZED["classic"]), self.sounds.packs())
        self.assertEqual(self.sounds.asset_url("classic", "ring"), "/pack-sounds/classic/ring.mp3")
        self.assertEqual(self.sounds.asset_url("classic", "pickup"), "")

    def test_mp3_wins_when_a_pack_ships_several_encodings(self):
        # Every browser plays mp3; ogg and friends are less reliable.
        self._pack("multi", ["ring.ogg", "ring.mp3", "ring.wav"])
        self.assertEqual(self.sounds.asset_url("multi", "ring"), "/pack-sounds/multi/ring.mp3")

    def test_a_pack_name_cannot_escape_the_assets_directory(self):
        for evil in ("../../etc", "..", "a/b", ""):
            with self.subTest(pack=evil):
                self.assertIsNone(self.sounds.file_for(evil, "ring"))

    def test_only_the_five_known_sounds_resolve(self):
        self._pack("vintage", ["ring.mp3", "voicemail.mp3"])
        self.assertIsNone(self.sounds.file_for("vintage", "voicemail"))

    def test_the_panel_dropdown_reads_packs_from_disk(self):
        # settings.schema_payload is what the panel builds its Sound set
        # dropdown from — a new folder has to reach it with no code change.
        self._pack("vintage", ["ring.mp3"])
        choices = settings_store.schema_payload()["fields"]["sound_pack"]["choices"]
        self.assertIn(["vintage", "Vintage"], [list(c) for c in choices])


class TestSigningInClimbsTheTier(_TempStores):
    """A caller on an open line can hold a guest code or the admin password to
    unlock the commands the operator gated above `anyone`. The server resolves
    the tier from X-Call-Key on /live and says whether there is a tier left to
    climb to (`signinAvailable`) — the chip only appears when signing in would
    actually change something."""

    def setUp(self):
        super().setUp()
        # admin_auth keeps its store path in a module global read once from the
        # env — point it at THIS test's temp dir so the guest/admin codes one
        # test sets can't leak into the next (the store is otherwise shared for
        # the whole run, and "no codes set" then reads whatever ran before).
        import admin_auth
        from pathlib import Path
        self._old_auth = admin_auth.AUTH_PATH
        admin_auth.AUTH_PATH = Path(self._tmp.name) / "admin-auth.json"

    def tearDown(self):
        import admin_auth
        admin_auth.AUTH_PATH = self._old_auth
        super().tearDown()

    def _live_for(self, key=""):
        from api.live import _for_this_caller

        headers = {"X-Call-Key": key} if key else {}
        req = types.SimpleNamespace(headers=headers, remote="9.9.9.9")
        payload = {
            "canAsk": {"allow_announcements": False},
            "askTiers": {"allow_announcements": "guest", "allow_requests": "open"},
        }
        return _for_this_caller(req, payload)

    def test_no_higher_tier_no_offer(self):
        # No codes set: signing in could reach nothing, so it is never offered.
        out = self._live_for()
        self.assertFalse(out["signinAvailable"])
        self.assertEqual(out["callerTier"], "open")

    def test_a_stranger_is_offered_the_climb_and_a_code_makes_it(self):
        import admin_auth
        import settings as settings_store

        admin_auth.set_guest_password("guest99")
        # The admin-only default (0.10.80) has no guest lane to climb into —
        # this test is about the climb, so open the guest door.
        settings_store.save({"front_access": "guest"})
        # A stranger sees the offer and cannot announce...
        stranger = self._live_for()
        self.assertTrue(stranger["signinAvailable"])
        self.assertFalse(stranger["canAsk"]["allow_announcements"])
        # ...the guest code climbs them and unlocks it.
        guest = self._live_for("guest99")
        self.assertEqual(guest["callerTier"], "guest")
        self.assertTrue(guest["canAsk"]["allow_announcements"])

    def test_the_top_tier_is_never_offered_the_climb(self):
        import admin_auth

        from api import auth as api_auth
        admin_auth.set_password("adminpass123")
        # caller_tier reads the admin key; an admin has nowhere to climb.
        out = self._live_for("adminpass123")
        self.assertEqual(out["callerTier"], "admin")
        self.assertFalse(out["signinAvailable"])

    def test_an_open_line_offers_no_guest_climb(self):
        # 0.10.66 made the doors one choice apiece: on an open line the code
        # does not elevate, so the chip must not offer that climb — while the
        # admin password stays a door in every mode, so setting one restores
        # the offer.
        import admin_auth

        admin_auth.set_guest_password("guest99")
        settings_store.save({"front_access": "open"})
        self.assertFalse(self._live_for()["signinAvailable"])
        self.assertEqual(self._live_for("guest99")["callerTier"], "open")
        admin_auth.set_password("adminpass123")
        self.assertTrue(self._live_for()["signinAvailable"])


class TestTheFinderIsNotAUsernameField(unittest.TestCase):
    """A password manager paired the panel's password box with the masthead
    finder as its "username" and autofilled it — which engaged the search
    results view and read as the page chips being broken (operator-reported,
    0.10.68). Two defences, both pinned: the vendor opt-out attributes in the
    markup, and the discard of any fill that arrives without focus."""

    def test_the_markup_asks_every_manager_to_skip_it(self):
        html = (REPO / "web-widget" / "panel.html").read_text(encoding="utf-8")
        i = html.index('id="settingsSearch"')
        tag = html[html.rindex("<input", 0, i):html.index("/>", i) + 2]
        for attr in ("data-1p-ignore", "data-bwignore",
                     'data-lpignore="true"', 'data-form-type="other"',
                     'autocomplete="off"'):
            self.assertIn(attr, tag, f"the finder lost {attr}")

    def test_an_unfocused_fill_is_discarded(self):
        js = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        start = js.index("bindSettingsSearch")
        self.assertIn("document.activeElement !== box", js[start:start + 4500],
                      "the finder must discard a fill that arrives without "
                      "focus — autofill does not listen to attributes alone")


class TestTheCallPageOffersFirstRunSetup(_TempStores):
    """Until an admin password exists the whole box is open to whoever walks
    up — so the FIRST page anyone reaches says it and takes one (operator's
    ask, 0.10.72 era). /live carries needsSetup per-request; the banner never
    shows in an embed, and the flag goes false forever once a hash exists."""

    def setUp(self):
        super().setUp()
        import admin_auth
        from pathlib import Path
        self._old_auth = admin_auth.AUTH_PATH
        admin_auth.AUTH_PATH = Path(self._tmp.name) / "admin-auth.json"
        self._old_key = os.environ.pop("CALLIN_ADMIN_KEY", None)

    def tearDown(self):
        import admin_auth
        admin_auth.AUTH_PATH = self._old_auth
        if self._old_key is not None:
            os.environ["CALLIN_ADMIN_KEY"] = self._old_key
        super().tearDown()

    def _live(self):
        from api.live import _for_this_caller

        req = types.SimpleNamespace(headers={}, remote="9.9.9.9")
        return _for_this_caller(req, {"canAsk": {}, "askTiers": {}})

    def test_an_unconfigured_box_asks_and_a_configured_one_never_does(self):
        import admin_auth

        self.assertTrue(self._live()["needsSetup"])
        admin_auth.set_password("hunter2hunter2")
        self.assertFalse(self._live()["needsSetup"])

    def test_the_env_break_glass_counts_as_configured(self):
        # ADMIN_KEY is read from the environment once, at boot — which is
        # when an operator sets it — so the test patches the module constant
        # rather than pretending the env can change under a running process.
        from api import auth as api_auth

        old = api_auth.ADMIN_KEY
        api_auth.ADMIN_KEY = "operator-break-glass"
        try:
            self.assertFalse(self._live()["needsSetup"])
        finally:
            api_auth.ADMIN_KEY = old

    def test_the_banner_exists_and_stays_out_of_embeds(self):
        html = (REPO / "web-widget" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="setupNudge"', html)
        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("!framed && !!(d && d.needsSetup)", js,
                      "the setup ask must be gated out of embeds — a host "
                      "page's visitors are not the operator")


class TestNoStyleUsesAnUndefinedToken(unittest.TestCase):
    """The chat text box shipped invisible because its CSS used tokens that do
    not exist (--field, --ink, --soft-border): `var(--field)` with no --field
    defined resolves to nothing, so the input rendered transparent and
    borderless on a transparent card. A whole feature was unusable and green.

    This reads style.css the way a browser would: every `var(--x)` must name a
    custom property that is DEFINED somewhere in the sheet. It would have
    failed the moment that input was written."""

    def test_every_var_reference_is_defined(self):
        import re

        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css, re.IGNORECASE))
        used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", css, re.IGNORECASE))
        # A var() may carry its own fallback — `var(--x, #fff)` — which is
        # legitimately defined-or-fallback; strip those from the requirement.
        with_fallback = set(re.findall(
            r"var\(\s*(--[a-z0-9-]+)\s*,", css, re.IGNORECASE))
        missing = sorted((used - defined) - with_fallback)
        self.assertEqual(
            missing, [],
            "these CSS custom properties are used via var() but defined "
            f"nowhere in style.css — they render as nothing: {missing}")


class TestTheCardIsOnlyEverInOneMode(unittest.TestCase):
    """Opening the text line used to leave the call's meters, its push-to-talk
    bar and the Call/Message doors all on screen under the input row: the card
    grew to ~525px and the place to type was buried among controls for a call
    nobody was on, with the input itself invisible (it inherited .rig's
    reserved-hidden visibility). All operator-reported. The card now carries a
    data-mode and the stylesheet shows only that mode's controls. These pin the
    pieces that made the regression possible."""

    @classmethod
    def setUpClass(cls):
        cls.css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        cls.call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        cls.index = (REPO / "web-widget" / "index.html").read_text(encoding="utf-8")

    def test_chat_mode_hides_every_call_control(self):
        import re

        # The bands that made the card huge in chat: if any stops being
        # hidden in chat mode, the input is back among controls for a call.
        # .staterow is not one of them any more — the chips moved into the
        # identity row, which chat mode keeps.
        for band in (".meters", ".talkrow", ".actionrow"):
            self.assertRegex(
                self.css,
                r'\.card\[data-mode="chat"\][^{]*' + re.escape(band),
                f"chat mode no longer hides {band} — the text line will show "
                "the call's controls again")

    def test_voicemail_keeps_the_bar_but_drops_speaker_and_mute(self):
        import re

        # Voicemail is push-to-talk (operator's ask), so the talk bar stays —
        # hiding the whole row left a "MIC OFF" card with nothing to hold. What
        # a recording does NOT need is Speaker and Mute (no DJ audio to route,
        # the bar is already the mic), and the DJ meter and volume (nothing is
        # playing back). Those are hidden; the row and the caller's own YOU
        # meter remain.
        self.assertNotRegex(
            self.css,
            r'\.card\[data-mode="voicemail"\]\s+\.talkrow\s*\{[^}]*display:\s*none',
            "voicemail must NOT hide the whole talk bar — that is the MIC-OFF "
            "regression with no control to hold")
        for hidden in ("#spkBtn", "#muteBtn", ".meters .dj", ".meters .vol"):
            self.assertRegex(
                self.css,
                r'\.card\[data-mode="voicemail"\][^}]*' + re.escape(hidden),
                f"voicemail should hide {hidden} — it has no use on a recording")

    def test_the_chat_input_declares_its_own_visibility(self):
        # The input lives in .rig, which is reserved-hidden between calls; the
        # text line is the ONE control meaningful off a call, so its row must
        # override that or it ships present-but-invisible — the exact bug.
        self.assertRegex(
            self.css,
            r'\.rig\s*>\s*\.chatrow\s*\{[^}]*visibility:\s*visible',
            "the chat input row no longer overrides .rig's hidden visibility — "
            "the text box will be invisible again")

    def test_each_state_switches_the_mode(self):
        # One switch drives the CSS: openChat -> chat, startCall -> call or
        # voicemail, and the idle paths back to idle. Miss one and that state
        # keeps the previous mode's controls.
        self.assertIn("setCardMode('chat')", self.call_js)
        self.assertIn("setCardMode(asVoicemail ? 'voicemail' : 'call')", self.call_js)
        self.assertIn("setCardMode('idle')", self.call_js)

    def test_the_card_starts_in_idle_mode(self):
        # First paint has no JS-set mode yet; the markup declares idle so the
        # chat/voicemail hide-rules have something to key against from load.
        self.assertRegex(self.index, r'data-mode="idle"')

    def test_one_footprint_whatever_the_entrance(self):
        # A caller who calls, leaves a message or texts meets the SAME object at
        # the same size — an embed must not jar or resize between modes. The
        # working area (.rig) is a flex column with a consistent min-height, and
        # the transcript flexes to fill it, so chat's fewer control bands become
        # more transcript rather than a shorter card.
        self.assertRegex(
            self.css,
            r'\.rig \{[^}]*display:\s*flex[^}]*flex-direction:\s*column',
            "the rig must be a flex column so the transcript can flex to fill")
        # This used to also require a min-height on the working area. The
        # player redesign buys the footprint at the card instead — fixed size,
        # every band flex:none, one elastic box — so a reserved height on the
        # rig is a second answer to the same question.
        self.assertRegex(
            self.css, r'\n  \.linebox \{[^}]*flex:\s*1',
            "the line box must absorb the working area's spare room")
        self.assertRegex(
            self.css, r'\n  \.linebox \{[^}]*min-height:\s*0',
            "without min-height:0 a flex child refuses to shrink below its "
            "content and the card grows after all")

    def test_a_refused_call_returns_the_card_to_idle(self):
        # The card flips to .oncall + Hang up the instant Call/Voicemail is
        # pressed (no ringing phase), so a 429/401 refusal MUST undo that or the
        # card sits on Hang up over an engaged-tone message (tester-caught).
        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        refusal = js.split("res.status === 429 || res.status === 401", 1)[1][:1400]
        self.assertIn("classList.remove('oncall')", refusal)
        self.assertIn("setCardMode('idle')", refusal)
        self.assertIn("hangBtn.hidden = true", refusal)


class TestEveryDoorReadsForItself(unittest.TestCase):
    """The single button_style (words / emoji / both for the whole row) could
    not say "Call worded, the two secondary doors as bare icons", which is what
    a tight embed wants. It is six switches now — a word tick and an icon tick
    per feature — carried on /live, with the widget falling back to the word if
    a door is left with neither so a button is never blank. The icon is a line
    drawing in the button's own ink, not an emoji glyph the theme cannot
    touch."""

    def test_live_carries_each_doors_two_switches(self):
        from api import live as api_live

        payload = api_live.look_payload({
            "call_show_words": True, "call_show_emoji": False,
            "vm_show_words": False, "vm_show_emoji": True,
            "chat_show_words": True, "chat_show_emoji": True,
        })
        self.assertTrue(payload["callShowWords"])
        self.assertFalse(payload["callShowEmoji"])
        self.assertFalse(payload["vmShowWords"])
        self.assertTrue(payload["vmShowEmoji"])
        self.assertTrue(payload["chatShowWords"])
        self.assertTrue(payload["chatShowEmoji"])

    def test_words_are_on_by_default_for_every_door(self):
        # The 0.10.80 default reading (operator's fresh-install review):
        # Call keeps its word — the card's one promise — and the two
        # secondary doors sit beside it as drawn icons.
        self.assertTrue(settings_store.FIELDS["call_show_words"][1])
        self.assertFalse(settings_store.FIELDS["call_show_emoji"][1])
        for f in ("vm_show_words", "chat_show_words"):
            self.assertFalse(settings_store.FIELDS[f][1], f"{f} should default off")
        for f in ("vm_show_emoji", "chat_show_emoji"):
            self.assertTrue(settings_store.FIELDS[f][1], f"{f} should default on")

    def test_a_door_left_with_neither_falls_back_to_its_word(self):
        # showParts is the widget's guard: both switches off must not blank the
        # button. An embed that turned both off should still be usable.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("if (!words && !emoji) words = true", call_js)

    def test_the_icon_is_a_currentColor_line_drawing(self):
        # An emoji glyph is a colour block the theme cannot touch (a yellow
        # phone on a slate card); the icon inherits the button's ink instead.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn('stroke="currentColor"', call_js)
        self.assertIn("BTN_ICONS", call_js)

    def test_the_old_single_button_style_is_fully_gone(self):
        # A half-migration — the select removed but the field or the /live key
        # left behind — is how a setting ends up reachable from nowhere. None
        # of the three may mention it.
        self.assertNotIn("button_style", settings_store.FIELDS)
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertNotIn("buttonStyle", call_js)
        panel_html = (REPO / "web-widget" / "panel.html").read_text(encoding="utf-8")
        self.assertNotIn('id="button_style"', panel_html)


class TestTheDoorsShareTheRowEvenly(unittest.TestCase):
    """The idle doors size to their CONTENT: a WORDED door takes the slack so
    its label fits, an ICON-ONLY door hugs its glyph and gives that room up.
    Equal thirds was wrong when the doors differ — "Call Danny Boy" clipped
    while two bare icons sat at the same width (operator-reported from an
    embed). A hidden door still gives up its width entirely (the :not([hidden])
    guard), which is what kept the row honest in the first place."""

    @classmethod
    def setUpClass(cls):
        cls.css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")

    def test_worded_doors_take_the_slack(self):
        # flex-basis auto + grow: a worded door widens to fit its label.
        self.assertRegex(
            self.css,
            r'\.actionrow #callBtn:not\(\.ringing\)[^{]*\{[^}]*flex:\s*1 1 auto',
            "a worded door must grow to fit its label")

    def test_an_icon_only_door_hugs_its_icon(self):
        # An icon-only door does not grow — it must not eat the space a worded
        # door beside it needs.
        self.assertRegex(
            self.css,
            r'\.actionrow #callBtn\.icononly[^{]*\{[^}]*flex:\s*0 1 auto',
            "an icon-only door must hug its icon (flex-grow 0)")

    def test_a_hidden_door_gives_up_its_width(self):
        # The display rule must be guarded so a hidden door is display:none,
        # not a full-width invisible box stealing the row.
        self.assertIn("#callBtn:not([hidden])", self.css)
        self.assertIn("#vmBtn:not([hidden])", self.css)


class TestTheLockedPanelShowsNothingButTheGate(unittest.TestCase):
    """A password-protected /settings used to render the whole DASHBOARD — The
    Line on/off, the call / voicemail / text switches, what's on air — to anyone
    who could reach the URL, before they logged in. That is station state a
    stranger has no business reading. The panel starts LOCKED and, while locked,
    shows nothing but the way-back bar and the login gate."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "web-widget" / "panel.html").read_text(encoding="utf-8")
        cls.css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        cls.js = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")

    def test_the_panel_starts_locked(self):
        # Locked from load so a protected panel never flashes its dashboard
        # before the 401 lands.
        self.assertRegex(self.html, r'id="panel"[^>]*class="[^"]*\blocked\b'
                                    r'|class="[^"]*\blocked\b[^"]*"[^>]*id="panel"')

    def test_locked_hides_everything_but_the_bar_and_the_gate(self):
        # The comprehensive rule — hides every direct child except the panelbar
        # and the gate — so a new section can't leak by being forgotten.
        self.assertRegex(
            self.css,
            r'#panel\.locked\s*>\s*\*:not\(\.panelbar\):not\(#loginGate\)'
            r'\s*\{[^}]*display:\s*none',
            "the locked panel must hide all children but the bar and the gate")

    def test_a_real_payload_drops_the_curtain(self):
        # loadSettings success removes .locked — otherwise an already-authed
        # operator's panel would stay behind the starts-locked curtain forever.
        self.assertIn("classList.remove('locked')", self.js)


class TestTheTextLineIsShapedForTyping(unittest.TestCase):
    """The input box gets its own full row with Close/Send beneath, not the
    old [Close][input][Send] where the box was squeezed into the middle third
    and the two buttons towered over the message (operator-reported: "the input
    box should be a full row, those buttons are too large"). And the terminal
    control is on the card from the instant a call/voicemail is initiated — no
    button phasing through Ringing -> Answering while the caller waits."""

    @classmethod
    def setUpClass(cls):
        cls.css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        cls.js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")

    def test_the_input_takes_its_own_row(self):
        # 100% basis on the input is what drops the buttons to the next line.
        self.assertRegex(
            self.css, r'\.chatrow input\s*\{[^}]*flex:\s*1 1 100%',
            "the chat input must span the row (flex-basis 100%) so Close/Send "
            "fall beneath it rather than squeezing it into the middle")

    def test_close_and_send_are_the_one_control_height(self):
        # One height for every button and input on a surface. The card used to
        # carry four (44 / 52 / 31 / 26), which is what made a row of controls
        # read as three unrelated things.
        self.assertRegex(
            self.css, r'\.chatrow button\s*\{[^}]*height:\s*var\(--control-h\)')

    def test_the_end_control_is_there_from_the_press(self):
        # startCall shows Hang up / End message and flips to .oncall BEFORE it
        # awaits the token — the state chip carries "Reaching the booth", so
        # the button never has to phase through Ringing -> Answering.
        start = self.js.split("async function startCall(", 1)[1]
        start = start.split("await fetch('/token'", 1)[0]
        self.assertIn("hangBtn.hidden = false", start)
        self.assertIn("'oncall'", start)
        self.assertIn("End message", start)   # voicemail's terminal label


class TestTheAskMenuOffersEveryPermission(unittest.TestCase):
    """The "What can I ask?" popup is built from ASKS in shared.js, and a
    caller permission with no example there is a capability the operator
    switched on that no caller is ever told about — takeover and skip both
    shipped that way once (the NEVER list even claimed a caller could never
    skip, three sections below the switch that lets them). This pins the menu
    to the permission set: every TIERED permission a caller can hold must
    have at least one ASKS entry, so a new gated tool cannot ship invisible."""

    def test_every_tiered_permission_has_an_ask(self):
        import re

        src = (REPO / "web-widget" / "shared.js").read_text(encoding="utf-8")
        needs = set(re.findall(r"need:\s*'([a-z_]+)'", src))
        for perm in settings_store.TIERED_PERMISSIONS:
            # allow_chat is the MODE (the surface itself), not something a
            # caller asks the DJ to do — it has no ask, by design.
            if perm == "allow_chat":
                continue
            self.assertIn(
                perm, needs,
                f"{perm} is a caller permission with no example in the "
                '"What can I ask?" menu — a capability nobody is told about')


class TestPushToTalkIsPerSurfaceAndOnByDefault(unittest.TestCase):
    """The bar is the caller's microphone, and whether it exists is the
    operator's per-surface answer, carried on /live like the corner controls.
    On by default since 0.10.9 — an open mic feeds the DJ the caller's whole
    room, and a beta tester's fresh install read the old default as broken
    (mic hot from pickup, a spacebar that did nothing). Open-mic is the
    opt-out now, per surface."""

    def test_defaults_on_on_both_surfaces(self):
        # The declared default itself, not a hand-built cfg — look_payload only
        # reflects what it is handed, so handing it {} would test nothing.
        self.assertTrue(settings_store.FIELDS["show_push_to_talk"][1])
        self.assertTrue(settings_store.FIELDS["embed_push_to_talk"][1])

    def test_each_surface_is_answered_separately(self):
        from api import live as api_live

        payload = api_live.look_payload(
            {"show_push_to_talk": True, "embed_push_to_talk": False})
        self.assertTrue(payload["ptt"])
        self.assertFalse(payload["embedPtt"])

    def test_the_widget_reads_both_keys(self):
        # The same drift trap as the corner controls: a key renamed on one
        # side silently loses the feature rather than raising anything.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("d.embedPtt : d.ptt", call_js)

    def test_the_mic_starts_closed_on_a_ptt_call(self):
        # Enabled first (that is the permission prompt and the track), then
        # closed straight away — a caller on a push-to-talk line whose mic
        # opens hot has been betrayed by the one promise the bar makes.
        # UNLESS they already pressed the bar during the ring: a latch made
        # early is a decision, and the post-connect close stomping it is how
        # a lit bar ended up muted on a real call.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        start = call_js.split("await room.connect(url, token);")[1][:1200]
        self.assertIn("setMicrophoneEnabled(true)", start)
        self.assertIn("setMicOpen(false)", start)
        self.assertIn("!pttOpen", start)

    def test_mic_switches_are_serialized_and_verified(self):
        # Concurrent setMicrophoneEnabled calls resolve in whatever order the
        # SDK pleases; the reported failure was a lit bar over a muted mic.
        # One queue driving toward the LATEST intent, and a reconcile that
        # tells the CALLER when the hardware still disagrees.
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("micOp = micOp.then", call_js)
        self.assertIn("pub.isMuted", call_js)
        self.assertIn("tap the bar again", call_js)

    def test_the_quiet_caller_nudge_knows_about_the_bar(self):
        # "Check your microphone" to somebody deliberately holding it closed
        # reads as the DJ not knowing its own phone.
        source = (AGENT_WORKER / "call" / "lifecycle.py").read_text(encoding="utf-8")
        self.assertIn("push to talk", source)
        self.assertIn("show_push_to_talk", source)

    def test_the_hold_bar_owns_its_touch_on_mobile(self):
        # A held touch on mobile only stayed engaged ~1s: `touch-action:
        # manipulation` let the browser claim the gesture for scrolling and
        # fire pointercancel mid-hold, and the release handler shut the mic.
        # The bar must take the WHOLE gesture (touch-action:none) and block the
        # long-press callout that cancels the pointer the same way.
        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        pttbar = css.split(".card .pttbar {", 1)[1].split("}")[0]
        self.assertIn("touch-action: none", pttbar)
        self.assertNotIn("touch-action: manipulation", pttbar)
        self.assertIn("-webkit-touch-callout: none", pttbar)
        # And the context menu (the other way a long press cancels the hold) is
        # swallowed on the bar.
        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("'contextmenu'", js)


class TestAHostThemeIsADefaultNotADecree(unittest.TestCase):
    """The operator embedded the widget on their own station page with
    data-theme="dark" and then reported the theme toggle missing: pinning the
    starting look and confiscating the control were one lever. They are two
    now — data-theme seeds the widget, data-lock-theme is the decree."""

    @classmethod
    def setUpClass(cls):
        cls.embed = (REPO / "web-widget" / "embed.js").read_text(encoding="utf-8")
        cls.shared = (REPO / "web-widget" / "shared.js").read_text(encoding="utf-8")

    def test_embed_sends_the_soft_param_by_default(self):
        self.assertIn('"&themeDefault="', self.embed)
        self.assertIn('data-lock-theme', self.embed)

    def test_only_a_real_force_hides_the_toggle(self):
        # themeForcedByHost keys on ?theme= alone; a default must leave the
        # toggle wired up.
        self.assertIn("themeForcedByHost = !!params.get('theme')", self.shared)
        self.assertIn("|| themeDefault", self.shared)

    def test_the_dj_show_line_cannot_dress_the_ticker(self):
        # "show" is both the DJ-show line's class and the ticker's visibility
        # class. The bare `.show` selector dressed every lit transcript line
        # as a bold uppercase micro-label — seen on a real embed. Scoped now,
        # and this pins it scoped.
        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        import re

        bare = [m for m in re.finditer(r"(?m)^\s*\.show\s*[,{]", css)]
        self.assertEqual([], bare,
                         "a bare .show selector will restyle the lit ticker")


class TestTheWidgetActuallyParses(unittest.TestCase):
    """Every .js in web-widget/, syntax-checked for real.

    0.9.128 shipped an unescaped apostrophe in one string literal in call.js.
    The whole IIFE died, every embed froze at "Checking…" with no height
    reporting, and 582 tests stayed green while it happened — the contract
    test READS these files but nothing ever PARSED one. `node --check` is
    that parse. CI's runner has node, so no broken widget reaches an image;
    locally the test skips if node is missing, and the talkwave-verify skill
    is the local backstop: load both pages, read the console.
    """

    def test_every_widget_script_parses(self):
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed here — CI enforces this check")
        for path in sorted((REPO / "web-widget").glob("*.js")):
            with self.subTest(script=path.name):
                proc = subprocess.run(
                    [node, "--check", str(path)],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(
                    0, proc.returncode,
                    f"{path.name} does not parse:\n{proc.stderr.strip()}",
                )


class TestThePaletteTravelsForTheCycle(unittest.TestCase):
    """The theme control cycles light / dark / station colours / match the
    page, and the station stop only exists when /live carries the palette.
    It used to be resolved only when the OPERATOR chose station colours;
    re-gating it that way would silently drop the viewer's third stop on
    every deployment where the operator picked something else."""

    def test_the_palette_is_not_gated_on_the_operators_choice(self):
        live_py = (AGENT_WORKER / "api" / "live.py").read_text(encoding="utf-8")
        i = live_py.index("station_palette(await station.themes())")
        setup = live_py[:i].rsplit("palette = None", 1)[-1]
        self.assertNotIn("widget_theme", setup,
                         "palette resolution re-gated on the colour setting")

    def test_the_widget_only_offers_the_stop_when_it_exists(self):
        js = widget_js()["call.js"]
        options = js[js.index("function themeOptions"):]
        options = options[:options.index("\n  }")]
        self.assertIn("stationTheme", options)
        self.assertIn("tokens", options)


class TestAVoicemailOnlyLineHasOneDoor(unittest.TestCase):
    """The operator watched both failures on their live page: a voicemail-
    only card still offering "Call Francesca" (which can only ring out into
    a refusal), and that refusal's cleanup restoring the Call button by hand
    while forgetting the message button — one failed call left the card
    without its one working door until a reload."""

    @classmethod
    def setUpClass(cls):
        cls.js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")

    def test_the_idle_buttons_paint_from_one_place(self):
        self.assertIn("function paintIdleButtons", self.js)
        # The poll and the refusal path both use it — the refusal path
        # hand-restoring buttons is the exact bug.
        self.assertIn("if (!room) paintIdleButtons(d);", self.js)
        self.assertIn("paintIdleButtons(live || {})", self.js)

    def test_voicemail_only_hides_the_call_button(self):
        branch = self.js.split("vmOnly && vmButton")[1][:300]
        self.assertIn("callBtn.hidden = true", branch)

    def test_a_refused_call_resets_the_voicemail_flag(self):
        refusal = self.js.split("res.status === 429 || res.status === 401")[1]
        self.assertIn("vmCall = false", refusal[:1600])


class TestTheKillSwitchOutranksEveryDoor(unittest.TestCase):
    """The operator drew the hierarchy out loud: THE LINE is the master, and
    the two transmission modes hang off it. Paused, the card offers nothing
    — not even the machine — and says why in a sentence; the panel greys the
    two mode cards; both modes off is the same closed line reached the other
    way. The server half (a paused line refusing the voicemail mint) is
    pinned in test_voicemail."""

    @classmethod
    def setUpClass(cls):
        js = widget_js()
        cls.js = js["call.js"]
        cls.panel = js["panel.js"]

    def test_a_paused_or_all_off_line_paints_closed(self):
        # Anchored inside paintIdleButtons: a bare split matched the
        # DECLARATION (`let lineClosedNow = false`) instead of the
        # assignment, which this test only got to say once it was actually
        # registered in test_sidecar.
        painter = self.js.split("function paintIdleButtons")[1]
        closed = painter.split("lineClosedNow = ")[1][:140]
        self.assertIn("d.callsPaused", closed)
        self.assertIn("d.liveCalls === false && !machineOn", closed)
        branch = painter.split("if (lineClosedNow) {")[1][:500]
        self.assertIn("callBtn.disabled = true", branch)

    def test_the_closed_card_says_why_in_a_sentence(self):
        # "Line closed" alone left callers wondering whose fault it was.
        self.assertIn("The booth isn't taking calls at the moment", self.js)

    def test_a_paused_line_never_offers_the_machine(self):
        # Both idle painters: the on-air card's buttons and the off-air card.
        self.assertIn("machineOn && !lineClosedNow", self.js)
        off_air = self.js.split("function paintOffAir")[1]
        off_air = off_air[:off_air.index("function paintIdleButtons")]
        self.assertIn("!paused", off_air)

    def test_the_panel_greys_the_mode_cards_while_paused(self):
        self.assertIn("$(id).disabled = paused", self.panel)

    def test_the_card_section_carries_the_line_override_note(self):
        # The preview is a real card, so a paused line previews as closed —
        # the note is what stops that reading as the preview being broken.
        self.assertIn("previewLineNote", self.panel)


class TestTheLauncherIsAPhoneInThePocket(unittest.TestCase):
    """data-mode="launcher": a pill that opens the widget in a fixed panel.
    Two promises worth pinning: collapsing hides and never unmounts —
    tearing the frame down would hang up a live call — and the snippet
    builder writes its data- attributes on the DIV, where embed.js actually
    reads them (they shipped on the script tag once, silently doing
    nothing)."""

    def test_collapse_hides_and_never_unmounts(self):
        js = (REPO / "web-widget" / "embed.js").read_text(encoding="utf-8")
        self.assertIn("data-mode", js)
        close_fn = js.split("function close()")[1][:500]
        self.assertIn('display = "none"', close_fn)
        self.assertNotIn("remove()", close_fn)
        self.assertNotIn("iframe = null", close_fn)

    def test_the_snippet_attrs_land_on_the_div(self):
        pjs = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        snippet = pjs.split("function paintEmbedSnippet")[1][:1200]
        self.assertIn("'<div id=\"subwave-callin\"' + attrs", snippet)


class TestTheEmbedIsJustTheCard(unittest.TestCase):
    """The 10px inset showed as a white ring on any host whose color-scheme
    the browser decided disagreed with ours — the frame's backdrop paints
    opaque and the inset frames the card in it. Edge to edge, square, the
    card IS the frame."""

    @classmethod
    def setUpClass(cls):
        cls.css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")

    def test_no_inset_and_no_rounded_corners_in_a_frame(self):
        # Read the RULE, not a fixed slice of characters after it — a slice
        # measures comment length, so writing down why a declaration is there
        # could push the declaration out of the window and fail the test.
        block = self.css.split("body.compact {")[1].split("}")[0]
        self.assertIn("padding: 0", block)
        card = self.css.split("body.compact .card {")[1].split("}")[0]
        self.assertIn("border-radius: 0", card)

    def test_the_overlay_offset_carries_no_dead_inset(self):
        self.assertIn("body.overlay-up { padding-top: var(--overlay-px, 0px); }",
                      self.css)


class TestTheEffectHasADial(unittest.TestCase):
    """voice_effect_level, 0-100: the effect at full character down to a
    hint of radio. The caller's chain and the panel's Test with effect run
    the same interpolation — a preview at a different intensity than the
    call would be a lie."""

    def test_the_level_travels_with_the_effect(self):
        import settings as settings_store
        from api.live import look_payload

        # 60 since 0.10.4: full character on every effect read as a costume
        # party; 60 keeps the colour audible with the words in front.
        self.assertEqual(60, settings_store.FIELDS["voice_effect_level"][1])
        self.assertEqual(40, look_payload(
            {"voice_effect": "cb", "voice_effect_level": 40},
            "X")["voiceEffectLevel"])
        # Out-of-range values are clamped, not trusted.
        self.assertEqual(100, look_payload(
            {"voice_effect_level": 400}, "X")["voiceEffectLevel"])

    def test_both_ends_interpolate_the_same_way(self):
        call_js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        panel_js = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        shared_maths = "lp + (16000 - spec.lp) * (1 - t)"
        self.assertIn(shared_maths, call_js)
        self.assertIn(shared_maths, panel_js)
        self.assertIn("voiceEffectLevel", call_js)


class TestThePanelReadsAtAGlance(unittest.TestCase):
    """Three operator reports in one sitting: matrix help doubled the page's
    height as a band per row; the section tags said "on" in the same grey as
    "off"; and the panel's theme control was a two-state toggle beside a
    card offering four stops."""

    @classmethod
    def setUpClass(cls):
        cls.js = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        cls.css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")

    def test_matrix_help_lives_in_the_label_cell(self):
        self.assertIn("'hint inlabel'", self.js)
        self.assertIn(".plabel .hint.inlabel", self.css)

    def test_the_tags_carry_their_state(self):
        self.assertIn("el.dataset.state", self.js)
        self.assertIn('.tag[data-state="on"]', self.css)
        self.assertIn('.tag[data-state="off"]', self.css)

    def test_the_dashboard_says_what_needs_doing(self):
        # 0.10.76: transmission shares the dashboard with a needs-attention
        # column, and the picker pins any page holding an item. The needs
        # derive from the SAME signals the tiles read (computeNeeds), so the
        # dashboard cannot disagree with itself.
        self.assertIn("function computeNeeds", self.js)
        self.assertIn("function paintNeeds", self.js)
        self.assertIn(".needrow", self.css)
        self.assertIn("a.attn::after", self.css)
        self.assertIn(".dashsplit", self.css)

    def test_the_panel_cycle_matches_the_cards(self):
        # Same four stops, same DRAWN icons, same stored key — two surfaces,
        # one mental model. 0.10.67 replaced the typed glyphs (the ☀ read as
        # a star, the station's ✳ as nothing at all) with one THEME_ICONS
        # table published by shared.js, so both cycles draw from it and
        # cannot drift.
        shared = (REPO / "web-widget" / "shared.js").read_text(encoding="utf-8")
        for key in ("light:", "dark:", "station:", "device:"):
            self.assertIn(key, shared.split("const THEME_ICONS")[1][:2000])
        for surface in (self.js,
                        (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")):
            self.assertIn("THEME_ICONS.station", surface)
            self.assertIn("localStorage.getItem('callinTheme')", surface)
        self.assertIn("panelThemeOptions", self.js)


class TestTheBeepIsPreviewableAndWavOnly(unittest.TestCase):
    """The beep's dropdown offered every upload, including the m4a the
    server can only ever turn into the tone — and there was no way to hear
    the default without placing a call."""

    def test_the_dropdown_filters_to_wav(self):
        # Anchored to the filter itself, not an occurrence count of the
        # slot name — a Play button added elsewhere once shifted the split.
        js = (REPO / "web-widget" / "panel-sounds.js").read_text(encoding="utf-8")
        i = js.index("const eligible = slot === 'vm_beep'")
        self.assertIn("\\.wav$", js[i:i + 200])

    def test_the_beep_is_previewable_from_its_card(self):
        # The per-moment test buttons became the ▶ on each slot card; the
        # beep's ▶ must still route through its OWN preview — its default is
        # synthesized server-side, and the browser sound engine can't play it.
        js = (REPO / "web-widget" / "panel-sounds.js").read_text(encoding="utf-8")
        self.assertIn("function previewBeep", js)
        play = js.split("play.onclick", 1)[1][:200]
        self.assertIn("previewBeep()", play)


class TestTheUrlRowsOnlyExistInUrlMode(unittest.TestCase):
    """Operator-reported from the deployed board: the .row skin sets its own
    display, an author display beats the UA's [hidden] rule, and all six URL
    rows sat fully visible under the slot cards — the whole sound section
    read as duplicated."""

    def test_the_hidden_attribute_wins_for_sloturl_rows(self):
        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".row.sloturl[hidden]", css)
        html = (REPO / "web-widget" / "panel.html").read_text(encoding="utf-8")
        # Every URL row ships hidden; paintSlotCards() is the only unhider.
        self.assertEqual(6, html.count('class="row sloturl" hidden'))


class TestTheStylesheetParsesToTheEnd(unittest.TestCase):
    """A single unclosed brace mid-file silently kills every rule after it —
    a duplicated selector line once dropped the whole compact block and the
    measuring rules, and the embed re-inflated to 896px with zero errors
    anywhere. Brace balance is a crude parser, but it catches exactly the
    editing accident that happened."""

    def test_braces_balance(self):
        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        self.assertEqual(css.count("{"), css.count("}"),
                         "style.css has unbalanced braces — every rule after "
                         "the break is silently dead in the browser")
        # The canary: the LAST load-bearing rule must still be reachable,
        # so a balanced-but-broken file still has to keep it intact.
        self.assertIn("body.measuring", css)

    def test_no_comment_is_left_open_or_closed_twice(self):
        # The other half of the same accident, and the braces check cannot see
        # it: editing the prose above a rule left a `*/` in the middle of a
        # comment, so the tail of that comment became live CSS and the parser
        # ate the rule underneath it. Braces still balanced, 23 modules still
        # green, and .skinart was simply not in the browser's stylesheet
        # (2026-08-14, caught by looking at the page rather than the suite).
        for name in ("style.css", "skins.css"):
            css = (REPO / "web-widget" / name).read_text(encoding="utf-8")
            depth, i, faults = 0, 0, []
            while i < len(css):
                nxt_open, nxt_close = css.find("/*", i), css.find("*/", i)
                if nxt_open == -1 and nxt_close == -1:
                    break
                if nxt_open != -1 and (nxt_close == -1 or nxt_open < nxt_close):
                    if depth:
                        faults.append(("nested /*", css.count("\n", 0, nxt_open) + 1))
                    depth, i = 1, nxt_open + 2
                else:
                    if not depth:
                        faults.append(("stray */", css.count("\n", 0, nxt_close) + 1))
                    depth, i = 0, nxt_close + 2
            self.assertEqual(faults, [], f"{name}: comment faults at these lines "
                                         f"— the CSS after each one is not what "
                                         f"you think it is: {faults}")
            self.assertFalse(depth, f"{name} ends inside an unclosed comment")


class TestHiddenActuallyHides(unittest.TestCase):
    """An author `display` beats the UA's [hidden] rule, and this codebase
    has now paid for that four separate times: .guestgate (spot-fixed long
    ago), the six URL rows sitting fully visible under the slot cards, the
    empty picker menu floating as a ghost box, and the calls toolbar's
    latent copy of the same fault. Every element the markup ships hidden
    whose class also sets a display must carry a `.cls[hidden]` spot rule —
    found mechanically, so the fifth one cannot ship."""

    def test_every_shipped_hidden_element_can_actually_hide(self):
        import re

        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")

        # Every selector whose SUBJECT (last compound) is class-based and
        # whose body sets a display other than none. Ancestor context is
        # ignored on purpose — over-matching there is a spot rule someone
        # writes once, under-matching is the fifth shipped ghost.
        subjects = []              # (tag or "", frozenset(classes))
        for rule in css.split("}"):
            if "{" not in rule:
                continue
            sel, body = rule.split("{", 1)
            # (?!\s*none): without the inner \s* the outer \s* backtracks a
            # space and the lookahead inspects " none", which passes.
            if not re.search(r"display\s*:(?!\s*none\b)", body):
                continue
            for one in sel.split(","):
                compound = one.strip().split()[-1] if one.strip() else ""
                if "[hidden]" in compound or ":" in compound:
                    continue
                tag = (re.match(r"([a-z][\w-]*)", compound) or [None, ""])[1]
                classes = frozenset(re.findall(r"\.([A-Za-z][\w-]*)", compound))
                if classes:
                    subjects.append((tag, classes))

        unhideable = []
        for page in ("index.html", "panel.html"):
            html = (REPO / "web-widget" / page).read_text(encoding="utf-8")
            for m in re.finditer(r"<(\w+)([^>]*)>", html):
                tag, attrs = m.group(1), m.group(2)
                cls = re.search(r'class="([^"]+)"', attrs)
                # The ATTRIBUTE, not the word: class="avatar hidden" names a
                # CSS class that hides by rule, not the browser attribute.
                bare = attrs.replace(cls.group(0), "") if cls else attrs
                # NOT \b: a word boundary treats the dash in aria-hidden as one,
                # so every decorative element carrying aria-hidden="true" read
                # as shipping hidden and was reported unhideable. Latent until
                # the first one with a class rule behind it (.skinart, 0.10.139).
                if not re.search(r"(?<![-\w])hidden\b", bare):
                    continue
                if not cls:
                    continue
                el_classes = set(cls.group(1).split())
                for sub_tag, sub_classes in subjects:
                    if sub_tag and sub_tag != tag:
                        continue
                    if not sub_classes <= el_classes:
                        continue
                    # A spot rule re-hiding any of the element's classes is
                    # the accepted fix; .pill's visibility reserve counts.
                    if any(re.search(r"\." + re.escape(c) + r"[^,{]*\[hidden\]",
                                     css) for c in el_classes):
                        continue
                    unhideable.append(
                        f"{page}: <{tag} class=\"{cls.group(1)}\">")
                    break

        self.assertEqual(
            [], sorted(set(unhideable)),
            "these ship hidden but a display rule targets them, which beats "
            "the UA's [hidden] rule — add a `.cls[hidden]` spot rule: "
            f"{sorted(set(unhideable))}")

class TestTheCallerIsNotRescuedMidAnnouncement(_TempStores):
    """MAX_HOLD_MS was 20s, set when the worker's own ceiling was 90s. Both
    halves of that reasoning are gone — the unconfirmed ceiling is 15s since
    0.10.113 and a measured voice.end ends a hold on the spot — and what 20s
    did instead was fire in the middle of every normal on-air hold. Measured
    on a call 2026-08-13: 30.4s of speech, a 35.7s hold, and at 20s the caller
    was handed the microphone and told "the booth is taking a while up there"
    while the DJ still had fifteen seconds on the air. From the caller's seat
    that is the DJ coming back early, and it was the widget doing it.
    """

    def test_the_backstop_clears_a_real_announcement(self):
        import re

        from tests.support import REPO

        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        m = re.search(r"const MAX_HOLD_MS = (\d+);", js)
        self.assertTrue(m, "MAX_HOLD_MS is gone or renamed")
        secs = int(m.group(1)) / 1000.0
        # The longest thing the DJ can legitimately air is a station segment,
        # whose fallback hold in call/tools/broadcast.py is 60s.
        self.assertGreaterEqual(secs, 60.0,
                                "the escape hatch fires during a normal hold")
        # Still a backstop, not an eternity.
        self.assertLessEqual(secs, 120.0)



# --------------------------------------------------------------------- skins
#
# Sixteen skins would be sixteen times the testing if a skin could reach the
# card. It cannot: skins.css is custom properties only, and these tests are
# what makes that a fact rather than an intention. The card's own geometry —
# the fixed 620×544, the one control height, the height reported to embed.js —
# is then out of a skin's reach BY CONSTRUCTION, which is the only reason this
# feature is affordable to own.

def _skins_css() -> str:
    return (REPO / "web-widget" / "skins.css").read_text(encoding="utf-8")


def _skin_blocks() -> dict[str, str]:
    """{skin id: the body of its block}. Comments stripped first, so a colour
    written in prose inside a comment can never read as a declaration."""
    css = re.sub(r"/\*.*?\*/", "", _skins_css(), flags=re.S)
    return {m.group(1): m.group(2) for m in
            re.finditer(r':root\[data-skin="([a-z0-9]+)"\]\s*\{(.*?)\}', css, re.S)}


class TestASkinCannotReachPastItsTokens(unittest.TestCase):
    """The one rule in skins.css: custom properties, and nothing else.

    This is the whole containment story. A skin that could write `height` or
    `padding` could break the fixed card, the embed's reported height, or the
    single control height — three things that took the player redesign and two
    releases to make true. Rather than re-checking those on sixteen skins, the
    stylesheet is not allowed to say anything that could affect them.
    """

    def test_every_declaration_is_a_custom_property(self):
        offenders = []
        for skin, body in _skin_blocks().items():
            for decl in body.split(";"):
                decl = decl.strip()
                if not decl:
                    continue
                prop = decl.split(":", 1)[0].strip()
                if not prop.startswith("--"):
                    offenders.append(f"{skin}: {decl[:60]}")
        self.assertEqual(
            offenders, [],
            "skins.css may only declare custom properties — anything else can "
            "reach the card's layout, which is exactly what skins are not "
            f"allowed to do: {offenders}")

    def test_the_file_contains_no_selector_but_a_skin_root(self):
        # An @media, an @keyframes or a bare `.card` rule would all escape the
        # containment above by being outside a skin block entirely.
        css = re.sub(r"/\*.*?\*/", "", _skins_css(), flags=re.S)
        css = re.sub(r':root\[data-skin="[a-z0-9]+"\]\s*\{.*?\}', "", css, flags=re.S)
        self.assertEqual(
            css.strip(), "",
            "skins.css has something in it that is not a :root[data-skin] "
            "block; skins may not declare selectors, media queries or "
            "keyframes of their own")

    def test_no_skin_sets_a_token_the_card_never_reads(self):
        # A misspelled --skin-radiius does nothing, silently, and the skin
        # ships three-quarters applied. Every --skin-* name a skin sets must
        # be one style.css actually declares a default for.
        style = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        known = set(re.findall(r"(--skin-[a-z-]+)\s*:", style))
        self.assertTrue(known, "style.css declares no --skin-* tokens at all")
        unknown = sorted({
            name for body in _skin_blocks().values()
            for name in re.findall(r"(--skin-[a-z-]+)\s*:", body)} - known)
        self.assertEqual(unknown, [], f"tokens no rule in style.css reads: {unknown}")

    def test_every_animation_a_skin_asks_for_exists(self):
        # A skin cannot declare @keyframes, so one naming an animation
        # style.css never defined is a skin whose artefact silently sits still.
        style = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        defined = set(re.findall(r"@keyframes\s+([A-Za-z0-9_-]+)", style))
        for skin, body in _skin_blocks().items():
            m = re.search(r"--skin-art-anim:\s*([A-Za-z0-9_-]+)", body)
            if m and m.group(1) != "none":
                self.assertIn(m.group(1), defined,
                              f"{skin} asks for an animation nothing defines")


class TestEverySkinOfferedActuallyExists(unittest.TestCase):
    """The offer and the stylesheet, in both directions.

    A skin in the dropdown with no block behind it selects, saves, and paints
    the default card — the operator picks Neon and nothing happens, with no
    error anywhere. A block with no offer is a skin nobody can reach. Both are
    the silent-unreachable shape this repo has shipped twice.
    """

    def test_the_dropdown_and_the_stylesheet_agree(self):
        offered = {v for v, _ in settings_store.STATIC_CHOICES["widget_skin"]}
        drawn = set(_skin_blocks())
        # `default` is the shipped card — style.css's own :root — so it is
        # deliberately NOT a block in skins.css.
        self.assertNotIn("default", drawn,
                         "there must be no `default` block: the default card is "
                         "style.css, and a block claiming to be it would be a "
                         "second copy that can drift")
        self.assertEqual(offered - {"default"}, drawn)

    def test_the_default_is_the_card_as_it_shipped(self):
        # The most consequential line in the change: every deployment that
        # never opens this setting must look exactly as it did before.
        self.assertEqual(settings_store.FIELDS["widget_skin"][1], "default")

    def test_the_card_carries_the_artefact_and_the_stylesheet(self):
        html = (REPO / "web-widget" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/skins.css"', html,
                      "the call page does not load skins.css, so no skin can "
                      "ever apply")
        self.assertIn('class="skinart"', html,
                      "the idle artefact's element is missing; --skin-art has "
                      "nothing to paint on")

    def test_the_artefact_is_idle_only_and_cannot_move_the_layout(self):
        # The two promises that make the artefact free: it is behind
        # everything, and it is gone the moment a call starts.
        css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")
        m = re.search(r"\.skinart\s*\{(.*?)\}", css, re.S)
        self.assertTrue(m, ".skinart has no rule")
        body = m.group(1)
        self.assertIn("position: absolute", body)
        self.assertIn("z-index: -1", body)
        self.assertIn("pointer-events: none", body)
        self.assertIn("display: none", body)
        self.assertIn('.card[data-mode="idle"] .skinart { display: block; }', css,
                      "the artefact is not restricted to the idle card, so it "
                      "would sit behind a live transcript")


class TestWhoYouAreTalkingToDoesNotChangeUnderYou(unittest.TestCase):
    """A show handover mid-conversation used to rewrite the card's DJ.

    Operator-reported 2026-08-14: a takeover during a text chat — often one
    the caller had just asked for — swapped the name, show and tagline on the
    next 20-second poll, while the voice they were actually talking to,
    resolved once when the conversation began, carried on unchanged. The card
    was the half that was lying. Everything else still follows the station;
    the operator's words were "I don't mind if the UI colour changes".
    """

    @staticmethod
    def _block(js, opener, closer):
        """The body between `opener` and the first line equal to `closer`."""
        start = js.index(opener) + len(opener)
        return js[start:js.index(closer, start)]

    def test_the_identity_is_only_repainted_from_an_idle_card(self):
        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        guarded = self._block(js, "if (!inConversation()) {", "\n      }")
        for el in ("djName", "djShow", "djTagline"):
            self.assertIn(el, guarded, f"{el} escaped the guard")

    def test_the_record_and_the_palette_are_not_frozen_with_it(self):
        # The point is narrow. Freezing the whole card would stop the record,
        # the clock and the station's colours following the show, which is
        # exactly what the operator asked to keep.
        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        guarded = self._block(js, "if (!inConversation()) {", "\n      }")
        for el in ("npTrack", "followStationPalette", "paintNowPlaying"):
            self.assertNotIn(el, guarded, f"{el} was frozen along with the DJ")

    def test_a_text_line_counts_as_a_conversation(self):
        # `room` alone would have missed the text line, which is the surface
        # the report came from — the predicate reads the card's mode instead.
        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        body = self._block(js, "function inConversation() {", "\n  }")
        self.assertIn("dataset.mode", body)
        self.assertNotIn("room", body)


class TestTheDoorsSitWhereTheOperatorPutThem(unittest.TestCase):
    """The caller's three buttons, in the operator's order.

    The stored value is a comma list somebody dragged into place, so it can be
    short, long, misspelled or repeat itself — and every one of those is a
    different kind of wrong on the card. It is cleaned server-side so the card
    is never the thing deciding what a bad setting means.
    """

    def _order(self, stored):
        from api.look import door_order

        return door_order({"door_order": stored})

    def test_the_default_is_the_order_the_card_already_had(self):
        # Nothing moves for a deployment that never touches this.
        self.assertEqual(settings_store.FIELDS["door_order"][1], "call,chat,vm")
        self.assertEqual(self._order("call,chat,vm"), ["call", "chat", "vm"])

    def test_an_operators_order_is_kept(self):
        self.assertEqual(self._order("vm,call,chat"), ["vm", "call", "chat"])

    def test_a_door_the_stored_order_never_mentions_still_appears(self):
        # The upgrade case, and the one that matters: a door added after the
        # operator last saved must not vanish from every card until somebody
        # opens the panel again.
        self.assertEqual(self._order("chat,call"), ["chat", "call", "vm"])
        self.assertEqual(self._order(""), ["call", "chat", "vm"])

    def test_rubbish_cannot_duplicate_or_lose_a_door(self):
        for stored in ("vm,vm,vm", "call,nonsense,chat", "  VM , call ,,",
                       "call,call,chat,vm,vm", None):
            with self.subTest(stored=stored):
                got = self._order(stored)
                self.assertEqual(sorted(got), ["call", "chat", "vm"],
                                 "a door was lost or duplicated")

    def test_the_widget_orders_by_flex_not_by_moving_nodes(self):
        # Reparenting fights the rules that show and hide these buttons, and a
        # 20-second poll that reparents is a poll that can move a button under
        # a finger. An `order` write is idempotent.
        js = (REPO / "web-widget" / "call.js").read_text(encoding="utf-8")
        self.assertIn("function applyDoorOrder(", js)
        body = js[js.index("function applyDoorOrder("):]
        body = body[:body.index("\n  }")]
        self.assertIn("style.order", body)
        for bad in ("appendChild", "insertBefore", "prepend"):
            self.assertNotIn(bad, body, f"the order is applied by {bad}")

    def test_the_panel_can_set_it_without_a_mouse(self):
        # An order you can only set by dragging is an order some people cannot
        # set at all.
        js = (REPO / "web-widget" / "panel.js").read_text(encoding="utf-8")
        self.assertIn("doormove", js)
        self.assertIn("move earlier", js)
        self.assertIn("move later", js)
