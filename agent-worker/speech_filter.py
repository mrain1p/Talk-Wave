"""
Last line of defence between the model and the speaker.

Two problems this solves, both observed on real calls:

1. **Stage directions.** Models narrate themselves — "*Sound of shuffling
   through records.*", "(laughs)", "[pause]" — and the TTS reads it out loud,
   which instantly breaks the illusion that you're on the phone with a person.
   A prompt rule helps but doesn't hold: models regress under pressure, and a
   single leak is audible. So it's stripped here as well.

2. **Expletives.** A live broadcast wants a filter that doesn't depend on the
   model choosing to behave.

Both run on the text on its way to synthesis, so they apply no matter which
provider or model is in use.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("callin.speech_filter")

# "*shuffles papers*", "*Sound of records*" — asterisk-delimited stage business.
_ASTERISK = re.compile(r"\*[^*\n]{1,120}\*")
# "[pause]", "[sound of rain]" — bracketed directions.
_BRACKET = re.compile(r"\[[^\]\n]{1,120}\]")
# Sound and action words models reach for when they narrate the scene rather
# than speak it. Needed because a stage direction doesn't always LEAD with the
# verb — "(Phone rings)" went out on a real call, because the older rule only
# recognised a parenthetical whose FIRST word was a verb form.
#
# A closed list rather than "any word ending in -s" on purpose: the loose rule
# also swallows ordinary speech like "(about three minutes)".
_DIRECTION_VERBS = (
    "rings|ringing|rang|clicks|clicking|crackles|crackling|fades|fading|"
    "scratches|buzzes|buzzing|beeps|hums|hisses|whirs|creaks|slams|clatters|"
    "thuds|clunks|closes|opens|starts|stops|plays|playing|dials|dialling|"
    "dialing|laughs|laughing|chuckles|sighs|sighing|coughs|pauses|shuffles|"
    "taps|clears|rustles|jingles|squeaks|blares|swells|cuts|continues"
)

# "(laughs)", "(chuckles softly)", "(Phone rings)" — parenthetical *actions*
# only. Ordinary parenthetical speech is left alone: the verb-first form is
# open-ended but must start on the verb, and the verb-last form only fires on
# the closed list above.
_PAREN_ACTION = re.compile(
    r"\((?:"
    r"[a-z]+(?:s|ing|ed)(?:\s+[a-z]+){0,3}"                     # "(shuffles records)"
    r"|"
    r"(?:[a-z]+\s+){1,3}(?:" + _DIRECTION_VERBS + r")"          # "(Phone rings)"
    r")\)",
    re.IGNORECASE,
)
# Musical/emoji noise that has no spoken form.
_SYMBOLS = re.compile(r"[☀-➿\U0001f000-\U0001faff♪♫♩-♯]")

_WHITESPACE = re.compile(r"\s{2,}")

# Deliberately compact. The intent is broadcast hygiene, not exhaustive
# censorship — a longer list belongs in settings where you can see it.
DEFAULT_PROFANITY = [
    "fuck", "fucking", "fucked", "shit", "shitty", "bitch", "bastard",
    "cunt", "dick", "prick", "asshole", "arsehole", "wanker", "bollocks",
    "motherfucker", "twat", "slut", "whore", "nigger", "faggot",
]


def strip_stage_directions(text: str) -> str:
    """Remove anything the model wrote *about* speaking rather than to say."""
    if not text:
        return text
    cleaned = _ASTERISK.sub(" ", text)
    cleaned = _BRACKET.sub(" ", cleaned)
    cleaned = _PAREN_ACTION.sub(" ", cleaned)
    cleaned = _SYMBOLS.sub(" ", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    # Tidy punctuation left stranded by a removal.
    cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"([,.!?;:])\1+", r"\1", cleaned)
    return cleaned


def _mask(word: str) -> str:
    return word[0] + "—" if len(word) > 1 else "—"


def filter_profanity(text: str, words: list[str], mode: str = "mask") -> str:
    """`mask` bleeps the word; `drop` removes it; anything else is a no-op.

    Matches on word boundaries so "assess" and "Scunthorpe" survive.
    """
    if not text or mode == "off" or not words:
        return text

    pattern = re.compile(
        r"\b(" + "|".join(re.escape(w) for w in words if w.strip()) + r")\b",
        re.IGNORECASE,
    )

    def repl(m: re.Match) -> str:
        return "" if mode == "drop" else _mask(m.group(0))

    cleaned, n = pattern.subn(repl, text)
    if n:
        log.info("profanity filter (%s) rewrote %d word(s)", mode, n)
    return _WHITESPACE.sub(" ", cleaned).strip()


def clean_for_speech(
    text: str,
    *,
    strip_directions: bool = True,
    profanity_mode: str = "mask",
    profanity_words: list[str] | None = None,
) -> str:
    out = text or ""
    if strip_directions:
        before = out
        out = strip_stage_directions(out)
        if out != before:
            log.info("stripped stage directions before speaking")
    out = filter_profanity(
        out,
        profanity_words if profanity_words is not None else DEFAULT_PROFANITY,
        profanity_mode,
    )
    return out
