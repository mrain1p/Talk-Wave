"""Whether the written material still describes the code.

README.md and docs/ are what a self-hoster reads before they trust this
with a phone line, and prose cannot self-heal — but it can be made to
fail loudly when the thing it describes moves.

Split out of test_house_rules.py; see tests/__init__.py.
"""

from __future__ import annotations

import unittest

import settings as settings_store
from tests.support import AGENT_WORKER, REPO


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
        # README plus docs/, because 0.9.107 cut the README down to a landing
        # page and moved the reference material behind it. Checking only the
        # README after that would have quietly stopped checking anything.
        cls.docs = "\n".join(
            [(root / "README.md").read_text(encoding="utf-8")]
            + [p.read_text(encoding="utf-8")
               for p in sorted((root / "docs").glob("*.md"))])
        cls.readme = (root / "README.md").read_text(encoding="utf-8")
        cls.envex = (root / ".env.example").read_text(encoding="utf-8")

    def test_every_settings_section_is_documented(self):
        # The panel builds its sections from GROUPS; the docs list them by
        # title. A new section that nobody can find in the docs may as well be
        # the unreachable-setting bug one level up.
        missing = [title for _, _, title, _ in settings_store.GROUPS
                   if title.lower() not in self.docs.lower()]
        self.assertFalse(
            missing,
            f"settings sections with no mention in README.md or docs/: {missing}")

    def test_the_scan_found_the_docs(self):
        # Guards the check above: if docs/ were ever emptied or renamed, every
        # section would go missing at once and the failure would read as a
        # documentation problem rather than a broken test.
        self.assertGreater(len(self.docs), 20000)
        self.assertGreater(len(list((REPO / "docs").glob("*.md"))), 2)

    def test_every_doc_the_readme_links_to_exists(self):
        # A landing page is only useful if the links work, and a moved file
        # breaks them silently — the README still renders perfectly.
        import re

        broken = []
        for target in re.findall(r"\]\((docs/[\w./-]+)\)", self.readme):
            if not (REPO / target).exists():
                broken.append(target)
        self.assertEqual(broken, [], f"README links to missing files: {broken}")

    def test_every_link_between_the_docs_resolves(self):
        # The README's own links were checked; the links docs make to EACH
        # OTHER were not, and two of them were broken for months — an anchor
        # pointing at a section that lives on a different page, so the reader
        # clicked and stayed exactly where they were. Covers relative file
        # links and same-page anchors, in every doc at once.
        import re

        broken = []
        pages = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]
        for page in pages:
            text = page.read_text(encoding="utf-8")
            headings = {
                # GitHub's slug, near enough: lowercased, punctuation dropped,
                # spaces to hyphens.
                re.sub(r"[^a-z0-9\s-]", "", line.lstrip("# ").strip().lower())
                   .replace(" ", "-")
                for line in text.splitlines() if line.startswith("#")
            }
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                if target.startswith("#"):
                    if target[1:] not in headings:
                        broken.append(f"{page.name} -> {target} (no such section)")
                    continue
                path = (page.parent / target.split("#", 1)[0]).resolve()
                if not path.exists():
                    broken.append(f"{page.name} -> {target}")
        self.assertEqual(broken, [], f"broken links: {broken}")

    def test_the_tool_surface_the_docs_describe_is_the_one_that_exists(self):
        # The settings reference used to say "the 17 MCP tools plus the entries
        # we serve ourselves" and then name eight of them, while the registry
        # held 33 and served 23 locally — a number nobody could have known was
        # wrong without counting. Counting is this test's job now.
        from call.tools.registry import LOCAL, MCP, NEVER, NONE, TOOLS

        counts = {
            "total": len(TOOLS),
            "mcp": len([t for t in TOOLS if t.served == MCP]),
            "local": len([t for t in TOOLS if t.served == LOCAL]),
            "never": len([t for t in TOOLS if t.served == NONE]),
        }
        self.assertEqual(counts["total"],
                         counts["mcp"] + counts["local"] + counts["never"])
        settings_doc = (REPO / "docs" / "settings.md").read_text(encoding="utf-8")
        row = next(ln for ln in settings_doc.splitlines() if "**Station tools**" in ln)
        for what, n in counts.items():
            self.assertIn(str(n), row,
                          f"docs/settings.md no longer says how many tools are {what} "
                          f"({n}) — the registry moved and the sentence did not")
        # And the gate the row promises: the destructive ones are never served.
        self.assertTrue(all(t.gate == NEVER for t in TOOLS if t.served == NONE))

    def test_everything_that_can_make_the_dj_speak_is_in_the_call_doc(self):
        """docs/the-call.md lists everything that can start a DJ turn, and
        which of them wait for the broadcast to finish.

        That list is the whole point of the page. Ten separate places can make
        the DJ talk — each added for a real incident, none aware of the others —
        and three of them do not check the air. An eleventh added quietly is
        exactly the kind of thing that only shows up as "the caller heard two of
        the same voice", so the page has to fail the build rather than fall
        behind.

        Mechanical, like everything else here: it checks the module is NAMED,
        not that the page describes it well.
        """
        call_dir = AGENT_WORKER / "call"
        speakers = sorted(
            p for p in call_dir.rglob("*.py")
            if any(needle in p.read_text(encoding="utf-8")
                   for needle in ("generate_reply(", ".say("))
        )
        self.assertGreaterEqual(len(speakers), 6,
                                "the scan found almost nothing — call/ moved?")
        page = (REPO / "docs" / "the-call.md").read_text(encoding="utf-8")
        missing = [
            str(p.relative_to(AGENT_WORKER)).replace("\\", "/")
            for p in speakers
            if str(p.relative_to(AGENT_WORKER)).replace("\\", "/") not in page
        ]
        self.assertEqual(
            missing, [],
            "these can make the DJ speak and docs/the-call.md does not name "
            f"them: {missing}. Add the row, and say whether it waits for the "
            "air — an injector nobody wrote down is one nobody coordinates.",
        )

    def test_the_record_gaps_the_call_doc_lists_are_still_gaps(self):
        """docs/the-call.md's "What a record does NOT contain" is the section a
        reader consults before diagnosing from a call file — it tells them what
        the record cannot answer, so they stop looking there.

        It described two gaps that 0.10.146 had already closed: tool arguments
        and the failed marker both reach the voice path now. Every other claim
        on that page had a test behind it and this one did not, so the page
        went on teaching a reader to distrust two fields that were sitting
        right there. Sending someone hunting for a fault in the wrong place is
        the specific damage a stale line does here.

        Mechanical, and deliberately narrow: it reads what the call path passes
        to CallRecord.tool() and fails if the page still denies it. A gap that
        is REALLY a gap keeps its bullet.
        """
        lifecycle = (AGENT_WORKER / "call" / "lifecycle.py").read_text(
            encoding="utf-8")
        page = (REPO / "docs" / "the-call.md").read_text(encoding="utf-8")
        marker = "## 7. What a record does NOT contain"
        self.assertIn(marker, page, "the section was renamed or removed")
        section = page.split(marker, 1)[1].split("\n## ", 1)[0]
        # A struck-through bullet is a closed gap kept for its reasoning; only
        # a live claim can be wrong about the code.
        live = "\n".join(ln for ln in section.splitlines()
                         if not ln.lstrip("- ").startswith("~~"))

        for passes, claim, what in (
            ("with_args(", "carry no arguments", "the tool's arguments"),
            ("failed=", "not marked failed", "the failed marker"),
        ):
            if passes in lifecycle and claim in live:
                self.fail(
                    f"docs/the-call.md section 7 still claims a call record is "
                    f"missing {what}, but call/lifecycle.py passes {passes!r} to "
                    "CallRecord.tool(). Strike the bullet rather than deleting "
                    "it — the reason it was written usually still holds."
                )

    def test_every_doc_links_back_to_the_readme(self):
        # Landed on from a search engine, a page in docs/ has to say what it is
        # part of, or it is a fragment of a manual with no manual.
        orphans = sorted(
            p.name for p in (REPO / "docs").glob("*.md")
            if "README.md" not in p.read_text(encoding="utf-8"))
        self.assertEqual(orphans, [], f"docs with no way back: {orphans}")

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

    def test_the_env_example_keeps_comments_off_the_value_lines(self):
        # docker compose's env_file format has no inline comments: everything
        # after `=` is the value, `#` included. A real deployment copied this
        # file and its container came up with CALLIN_INTERNAL_URL holding
        # half a sentence of English — and nothing complained anywhere
        # (operator's NAS, 0.10.81). Comments go on their own lines.
        offenders = [
            line for line in self.envex.splitlines()
            if not line.lstrip().startswith("#") and "=" in line
            and "#" in line.split("=", 1)[1]
        ]
        self.assertEqual(offenders, [],
                         "inline comments leak into env values under "
                         f"compose's env_file: {offenders}")

    def test_the_shipped_compose_uses_the_data_directory_both_services_share(self):
        # They are one image in two containers and must see the same data/,
        # or a settings change never reaches the worker. (An init service
        # briefly made this three at 0.10.75; the operator preferred the
        # documented chown step to a whole extra container, 0.10.77.)
        compose = (REPO / "docker-compose.yaml").read_text(
            encoding="utf-8")
        self.assertEqual(
            compose.count("./data:/data"), 2,
            "both python services must mount the same data directory")

    def test_the_compose_keeps_its_load_bearing_lines(self):
        # 0.10.71 rewrote the example compose for readability, and the
        # operator DELIBERATELY dropped the healthcheck/service_healthy
        # pairing this test used to require (the cost: a restart can 502 the
        # TLS door for a few seconds — accepted). What must survive any
        # future trim is pinned here instead: the internal URLs that name
        # other services must name services that exist, the worker keeps its
        # shutdown budget, and a service_healthy gate may only return
        # together with a healthcheck to give it meaning. Read as text
        # rather than parsed, because pyyaml is not a dependency of this
        # suite and never should become one.
        compose = (REPO / "docker-compose.yaml").read_text(encoding="utf-8")
        for name in ("talkwave-worker:", "talkwave-web:", "livekit-server:"):
            self.assertIn("\n  " + name, compose,
                          f"service {name} renamed — every URL and doc that "
                          "names it must move in the same commit")
        self.assertIn(
            "CALLIN_INTERNAL_URL=http://talkwave-web:8100", compose,
            "the worker's slot-release URL must name the web service — a "
            "rename that misses it jams the line for 30 minutes per call")
        self.assertIn(
            "stop_grace_period", compose,
            "without a stop grace the default 10s SIGKILL lands mid-call on "
            "every redeploy")
        if "service_healthy" in compose:
            self.assertIn(
                "healthcheck:", compose,
                "a service_healthy condition without a healthcheck can never "
                "be satisfied — the stack would not start")


class TestTheDocsStayInThePresent(unittest.TestCase):
    """The operator's rule (2026-08-24): the documentation describes the
    current state only. Version archaeology — "until 0.98.22", "closed at
    0.10.146", "since 0.97.66" — belongs in CHANGELOG.md and git history;
    on a docs page it makes every reader parse a timeline to learn what the
    thing does TODAY. The whole tree was swept once; this keeps it swept.

    CHANGELOG.md is the version record and is exempt. SUB/WAVE version
    requirements are compatibility facts, not archaeology — write them as
    dates ("a SUB/WAVE from July 2026 or newer"), which this pattern does
    not match.
    """

    def test_no_talk_wave_version_number_in_the_docs(self):
        import re

        pat = re.compile(r"\b0\.(?:9\d?|10)\.\d+\b")
        offenders = []
        pages = [REPO / "README.md"] + sorted((REPO / "docs").glob("*.md"))
        for page in pages:
            for i, line in enumerate(
                    page.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line):
                    offenders.append(f"{page.name}:{i}: {line.strip()[:80]}")
        self.assertEqual(
            [], offenders,
            "version numbers in current-state documentation — say what the "
            "thing does now, and leave when it changed to the changelog: "
            f"{offenders}")
