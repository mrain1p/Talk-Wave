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
        "panel.html": ("shared.js", "panel.js"),
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
        self.assertEqual(set(self.sources), {"shared.js", "call.js", "panel.js"})

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


class TestBothSurfacesOfferTheSameControls(unittest.TestCase):
    """The call card's corner controls are the server's decision, not the
    stylesheet's.

    Before 0.9.95 they were three unrelated mechanisms in the widget, and the
    settings gear was hidden by a rule that existed only for embeds — so the
    call page and an embed offered different controls, which nobody had
    decided. Anything the widget subtracts from this it subtracts for a
    reason this side cannot see (a host page that pinned a theme, an embed
    with no panel loaded); it may never ADD one.
    """

    def test_the_help_button_follows_its_setting(self):
        from api import live as api_live

        self.assertTrue(api_live.corner_controls(
            {"show_caller_help": True})["help"])
        self.assertFalse(api_live.corner_controls(
            {"show_caller_help": False})["help"])

    def test_pinning_a_theme_takes_the_toggle_away(self):
        from api import live as api_live

        for pinned in ("light", "dark"):
            with self.subTest(theme=pinned):
                self.assertFalse(api_live.corner_controls(
                    {"widget_theme": pinned})["theme"],
                    "a pinned theme leaves nothing to toggle")

    def test_auto_and_inherit_keep_the_toggle(self):
        from api import live as api_live

        # "inherit" is not a pinned theme: on the standalone page, where
        # there is no host to inherit from, it behaves as auto.
        for choice in ("auto", "inherit", "", None):
            with self.subTest(theme=choice):
                self.assertTrue(api_live.corner_controls(
                    {"widget_theme": choice})["theme"])

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
