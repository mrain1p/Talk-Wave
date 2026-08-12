"""Whether the written material still describes the code.

README.md and docs/ are what a self-hoster reads before they trust this
with a phone line, and prose cannot self-heal — but it can be made to
fail loudly when the thing it describes moves.

Split out of test_house_rules.py; see tests/__init__.py.
"""

from __future__ import annotations

import unittest

import settings as settings_store
from tests.support import REPO


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

    def test_the_shipped_compose_uses_the_data_directory_both_services_share(self):
        # They are one image in two containers and must see the same data/,
        # or a settings change never reaches the worker. Three mounts since
        # 0.10.75: the two app services plus the init service that hands the
        # directory to their uid before either starts.
        compose = (REPO / "docker-compose.yaml").read_text(
            encoding="utf-8")
        self.assertEqual(
            compose.count("./data:/data"), 3,
            "the worker, the web half and the init service must all mount "
            "the same data directory")

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
        # The init service is why the quick start has no chown step: it hands
        # data/ to uid 1000 before the app starts. A fresh box without it is
        # a silent lockout (root-owned data/ reads as "password configured").
        self.assertIn("talkwave-init:", compose)
        self.assertIn("service_completed_successfully", compose)
        if "service_healthy" in compose:
            self.assertIn(
                "healthcheck:", compose,
                "a service_healthy condition without a healthcheck can never "
                "be satisfied — the stack would not start")
