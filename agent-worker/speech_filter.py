"""
Last line of defence between the model and the speaker.

Three problems this solves, all observed on real calls:

1. **Stage directions.** Models narrate themselves — "*Sound of shuffling
   through records.*", "(laughs)", "[pause]" — and the TTS reads it out loud,
   which instantly breaks the illusion that you're on the phone with a person.
   A prompt rule helps but doesn't hold: models regress under pressure, and a
   single leak is audible. So it's stripped here as well.

2. **Expletives.** A live broadcast wants a filter that doesn't depend on the
   model choosing to behave.

3. **Tool calls typed out as speech.** See `strip_tool_code`.

All three run on the text on its way to synthesis, so they apply no matter
which provider or model is in use.
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

# Speaker labels. Models drift into screenplay format under pressure and write
# "Francesca: Hey there, thanks for holding on" — and the voice reads the name
# out, every turn, like a script being performed rather than a person talking.
#
# Only labels we KNOW are labels get stripped: the persona's own name (set per
# call) and the generic role words. A blanket "Word:" rule would eat ordinary
# speech — "Listen:", "One thing:", "Here's the deal:" are all things a DJ says.
_ROLE_LABELS = ["dj", "host", "caller", "announcer", "narrator", "speaker",
                "you", "me", "assistant", "agent"]
_speaker_names: list[str] = []


def set_speaker(name: str) -> None:
    """Register whose voice this is, so their name stops being read out as a
    script label. Called once per call, when the persona is resolved."""
    global _speaker_names
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    # Full name and first name both: models write either.
    _speaker_names = ([" ".join(parts)] if len(parts) > 1 else []) + parts[:1]


def _label_pattern() -> re.Pattern | None:
    words = [re.escape(w) for w in _speaker_names + _ROLE_LABELS if w]
    if not words:
        return None
    # At the start, or at the start of any line. Optional markdown bold, since
    # models like "**Francesca:**".
    # The bold markers can wrap the name alone ("**Francesca**:") or the whole
    # label including the colon ("**Francesca:**"), so allow them on both sides.
    #
    # Both are matched as an exact PAIR, and the trailing one must sit flush
    # against the colon. A looser rule swallowed the opening asterisk of what
    # came next — "Francesca: *adjusts headphones*" lost its first star, which
    # left the stage-direction rule nothing to match and put the direction on
    # air. That is the exact bug this function exists to prevent.
    return re.compile(
        r"(?:^|(?<=\n))\s*(?:\*\*)?(?:" + "|".join(words)
        + r")(?:\*\*)?\s*:(?:\*\*)?\s*",
        re.IGNORECASE,
    )


def strip_speaker_labels(text: str) -> str:
    """Remove "Name:" script labels from the front of a line."""
    if not text:
        return text
    pattern = _label_pattern()
    if pattern is None:
        return text
    cleaned = pattern.sub("", text)
    if cleaned != text:
        log.info("stripped a speaker label before speaking")
    return cleaned.strip()

# A tool call the model TYPED instead of making. Observed on a real call on
# gemini-2.5-flash-lite, as the whole of the DJ's final turn:
#
#     tool_code
#     print(default_api.subwave_request_song(request='Something relaxing'))
#
# The caller had asked for something relaxing; the record shows no tool ran and
# no song was ever requested, and this went to the TTS, so what they actually
# heard was Python being read aloud. The real fix is a model that routes tools
# properly — but "the model regressed" is the one thing this file exists to
# survive, and a DJ must never be able to speak code down the line.
#
# Four shapes, because the models that do this do not agree on one:
# a fenced block, a bare marker line, an XML-style tag, and the call itself.
_CODE_FENCE = re.compile(r"```[\s\S]*?(?:```|$)")
# Marker lines. Gemini's `tool_code`/`tool_outputs`, plus the words other
# families put on their own line before a call (0.10.58 review broadened this
# past the Gemini-only anchor).
_TOOL_MARKER = re.compile(
    r"(?:^|\n)[ \t]*(?:tool_code|tool_outputs|tool_call|tool_use|"
    r"function_call|functioncall)[ \t]*(?=\n|$)",
    re.IGNORECASE,
)
# XML-tag calls: Hermes/Qwen and others wrap the call in <tool_call>…</tool_call>
# or <function_call>…</function_call>. Strip the whole span, tags and all.
_TOOL_XML = re.compile(
    r"<(tool_call|function_call|tool_use)>[\s\S]*?(?:</\1>|$)",
    re.IGNORECASE,
)
# `print(default_api.foo(...))`, or the bare `<namespace>.foo(...)`. Anchored on
# the namespaces the model families use, at line start, so ordinary speech that
# happens to contain a bracket is untouched. The list is the known-incomplete
# edge the file exists to survive — add a namespace when a model regresses.
_TOOL_CALL = re.compile(
    r"(?:^|\n)[ \t]*(?:print[ \t]*\([ \t]*)?"
    r"(?:default_api|functions|tool_api|tools|multi_tool_use|api|toolbox)"
    r"\.[A-Za-z_]\w*[ \t]*\([^\n]*"
)


def looks_like_tool_code(text: str) -> bool:
    """Did the model type a tool call instead of making one?

    Exposed so the call record can say so — a turn that vanishes here is a
    turn where the caller asked for something and nothing happened, and that
    is worth a line in the transcript rather than silence.
    """
    if not text:
        return False
    return bool(_TOOL_MARKER.search(text) or _TOOL_CALL.search(text)
                or _TOOL_XML.search(text))


def strip_tool_code(text: str) -> str:
    """Remove anything the model wrote as code rather than as speech."""
    if not text:
        return text
    cleaned = _CODE_FENCE.sub(" ", text)
    cleaned = _TOOL_XML.sub(" ", cleaned)
    cleaned = _TOOL_CALL.sub(" ", cleaned)
    cleaned = _TOOL_MARKER.sub(" ", cleaned)
    if cleaned != text:
        log.warning(
            "the model typed a tool call instead of making one — not speaking it"
        )
    return _WHITESPACE.sub(" ", cleaned).strip()


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


# Characters TTS backends read out strangely, replaced with what a person
# would actually say. "&" arrives as "ampersand" on some voices, an em dash
# read literally becomes a hard stop mid-thought — a comma's pause is what
# the writer meant. Operator-reported, both.
_AMP = re.compile(r"\s*&\s*")
_DASH = re.compile(r"\s*[—–]\s*")


def normalize_for_tts(text: str, dash_style: str = "pause") -> str:
    """dash_style is the operator's toggle: 'pause' speaks an em dash as a
    breath (", "), 'hyphen' as a spoken " - ", 'keep' leaves it for the
    backend to interpret. Ampersands always become "and" — no backend says
    that one well."""
    out = _AMP.sub(" and ", text or "")
    if dash_style == "hyphen":
        out = _DASH.sub(" - ", out)
    elif dash_style != "keep":
        out = _DASH.sub(", ", out)
    return out


def clean_for_speech(
    text: str,
    *,
    strip_directions: bool = True,
    profanity_mode: str = "mask",
    profanity_words: list[str] | None = None,
    dash_style: str = "pause",
) -> str:
    out = text or ""
    # Labels first: "Francesca: *adjusts headphones* right then" needs the
    # label gone before the direction rules look at what's left.
    out = strip_speaker_labels(out)
    # Not behind `strip_directions`. That toggle is a matter of taste — an
    # operator can reasonably want "(laughs)" spoken by a theatrical persona.
    # There is no setting under which reading out a function call is wanted.
    out = strip_tool_code(out)
    if strip_directions:
        before = out
        out = strip_stage_directions(out)
        if out != before:
            log.info("stripped stage directions before speaking")
    # BEFORE the profanity mask, which spells its bleep with an em dash
    # ("s—") — normalising after would turn the mask into "s, ".
    out = normalize_for_tts(out, dash_style)
    out = filter_profanity(
        out,
        profanity_words if profanity_words is not None else DEFAULT_PROFANITY,
        profanity_mode,
    )
    return out
