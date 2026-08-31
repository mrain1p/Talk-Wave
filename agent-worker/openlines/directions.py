"""Targeted directions for the DJ's call-in topics — a catalogue, like skills.

The operator's verdict on the invented premises (2026-08-31): "too much, and
always very nebulous — 'song that xyz, call in!'". The open producer note
gives a weak model nothing to push against, so every topic comes out the same
shape. A DIRECTION is the push: a named, specific angle — the way a SUB/WAVE
skill is a named bit with its own brief — that the DJ still writes the actual
subject from, in persona, against tonight's show.

The catalogue is deliberately concrete. A good direction names the SHAPE of
the answer a listener would phone in with (a record, a verdict, a memory),
because "share a song that means something" produces radio mush and "defend
the record you're embarrassed to love" produces calls.

`open_lines_directions` narrows the list (comma-separated ids or label words,
blank = all); the "directions" source picks one at random per open.
"""

from __future__ import annotations

import random

# id, label (the panel's word for it), brief (the producer's angle, handed to
# the DJ verbatim inside the invent note). Keep briefs to one sentence — the
# premise budget belongs to the DJ's own writing, not to this note.
CATALOGUE: tuple[tuple[str, str, str], ...] = (
    ("guilty-pleasure", "Guilty pleasure",
     "Ask them to defend the record they're embarrassed to love — name one "
     "of your own to get it rolling."),
    ("first-record", "First record",
     "The first album or single they ever bought with their own money, and "
     "whether it holds up."),
    ("night-drive", "Night drive",
     "One song for a drive after midnight — windows down, nowhere urgent to "
     "be."),
    ("cover-verdict", "Cover verdict",
     "A cover that beats the original — or one that should never have been "
     "made. Take a side yourself."),
    ("always-skip", "The skip",
     "The song everyone else loves that they always skip, and what it costs "
     "them socially."),
    ("one-lyric", "One lyric",
     "A lyric that stuck with them for years — one line, and why it won't "
     "leave."),
    ("live-moment", "Live moment",
     "The best live set they ever stood in front of — who, where, and the "
     "moment that made it."),
    ("got-them-through", "Got them through",
     "The record that got them through something real — no need to name the "
     "something."),
    ("undiscovered", "Undiscovered",
     "The best song nobody they know has heard — the one they push on "
     "friends who never listen."),
    ("hometown-sound", "Hometown sound",
     "A song that sounds like where they grew up — and what it gets right "
     "or wrong about the place."),
    ("dream-duet", "Dream duet",
     "Two artists, living or dead, who should have made one record together "
     "— and what it would sound like."),
    ("tonights-thread", "Tonight's thread",
     "Pull one thread from tonight's show — a record you played, a theme "
     "you keep circling — and ask the audience to pull it further."),
)


def enabled(cfg: dict) -> list[tuple[str, str, str]]:
    """The catalogue, narrowed by the operator's list. Blank = all of it.

    Matched on the id OR any word of the label, case-insensitively — the same
    forgiveness the persona allowlist learned the hard way: a list typed by
    hand must not silently match nothing.
    """
    raw = str(cfg.get("open_lines_directions") or "").strip()
    if not raw:
        return list(CATALOGUE)
    wanted = {w.strip().casefold() for w in raw.split(",") if w.strip()}
    out = []
    for did, label, brief in CATALOGUE:
        words = {did.casefold()} | {w.casefold() for w in label.split()}
        if wanted & words:
            out.append((did, label, brief))
    # A list that matches nothing is a typo, not a decision to run with an
    # empty catalogue — fall back to everything rather than going silent.
    return out or list(CATALOGUE)


def pick(cfg: dict) -> tuple[str, str, str]:
    """One direction, at random, from the enabled list — the operator's
    "random mode". Random per OPEN, not per boot: two lines in one evening
    should not share an angle just because the process is long-lived."""
    return random.choice(enabled(cfg))
