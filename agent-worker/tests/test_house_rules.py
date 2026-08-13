"""Tests about this repo rather than about the product: its structure, its
skills and its commit gate. If one of these fails, something about how the
project is kept has slipped rather than something a caller would hear.

Documentation drift lives next door in test_docs.py.

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


class TestTheToolEvalUsesFakeTools(unittest.TestCase):
    """tools/tool_eval.py runs the REAL model (`build_llm`) to MEASURE whether
    the DJ fires the right tool on a caller line — but the tools it hands the
    model are FAKE: they only record what was called and return a canned
    string, so an eval run can never take over a show, request a song, or make
    any sound on a live station. Source-read from here (a run needs a provider
    key and a live model, which the suite must never do); this also keeps the
    dev harness NAMED in the suite, the coverage floor every module owes."""

    def test_the_tools_are_fake_and_the_station_is_never_touched(self):
        src = (REPO / "tools" / "tool_eval.py").read_text(encoding="utf-8")
        self.assertIn("FIRED.append", src)   # tools only RECORD
        self.assertIn("FAKE tools", src)      # and say so
        # No real station client or MCP toolset is built — the model is handed
        # the recording stubs, nothing that reaches the booth.
        self.assertNotIn("StationClient", src)
        self.assertNotIn("MCPToolset", src)


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
            "mostly DEFAULTS and GROUPS — a declaration table, not logic. Long "
            "because the station has a lot of settings, and reading it top to "
            "bottom is how you find one. It is supposed to grow.",
        "web-widget/panel.html":
            "a settings form with eighty-odd controls is this long. There is no "
            "build step here by choice, so there is no include mechanism to "
            "break it up with, and splitting the markup across pages would "
            "scatter one form the operator reads top to bottom.",
        "agent-worker/api/diagnostics.py":
            "one module per job, and /test/* is genuinely one job: eight probes "
            "that all answer 'can this box reach that thing'. Splitting them "
            "would scatter one answer across eight files.",
        "agent-worker/station.py":
            "the read-only SUB/WAVE REST client — one method per station "
            "endpoint plus the persona/show resolution that stitches several "
            "reads into one answer. It grows a method when the station gains "
            "an endpoint, the same declarative-surface reason settings.py is "
            "exempt; splitting it scatters one client across files that all "
            "hold the same httpx session and the same last-known-good caches.",
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
            "both pages load it and splitting does not help: only 193 lines "
            "are panel-only while 308 are used by both, so the split leaves "
            "two files that still need each other and one still over the "
            "ceiling. The real problem was that 1,103 lines carried one "
            "comment; it has a table of contents and twelve section markers "
            "now, which is what made it hard to read.",
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
    }

    # path -> (lines when the entry was written, what it is waiting to become).
    # Shrinking is always fine and the number should be lowered when it happens.
    # Growing past the recorded size means: split it, or raise the number in the
    # same commit and say in the message what made that the right call.
    # Add an entry here when something is too long AND has a seam worth
    # cutting — the two splits that came out of 0.9.102 and 0.9.106 both
    # started as one.
    SPLITTING = {
        # 0.10.121 pushed it over with the ducking timeline. The seam was
        # already named in web-widget/CLAUDE.md and is genuinely two viewers:
        # the LOG viewer (renderLog, the level filter, the tail) against the
        # CALL viewer (renderCall, callTime, the action vocabulary, the row
        # list). They share only afetch and showResult, both from Panel.
        # Deliberately NOT cut here: the ducking is being diagnosed on a live
        # deployment, and moving this file in the same change would give any
        # regression two candidate causes.
        "web-widget/panel-viewers.js": (606, "the log viewer split from the "
                                             "call viewer; they share only "
                                             "Panel.afetch and showResult"),
        # 0.9.122 pushed it over adding per-adapter auth and the {voice}
        # endpoint templating for ElevenLabs. The seam is real and one-way:
        # discovery (parse_voice_list, available_voices, pick_speakable_voice,
        # adapter_api_key/adapter_headers) against synthesis (AdapterTTS and
        # its stream). Discovery never reads the class; the class needs two
        # helper names back.
        "agent-worker/tts_adapter.py": (654, "a voice-discovery module split "
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
        # Raised 629 -> 650 at 0.10.121 for the timeline hooks, and 650 -> 670
        # at 0.10.124 for the tail() rewrite, whose comment carries the
        # measurement that overturned the old premise. This is the
        # file that has been diagnosed by hand three times, and the twenty
        # lines that make a duck readable from a call record are worth more
        # than the twenty lines of headroom. The seam below is unchanged.
        "agent-worker/call/air.py": (665, "a verdict module (speaking_secs, "
                                          "DUCK_PAD_SECS, _push_verdict, "
                                          "_assess, _settle) split from the "
                                          "live guard and its watch loop"),
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
