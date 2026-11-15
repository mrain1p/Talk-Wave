"""A caller asking for something this line was configured not to offer.

The Mina call, 2026-08-22: `allow_album_queue` is `guest`, the caller was
`open`, so the mix tools were never built — and the prompt's off-list did not
mention them either (fixed in 0.98.51). No tool AND no sentence, so the DJ
invented *"it's been a bit stubborn with the queue"*. Nothing was stubborn; a
setting withheld it, and the caller was told a story about a fault instead.

0.98.51 fixed the sentence. This fixes the STRUCTURE, per the receipt-channel
direction in the master plan: a tier denial is the one refusal with nothing to
hang a receipt on, because the tool was never built and so nothing fires. The
answer is not to build refusing stubs (the tool list is long enough) and not
to guess from the DJ's wording (the mistake this whole family of guards keeps
making) — it is to hold the withheld set for the call and watch the CALLER's
turn against it. When they ask for something the line does not offer:

  * the caller gets a card — the operator's "denied card", the fact in a
    channel the persona cannot spin;
  * the DJ gets a note BEFORE it answers, on the same insertion point as
    `door.py` and `stuck.py`, so the first reply is the honest one rather
    than a retraction after a guard caught the second.

Patterns here are deliberately the OPPOSITE trade to `call/asks.py`. That
detector wants recall — a missed ask silently disables the guards built on
it. This one wants PRECISION: a "Not on this line tonight" card for something
the caller never asked about, or worse for something the line actually offers,
is the widget itself telling the caller a falsehood. Every pattern is narrow,
and a miss costs only what tonight already costs — the prompt's off-list
sentence still stands and the no-miming floor still holds.
"""

from __future__ import annotations

import re

from brain.tool_rules import OFF_LIST

# Two station-wide asks the prompt answers with a both-directions rule of its
# own rather than an OFF_LIST sentence (tool_rules.OFF_LIST_EXEMPT says why:
# takeover_bullet and cancel_rule state the refusal outright). That covered
# the PROMPT and left the card: neither gate could ever reach `_off`, so
# "switch the show to Donovan's Pub" or "cancel my request" on a line with
# the switch off got no denied card and no pre-answer note — the exact
# no-receipt refusal this module exists to close (review, 2026-11-15). The
# phrase is the card's wording, in OFF_LIST's own grammar.
ALSO_WITHHELD: tuple[tuple[str, str], ...] = (
    ("allow_takeover", "put a different show or DJ on the air"),
    ("allow_cancel_queue", "take a queued track back out"),
)

# What a caller SAYS when they ask for each gated capability. Keyed by the
# same gate names as OFF_LIST (plus ALSO_WITHHELD's two) so the lists cannot
# name different worlds; a gate with no pattern here is simply never carded
# (the prompt still covers it).
#
# allow_requests is special-cased in `Withheld.__init__`: "play X" is served
# by EITHER the request tool or the exact-queue path, so the music patterns
# only count as withheld when both switches are off.
ASKS_FOR: dict[str, re.Pattern] = {
    "allow_requests": re.compile(
        r"\b(play \S+|request|queue|put on |spin (?:me|us|some)|"
        r"(?:want|wanna|like|love) to hear)\b", re.IGNORECASE),
    "allow_announcements": re.compile(
        r"\b(shout ?out|dedicat\w*|say (?:hi|hello) to|"
        r"tell (?:everyone|the listeners)|announce|"
        r"message (?:on|to) (?:the )?air)\b", re.IGNORECASE),
    "allow_favorite": re.compile(
        r"\b((?:heart|like|favou?rite) (?:this|that|it)\b|"
        r"add (?:this|that|it) to (?:the |your )?favou?rites)\b", re.IGNORECASE),
    "allow_unfavorite": re.compile(
        r"\b(un-?like|un-?heart|"
        r"take (?:the |that |my )?(?:heart|like) (?:off|back)|"
        r"remove (?:the |that |my )?(?:heart|like))\b", re.IGNORECASE),
    "allow_skip_track": re.compile(
        r"\b(skip (?:this|it|that|the|current|song|track)|"
        r"next (?:song|track|one))\b", re.IGNORECASE),
    "allow_skills": re.compile(
        r"\b((?:do|run|play|read) (?:me |us )?(?:a |an |the )?"
        r"(?:weather|news|traffic|segment)|weather report|news update)\b",
        re.IGNORECASE),
    "allow_never_play": re.compile(
        r"\b(never play|ban (?:this|that|it)|block (?:this|that|it)|"
        r"take (?:this|that) (?:off|out of) (?:the )?"
        r"(?:rotation|station|playlist|air))\b", re.IGNORECASE),
    "allow_album_queue": re.compile(
        r"\b((?:whole|full|entire) album|"
        r"queue (?:up )?(?:the |an? )?album|"
        r"(?:make|create|build|spin) (?:me |us )?a (?:mix|playlist)|"
        r"a mix of|mix of them)\b", re.IGNORECASE),
    # The three station-wide asks (review, 2026-11-15). Each is narrow on
    # purpose — see the module docstring: a wrong card is worse than none.
    #
    # A steer is a DIRECTION held for a STRETCH. The stretch is what makes
    # it one: "play something more upbeat" is a single-song request and must
    # never trip this, "more upbeat for the next hour" must. So a direction
    # word has to be followed, within three words, by a duration — close
    # enough that "play that for my mum, she's been up all night" and "I
    # need more coffee to stay up all night" are not steers. "keep it
    # up/going" is praise, not a direction, and is excluded by name; "for a
    # while" is how people talk about themselves, so it is not a duration.
    "allow_steer": re.compile(
        r"\b(steer (?:the )?(?:station|music|show|picks)|"
        r"(?:more|less|fewer|nothing but|play|"
        r"keep (?:it|things|the music|the station)"
        r"(?! up\b| going\b| coming\b))"
        r"(?: [\w'&,-]+){1,3}? "
        r"(?:for (?:the )?(?:next|rest of the) (?:\w+ ){0,2}?"
        r"(?:hours?|minutes|while|night|evening|morning|afternoon|show|day)|"
        r"for an hour|for (?:\w+ )?(?:hours|minutes)|"
        r"all (?:night|evening|day|morning|afternoon)|"
        r"(?:the )?rest of the (?:night|evening|show|day|hour)|"
        r"from (?:now|here) on))\b", re.IGNORECASE),
    # A takeover names a SHOW or a DJ as the thing to change. Bare "put Wade
    # on" is deliberately NOT here: without the station's roster it is the
    # same sentence as "put Dreams on", and carding a song request as a
    # refused show change is the widget telling the caller a falsehood.
    "allow_takeover": re.compile(
        r"\b((?:change|switch|swap) (?:the )?(?:dj|host|presenter|show)\b|"
        r"(?:a|another) different (?:dj|host|presenter|show)\b|"
        r"put (?:someone|somebody) else on|"
        r"(?:switch|change|go|flip) (?:over )?to (?:the )?[\w' ]{1,40}? show\b|"
        r"put (?:the )?[\w' ]{1,40}? show on|"
        r"take ?over (?:the )?(?:show|station|schedule|air))",
        re.IGNORECASE),
    # A cancel names the queued THING — a song, a track, a request — so
    # "cancel the takeover" and "take the heart back" stay with their own
    # gates.
    "allow_cancel_queue": re.compile(
        r"\b(cancel (?:my|that|the|this|our) (?:last )?"
        r"(?:song|track|request|one|pick)|"
        r"un-?queue|"
        r"(?:take|pull|remove|drop|get) [^.?!]{0,40}?"
        r"(?:out of|off|from) (?:the |my )?queue|"
        r"take (?:that|my) (?:song|track|request) back)\b", re.IGNORECASE),
}


def _note(phrase: str) -> str:
    # The same contract as CallActions.refusal(): name the card so a story
    # that contradicts it will be caught, forbid the invented fault, and
    # point at what the line still has.
    return (
        "[Note to you, not from the caller: they have just asked to "
        f"{phrase}, and this line does not offer that tonight — the switch "
        "for it is off. The caller has been shown an official NOT ON THIS "
        "LINE TONIGHT card, so the fact is already public: a story that "
        "contradicts it will be caught. Do NOT invent a station fault, do "
        "not claim you tried and it failed, and do not promise it for later "
        "in the call. Say plainly, in character, that this line doesn't do "
        "that tonight, and offer something the line does have.]"
    )


class Withheld:
    """One conversation's withheld capabilities, watched against each turn.

    Built from the same resolved permission set the tools were built from,
    so it can never disagree with the tool list about what exists tonight.
    """

    def __init__(self, cfg: dict, actions=None) -> None:
        self.actions = actions
        self._carded: set[str] = set()
        self.retune(cfg)

    def retune(self, cfg: dict) -> None:
        """Recompute the withheld set from a fresh permission read.

        The phone freezes its cfg at pickup and never calls this again; the
        text line re-reads settings per message and retunes, so an operator
        flipping a switch mid-chat changes what gets carded — the same
        freshness rule its tools already follow. Cards already shown stand:
        a capability that was off when the caller asked was truly off then.
        """
        cfg = cfg or {}
        self._off: list[tuple[str, str]] = []
        for gate, phrase in OFF_LIST + ALSO_WITHHELD:
            if cfg.get(gate) or gate not in ASKS_FOR:
                continue
            # "play X" is served by either path; only card music asks when
            # neither can. See ASKS_FOR.
            if gate == "allow_requests" and cfg.get("allow_exact_queue"):
                continue
            self._off.append((gate, phrase))

    def hint_for(self, caller_text: str) -> str:
        """The note to put in front of the model, or "" for nothing.

        Cards as it goes, once per gate per conversation: a caller who asks
        for the withheld thing three times has been shown the card already,
        and the third identical card would bury the receipts that matter.
        The NOTE repeats every time — each new turn is a new chance for the
        DJ to invent a fault, and the note is free.
        """
        said = str(caller_text or "").strip()
        if not said:
            return ""
        for gate, phrase in self._off:
            if not ASKS_FOR[gate].search(said):
                continue
            if gate not in self._carded:
                self._carded.add(gate)
                if self.actions is not None:
                    self.actions.denied("not tonight", phrase)
            return _note(phrase)
        return ""
