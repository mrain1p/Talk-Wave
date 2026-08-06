"""Tests about this repo rather than about the product: the structure, the docs, the skills and the commit gate. If one of these fails, something about how the project is kept has slipped.

Split out of test_sidecar.py; see tests/__init__.py.
"""

from __future__ import annotations

import json
import os
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


class TestTheDocsKeepUpWithTheCode(unittest.TestCase):
    """Documentation drift, caught the same way everything else here is.

    0.9.78 added a whole settings section and made call recording optional, and
    the README went on describing neither — the settings table had no row for
    Turn-taking, and "Diagnosing a call" still opened with "each call writes one
    file as it ends", which had just stopped being unconditionally true. Both
    were found by being asked, not by checking, which is the wrong order.

    Deliberately mechanical: it checks that a thing is *mentioned*, not that it
    is described well. A missing row is the failure that actually happens; bad
    prose is a review problem and this cannot judge it.
    """

    @classmethod
    def setUpClass(cls):
        root = REPO
        cls.readme = (root / "README.md").read_text(encoding="utf-8")
        cls.envex = (root / ".env.example").read_text(encoding="utf-8")

    def test_every_settings_section_is_in_the_readme_table(self):
        # The panel builds its sections from GROUPS; the README lists them by
        # title. A new section that nobody can find in the docs may as well be
        # the unreachable-setting bug one level up.
        missing = [title for _, _, title, _ in settings_store.GROUPS
                   if title.lower() not in self.readme.lower()]
        self.assertFalse(
            missing,
            f"settings sections with no mention in README.md: {missing}")

    def test_every_environment_variable_is_documented(self):
        # Only the CURRENT name of each field. Legacy aliases (DEEPGRAM_MODEL)
        # are deliberately undocumented — they exist so an old .env keeps
        # working, not so a new one copies them.
        wanted = set()
        for env_var, _ in settings_store.FIELDS.values():
            if isinstance(env_var, str) and env_var:
                wanted.add(env_var)
            elif isinstance(env_var, tuple) and env_var:
                wanted.add(env_var[0])
        missing = sorted(v for v in wanted if v not in self.envex)
        self.assertFalse(
            missing, f"env vars a setting reads but .env.example never names: "
                     f"{missing}")

    def test_the_shipped_compose_uses_the_data_directory_both_services_share(self):
        # They are one image in two containers and must see the same data/,
        # or a settings change never reaches the worker.
        compose = (REPO / "docker-compose.yaml").read_text(
            encoding="utf-8")
        self.assertEqual(
            compose.count("./data:/data"), 2,
            "both python services must mount the same data directory")


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
        return [p for p in (root / "CLAUDE.md",
                            root / "agent-worker" / "CLAUDE.md",
                            root / "web-widget" / "CLAUDE.md") if p.is_file()]

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
                missing.append(f"{doc.name} -> {ref}")

        self.assertGreater(checked, 10, "found almost no paths to check — the "
                                        "scan regex has probably stopped matching")
        self.assertEqual(missing, [],
                         f"CLAUDE.md names source files that do not exist: {missing}")


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
        gate = [c for c in commands if "test_sidecar" in c]
        self.assertTrue(
            gate, "no PreToolUse hook runs test_sidecar any more — commits are "
                  "no longer gated on the suite")

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
        "agent-worker/settings.py":
            "mostly DEFAULTS and GROUPS — a declaration table, not logic. Long "
            "because the station has a lot of settings, and reading it top to "
            "bottom is how you find one. It is supposed to grow.",
        "agent-worker/api/diagnostics.py":
            "one module per job, and /test/* is genuinely one job: eight probes "
            "that all answer 'can this box reach that thing'. Splitting them "
            "would scatter one answer across eight files.",
    }

    # path -> (lines when the entry was written, what it is waiting to become).
    # Shrinking is always fine and the number should be lowered when it happens.
    # Growing past the recorded size means: split it, or raise the number in the
    # same commit and say in the message what made that the right call.
    SPLITTING = {
        "web-widget/call.js": (
            1107, "the call surface, out of the old app.js. Still above the "
                  "ceiling: the captions, meters and LiveKit wiring each want "
                  "their own file. Next after the panel."),
        "web-widget/panel.js": (
            2105, "the operator surface, out of the old app.js. Settings form, "
                  "the /test/* probes, uploads and the log and call viewers are "
                  "four separable jobs sharing one file. Being split."),
        "web-widget/style.css": (
            1094, "themes both surfaces. Splits when the panel gets its own "
                  "page and can take its own stylesheet with it."),
        "web-widget/index.html": (
            755, "the call page and the panel in one document. The panel moves "
                 "to its own page next."),
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
