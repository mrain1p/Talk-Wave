"""The browser half, guarded from here because it has no toolchain of its own and no runner to add one to.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
import settings as settings_store
from tests.support import AGENT_WORKER, REPO, widget_js


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

    Checked PER PAGE since the panel moved to /panel. That is stricter than
    the old whole-widget check, not looser: panel.js reaching for an id that
    only exists on the call page used to pass, because both surfaces were one
    document and every id was in scope. Now it fails, which is right — those
    two pages never load each other's script.
    """

    # page -> the scripts that page loads, in load order.
    PAGES = {
        "index.html": ("shared.js", "call.js"),
        # panel-viewers.js reads window.Panel, which panel.js publishes, so the
        # order is load-bearing rather than cosmetic.
        "panel.html": ("shared.js", "panel.js", "panel-viewers.js"),
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
            {"shared.js", "call.js", "panel.js", "panel-viewers.js"})

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
        # the card changes height the moment a call starts.
        for rule in (".rig { visibility: hidden; }",
                     ".pill[hidden] { visibility: hidden; }",
                     ".ticker[hidden] { display: grid; visibility: hidden; }"):
            with self.subTest(rule=rule):
                self.assertIn(rule, self.css)

    def test_the_line_area_is_always_the_same_three_lines(self):
        # Reserved from first paint at a fixed height, the same on every
        # surface. Three lines of 12.5px/1.45 plus 9px padding each side plus
        # the border — three rather than two because the box carries more
        # than speech now: the door code and the post-call strip live inside
        # it instead of being bands that grew the card.
        block = self.css.split(".linebox {")[1].split("}")[0]
        self.assertIn("height: var(--lines-h)", block)
        self.assertNotIn("height: auto", block)
        self.assertIn("--lines-h: 75px", self.css)
        # No per-surface override left: one height, chosen once.
        self.assertNotIn("body:not(.compact) { --lines-h", self.css)

    def test_only_reading_back_a_finished_call_may_change_it(self):
        # A deliberate click, by somebody who wants the room, when there is no
        # call left for the resize to interrupt.
        self.assertIn(".linebox.open { height: 200px; }", self.css)

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
        # card reports over ~356 (400 minus the host's caption row) came
        # back as BLANK SPACE between the sleeve and "Up next" on a live
        # station page, twice. The budget block is the fix; this pins its
        # load-bearing pieces so a future band can't quietly regrow it.
        self.assertIn("THE HEIGHT BUDGET", self.css)
        for pinned in ("body.compact .eyebrow { height: 30px",
                       "body.compact .tagline { display: none; }",
                       "body.compact .bars { height: 14px; }"):
            self.assertIn(pinned, self.css)

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


class TestPushToTalkIsPerSurfaceAndOffByDefault(unittest.TestCase):
    """The bar is the caller's microphone, and whether it exists is the
    operator's per-surface answer, carried on /live like the corner controls.
    Off by default: open-mic is what every existing deployment does, and a
    caller suddenly needing to press a button to be heard is a behaviour
    change, not a repaint."""

    def test_defaults_off_on_both_surfaces(self):
        from api import live as api_live

        payload = api_live.look_payload({})
        self.assertFalse(payload["ptt"])
        self.assertFalse(payload["embedPtt"])

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
        start = call_js.split("await room.connect(url, token);")[1][:700]
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
    locally the test skips if node is missing, and the wavetalk-verify skill
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


class TestTheEmbedIsJustTheCard(unittest.TestCase):
    """The 10px inset showed as a white ring on any host whose color-scheme
    the browser decided disagreed with ours — the frame's backdrop paints
    opaque and the inset frames the card in it. Edge to edge, square, the
    card IS the frame."""

    @classmethod
    def setUpClass(cls):
        cls.css = (REPO / "web-widget" / "style.css").read_text(encoding="utf-8")

    def test_no_inset_and_no_rounded_corners_in_a_frame(self):
        block = self.css.split("body.compact {")[1][:600]
        self.assertIn("padding: 0", block)
        card = self.css.split("body.compact .card {")[1][:400]
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

        self.assertEqual(100, settings_store.FIELDS["voice_effect_level"][1])
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
