"""Tests about this repo rather than about the product: its structure, its
skills and its commit gate. If one of these fails, something about how the
project is kept has slipped rather than something a caller would hear.

Documentation drift lives next door in test_docs.py.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
import settings as settings_store
from tests.support import AGENT_WORKER, REPO


class TestTheSuiteIsNotQuietlyNotRunning(unittest.TestCase):
    """A test that passes because it never ran is worse than no test.

    Three file-mode tests were written `skipUnless(POSIX)`, so on the author's
    Windows box they reported success while containing a broken constructor —
    only CI, on Linux, ever executed them. That is the failure mode this
    guards: the suite looking green while part of it is inert.

    Deliberately narrow. It does not try to judge whether a test is any good;
    it checks the two things that make one silently worthless — never
    executing, and having no assertions at all.
    """

    def _classes(self):
        import inspect
        import test_sidecar

        # Leading underscore is this suite's marker for a shared fixture
        # (_TempStores), which is a base class and correctly has no tests.
        return [
            (name, obj) for name, obj in vars(test_sidecar).items()
            if inspect.isclass(obj) and issubclass(obj, unittest.TestCase)
            and not name.startswith("_")
        ]

    def test_every_test_class_actually_has_tests(self):
        empty = sorted(
            name for name, cls in self._classes()
            if not [m for m in dir(cls) if m.startswith("test")]
        )
        self.assertFalse(empty, f"test classes with no tests in them: {empty}")

    def test_every_test_asserts_something(self):
        import inspect
        import re

        silent = []
        for name, cls in self._classes():
            for attr in dir(cls):
                if not attr.startswith("test"):
                    continue
                try:
                    src = inspect.getsource(getattr(cls, attr))
                except (OSError, TypeError):
                    continue
                if not re.search(r"\bself\.(assert|fail)\w*\(", src):
                    silent.append(f"{name}.{attr}")
        self.assertFalse(
            sorted(silent),
            f"tests that assert nothing, so they cannot fail: {sorted(silent)}")

    @unittest.skipUnless(hasattr(os, "getuid"), "POSIX-only tests are the point")
    def test_the_posix_only_tests_are_reachable_somewhere(self):
        # On POSIX — which is CI, and the container — nothing may be skipped
        # for the POSIX reason. If this ever fails, a test is inert everywhere.
        import test_sidecar

        for name in ("TestWrittenFilesGetExplicitModes",):
            cls = getattr(test_sidecar, name)
            reason = getattr(cls, "__unittest_skip_why__", "")
            self.assertFalse(
                getattr(cls, "__unittest_skip__", False),
                f"{name} is skipped on POSIX too, so it never runs: {reason}")


class TestTheRoutingTableIsInOnePlace(unittest.TestCase):
    """`token_server.py` is a map and nothing else: every handler lives in
    `api/`, and every route is registered in that one block.

    Two things depend on it holding. TestWidgetServerContract reads
    `token_server.py` alone to check that every path the widget fetches is served —
    a route registered inside `api/` would be invisible to it, and the widget
    would 404 with nothing to say so. And a handler nobody routes is the
    failure mode this codebase keeps producing in other forms: the control
    exists, the code is right, and there is no way to reach it.
    """

    @classmethod
    def setUpClass(cls):
        import ast

        here = AGENT_WORKER
        cls.server = (here / "token_server.py").read_text(encoding="utf-8")
        cls.modules = sorted((here / "api").glob("*.py"))
        cls.handlers = {}
        for path in cls.modules:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if (isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                        and node.name.startswith("handle_")):
                    cls.handlers[node.name] = path.name

    def test_the_scan_found_the_package(self):
        # A scan that quietly matched nothing would make the rest pass forever.
        self.assertGreater(len(self.modules), 8)
        self.assertGreater(len(self.handlers), 20)

    def test_every_handler_in_the_package_is_routed(self):
        orphans = sorted(f"{mod}:{name}" for name, mod in self.handlers.items()
                         if name not in self.server)
        self.assertEqual(
            orphans, [],
            "these handlers exist and nothing serves them — either register "
            f"them in build_app() or delete them: {orphans}")

    def test_no_module_registers_routes_of_its_own(self):
        stray = sorted(p.name for p in self.modules
                       if "router.add" in p.read_text(encoding="utf-8"))
        self.assertEqual(
            stray, [],
            "routes registered outside token_server.py are invisible to the "
            f"widget's contract test: {stray}")

    def test_nothing_in_the_package_imports_the_server(self):
        # An import back the other way is how a split quietly becomes one
        # module in twelve files.
        back = sorted(p.name for p in self.modules
                      if "import token_server" in p.read_text(encoding="utf-8"))
        self.assertEqual(back, [], f"api/ must not depend on its caller: {back}")


class TestEveryStoreDefaultsToTheOneDataDir(unittest.TestCase):
    """With no `*_PATH` env set, every on-disk store must resolve its default
    to the ONE shared `data/` dir. The suite overrides each path into a temp
    dir, so the default branch of each store constant — the
    `Path(__file__).parent.parent[.parent] / "data" / ...` in settings,
    secrets, admin-auth, call records and the listener log — is otherwise
    never exercised. A file moved to a new depth without fixing its `.parent`
    chain would ship a store writing somewhere else, silently, and nothing in
    the suite would see it (every test sets the path). This runs a clean
    subinterpreter with the path env cleared and pins where each default lands.
    It is also the guard a future `settings.data_dir()` consolidation would
    need before it could safely route these constants through one helper.
    """

    # module attribute -> the basename it must own under the shared data/ dir.
    STORES = {
        "settings.SETTINGS_PATH": "settings.json",
        "secrets_store.SECRETS_PATH": "secrets.json",
        "admin_auth.AUTH_PATH": "admin-auth.json",
        "call.record.CALLS_DIR": "calls",
        "api.stats.LISTENERS_PATH": "listeners.json",
    }

    def test_all_store_defaults_share_one_data_dir(self):
        probe = (
            "import json\n"
            "import settings, secrets_store, admin_auth\n"
            "from call import record\n"
            "from api import stats\n"
            "print(json.dumps({\n"
            "  'settings.SETTINGS_PATH': str(settings.SETTINGS_PATH),\n"
            "  'secrets_store.SECRETS_PATH': str(secrets_store.SECRETS_PATH),\n"
            "  'admin_auth.AUTH_PATH': str(admin_auth.AUTH_PATH),\n"
            "  'call.record.CALLS_DIR': str(record.CALLS_DIR),\n"
            "  'api.stats.LISTENERS_PATH': str(stats.LISTENERS_PATH),\n"
            "}))\n"
        )
        # Clear the five overrides the suite sets; keep the rest of the env (a
        # subprocess still needs PATH etc.). LOG_TO_FILE off so an import can't
        # touch data/logs.
        env = {k: v for k, v in os.environ.items()
               if k not in ("SETTINGS_PATH", "SECRETS_PATH", "ADMIN_AUTH_PATH",
                            "CALLS_PATH", "LISTENERS_PATH")}
        env["LOG_TO_FILE"] = "0"
        out = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(AGENT_WORKER), env=env,
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        # Logs go to stderr; the last stdout line is the JSON regardless.
        resolved = json.loads(out.stdout.strip().splitlines()[-1])

        parents = set()
        for attr, basename in self.STORES.items():
            p = Path(resolved[attr])
            self.assertEqual(p.name, basename,
                             f"{attr} default basename changed: {p}")
            self.assertEqual(p.parent.name, "data",
                             f"{attr} default is not under a data/ dir: {p}")
            parents.add(str(p.parent))
        self.assertEqual(
            len(parents), 1,
            f"store defaults scattered, not one data/ dir: {sorted(parents)}")


class TestTheParallelRunnerRunsTheSameSuite(unittest.TestCase):
    """run_tests.py runs the suite in parallel, one process per test module —
    the fast path for the pre-commit hook and CI. Its correctness rests on one
    claim: it runs EXACTLY the modules the aggregator names, so it can neither
    miss nor invent a test. This pins that its discovery matches the modules
    test_sidecar imports (and names run_tests.py for the untested-module
    guard next door)."""

    def test_it_discovers_every_test_module(self):
        import run_tests

        discovered = set(run_tests._modules())
        on_disk = {
            f"tests.{p.stem}"
            for p in (AGENT_WORKER / "tests").glob("test_*.py")
        }
        self.assertEqual(discovered, on_disk)
        self.assertIn("tests.test_house_rules", discovered)


class TestNewCodeDoesNotArriveUntested(unittest.TestCase):
    """A new module with no tests is the way coverage rots — quietly, one file
    at a time, while the suite stays green and says nothing.

    The bar here is deliberately low: every module must be *reached* by the
    suite at all. It does not judge how well. It exists so that adding a file
    is a decision to test it rather than an oversight, and it adapts on its own
    — a module added tomorrow is covered by this rule the moment it lands.
    """

    def test_every_module_is_reached_by_the_suite(self):
        here = AGENT_WORKER
        # The whole package, not this file. When the suite was one file, "the
        # suite's source" and "this file" were the same string; after the split
        # they are not, and reading only this module would have quietly dropped
        # the check to whatever test_house_rules.py happens to mention.
        suite_src = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((here / "tests").glob("*.py")))

        untested = []
        for path in sorted(here.rglob("*.py")):
            if path.name in ("test_sidecar.py", "__init__.py"):
                continue
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            # The tests are what does the covering; they are not code awaiting
            # coverage of their own.
            if "tests" in path.parts:
                continue
            rel = path.relative_to(here)
            dotted = str(rel.with_suffix("")).replace("\\", "/").replace("/", ".")
            if dotted not in suite_src and path.stem not in suite_src:
                untested.append(str(rel).replace("\\", "/"))

        self.assertEqual(
            untested, [],
            "these modules are never imported or named anywhere in the suite, so "
            "nothing here would notice if they broke. Write a test, or say in the "
            f"test file why they cannot have one: {untested}",
        )


class TestTheCallHarnessOnlyDialsLocal(unittest.TestCase):
    """tools/call_harness.py places REAL calls — a minted token dispatches an
    agent job that spends LLM and TTS money and, on the live box, occupies the
    concurrency slot. The master plan's toolbox rule is 'never mint a token
    against the live deployment', so the harness hard-refuses any non-localhost
    server with no override flag: a convenience flag is how 'never' becomes
    'once, by accident'. Source-read from here (the harness itself needs a
    running stack, which the suite must never touch) in the same way
    TestWidgetServerContract reads the widget."""

    def test_the_refusal_exists_and_has_no_escape_hatch(self):
        src = (REPO / "tools" / "call_harness.py").read_text(encoding="utf-8")
        self.assertIn("refuse_remote(args.server)", src,
                      "the harness no longer checks its target before minting")
        self.assertIn('("localhost", "127.0.0.1", "::1")', src,
                      "the localhost allowlist changed shape — make sure it "
                      "still refuses everything else")
        for flag in ("--remote-ok", "--force", "--i-know", "--unsafe"):
            self.assertNotIn(flag, src,
                             "the guard has grown an override flag; remove it "
                             "— pointing this at a deployment is never right")


class TestTheWidgetHarnessOnlyDrivesLocal(unittest.TestCase):
    """tools/widget_check.py (A4, adopted 2026-08-28) drives a real browser
    at a widget stub. Pointed at the operator's deployment it would hammer a
    live box to answer a dev-box question, so it hard-refuses any
    non-localhost base with no override flag — the same 'never becomes once,
    by accident' rule as the call harness above. Source-read, because the
    harness needs playwright and a browser, which the suite must never."""

    def test_the_refusal_exists_and_has_no_escape_hatch(self):
        src = (REPO / "tools" / "widget_check.py").read_text(encoding="utf-8")
        self.assertIn("refuse_remote(args.base)", src,
                      "the harness no longer checks its target")
        self.assertIn('("localhost", "127.0.0.1", "::1")', src,
                      "the localhost allowlist changed shape")
        for flag in ("--remote-ok", "--force", "--i-know", "--unsafe"):
            self.assertNotIn(flag, src,
                             "the guard has grown an override flag; remove "
                             "it — a browser harness never drives a "
                             "deployment")
        # And the suite's own no-new-dependency rule holds: playwright is
        # imported lazily with a helpful refusal, never at module top.
        self.assertIn("from playwright.sync_api import", src)
        self.assertLess(src.index("def main"),
                        src.index("from playwright.sync_api import"),
                        "playwright import moved to module scope — the "
                        "tool must degrade to a message, not a crash, on "
                        "a box without it")


class TestTheRetiredEvalStaysRetired(unittest.TestCase):
    """tools/tool_eval.py was deleted at 0.10.146. This is the note saying why,
    so it does not get rebuilt.

    It asked the same question `SCENARIO_SET=triage` asks — does the DJ fire the
    right tool — with three scenarios, FAKE tools that only recorded, and no
    grading beyond printing what happened. The triage set asks it with eleven
    scenarios, the REAL wrappers (so a tool's own guidance is under test too), a
    PASS/FAIL per scenario and a rate over repeats. Two harnesses answering one
    question is how they drift apart, and the weaker one is the one nobody
    notices has rotted — which is exactly what happened to the drill between
    0.10.138 and 0.10.145.

    Its one intent with no home moved with it: "a show change is a takeover, not
    a song request" is a triage scenario now. The other two were config-
    dependent (confirm-before-sending, which rides a setting) and chat-only (the
    Close button), and both are held at the prompt level in test_conduct and
    test_chat.
    """

    def test_it_has_not_come_back(self):
        self.assertFalse(
            (REPO / "tools" / "tool_eval.py").exists(),
            "tool_eval.py is back. If a second tool-routing harness is really "
            "wanted, say why here — otherwise the triage set is the one that is "
            "maintained, graded and run.")

    def test_its_surviving_scenario_lives_in_the_triage_set(self):
        src = (AGENT_WORKER / "scripted_call.py").read_text(encoding="utf-8")
        self.assertIn("a show change is a takeover, not a song request", src)
        self.assertIn("subwave_takeover_show", src)


class TestTheDrillHarnessTracksTheModulesItDrives(unittest.TestCase):
    """scripted_call.py is the instrument every conduct verdict rests on, and
    it broke silently for seven versions because nothing held it to the code it
    drives.

    0.10.138 moved the promise patterns into `promises.py` and made `_NUDGE` a
    dict keyed by kind. The harness went on naming `promise_guard.PROMISES_ACTION`
    and passing `promise_guard._NUDGE` as a string — an AttributeError on the
    first turn where the DJ speaks without calling a tool, which is the exact
    case the sweep exists to measure. So from 0.10.138 to 0.10.145 the drill
    could not run, the suite was green throughout, and the last numbers anyone
    quoted were taken before the break.

    The product modules already hold each other honest this way (test_chat pins
    the chat line and the phone to one `unbacked`). This does the same for the
    harness: every first-party name it imports, and every attribute it reads off
    a first-party module, has to still be there. Static — the harness itself
    needs a provider key and a live station, which the suite must never touch.
    """

    HARNESS = "scripted_call.py"

    @classmethod
    def setUpClass(cls):
        import ast

        cls.tree = ast.parse((AGENT_WORKER / cls.HARNESS).read_text(encoding="utf-8"))
        cls.ast = ast

    def _first_party(self, dotted: str) -> bool:
        """A module that lives in this repo, as opposed to the SDK or stdlib.

        Only ours are checked: a third-party rename is not this test's business,
        and importing whatever happens to be installed is how a house-rules test
        starts failing for reasons that have nothing to do with the house.
        """
        top = dotted.split(".")[0]
        return ((AGENT_WORKER / f"{top}.py").is_file()
                or (AGENT_WORKER / top / "__init__.py").is_file())

    def test_the_scan_found_the_harness(self):
        # Guard the guard: an empty parse would make everything below vacuous.
        self.assertTrue(
            any(isinstance(n, (self.ast.FunctionDef, self.ast.AsyncFunctionDef))
                and n.name == "run_scenario"
                for n in self.ast.walk(self.tree)),
            "scripted_call.py no longer has run_scenario — this test is "
            "reading the wrong file or the harness has been rewritten",
        )

    def test_every_name_it_imports_from_our_code_still_exists(self):
        import importlib

        missing = []
        for node in self.ast.walk(self.tree):
            if not isinstance(node, self.ast.ImportFrom) or node.level:
                continue
            if not node.module or not self._first_party(node.module):
                continue
            module = importlib.import_module(node.module)
            for alias in node.names:
                if hasattr(module, alias.name):
                    continue
                # `from brain import conduct_chat` is a SUBMODULE import and is
                # perfectly valid even though `brain/__init__.py` does not pull
                # it in — the attribute only appears once something imports it.
                # Without this the guard fails on a legal import, which it did
                # the first time the harness reached for the typed conduct.
                try:
                    importlib.import_module(f"{node.module}.{alias.name}")
                except ImportError:
                    missing.append(f"{node.module}.{alias.name}")
        self.assertEqual(
            missing, [],
            "the harness imports names our code no longer defines: "
            f"{missing}. It will raise on the run, not here — fix the harness "
            "against the module that moved.",
        )

    def test_every_attribute_it_reads_off_our_modules_still_exists(self):
        import importlib

        # alias -> dotted module, for `import x`, `import x as y` and
        # `from a import b` (where b is itself a module, e.g. `from call
        # import promise_guard`).
        bound: dict[str, str] = {}
        for node in self.ast.walk(self.tree):
            if isinstance(node, self.ast.Import):
                for alias in node.names:
                    if self._first_party(alias.name):
                        bound[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, self.ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    dotted = f"{node.module}.{alias.name}"
                    if self._first_party(dotted) and (
                            AGENT_WORKER / node.module / f"{alias.name}.py").is_file():
                        bound[alias.asname or alias.name] = dotted

        self.assertIn("promise_guard", bound,
                      "the harness no longer imports the promise guard — if "
                      "that is deliberate, the sweep has stopped measuring the "
                      "DJ the product ships")

        missing = []
        for node in self.ast.walk(self.tree):
            if not isinstance(node, self.ast.Attribute):
                continue
            if not isinstance(node.value, self.ast.Name):
                continue
            dotted = bound.get(node.value.id)
            if not dotted:
                continue
            module = importlib.import_module(dotted)
            if not hasattr(module, node.attr):
                missing.append(f"{node.value.id}.{node.attr}")
        self.assertEqual(
            sorted(set(missing)), [],
            "the harness reads attributes our code no longer has: "
            f"{sorted(set(missing))}. This is the 0.10.138 break, again.",
        )


class TestTheDrillBuildsEveryToolTheCallDoes(unittest.TestCase):
    """The sweep must hand the model the same tool surface a caller gets.

    It did not, for ninety-odd versions. `build_curation_tools` was added to
    call/session.py at 0.10.132 and never to scripted_call.py, so the hearts and
    the never-play list could not be exercised — and, worse, could not be seen
    to be missing: COVERAGE lists what the surface HAS against what fired, so a
    tool absent from the surface appears in neither column. The drill reported
    full coverage of a surface four tools short.

    What it looked like instead was a conduct fault. Asked to put a heart on the
    record, the DJ had no like tool and mimed it with an on-air announcement —
    the exact shape 0.10.93 exists to prevent, from the instrument rather than
    the product. And 0.97.24, a fix to the un-like path, shipped through a gate
    that structurally could not test it.

    Static, and by builder rather than by tool name: the harness needs a
    provider key and a live station, which the suite must never touch.
    """

    @staticmethod
    def _builders(path):
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {n.func.id for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id.startswith("build_") and n.func.id.endswith("_tools")}

    def test_the_scan_found_both_files(self):
        # Guard the guard: two empty sets compare equal and prove nothing.
        self.assertIn("build_library_tools",
                      self._builders(AGENT_WORKER / "call" / "session.py"))
        self.assertIn("build_library_tools",
                      self._builders(AGENT_WORKER / "scripted_call.py"))

    def test_the_harness_builds_what_the_call_builds(self):
        call = self._builders(AGENT_WORKER / "call" / "session.py")
        drill = self._builders(AGENT_WORKER / "scripted_call.py")
        self.assertEqual(
            call - drill, set(),
            "the drill is missing a tool family the call gives every caller: "
            f"{sorted(call - drill)}. It will not be reported as unreached — "
            "COVERAGE can only compare against the surface it was handed — so "
            "the sweep will read as clean while never testing those tools. Add "
            "the builder to the tools list in scripted_call.py's main().",
        )


class TestTheWrittenInstructionsStillDescribeTheCode(unittest.TestCase):
    """CLAUDE.md is loaded into every agent's context, so a stale path there is
    worse than no path — it sends the next person (or model) confidently to a
    file that moved. Prose cannot self-heal, but it can be made to fail loudly
    when the tree moves underneath it.

    Only source paths under agent-worker/ and web-widget/ are checked: those are
    tracked, so this holds in CI and inside the image. The long-form design docs
    are gitignored and deliberately not referenced this way.
    """

    def _claude_mds(self):
        root = REPO
        # The skills too, and for the same reason: a skill is instructions a
        # model follows without checking. Splitting app.js and moving the panel
        # to its own page left three of them naming files that no longer
        # existed, and one still sent you to put a new settings control in
        # index.html — where the panel would never have found it. All three
        # were caught by reading, which is exactly what this class exists to
        # stop being the mechanism.
        return [p for p in (root / "CLAUDE.md",
                            root / "agent-worker" / "CLAUDE.md",
                            root / "web-widget" / "CLAUDE.md") if p.is_file()] + \
            sorted((root / ".claude" / "skills").glob("*/SKILL.md"))

    def test_every_source_path_they_name_exists(self):
        import re

        docs = self._claude_mds()
        if not docs:
            self.skipTest("no CLAUDE.md in this checkout (not copied into the image)")

        root = REPO
        # Every source filename in the tree, so a doc may name a module the way
        # a person would ("session.py") without spelling out its directory.
        present = {
            p.name
            for d in ("agent-worker", "web-widget")
            for p in (root / d).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }

        missing = []
        checked = 0
        for doc in docs:
            base = doc.parent
            for ref in re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|js|html|css))`",
                                  doc.read_text(encoding="utf-8")):
                if ref.startswith("/"):
                    continue        # a served route (/call.js), not a path on disk
                # Resolve as a path relative to the doc or the repo root, else
                # as a bare filename anywhere in the source tree.
                if (base / ref).exists() or (root / ref).exists() \
                        or Path(ref).name in present:
                    checked += 1
                    continue
                # Named by the doc that contains it, since a bare SKILL.md
                # tells you nothing about which skill is wrong.
                label = (doc.parent.name if doc.name == "SKILL.md"
                         else f"{doc.parent.name}/{doc.name}")
                missing.append(f"{label} -> {ref}")

        self.assertGreater(checked, 10, "found almost no paths to check — the "
                                        "scan regex has probably stopped matching")
        self.assertEqual(
            missing, [],
            "instructions naming source files that do not exist. These are read "
            "and followed without being checked, so a stale path is worse than "
            f"no path: {missing}")


class TestEverySkillWouldActuallyLoad(unittest.TestCase):
    """A skill with broken frontmatter does not error — it is simply never
    offered, which looks identical to the model choosing not to use it. That is
    the worst failure mode available: silent, and indistinguishable from
    working. Adapts on its own; a skill added tomorrow is checked by this.
    """

    def _skills(self):
        d = REPO / ".claude" / "skills"
        return sorted(d.glob("*/SKILL.md")) if d.is_dir() else []

    def test_frontmatter_is_present_and_names_match_their_directories(self):
        import re

        skills = self._skills()
        if not skills:
            self.skipTest(".claude/skills not in this checkout (not copied into the image)")

        problems = []
        for skill in skills:
            text = skill.read_text(encoding="utf-8")
            if not text.startswith("---"):
                problems.append(f"{skill.parent.name}: no frontmatter block")
                continue
            name = re.search(r"^name:\s*(.+)$", text, re.M)
            desc = re.search(r"^description:\s*(.+)$", text, re.M)
            if not name or name.group(1).strip() != skill.parent.name:
                problems.append(
                    f"{skill.parent.name}: name field is "
                    f"{name.group(1).strip() if name else 'missing'}, which does not "
                    "match the directory, so the skill cannot be invoked by name")
            if not desc or len(desc.group(1).strip()) < 40:
                problems.append(
                    f"{skill.parent.name}: description missing or too short to "
                    "trigger on — it is the only part always in context")

        self.assertEqual(problems, [], f"skills that would not load correctly: {problems}")


class TestTheCommitGateIsStillWiredUp(unittest.TestCase):
    """A malformed .claude/settings.json does not raise — it silently disables
    every setting in that file, the pre-commit gate included. The failure looks
    exactly like a gate that decided everything was fine, which is the worst
    kind available."""

    def _settings(self):
        return REPO / ".claude" / "settings.json"

    def test_it_is_valid_json_and_still_guards_commits(self):
        path = self._settings()
        if not path.is_file():
            self.skipTest(".claude/ not in this checkout (not copied into the image)")

        data = json.loads(path.read_text(encoding="utf-8"))   # raises if malformed
        commands = [
            hook.get("command", "")
            for entry in data.get("hooks", {}).get("PreToolUse", [])
            for hook in entry.get("hooks", [])
        ]
        # Either name: run_tests.py runs the SAME suite as test_sidecar, in
        # parallel, and is what the hook actually invokes. This used to match
        # on "test_sidecar" alone, which meant it was really checking a phrase
        # in the DENIAL MESSAGE rather than that anything ran — and it went
        # red when that message was reworded (0.10.117).
        gate = [c for c in commands
                if "run_tests.py" in c or "test_sidecar" in c]
        self.assertTrue(
            gate, "no PreToolUse hook runs the suite any more — commits are "
                  "no longer gated on it")

        # It must filter on its own stdin: the `if` field alone does not
        # restrict, so without this the gate runs on every single Bash call.
        self.assertIn("git commit", gate[0],
                      "the hook does not check that the command is a commit")


class TestNoFileGrowsWithoutSomebodyDeciding(unittest.TestCase):
    """Nothing in this repo has ever objected to a file getting longer, and it
    shows: app.js reached 3,354 lines and this suite 5,791, one reasonable
    commit at a time. No single one of those commits was wrong. That is the
    whole problem — a file only becomes unreadable in increments small enough
    that nobody stops.

    So the rule is not "files must be short". It is "a file above the ceiling
    must be a decision somebody wrote down". Everything below the ceiling is
    unaffected and always will be.

    Two kinds of decision, deliberately kept apart, because treating them the
    same produced friction with nothing on the other end of it:

    EXEMPT is "this file is meant to be long, and that is the right answer".
    A declaration table is the clear case: settings.py grows by a few lines
    every time the station gains a setting, and making that ordinary act come
    and edit a number in this file would be ceremony no reader ever benefits
    from. Exempt files are not measured, only justified.

    SPLITTING is debt: too long, known, and going to be dealt with. Those are
    ratcheted — the recorded number is the size when the entry was written, and
    the file may shrink freely but never grow past it. An entry whose file has
    come back under the ceiling must be deleted, so the list cannot drift into
    describing a problem that no longer exists.

    The distinction matters in the other direction too. A ceiling that only
    ever means "apologise" pushes toward splitting files to satisfy the number
    rather than to help a reader, which is how you end up with a file per
    function. Being long has to stay a legitimate permanent answer.
    """

    CEILING = 600

    # Long on purpose. Not measured — only required to still exist and still
    # say why.
    EXEMPT = {
        "agent-worker/scripted_call.py":
            "the conduct harness, and it cannot be split: it is DELIVERED by "
            "stdin — `docker exec -i <worker> python - < scripted_call.py` — "
            "so a second module would simply not be there when it ran, which "
            "is the whole reason it is one file. Most of its length is the "
            "scenario tables, and those are supposed to grow: every real call "
            "worth not repeating becomes a few lines of caller turns here.",
        "agent-worker/settings.py":
            "the layered store (FIELDS, load/save/_migrate) plus the resolver "
            "machinery that reads the tables (schema_payload, _choices_for, "
            "provider_base_urls, mcp_tools_payload). The approved tables-out "
            "seam (RULED 2026-08-28) was CUT 2026-08-29 (Batch 1): the panel/"
            "vocab presentation DATA (SCHEMA/GROUPS/SUPERGROUPS + the provider "
            "and vocab tables) moved to settings_schema.py, and the caller-tier "
            "security ladder — unrelated logic buried between FIELDS and the "
            "panel copy — to caller_tiers.py. Both are pure leaves, re-exported "
            "so settings_store.* is byte-identical; the crossing is one-way "
            "(machinery reads the tables, the tables call nothing). What is "
            "left is still over the ceiling because the store's field handling "
            "and its resolvers are genuinely one subject; stays exempt.",
        "agent-worker/settings_schema.py":
            "the operator panel's declarative presentation data (SUPERGROUPS/"
            "GROUPS/SCHEMA) plus the mirrored provider/vocab tables — a "
            "declaration table, not logic, peeled from settings.py at Batch 1 "
            "(the tables-out seam the operator ruled on 2026-08-28). Long "
            "because the station has a lot of settings, and reading it top to "
            "bottom is how you find one — the same reasoning settings.py itself "
            "carried. Pure data: the functions that read it stayed in "
            "settings.py, so nothing here is logic that could be split.",
        "agent-worker/tests/test_open_lines.py":
            "one feature, and the seam was measured before exempting it. Open "
            "Lines is six modules (state, premise, premises, schedule, air, "
            "followup) plus a director loop, and every class here shares the "
            "_OnDisk fixture that redirects the record off the developer's own "
            "deployment — split it and both halves need that fixture, the "
            "record builder and the fake LLM, which is a seam of three names "
            "in both directions. The panel-CSS class that did NOT belong was "
            "moved to test_widget.py, where its subject is. What is left is "
            "the feature's own behaviour, and the first test in the file is "
            "the one the feature was allowed to exist on.",
        "web-widget/panel.html":
            "a settings form with eighty-odd controls is this long. There is no "
            "build step here by choice, so there is no include mechanism to "
            "break it up with, and splitting the markup across pages would "
            "scatter one form the operator reads top to bottom.",
        "agent-worker/api/diagnostics.py":
            "one module per job, and /test/* is genuinely one job: eight probes "
            "that all answer 'can this box reach that thing', plus the prompt "
            "preview. The call-record and log readback handlers — a DIFFERENT "
            "job (reading back what happened, not probing whether it works) — "
            "were split to api/readback.py at Batch 2 (2026-08-29). Splitting "
            "the probes from each other would scatter one answer across eight "
            "files.",
        "agent-worker/station.py":
            "the SUB/WAVE REST client — reads that assemble the prompt plus the "
            "admin-gated write wrappers behind the DJ's tools (one class per "
            "external service; the 'read-only' claim was corrected at Batch 1). "
            "One method per station endpoint plus the persona/show resolution "
            "that stitches several reads into one answer. It grows a method "
            "when the station gains an endpoint, the same declarative-surface "
            "reason settings.py is exempt; splitting it scatters one client "
            "across files that all hold the same httpx session and the same "
            "last-known-good caches.",
        "web-widget/panel.js":
            "measured again after the 0.10.85 split took the sound board out "
            "(4481 → 3715): the regions left are the settings form, the "
            "dashboard, the permission matrix and the test probes, and every "
            "one of them reads the same draft, resolved, secrets and options "
            "state — the fifteen-names verdict from 0.9.106 holds for what "
            "remains. The one real seam this file grew has been cut.",
        "web-widget/panel-sounds.js":
            "one subject: the sound board — slot cards, the shelf, previews "
            "and uploads. Split from panel.js at 0.10.85 along the ratchet's "
            "recorded seam; it grows a row when the board gains a moment, "
            "and splitting a board across files would scatter one table.",
        "web-widget/call.js":
            "measured, and every candidate region is coupled BOTH ways. The "
            "corner controls need two names out and owe fifteen back; the call "
            "itself needs twenty-five. The best candidate, captions, is five "
            "crossings for 140 lines and would still leave this file over the "
            "ceiling. Every part of a call touches room, live, callBtn, capBox "
            "and muted, because that is what a call is.",
        "web-widget/style.css":
            "the call card and everything both pages share, since 0.99.2 "
            "cut the panel-only half out: the old '193 panel-only lines' "
            "claim had drifted seventeen-fold while every embed downloaded "
            "all of it. What remains is one surface's styling plus the "
            "shared base, and the regions left genuinely serve the card.",
        "web-widget/panel.css":
            "the operator-page half of the 0.99.2 cut — the settings run, "
            "the preview stage, the Players page and the panelpage "
            "newspaper redesign, loaded by panel.html alone. One page, one "
            "file; the leak check both ways is written in its header, and "
            "TestThePanelStylesStayOffTheCallPage holds it.",
        # The three test modules that crossed the ceiling in 0.9.111, and they
        # are here for one reason that applies to all of tests/: this ceiling
        # and agent-worker/CLAUDE.md's placement rule point in opposite
        # directions for a test file. That rule says a test goes in the module
        # whose SUBJECT it defends, and that a subject with no home is a new
        # subject rather than an excuse to use whichever file is shortest.
        # Obeying the ceiling instead means moving a test away from its
        # subject to satisfy a number, which is the failure mode the ceiling's
        # own docstring warns about — a file per function, arrived at from the
        # other end. Subject placement wins; these grow with coverage.
        #
        # What still holds them: they are one subject each, and the day one of
        # them stops being one subject is the day it gets split — which is
        # what happened to test_sidecar.py at 5,791 lines, and that split was
        # by subject, not by size.
        "agent-worker/tests/test_call_flow.py":
            "one subject: a call while it runs. Answering, holding for the "
            "broadcast, coming back from it, the silence ladder, ending.",
        "agent-worker/tests/test_http.py":
            "one subject: the HTTP edge — caller identity (who the server "
            "believes you are, for cooldown and the unspoofable auth lockout), "
            "mint ceilings, the listener sampler and feedback endpoints. Grows "
            "a case per identity/rate rule, the same subject-placement rule as "
            "the other module tests here.",
        "agent-worker/tests/test_call_record.py":
            "one subject: what is written down about a call, and what is "
            "deliberately not — now including the caller's own verdict.",
        "agent-worker/tests/test_brain.py":
            "one subject: prompt assembly and what the DJ is told — the "
            "briefing/conduct seam, the caller's context, the budget caps that "
            "stop one bad track swallowing the prompt, and now whether every "
            "mouth speaks as the same DJ. Crossed the ceiling at 0.10.146 when "
            "the back-to-air line and the voicemail greeting were brought back "
            "to the persona's own card; same subject-placement rule as the "
            "test modules around it.",
        "agent-worker/tests/test_widget.py":
            "one subject: the browser half, guarded from here. It is the "
            "substitute for the JS unit tests this repo has no toolchain to "
            "run, so it carries text checks the widget cannot make itself.",
        "agent-worker/tests/test_settings.py":
            "one subject: the layered config — file over env over defaults, "
            "clearing meaning fall-through, and every provider/setting reaching "
            "the thing it configures. Grows a case per setting, the same "
            "subject-placement rule as the modules below.",
        "agent-worker/tests/test_voicemail.py":
            "one subject: the answering machine — greeting resolution, the "
            "beep as a cue, the bounded recording, delivery, and now the "
            "caller seeing their own words land. Same subject-placement rule "
            "as the test modules below.",
        "agent-worker/tests/test_tools_surface.py":
            "a census, not prose: the ROUTES and TOOLS manifests gain a "
            "pinned line (and its justifying comment) every time the surface "
            "gains a route or a tool, and pinning a new route should not "
            "have to come and edit this list — the same declaration-table "
            "reasoning as settings.py. The assertions are a page; the "
            "manifests are the length. Crossed the ceiling on 2026-08-17 "
            "when the soundbite routes and the cover proxy were pinned in "
            "one evening.",
        "agent-worker/tests/test_chat.py":
            "one subject: the text line — the door, the flood brakes, the "
            "typed register of the one brain, and now where a tool run's "
            "receipt card lands. Crossed the ceiling when 0.10.65 added the "
            "card-routing cases; same subject-placement rule as the modules "
            "around it.",
        "agent-worker/tests/test_voice.py":
            "one subject: whether a speech backend can say the thing — "
            "discovery, sample rates, pace, and now the shipped adapter "
            "contracts. Crossed the ceiling when 0.9.122 added vendor "
            "adapters and their guards; same subject-placement rule as the "
            "three above.",
        "agent-worker/tests/test_station.py":
            "one subject: what the station says, and what the card and the "
            "DJ say about it. Crossed the ceiling when 0.10.91 pinned "
            "refusals naming their blocklist rule; same subject-placement "
            "rule as the modules around it.",
        "agent-worker/tests/test_webhooks.py":
            "one subject: the station's pushes — registering for them, "
            "proving one arrived, and what a verified push may steer. "
            "Crossed the ceiling when 0.10.89 pinned the voice lifecycle's "
            "phased entries; same subject-placement rule as the modules "
            "around it.",
        "agent-worker/tests/test_conduct.py":
            "one subject: what the prompt is allowed to promise. It gains a "
            "test whenever a tool or a conduct rule is added, because that is "
            "precisely when the prompt can start naming something the line "
            "does not carry — the failure that taught the DJ to MIME an "
            "action. Crossed the ceiling at 0.98.22 with the reads row and "
            "the finder mode; same subject-placement rule as the two modules "
            "below, and trimming the comments to sit under a number is the "
            "thing this class's own docstring warns against.",
        "agent-worker/tests/test_tools_logic.py":
            "one subject: what a tool does once reached — provider "
            "construction, adapters, and what the DJ may claim afterwards. "
            "Crossed the ceiling when 0.10.86 pinned the Google-STT "
            "service-account trap; same subject-placement rule as the "
            "modules around it.",
        "agent-worker/tests/test_house_rules.py":
            "one subject: how this repo is kept. It grows a rule per "
            "incident — the aggregator sweep alone (0.10.5) came from two "
            "test classes that had silently never run — and splitting the "
            "rules about structure across files would defeat the point of "
            "having one place that states them.",
        "agent-worker/tests/test_discovery.py":
            "one subject: the ways into the library that are not a name "
            "search. Crossed the ceiling at 0.98.17, when browsing learned to "
            "speak the station's own vocabulary — and the vocabulary tests "
            "belong beside the browse tests, because every one of these bugs "
            "was the tool and the station disagreeing about a word.",
        "agent-worker/tests/test_album_tools.py":
            "one subject: putting a RUN of tracks in and taking one back "
            "out — the album, the mix, and the clear-out that mirrors "
            "them. Crossed the ceiling at 0.98.16, when a mix became "
            "undoable by the label it was queued under; the tests for the "
            "undo belong beside the tests for the queueing, because the "
            "bug was that the two had nothing in common.",
        "agent-worker/tests/test_takeover.py":
            "one subject: putting a show on air, the one caller action "
            "that outlives the call — and, since 0.98.16, what the tool "
            "says when it cannot tell which show was meant. The miss is "
            "the same subject as the match: both decide what a whole "
            "station hears for an hour.",
    }

    # path -> (lines when the entry was written, what it is waiting to become).
    # Shrinking is always fine and the number should be lowered when it happens.
    # Growing past the recorded size means: split it, or raise the number in the
    # same commit and say in the message what made that the right call.
    # Add an entry here when something is too long AND has a seam worth
    # cutting — the two splits that came out of 0.9.102 and 0.9.106 both
    # started as one.
    SPLITTING = {
        # 0.97.73 pushed it over with the mid-call track note (the frozen-
        # briefing fix — docs/the-call.md's last open disagreement). The seam
        # is real and has been in the file all along: the GUARD half (the air
        # state machine — verdicts, holds, the watch loop, and now the track
        # note) against the AGENT half (CallAgent, the reply path: the door
        # hint, the note injection, the wait-for-clear). They meet only at
        # the guard object CallAgent is handed. Deliberately NOT cut in the
        # change that grew it — a regression in either half would get two
        # candidate causes, the same deferral panel-viewers.js records.
        # RAISED to 684 (2026-08-18): nine lines of comment recording the
        # #1390 review — station 1.8 stamps the booth log at air time, the
        # HANDOFF_LAG pad is deliberately kept, and why. The seam has not
        # moved; the growth is the write-down of a decision, which is what
        # this file's comments are for. 707: the greeting-race fix (0.98.2)
        # primes the gate from the push file in the guard's constructor —
        # the primed state must exist before anyone else looks, so it cannot
        # live anywhere but the guard half. The split is still owed.
        # 718: the stuck hint (0.98.22) joined the door hint on the reply
        # path — eleven lines, all of them in CallAgent.on_user_turn_completed,
        # which is the AGENT half this seam already names. The growth lands
        # entirely on one side of the cut, so it makes the split easier rather
        # than harder. Still owed.
        # 634 at 0.98.51, when the off-list became DATA (OFF_LIST and
        # OFF_LIST_EXEMPT) so a guard could read the same list the prompt does
        # — the fix for a gate that had a tool and no sentence, which let a DJ
        # invent a station fault about a mix. The seam is visible now and it is
        # the one the file grew into: the DECLARATIONS at the top (OFF_LIST,
        # OFF_LIST_EXEMPT, SECTIONS) against the RULE BUILDERS below them, which
        # meet only by name. Not cut in the change that grew it: the off-list
        # bug is the thing under test this week and a regression in it should
        # have one candidate cause, not two. Same deferral call/air.py records.
        # 659: the soundtrack-is-knowledge block joined finding_rule (the
        # Casino calls, 2026-08-26 — three thumbs-down in one evening from a
        # DJ disowning its own knowledge). It lands entirely in the RULE
        # BUILDERS half of the recorded seam, so the split gets no harder.
        # 666: the earlier-call bullet joined the same table (the same
        # night's opening line — "did you cancel my queue?" answered from
        # per-call memory instead of the readable queue). Same half of the
        # seam again.
        # 668: the earlier-call bullet learns the booth log by name (the
        # day-log's reader, decision 3) — same rule-builders half.
        # 683 at 0.99.6 (top-down review, 2026-08-28): the "briefing is LIVE"
        # line now forks on avoid_on_air_overlap — a station that doesn't hold
        # for callers is told the briefing shows what played when the call
        # CONNECTED, not what is live now. Fifteen lines, all in the rule
        # builders half this seam already names; the split is no harder.
        "agent-worker/brain/tool_rules.py": (683, "the declarations at the top "
                                                  "split from the rule builders "
                                                  "below them"),
        # 729: the withheld watcher joins on_user_turn_completed (0.98.55) —
        # the same insertion point door and stuck already use, which is the
        # CallAgent half this split will carry away together. 755:
        # prime_buffer, same release — the advertised-buffer cold-start fill,
        # beside stream_buffer where its state lives.
        # 765: the arc hint (0.98.69+, the director's first slice — a call
        # that has ended must not restart) joins on_user_turn_completed, the
        # same insertion point door/stuck/withheld already use — all of it on
        # the CallAgent half this split will carry away together.
        "agent-worker/call/air.py": (765, "the CallAgent half (the reply "
                                          "path) split from the guard half "
                                          "(the air state machine)"),
        # 618: the per-caller door verdicts (0.98.4) joined _for_this_caller —
        # they belong beside canAsk, which is the per-request half of a file
        # whose other half builds the shared payload. That seam (shared build
        # vs per-caller resolve) is the split when it comes. 628: the door
        # gained its live/tape mode and the quick kill's own state (0.98.5),
        # ten lines on the build side so the panel's wiring warning can tell
        # a closed door from a missing network.
        # 635: the stationQuiet verdict joined the payload beside the door it
        # mirrors (quiet-the-station, 0.98.13) — seven lines of key + comment.
        # 646: openLinesTrigger, on the PER-CALLER side of the seam with the
        # other three door verdicts it sits beside. It has to be computed
        # here rather than in the shared payload for the same reason they
        # are: the answer depends on the tier, and the shared payload is
        # cached across every caller. Eleven lines, eight of them the note
        # saying why the shelf itself is never published with it.
        # 658 at 0.99.6 (top-down review, 2026-08-28): on_air now requires a
        # REAL persona, not merely a reachable station — an unconfigured box
        # with a default persona reads as off-air, so the widget stops claiming
        # a DJ is on when none is. The decision is a named _reachability helper
        # so a unit test can pin it (test_station) rather than the whole
        # handler; on the shared-build side of the seam where the card is
        # assembled.
        "agent-worker/api/live.py": (658, "the shared payload build split "
                                          "from the per-caller resolve"),
        # 0.97.77 pushed it over making the ringing concurrent (the mint-time
        # snapshot head start, the MCP warm-up, the join riding prepare). The
        # seam is the phase boundary the docstring has named all along:
        # everything BEFORE a session exists (prepare/_resolve, the station
        # server and its warm-up — the ringing) against everything after
        # (start, the behaviours, greet, shutdown — the live call). They meet
        # only at the attributes prepare leaves behind. Deliberately NOT cut
        # in the change that grew it: the pickup path was reworked in that
        # same change, and a slow or broken pickup must have one candidate
        # cause, not two.
        # 710: tape mode's prompt variant (0.98.5) — the on-air framing forks
        # on relay.tape, and the fork has to live where the live framing
        # lives. The seam above is untouched by it. 730: the station client
        # now closes in _on_shutdown's finally instead of racing it as its
        # own callback (0.98.8, the first tape soak's dead brackets) — the
        # ordering IS the shutdown work, so it cannot live elsewhere.
        # 794: quiet-the-station's four hooks (engage on each scope's moment,
        # the heartbeat, the sweep/tail marker pair) plus 0.98.14's shutdown
        # beat — each is pinned to a phase by the concurrent-shutdown
        # ordering, so none can move out.
        # 802: the Stuck object is built beside the Door (0.98.22) and
        # handed to CallAgent with it — eight lines, both of them on the
        # per-call-state side of this seam rather than across it. 807: the
        # finder mode swap, one call at the END of the tool build because it
        # reads the assembled list — the LIVE CALL side of the seam, and the
        # last line of the function it belongs to. 812: the show name, held
        # for the greeting's open-line check. Five lines, and all five are on
        # the RINGING side — resolved in prepare beside the persona and the
        # voice, handed to greet with it. It could not live in greeting.py:
        # the pickup has no station and no snapshot, and an open line belongs
        # to one DJ AND one show, so it has to make the same check the prompt
        # block does.
        # 813: the promise guard is handed the call's Asks (0.98.52), so an
        # obligation comes from the CALLER's request rather than the DJ's
        # vocabulary. One argument, on the same side of the seam.
        # 819: the withheld watcher is built beside door and stuck (0.98.55)
        # — per-call state, constructed where its siblings are, prepare side.
        # 825: prime_buffer wired off the snapshot read, same release — six
        # lines beside the read that pays for them.
        # 831: the call arc (goodbyes-are-done state, call/arc.py) built
        # beside the door in prepare and watched in start — per-call state,
        # constructed where its siblings are, exactly the door's own lines.
        # 836 (beta spike): the guards folded into one ConversationState
        # (call/state.py, NORTH STAR move 1) — five lines of holder build in
        # prepare, and air.py SHRANK by 26 in the same change.
        # 837: the door tier rides into CallActions for the day-log's
        # attribution — one argument, prepare side.
        # 841: the open-ask comeback rides the guard like the arc does -
        # four lines of attach beside their siblings, prepare side.
        # 896 at 0.99.1: the blind-call fallback (a decisively failed MCP
        # warm builds the chat's local read twins instead of a dead
        # toolset) and the optional thinking-sound player — both belong in
        # start() beside the session they configure; the recorded split is
        # still the right one and neither piece moves it.
        # 918 at 0.99.6 (top-down review, 2026-08-28): a _started flag guards
        # the hush sweep from firing before the session exists (a fast hang-up
        # during ringing used to raise on a half-built call), and the arc's
        # come-back task now has a shutdown callback that cancels it. Both are
        # on the LIVE half — start and shutdown ordering — where the seam
        # already puts them; twenty-two lines, none of it across the cut.
        "agent-worker/call/session.py": (918, "the ringing half (prepare, "
                                              "resolve, the station server) "
                                              "split from the live half "
                                              "(start, behaviours, shutdown)"),
        # 602 at 0.98.16: two lines over, from the fix that gave the text
        # line a `problems` list it actually writes to and a paragraph break
        # between a nudged retry and the line before it. The seam is the one
        # the file has carried since ChatShelf arrived — ONE CONVERSATION
        # (the tool loop, the nudge, the record it writes) against THE
        # COLLECTION (opening, resuming, idling out, sweeping) — and they
        # meet at get_or_open and nowhere else. Deliberately not cut in the
        # change that grew it: the tool loop is exactly what just changed,
        # and a regression there should not have two candidate causes. The
        # same deferral call/air.py records.
        # 622: the stuck hint (0.98.22). The typed line has no SDK hook to
        # hang a per-turn note on, so it goes in beside the caller's message
        # in ask() — the one-conversation half, which is the side of this
        # seam that was already going to carry it. 625: the finder mode swap,
        # three lines beside the tool build it belongs to, same half.
        # 635: the text line grows an Asks of its own (0.98.52) — the phone has
        # had one since 0.10.149 and chat's guard had only wording to go on.
        # 652: the withheld watcher (0.98.55), wired beside the stuck hint for
        # the same reason and on the same side of the seam. 660: the
        # claims-again grading after the nudge, same release, beside the
        # nudge it grades.
        # 661: the door tier rides into chat's CallActions for the day-log's
        # attribution — the same one-argument change the phone got.
        # 705: vet-before-show (2026-08-28) - the held round, the one
        # rewrite, and the honest release, all in the ChatSession half
        # the recorded seam already names.
        # 762 at 0.99.6 (top-down review, 2026-08-28): chat now writes the
        # shared postmortem notes on a SimpleNamespace view (repeat/correct,
        # door, ask, lookup — the same record a call leaves), the vet-before-
        # show path suppresses a held round WITHOUT dropping its tool calls,
        # the sweep skips a locked conversation before the idle check, and the
        # opener/nudge tell the shared state what the DJ said. All in the
        # ChatSession half; thirty-three lines, none across the seam.
        "agent-worker/chat/session.py": (762, "the one-conversation half "
                                              "(ChatSession: the tool loop, "
                                              "the nudge, the record) split "
                                              "from the collection half "
                                              "(ChatShelf: open, resume, "
                                              "idle out, sweep). 726 at "
                                              "0.99.4: NORTH STAR move 3 gave "
                                              "chat the shared ConversationState "
                                              "— the wiring shrank the "
                                              "consultation but the holder + "
                                              "its comment grew the file; the "
                                              "recorded split is unaffected"),
        # Over the ceiling at 631 at 0.99.6 (top-down review, 2026-08-28),
        # from 597. The three fixes all land on the REGISTRATION side: the
        # station is re-registered when the receiver URL drifts (a container
        # that moved LAN address kept pushing to the old one), a single
        # asyncio.Lock serialises the two register call sites so a warm-ping
        # race can't double-register, and the mis-keyed re-key is cooldown-
        # gated so a station that never accepts the key isn't hammered. The
        # seam the file has carried all along is the REGISTRATION state machine
        # (_registration_due, _mis_keyed, _stand_down, register_station_webhook)
        # against the WARM-PING / TEST loop (keep_station_warm, fire_test_hook,
        # handle_hooks_test); they meet only at the shared _hook_state and the
        # admin-client helpers. Deliberately NOT cut in the change that grew
        # it — the registration path is exactly what these fixes touched, and a
        # regression in it should have one candidate cause, not two. The same
        # deferral call/air.py records.
        "agent-worker/api/hooks.py": (631, "the registration state machine "
                                           "split from the warm-ping / test "
                                           "loop"),
        # Back over the ceiling at 630 with the tape-mode class (0.98.5); its
        # earlier entry (676) was rightly deleted when a split took it under.
        # The seam is the same one it has always had: the chunk-store half
        # against the relay-behaviour half. 647: the door's which-kind-of-shut
        # pin (0.98.6) — it belongs in the door class it extends. 741: the
        # heard-mode class and the caller-less tape pin (0.98.9) — same relay,
        # same fakes, and the split above is still the one worth making.
        # 1025: the hush classes (quiet-the-station, 0.98.13) + 0.98.14's
        # shutdown-beat and ceiling-ratio pins — they live on the same
        # _ChunkStore base as everything else here, and the split worth
        # making is now three-way: chunks / relay / hush.
        # 612 at 0.98.68: the never-aired hedge pin joined
        # TestWhatsNewInTheLibrary (the #1456 mirror — a "fresh find" claim
        # must not outrun the data behind it). The seam has been in the file
        # all along: TestALateMatchStillReachesTheCaller defends late_match.py,
        # its own module with its own fixtures, while everything else here is
        # searches and requests — the two share nothing but the imports line.
        # Not cut in the change that grew it, per the standing rule: the hedge
        # is the thing under test this week and a regression in it should have
        # one candidate cause.
        "agent-worker/tests/test_music_tools.py": (612, "the late-match class "
                                                        "split from the "
                                                        "search-and-request "
                                                        "classes"),
        "agent-worker/tests/test_onair.py": (1025, "the chunk-store half "
                                                   "split from the relay "
                                                   "half"),
        # 0.10.121 pushed it over with the ducking timeline. The seam was
        # already named in web-widget/CLAUDE.md and is genuinely two viewers:
        # the LOG viewer (renderLog, the level filter, the tail) against the
        # CALL viewer (renderCall, callTime, the action vocabulary, the row
        # list). They share only afetch and showResult, both from Panel.
        # Deliberately NOT cut here: the ducking is being diagnosed on a live
        # deployment, and moving this file in the same change would give any
        # regression two candidate causes.
        #
        # RAISED to 781 for the operator's marks and the copy buttons (callText,
        # copyText, flashCopy, the row's action cell). Same reasoning as the
        # first deferral, which is the honest one rather than a convenient one:
        # this landed in a change that also moved the call card, the dashboard
        # strip and the notifications box, and cutting the file in the same
        # commit would hand any regression in it two candidate causes. The seam
        # has not moved — everything added here is on the CALL side of it — but
        # the debt is now 181 lines over the ceiling and the next thing this
        # file gains should be the split.
        # 801: the free-text find over both sides of a conversation (0.98.51) —
        # the operator's own ask, and it lands in the call viewer's filter
        # pipeline, which is the half of this file the seam already names.
        # 807: the DJ cell's name span (2026-08-24 settings review) — the
        # tier chip was being ellipsis-clipped to a border sliver on chat
        # and voicemail rows, and the fix is a wrapper the summary builder
        # has to create. Six lines, same call-viewer half of the seam.
        # 617 the day the settings page grew a station transport. The engine
        # that plays the broadcast — playFirstWorking, its CORS retry and its
        # mixed-content warning — had to leave call.js the moment a SECOND
        # surface played the stream: the alternative was a copy, and a copy of
        # that function would only ever have carried the older bugs of the
        # three incidents it took to get right. The handoff that carries the
        # music between the two pages came with it, for the same reason: both
        # surfaces write it and both read it.
        #
        # The seam is real, measured, and has been in the file since long
        # before this: the caller-facing COPY TABLES (ASK_GROUPS, ASKS, NEVER
        # — ~128 lines of string literals describing what a caller may ask
        # for) against the RUNTIME FOUNDATION around them (params, theme and
        # skin resolution, the sound engine, the call key, and now the stream).
        # The crossing is zero in both directions: the tables call nothing and
        # nothing in the file reads them — they exist only to be exported, and
        # each page renders them itself.
        #
        # Deliberately NOT cut in the change that grew it. Splitting means a
        # third shared script on BOTH pages, which moves the load-order
        # contract in TestWidgetServerContract and the file table in
        # web-widget/CLAUDE.md; doing that in the same commit as a new player
        # would give any regression in either two candidate causes. Same
        # deferral the two entries below record.
        "web-widget/shared.js": (620, "the caller-facing copy tables "
                                      "(ASK_GROUPS, ASKS, NEVER) split from "
                                      "the runtime foundation; the crossing "
                                      "is zero in both directions"),
        "web-widget/panel-viewers.js": (807, "the log viewer split from the "
                                             "call viewer; they share only "
                                             "Panel.afetch and showResult"),
        # 0.9.122 pushed it over adding per-adapter auth and the {voice}
        # endpoint templating for ElevenLabs. The seam is real and one-way:
        # discovery (parse_voice_list, available_voices, pick_speakable_voice,
        # adapter_api_key/adapter_headers) against synthesis (AdapterTTS and
        # its stream). Discovery never reads the class; the class needs two
        # helper names back.
        # 669 at Batch 1 (2026-08-29): the fail-loud endpoint_path guard in
        # load_adapter (the report-only type check is blind at this untyped-dict
        # seam) plus a docstring paragraph naming the discovery half. Both are
        # on the synthesis/config side; the seam above has not moved and the
        # discovery split is still the one worth making.
        "agent-worker/tts_adapter.py": (669, "a voice-discovery module split "
                                             "out from the AdapterTTS class"),
        # 0.10.113 pushed it over while rebuilding the duck: the pads were
        # collapsed into one constant and a measured voice.end was made to
        # beat our own estimate, both of which needed the reasoning written
        # down beside them (this file is where a timing decision gets
        # re-litigated at 2am, and an uncommented constant is how the last
        # round of them accumulated).
        #
        # The seam is real and one-way: the VERDICT logic — speaking_secs,
        # DUCK_PAD_SECS, _push_verdict, _log_says_busy, _assess, _settle — is
        # a pure question ("given this evidence, is the air busy") that reads
        # no live state except the clock and a handful of deadlines, and it is
        # what every test in test_call_flow and test_webhooks actually
        # exercises. What is left is the live guard: the watch loop, the
        # publish, wait_until_clear, the come-back line, CallAgent.
        #
        # Deliberately NOT cut in the same change as the timing fix. The
        # operator is on a broken deployment; moving this logic and altering
        # it at once means a regression has two candidate causes.

        # api/hooks.py was here from 0.10.69 until 0.10.89, when the voice
        # lifecycle grew the receiver past the ratchet and the recorded seam
        # was cut for real: the receiver (push verification, the air file,
        # the shared identity/secret/state) moved to api/hook_receiver.py
        # (240 lines) and registration kept hooks.py (460), importing the
        # shared names one-way. Both sides came back under the ceiling.
        # panel.js was here from 0.10.79 (the sound-board seam) until
        # 0.10.85, when the recorded cut happened for real: the previewers,
        # slot cards, board and uploads moved to panel-sounds.js (787
        # lines, three names owed back as Panel.sounds, five taken from the
        # Panel global) and panel.js dropped 4481 → 3715. Both files are in
        # EXEMPT above with the post-split measurements.
        # lifecycle.py was here from 0.9.125 until 0.10.9, when the recorded
        # seam was cut for real: the back-to-air mention and the transcript
        # reader moved to call/handoff.py and lifecycle came back under the
        # ceiling.
    }

    # Where shipped code lives. tools/ is developer scaffolding and docs are
    # prose, so neither is held to a source-file ceiling.
    ROOTS = ("agent-worker", "web-widget")
    SUFFIXES = (".py", ".js", ".css", ".html")

    @classmethod
    def setUpClass(cls):
        root = REPO
        cls.root = root
        cls.sizes = {}
        for name in cls.ROOTS:
            for path in sorted((root / name).rglob("*")):
                if not path.is_file() or path.suffix not in cls.SUFFIXES:
                    continue
                if "__pycache__" in path.parts or ".venv" in path.parts:
                    continue
                rel = str(path.relative_to(root)).replace("\\", "/")
                cls.sizes[rel] = len(
                    path.read_text(encoding="utf-8", errors="replace").splitlines())

    def test_the_scan_found_the_source_tree(self):
        # A scan that quietly matched nothing would make every check below pass
        # forever, which is the failure mode this suite keeps guarding against.
        self.assertGreater(len(self.sizes), 40,
                           "the file scan has stopped finding the source tree")

    def test_nothing_is_over_the_ceiling_without_a_decision(self):
        decided = set(self.EXEMPT) | set(self.SPLITTING)
        over = sorted(
            f"{path} ({n} lines)"
            for path, n in self.sizes.items()
            if n > self.CEILING and path not in decided
        )
        self.assertEqual(
            over, [],
            f"these are over the {self.CEILING}-line ceiling and nobody decided "
            "that was right. Split them; or add them to SPLITTING if that is "
            "coming, or to EXEMPT if being this long is the correct answer: "
            f"{over}")

    def test_nothing_is_being_split_and_exempt_at_once(self):
        # The two lists mean opposite things — "this is debt" and "this is
        # right". A file in both says nobody decided which.
        both = sorted(set(self.EXEMPT) & set(self.SPLITTING))
        self.assertEqual(both, [], f"listed as both debt and deliberate: {both}")

    def test_no_file_being_split_has_grown(self):
        grown = sorted(
            f"{path} was {was}, is now {self.sizes[path]}"
            for path, (was, _) in self.SPLITTING.items()
            if path in self.sizes and self.sizes[path] > was
        )
        self.assertEqual(
            grown, [],
            "these are on the list because they are too long and being dealt "
            "with, so the recorded size is a ceiling of its own. Shrink them, "
            "or raise the number in the same commit and say why that was the "
            f"right call: {grown}")

    def test_no_entry_outlives_the_thing_it_describes(self):
        # Three ways an entry goes stale: the file is gone, or it has come back
        # under the ceiling, or (for EXEMPT) it was never over it. Any of them
        # and the list has started describing a repo that isn't this one.
        stale = sorted(
            path for path in (set(self.EXEMPT) | set(self.SPLITTING))
            if path not in self.sizes or self.sizes[path] <= self.CEILING
        )
        self.assertEqual(
            stale, [],
            "these entries no longer describe anything — the file is gone or is "
            f"back under the ceiling. Delete them: {stale}")

    def test_every_entry_says_why(self):
        # An entry with no reason is indistinguishable from one added to make
        # the suite go green, which is precisely what this must not become.
        reasons = dict(self.EXEMPT)
        reasons.update({p: why for p, (_, why) in self.SPLITTING.items()})
        thin = sorted(path for path, why in reasons.items()
                      if len(why.strip()) < 40)
        self.assertEqual(
            thin, [],
            f"entries must say why the size is what it is, not merely that it "
            f"is: {thin}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTheLogKeepsTheLinesThatMatter(unittest.TestCase):
    """Third-party chatter drowns the real events. The widget polls /live every
    20 seconds forever, and the panel's log viewer reads the same ring buffer,
    so unfiltered noise makes it useless."""

    def setUp(self):
        import log_setup

        log_setup.setup("tests", console=False)     # idempotent
        log_setup.RECENT.clear()

    def test_the_widgets_polling_is_dropped(self):
        import logging as _logging

        import log_setup

        access = _logging.getLogger("aiohttp.access")
        access.info('GET /live HTTP/1.1 200')
        access.info('GET /health HTTP/1.1 200')
        self.assertEqual(log_setup.recent_lines(), [])

    def test_real_requests_are_kept(self):
        import logging as _logging

        import log_setup

        _logging.getLogger("aiohttp.access").info('POST /token HTTP/1.1 200')
        self.assertTrue(any("/token" in line for line in log_setup.recent_lines()))

    def test_the_access_line_carries_no_user_agent_or_referer(self):
        # aiohttp's default spends 120 characters on the referer and the
        # browser's full user-agent, and repeats a timestamp the formatter has
        # already written — in the panel's viewer every request wrapped onto
        # three lines and buried the events worth reading (2026-08-12).
        import token_server

        fmt = token_server.ACCESS_LOG_FORMAT
        for noise in ("User-Agent", "Referer", "%t"):
            self.assertNotIn(noise, fmt)
        for kept in ("%a", "%r", "%s", "%b"):
            self.assertIn(kept, fmt)

    def test_an_unchanged_voice_mirror_stops_saying_so(self):
        # "mirroring 18 persona voices from station settings" is an event the
        # first time and noise every time after — it appeared twice in the
        # same second in the operator's log viewer.
        import logging as _logging

        import log_setup
        import station_config

        station_config._LAST_MIRRORED = {}
        voices = {"p1": "v1", "p2": "v2"}
        station_config._note_mirrored(voices)
        station_config._note_mirrored(dict(voices))
        station_config._note_mirrored(dict(voices))
        said = [l for l in log_setup.recent_lines() if "mirroring" in l]
        self.assertEqual(len(said), 1, f"repeated an unchanged mirror: {said}")
        # A real change still speaks up.
        station_config._note_mirrored({"p1": "v1", "p2": "v9", "p3": "v3"})
        said = [l for l in log_setup.recent_lines() if "mirroring" in l]
        self.assertEqual(len(said), 2)
        self.assertIn("changed from 2", said[-1])
        _logging.getLogger("callin.test").debug("")

    def test_the_panel_can_read_recent_lines_without_docker(self):
        import logging as _logging

        import log_setup

        for i in range(5):
            _logging.getLogger("callin.test").info("event %d", i)
        lines = log_setup.recent_lines(3)
        self.assertEqual(len(lines), 3)
        self.assertIn("event 4", lines[-1])

    def test_setting_up_twice_does_not_double_every_line(self):
        # The token server's test endpoints import main.py, whose module-level
        # setup("worker") would otherwise add a second handler.
        import logging as _logging

        import log_setup

        before = len(_logging.getLogger().handlers)
        log_setup.setup("tests-again", console=True)
        self.assertEqual(len(_logging.getLogger().handlers), before)


class TestEveryTestClassIsAggregated(unittest.TestCase):
    """test_sidecar.py is the one entry CI and the commit hook run — a class
    that exists in tests/ but is not imported there NEVER RUNS, silently.
    TestTheKillSwitchOutranksEveryDoor shipped that way in 0.9.153 and sat
    green-by-absence for four releases; its first real run then failed on
    its own assertions. The aggregator is a hand-written list, so this is
    the test that reads both sides."""

    def test_no_test_class_is_silently_skipped(self):
        import ast
        from pathlib import Path

        tests_dir = Path(__file__).parent
        defined = set()
        for path in sorted(tests_dir.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    defined.add(node.name)
        aggregator = (tests_dir.parent / "test_sidecar.py").read_text(
            encoding="utf-8")
        missing = sorted(name for name in defined if name not in aggregator)
        self.assertEqual(
            [], missing,
            "defined under tests/ but never imported by test_sidecar.py — "
            "these classes DO NOT RUN in CI or the commit hook: "
            f"{missing}")


class TestTheInstallerTellsTheTruth(unittest.TestCase):
    """install.sh is the one-command fresh install (curl | bash). A script
    that fetches files by name drifts the moment a file is renamed — the
    compose rename this same week would have broken it silently — so every
    name it fetches must exist in the repo, and its refuse-to-overwrite
    guard must stay: it is an installer, not an updater."""

    def setUp(self):
        from tests.support import REPO
        self.repo = REPO
        self.script = (REPO / "install.sh").read_text(encoding="utf-8")

    def test_every_fetched_file_exists(self):
        import re

        fetched = re.findall(r'"\$RAW/([^"]+)"', self.script)
        self.assertGreaterEqual(len(fetched), 4, "the fetch list shrank")
        for name in fetched:
            with self.subTest(name=name):
                self.assertTrue((self.repo / name).is_file(),
                                f"install.sh fetches {name}, which is not in "
                                "the repo")

    def test_it_refuses_to_overwrite_a_deployment(self):
        self.assertIn("refusing to overwrite", self.script)
        self.assertIn('[ -e "$DIR/.env" ]', self.script)

    def test_the_secret_lands_in_both_files(self):
        # One generated secret, two files that must agree — the exact dance
        # the installer exists to do for people.
        self.assertIn("REPLACE_WITH_A_FRESH_SECRET", self.script)
        self.assertIn("LIVEKIT_API_SECRET=", self.script)


class TestTheHarnessCanBeInjectedIntoAnOlderImage(unittest.TestCase):
    """`scripted_call.py` is piped into whatever image is deployed, and the
    modules it overrides go in by hand — so the injection list IS the contract.

    Both rules below were bought on 2026-08-15 and each cost a full run against
    the operator's box, which is roughly eight minutes and a slice of their LLM
    bill. Neither is checkable by running the harness (it needs a live station
    and a provider key, which the suite must never touch), so both are read off
    the source.
    """

    HARNESS = AGENT_WORKER / "scripted_call.py"

    def _injection_pairs(self):
        """The (NEW_VAR, module.path) pairs, in the order they are installed."""
        import re

        src = self.HARNESS.read_text(encoding="utf-8")
        start = src.index("for _var, _path in (")
        body = src[start:src.index("):", start)]
        return re.findall(r'\("(NEW_[A-Z_]+)",\s*"([\w.]+)"\)', body)

    def test_the_scan_found_the_injection_list(self):
        # Guard the guard: an empty list would make the order test vacuous.
        pairs = self._injection_pairs()
        self.assertGreaterEqual(len(pairs), 2, "no injection pairs parsed — "
                                "the loop has been rewritten and these tests "
                                "are reading the wrong thing")

    def test_a_module_is_installed_before_anything_that_imports_it(self):
        """Leaves first, or a dependent binds to the image's old copy.

        `call.promise_guard` imports `promises` and `spoken_rules`. Installed
        in the wrong order it took the DEPLOYED `promises`, whose `unbacked`
        had never heard of the "refused" kind, and the run died three scenarios
        in with `KeyError: 'refused'` — after spending the model calls.
        """
        order = [path for _var, path in self._injection_pairs()]
        index = {p: i for i, p in enumerate(order)}
        # (a module, the modules it imports). Only first-party pairs that are
        # actually in the list are checked, so adding an injection does not
        # oblige anyone to edit this.
        for dependent, needs in (
            ("call.promise_guard", ("promises", "spoken_rules")),
            ("brain.conduct", ("brain.tool_rules",)),
            ("brain.conduct_chat", ("brain.tool_rules",)),
            ("promises", ("spoken_rules",)),
        ):
            if dependent not in index:
                continue
            for leaf in needs:
                if leaf in index:
                    self.assertLess(
                        index[leaf], index[dependent],
                        f"{leaf} is injected after {dependent}, which imports "
                        f"it — {dependent} will bind to the image's copy and "
                        "the run fails partway through, having already spent "
                        "the model calls",
                    )

    def test_every_injectable_module_actually_exists(self):
        for _var, path in self._injection_pairs():
            candidate = AGENT_WORKER / (path.replace(".", "/") + ".py")
            self.assertTrue(candidate.is_file(),
                            f"{path} is in the injection list but there is no "
                            f"{candidate.name} to inject")

    def test_every_scenario_set_the_docstring_offers_can_be_selected(self):
        """A set defined and never wired up falls back to the default silently.

        `SCENARIO_SET=banter` would have run the ordinary scenarios and
        reported reply lengths for the wrong thing — the shape of failure that
        looks like a result.
        """
        import re

        src = self.HARNESS.read_text(encoding="utf-8")
        offered = set(re.findall(r"SCENARIO_SET=(\w+)", src))
        # `[,}]` and not just a comma: the last entry of the table carries the
        # closing brace instead, and matching only commas silently exempted it.
        selectable = set(re.findall(r'"(\w+)":\s*[A-Z_]+\s*[,}]', src))
        # `extra` is the fallback the dict resolves to, not a key in it.
        missing = offered - selectable - {"extra"}
        self.assertEqual(
            set(), missing,
            f"named in the docstring but not in the sets table: {sorted(missing)} "
            "— SCENARIO_SET would fall back to the default set and the run "
            "would report numbers for scenarios nobody asked for")

    def test_a_scenario_whose_fault_never_fires_is_not_scored(self):
        """The 0.10.150 lesson, pinned so it cannot quietly regress.

        A scenario that arms a refusal the DJ never walks into graded the happy
        path, and a happy path scores the same in both arms of an ablation.
        """
        src = self.HARNESS.read_text(encoding="utf-8")
        self.assertIn("FAULTS_FIRED", src)
        self.assertIn("the fault this scenario", src)


class TestEveryScenarioIsWellFormedBeforeItCostsAnything(unittest.TestCase):
    """A malformed scenario is only discovered by spending an arm.

    `scripted_call.py` is piped into the deployed worker and billed per round,
    so a typo in an expectation key does not fail loudly — it silently grades
    nothing and reports a PASS, which is the most expensive kind of wrong this
    repo has. `must_not_say` typed as `must_not_stay` would have read as a
    clean sweep.

    Read off the AST rather than imported: importing the harness pulls in
    livekit and a station client, and the suite must never touch the network.
    Same technique as TestTheHarnessCanBeInjectedIntoAnOlderImage above.
    """

    SETS = {"SCENARIOS", "EXTRA", "COVERAGE", "TRIAGE", "CONVERSATIONS",
            "CLOSING_SET", "REFUSALS", "BANTER", "MIMICRY"}

    #: Every key the grader actually reads. A key outside this set is dead
    #: weight that looks like a rule — see `_grade` in scripted_call.py.
    KEYS = {"want", "avoid", "must_say", "must_not_say", "faults",
            "grade_from_turn", "believed"}

    def _sets(self):
        import ast

        src = (AGENT_WORKER / "scripted_call.py").read_text(encoding="utf-8")
        out = {}
        for node in ast.parse(src).body:
            if (isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in self.SETS):
                out[node.targets[0].id] = node.value
        return out

    def test_every_named_set_exists(self):
        missing = sorted(self.SETS - set(self._sets()))
        self.assertEqual(missing, [], f"named in SCENARIO_SET but absent: {missing}")

    def test_every_scenario_has_a_name_and_caller_turns(self):
        import ast

        for name, node in self._sets().items():
            self.assertIsInstance(node, ast.List, f"{name} is not a list")
            for i, el in enumerate(node.elts):
                with self.subTest(set=name, index=i):
                    self.assertIsInstance(el, ast.Tuple)
                    self.assertIn(len(el.elts), (2, 3))
                    self.assertIsInstance(el.elts[0], ast.Constant)
                    self.assertIsInstance(el.elts[0].value, str)
                    self.assertIsInstance(el.elts[1], ast.List)
                    self.assertTrue(el.elts[1].elts,
                                    "a scenario with no caller turns runs nothing")

    def test_no_expectation_key_is_a_typo(self):
        """The one that would cost money: a key the grader never reads."""
        import ast

        for name, node in self._sets().items():
            for el in node.elts:
                if len(el.elts) < 3 or not isinstance(el.elts[2], ast.Dict):
                    continue
                label = el.elts[0].value
                for k in el.elts[2].keys:
                    with self.subTest(set=name, scenario=label, key=k.value):
                        self.assertIn(
                            k.value, self.KEYS,
                            f"{k.value!r} is not a key the grader reads, so "
                            "this scenario grades less than it looks like it "
                            "does and would report a clean pass",
                        )

    def test_the_reads_have_a_scenario_at_all(self):
        """The gap that cost a caller 157 seconds on 2026-08-20.

        Every triage scenario was about finding a record to PLAY. The
        commonest question a caller asks — what IS this — had no row, so the
        set could not have caught eleven calls to the lyrics tool and none to
        now_playing. Kept as a test because the absence of a scenario is
        exactly the thing nobody notices.
        """
        import ast

        triage = self._sets()["TRIAGE"]
        names = [el.elts[0].value for el in triage.elts
                 if isinstance(el.elts[0], ast.Constant)]
        lyrics = [n for n in names if "lyric" in n.lower()]
        self.assertGreaterEqual(
            len(lyrics), 3,
            "the reads scenarios are gone: nothing grades 'what song is this' "
            f"any more. Names present: {names}")


# --- the complexity ceiling: the size ledger's missing half ------------------
# Added 2026-08-29 (the maintainability plan, Batch 0). TestNoFileGrowsWithout-
# SomebodyDeciding stops a FILE growing past 600 lines, but a file can sit under
# that ceiling and still be a knot of deeply nested functions — which is exactly
# where a regression ends up with "two candidate causes". This measures
# cyclomatic complexity per function and holds it the same way: over the line is
# a written decision, and a recorded number is a ceiling of its own.

def _cyclomatic_over(root):
    """Every function in agent-worker/ (tests excluded) keyed
    "agent-worker/rel::Qualified.name" -> cyclomatic complexity.

    complexity = 1 + decision points (if / for / while / each with-item /
    except / each boolean operand beyond the first / ternary / comprehension-if
    / assert / match-case), counted over each function's OWN body — a nested def
    is its own entry, not folded into its encloser. The LEDGER below records the
    numbers this produces, so the two can never disagree.
    """
    import ast

    decision = (ast.If, ast.For, ast.AsyncFor, ast.While,
                ast.ExceptHandler, ast.IfExp, ast.Assert)

    def score(body):
        total, stack = 0, list(body)
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # scored as its own entry
            if isinstance(n, decision):
                total += 1
            elif isinstance(n, ast.BoolOp):
                total += len(n.values) - 1
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                total += len(n.items)
            elif isinstance(n, ast.comprehension):
                total += len(n.ifs)
            elif isinstance(n, ast.match_case):
                total += 1
            stack.extend(ast.iter_child_nodes(n))
        return total

    scores = {}
    for path in sorted(Path(root).rglob("*.py")):
        parts = path.parts
        if "tests" in parts or "__pycache__" in parts or ".pytest_cache" in parts:
            continue
        rel = "agent-worker/" + str(path.relative_to(root)).replace("\\", "/")
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))

        def visit(node, prefix):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = prefix + child.name
                    scores["%s::%s" % (rel, name)] = 1 + score(child.body)
                    visit(child, name + ".")
                elif isinstance(child, ast.ClassDef):
                    visit(child, prefix + child.name + ".")
                else:
                    visit(child, prefix)

        visit(tree, "")
    return scores


class TestNoFunctionGrowsTooComplex(unittest.TestCase):
    """Cyclomatic complexity has a ceiling, ratcheted like file size.

    The LEDGER doubles as a map into the review batches (the maintainability
    plan): every entry names the batch that owns the eventual simplification, so
    a walk through the plan and a walk down this list are the same walk. The
    drill harness (scripted_call.py) is here too — it is not shipped, but it is
    scanned like everything else and its branchy dispatch is recorded honestly.
    """

    CEILING = 25

    # "file::qualified.name" -> (recorded complexity, owning batch / reason).
    # Recorded numbers are ceilings: a function may shrink freely, but growing
    # past its number needs a decision in the same commit.
    LEDGER = {
        # scripted_call.py — the drill harness (test tooling, not shipped);
        # scenario dispatch and grading are inherently branchy.
        "agent-worker/scripted_call.py::run_scenario": (75, "harness — scenario runner"),
        "agent-worker/scripted_call.py::summarise": (36, "harness — result summary"),
        "agent-worker/scripted_call.py::main": (35, "harness — arg/lever dispatch"),
        "agent-worker/scripted_call.py::grade_scenario": (33, "harness — grading"),
        # Batch 1 — platform hubs
        "agent-worker/settings.py::_migrate": (33, "Batch 1 — settings migration ladder"),
        # Batch 2 — the api edge
        "agent-worker/api/live.py::handle_live": (55, "Batch 2 — the /live god-dict assembler"),
        "agent-worker/api/diagnostics.py::handle_speed_test": (47, "Batch 2 — diagnostics god-module"),
        "agent-worker/api/chat.py::handle_chat_ws": (46, "Batch 2 — the chat websocket loop"),
        "agent-worker/api/hooks.py::register_station_webhook": (44, "Batch 2 — webhook reconcile"),
        "agent-worker/api/tokens.py::handle_token": (41, "Batch 2 — mint + usage-ceiling ladder"),
        "agent-worker/api/hook_receiver.py::_remember_air": (34, "Batch 2 — two-generation air merge"),
        "agent-worker/api/diagnostics.py::handle_test_env": (33, "Batch 2 — diagnostics god-module"),
        "agent-worker/api/diagnostics.py::handle_test_llm": (32, "Batch 2 — diagnostics god-module"),
        "agent-worker/api/voicemail.py::handle_voicemail_status": (27, "Batch 2 — voicemail status handler"),
        "agent-worker/api/voicemail.py::handle_voicemail_stage": (27, "Batch 2 — voicemail stage handler"),
        "agent-worker/api/settings.py::handle_settings_options": (25, "Batch 2 — provider-discovery gather"),
        # Batch 3 — the call core
        "agent-worker/call/air.py::OnAirGuard.watch": (47, "Batch 3 — the on-air watch loop"),
        "agent-worker/call/providers.py::build_llm": (34, "Batch 3 — multi-provider LLM constructor"),
        # air_verdict._push_verdict was here at 26; Batch 3 folded its three
        # speaking_secs copies into AirVerdict._spoken_secs, dropping it under
        # the ceiling — row removed, per the ratchet.
        # Batch 4 — the call tools
        "agent-worker/call/tools/removal.py::build_removal_tools.clear_from_queue": (58, "Batch 4 — queue-clear matcher"),
        "agent-worker/call/tools/discovery.py::build_discovery_tools.browse_library": (37, "Batch 4 — library browse"),
        "agent-worker/call/tools/albums.py::build_album_tools.queue_album": (32, "Batch 4 — album queue"),
        "agent-worker/call/tools/albums.py::build_album_tools.queue_mix": (25, "Batch 4 — mix queue"),
        # Batch 5 — the brain
        "agent-worker/brain/briefing.py::_fmt_now_playing": (32, "Batch 5 — now-playing formatter"),
        "agent-worker/brain/assemble.py::build_system_prompt": (27, "Batch 5 — prompt assembler entry"),
        "agent-worker/brain/tool_rules.py::_tools": (27, "Batch 5 — the prompt god-function"),
        # Batch 6 — chat / onair / openlines / voicemail
        "agent-worker/voicemail/capture.py::answer": (45, "Batch 6 — voicemail answer pipeline"),
        "agent-worker/openlines/director.py::open_now": (38, "Batch 6 — premise-source ladder"),
        "agent-worker/chat/session.py::ChatSession._tool_loop": (33, "Batch 6 — hand-rolled chat tool loop"),
        "agent-worker/voicemail/deliver.py::_triage": (32, "Batch 6 — voicemail triage dispatch"),
        "agent-worker/openlines/quiz.py::facts_from": (28, "Batch 6 — quiz fact extraction"),
        "agent-worker/voicemail/air.py::deliver": (25, "Batch 6 — voicemail delivery branches"),
    }

    @classmethod
    def setUpClass(cls):
        cls.scores = _cyclomatic_over(AGENT_WORKER)

    def test_the_scan_found_the_functions(self):
        self.assertGreater(len(self.scores), 400,
                           "the function scan has stopped finding the tree")

    def test_no_complex_function_is_unaccounted(self):
        over = sorted("%s (%d)" % (k, c) for k, c in self.scores.items()
                      if c >= self.CEILING and k not in self.LEDGER)
        self.assertEqual(
            over, [],
            "these functions are at or over the complexity ceiling of "
            f"{self.CEILING} and nobody decided that was right. Simplify them, "
            "or add each to LEDGER with the review batch that will: %r" % (over,))

    def test_no_ledgered_function_grew(self):
        grown = sorted("%s was %d, is now %d" % (k, was, self.scores[k])
                       for k, (was, _) in self.LEDGER.items()
                       if k in self.scores and self.scores[k] > was)
        self.assertEqual(
            grown, [],
            "recorded complexity is a ceiling of its own. Simplify, or raise "
            "the number in the same commit and say why that was right: %r" % (grown,))

    def test_no_ledger_entry_outlives_its_complexity(self):
        stale = sorted(k for k in self.LEDGER
                       if k not in self.scores or self.scores[k] < self.CEILING)
        self.assertEqual(
            stale, [],
            "these ledger rows were simplified under the ceiling (or the "
            "function was renamed/removed) — drop the row so it stops masking a "
            "future regression behind an old allowance: %r" % (stale,))


# --- the import-layering guard: an AST sibling of the routing-table test ------
# Added 2026-08-29 (the maintainability plan, Batch 0). The repo already
# AST-asserts one boundary (nothing under api/ imports token_server); this
# encodes the whole layer map so the clean structure can't erode silently.

def _layer_backedges(root):
    """Every cross-layer import in agent-worker/ that runs AGAINST the intended
    dependency direction, as (from_module, to_module, from_layer, to_layer).

    The intended order (an importer may only reach a STRICTLY LOWER layer):
      entrypoints > api > surfaces > call_tools > call > brain > transport > platform
    call and call.tools are a co-recursive PAIR (session builds tools; tools
    reach call helpers), so both directions between them are forward. onair/*
    and voicemail/master are pure low-level transports that depend only on
    platform. Sanctioning happens in the test, not here — this reports the raw
    backward edges so a sanction can't outlive the import it excuses.
    """
    import ast

    order = ["entrypoints", "api", "surfaces", "call_tools", "call",
             "brain", "transport", "platform"]
    rank = {n: i for i, n in enumerate(order)}
    peers = {frozenset({"call", "call_tools"})}

    root = Path(root)
    known, internal = set(), set()
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        dotted = str(path.relative_to(root))[:-3].replace("\\", "/").replace("/", ".")
        if dotted.endswith(".__init__"):
            dotted = dotted[:-len(".__init__")]
        known.add(dotted)
        internal.add(dotted.split(".")[0])

    def layer(mod):
        p = mod.split(".")
        top = p[0]
        if top in ("token_server", "main"):
            return "entrypoints"
        if top == "api":
            return "api"
        if top == "call":
            return "call_tools" if len(p) >= 2 and p[1] == "tools" else "call"
        if top == "brain":
            return "brain"
        if top == "onair":
            return "transport"
        if top == "voicemail":
            return "transport" if (len(p) >= 2 and p[1] == "master") else "surfaces"
        if top in ("chat", "openlines"):
            return "surfaces"
        if top in internal:
            return "platform"
        return None

    def targets_of(tree, pkg):
        found = []

        def add_from(base, names):
            subs = [base + "." + n for n in names if (base + "." + n) in known]
            if subs:
                found.extend(subs)
                if any((base + "." + n) not in known for n in names):
                    found.append(base)
            else:
                found.append(base)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
                if node.level == 0:
                    if node.module:
                        add_from(node.module, names)
                else:
                    base = ".".join(pkg[:len(pkg) - (node.level - 1)]
                                    + ([node.module] if node.module else []))
                    add_from(base, names)
        return found

    edges = []
    for path in sorted(root.rglob("*.py")):
        parts = path.parts
        if "tests" in parts or "__pycache__" in parts or ".pytest_cache" in parts:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        if rel == "scripted_call.py":
            continue  # the drill harness imports product modules to inject them
        from_mod = rel[:-3].replace("/", ".")
        pkg = from_mod.split(".")[:-1]
        fl = layer(from_mod)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for tm in targets_of(tree, pkg):
            tl = layer(tm)
            if tl is None or tl == fl:
                continue
            if rank[tl] > rank[fl] or frozenset({fl, tl}) in peers:
                continue
            edges.append((from_mod, tm, fl, tl))
    return sorted(set(edges))


class TestTheImportLayeringHolds(unittest.TestCase):
    """No module imports against the grain of the layer map, except the few
    deliberate, mostly-deferred back-edges recorded here with their reason.

    The five original exceptions were named in the architecture recon; the two
    open-lines ones are the additive greeting/prompt clause that keeps the DJ
    byte-identical when no line is up. A sanction is scoped to the exact (from,
    to) modules so it excuses that import and nothing broader.
    """

    # (from_module_prefix, to_module_prefix) -> both matched by startswith.
    SANCTIONED = {
        ("settings", "call.air_timing"),       # platform -> call: DUCK_PAD default at load, guarded
        ("settings", "call.tools.registry"),   # platform -> call_tools: the MCP allowlist is derived there
        ("openlines.air", "api.env"),          # surfaces -> api: the public dial-in URL
        ("openlines.director", "api.stats"),   # surfaces -> api: the "nobody listening" gate
        ("voicemail.capture", "api.sounds"),   # surfaces -> api: the uploaded custom-beep path
        ("call.greeting", "openlines.prompt"),  # call -> surfaces: additive open-lines greeting clause
        ("brain.assemble", "openlines.prompt"),  # brain -> surfaces: additive open-lines prompt block
    }

    @classmethod
    def setUpClass(cls):
        cls.back = _layer_backedges(AGENT_WORKER)

    def _sanctioned(self, fm, tm):
        return any(fm.startswith(sf) and tm.startswith(st)
                   for sf, st in self.SANCTIONED)

    def test_the_scan_saw_the_tree(self):
        # A model that matched nothing would pass the grain check forever; the
        # known back-edges are the proof it is still reading real imports.
        self.assertGreaterEqual(
            len(self.back), len(self.SANCTIONED),
            "the import scan stopped finding the known back-edges")

    def test_no_module_imports_against_the_grain(self):
        bad = sorted("%s -> %s (%s -> %s)" % (fm, tm, fl, tl)
                     for fm, tm, fl, tl in self.back if not self._sanctioned(fm, tm))
        self.assertEqual(
            bad, [],
            "these imports run against the layer order (entrypoints > api > "
            "surfaces > call_tools > call > brain > transport > platform; "
            "call <-> call.tools are peers). Move the code to a lower layer, or "
            "— if it is a deliberate, usually deferred exception like the seven "
            "already sanctioned — add the (from, to) pair to SANCTIONED with a "
            "reason: %r" % (bad,))

    def test_no_sanctioned_exception_outlives_its_import(self):
        present = {(fm, tm) for fm, tm, _, _ in self.back}
        stale = sorted(
            "%s -> %s" % (sf, st) for sf, st in self.SANCTIONED
            if not any(fm.startswith(sf) and tm.startswith(st)
                       for fm, tm in present))
        self.assertEqual(
            stale, [],
            "these sanctioned back-edges no longer correspond to a real import "
            "(the code moved) — drop the row so the exception list stays "
            "honest: %r" % (stale,))
