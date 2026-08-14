"""What the card LOOKS like — the answer to "how should this widget dress",
independent of who is asking.

Split from api/live.py at 0.10.131, when the now-playing rail's two new fields
pushed that file over the ceiling. The seam is real and one-way: nothing here
touches a request, a session or the station client. These are pure functions of
the settings map, which is why the suite can call look_payload() with a dict
literal and no server at all — and why they were the obvious 250 lines to lift
out of a module whose other half is four HTTP handlers.

live.py imports these; nothing here imports live.py back.
"""

from __future__ import annotations

import logging
import re

import settings as settings_store
import tune_in
import voice_effects
from api.sounds import _resolved_sound

log = logging.getLogger("callin.token")


def corner_controls(cfg: dict, embed: bool = False) -> dict:
    """Which buttons the call card offers in its top-right corner.

    One decision, made here, for both surfaces. It used to be three unrelated
    mechanisms in the widget — the settings gear hidden by a CSS rule that
    only existed for embeds, the theme toggle by an inline style set in two
    different places, the help button by whether `canAsk` came back — so what
    a caller was offered depended on which surface they happened to be
    looking at, and nobody had decided that. It was just where the rules
    happened to live.

    Still one decision, but now it is asked twice: the standalone page and an
    embed on somebody else's site are different audiences, and answering both
    with one switch made every answer a compromise. `/live` carries both, and
    the widget picks by whether it is in a frame — the server cannot know
    that, and the answer is cached for 30 seconds across every caller anyway.

    The widget may still subtract from this, but only for things this side
    cannot know: a host page that pinned ?theme= has already chosen, and an
    embed never loads the settings panel at all, so a gear there opens
    nothing.
    """
    return {
        "help": bool(cfg.get("embed_caller_help" if embed else "show_caller_help")),
        # Two gates, and both have to pass. The operator can switch the toggle
        # off outright; pinning light or dark also removes it, because there is
        # then nothing to toggle between. "inherit" is not pinned — on the
        # standalone page it behaves as auto.
        #
        # "station" KEEPS the toggle. It used to be pinned too, because the
        # palette's tokens are inline custom properties on :root that outrank
        # every data-theme rule — the toggle flipped the attribute and nothing
        # on screen changed. The operator who chose station colours then
        # reported the toggle "not surfacing" as a bug, which it reads as: the
        # setting that shows the toggle was on, and a different setting was
        # silently vetoing it. The widget now clears the inline tokens when
        # the viewer toggles (shared.js), so the control works instead of
        # being hidden: station colours are the default look, and an explicit
        # viewer choice overrides them.
        "theme": (
            bool(cfg.get("embed_theme_toggle" if embed else "show_theme_toggle"))
            and str(cfg.get("widget_theme") or "auto") not in ("light", "dark")
        ),
        # Never in an embed, and not a setting there: an embed does not load
        # the panel's code, so the gear would open nothing whichever way an
        # operator set it.
        "settings": False if embed else bool(cfg.get("show_settings_gear")),
        # The operator's per-surface switch for the sign-in chip. Whether it
        # actually SHOWS is decided per request (a code must exist and the
        # caller must have a tier to climb to) — see _for_this_caller,
        # `signinAvailable`, because that answer depends on the X-Call-Key and
        # cannot ride the cached payload.
        "signin": bool(cfg.get("embed_signin" if embed else "show_signin")),
    }


# The station names its palette one way and this widget names it another, and
# neither is going to change: the station's names are what its own player and
# admin UI are written against, and the widget's are the ones HOST-STYLE-GUIDE
# publishes for host pages to post over `swtv:theme`. So the translation lives
# here, in one direction, once.
#
# Deliberately partial. --ok, --cool and --shadow have no counterpart in the
# station's set, so they keep the widget's own light/dark value and are picked
# up from `mode` below — a green that means "the line is open" is the widget's
# own vocabulary, not the station's, and inventing one from --accent-2 would
# make the state chip stop reporting the transition a caller waits on.
_STATION_TOKENS = {
    "--bg": "--pine",
    "--surface": "--granite",
    "--field": "--granite-hi",
    "--ink": "--alpenglow",
    "--muted": "--sage",
    "--ink-faint": "--sage-dim",
    "--accent": "--coral",
    "--accent-2": "--amber",
    "--soft-border": "--hairline",
    "--line": "--edge",
}

# A colour, and nothing that could be anything else. These values reach a
# browser and are written into inline style, so the widget refuses anything
# suspicious on arrival too — but a station is a trusted source that can still
# be misconfigured, and a token that silently poisons every embed's stylesheet
# is not a failure anyone would trace back to a theme file.
_SAFE_TOKEN = re.compile(r"^[#a-zA-Z0-9(),.%/ _-]{1,120}$")


def station_palette(payload: dict) -> dict | None:
    """The on-air show's palette, in this widget's token names.

    `effective` is the station's own answer to "what should a client paint
    right now" — a show's themeId outranks the station default while it is on
    air — so this follows the programme rather than the settings page, which
    is what "the station's own colours" has to mean for a card sitting next to
    a player that already moved.
    """
    themes = payload.get("themes") or []
    wanted = payload.get("effective") or payload.get("active")
    theme = None
    for t in themes:
        if isinstance(t, dict) and t.get("id") == wanted:
            theme = t
            break
    if theme is None and isinstance(wanted, dict):
        theme = wanted              # some builds send the theme, not its id
    if not isinstance(theme, dict):
        return None

    tokens = {}
    for their, ours in _STATION_TOKENS.items():
        value = str((theme.get("tokens") or {}).get(their) or "").strip()
        if value and _SAFE_TOKEN.match(value):
            tokens[ours] = value
    if not tokens:
        return None
    return {
        "id": theme.get("id") or "",
        "name": theme.get("name") or "",
        # light or dark decides the tokens we did NOT get from the station,
        # and the browser's own form controls and scrollbars.
        "mode": "light" if str(theme.get("mode")).lower() == "light" else "dark",
        "tokens": tokens,
    }


def card_identity(cfg: dict, embed: bool = False) -> dict:
    """Which lines of the "who is on air" block the card paints.

    An embed sits in a column beside the host page's own now-playing ticker
    and show heading, so a second copy of both is noise — but on the
    standalone page they are the only thing saying who you are about to ring.
    Hence one answer each. The DJ's NAME is not switchable: a call card that
    doesn't say who answers isn't a call card.
    """
    p = "embed_" if embed else "show_"
    return {
        "avatar": bool(cfg.get(p + "dj_avatar")),
        "show": bool(cfg.get(p + "dj_show")),
        "tagline": bool(cfg.get(p + "dj_tagline")),
        "track": bool(cfg.get(p + "now_playing")),
    }


def call_button_label(cfg: dict, persona_name: str = "") -> str:
    """What the Call button says before a call starts.

    Resolved here rather than in the widget so the two surfaces cannot drift,
    and so "use the DJ's name" follows the live roster without the widget
    having to know the rule. Falls back the moment the name is missing —
    "Call " with nothing after it is worse than the generic label.
    """
    mode = str(cfg.get("call_button_mode") or "default").lower()
    if mode == "name" and str(persona_name or "").strip():
        return f"Call {str(persona_name).strip()}"
    if mode == "custom":
        return str(cfg.get("call_button_label") or "").strip() or "Call the DJ"
    return "Call the DJ"


def look_payload(cfg: dict, persona_name: str = "") -> dict:
    """Everything about the card that is a LOOK rather than a fact.

    Split out so the settings panel's live preview and a real caller's /live
    resolve it through the same code. The panel previews unsaved settings, and
    the alternative was the panel reimplementing corner_controls,
    card_identity and call_button_label in JavaScript — three rules that
    already exist here, in a file whose whole job is being the one place they
    exist. A preview that disagrees with the card is worse than no preview:
    it is confidently wrong about the thing you opened it to check.

    Nothing here reads the station. It is settings in, appearance out.
    """
    return {
        "theme": str(cfg.get("widget_theme") or "auto"),
        "controls": corner_controls(cfg),
        "embedControls": corner_controls(cfg, embed=True),
        "card": card_identity(cfg),
        "embedCard": card_identity(cfg, embed=True),
        "avatarStyle": (
            "square" if str(cfg.get("avatar_style")) == "square" else "round"
        ),
        "speakerDefault": bool(cfg.get("default_to_speaker")),
        # Like controls/card: /live is cached across every caller and cannot
        # know which surface is asking, so both answers travel and the widget
        # picks on `framed`.
        "liveCalls": bool(cfg.get("live_calls_enabled", True)),
        "vmBtn": bool(cfg.get("show_voicemail_button")),
        "embedVmBtn": bool(cfg.get("embed_voicemail_button")),
        # The text line: enabled is the door's state (The Line's pause is
        # applied widget-side like the other modes), the buttons are per
        # surface like the voicemail pair.
        "chatEnabled": bool(cfg.get("chat_enabled")),
        "chatBtn": bool(cfg.get("show_chat_button")),
        "embedChatBtn": bool(cfg.get("embed_chat_button")),
        # How each door reads — its word, its icon, or both — one answer per
        # feature. The words themselves are the wording overrides; these only
        # decide whether an icon rides in front and whether the word shows at
        # all. The widget shows the word if a door has neither, so it is never
        # blank.
        "callShowWords": bool(cfg.get("call_show_words")),
        "callShowEmoji": bool(cfg.get("call_show_emoji")),
        "vmShowWords": bool(cfg.get("vm_show_words")),
        "vmShowEmoji": bool(cfg.get("vm_show_emoji")),
        # How the DJ's reply arrives, and how fast. Client-side only: the
        # reveal happens in the caller's browser, so the widget needs both.
        "chatReveal": str(cfg.get("chat_reveal") or "typing"),
        "chatTypePace": str(cfg.get("chat_type_pace") or "natural"),
        "chatShowWords": bool(cfg.get("chat_show_words")),
        "chatShowEmoji": bool(cfg.get("chat_show_emoji")),
        "ptt": bool(cfg.get("show_push_to_talk")),
        "embedPtt": bool(cfg.get("embed_push_to_talk")),
        # The card's fixed strings, overrides only — the defaults live in
        # the widget, so a blank costs nothing on the wire.
        "wording": {
            k[len("word_"):]: str(cfg.get(k) or "")
            for k in ("word_ringing", "word_answering", "word_online",
                      "word_recording", "word_hangup", "word_vm_button",
                      "word_ptt", "word_closed", "word_message_only",
                      "word_send", "word_connecting", "word_waiting",
                      "word_ended")
            if cfg.get(k)
        },
        # Whether the transcript labels the DJ's lines with their name.
        "transcriptDjName": bool(cfg.get("transcript_dj_name")),
        "voiceEffect": str(cfg.get("voice_effect") or "none"),
        # Not `or 60`: a stored 0 is a real answer (the clean voice), and
        # the old `or 100` silently turned intensity-zero into full blast.
        # Blank only happens through the preview's unsaved patch.
        "voiceEffectLevel": max(0, min(100, int(
            lvl if (lvl := cfg.get("voice_effect_level")) not in ("", None)
            else 60))),
        # In look_payload as well as /live: the panel's preview exists to
        # show what a setting does to the card, and this one can turn the
        # Call button into "Leave a message".
        "voicemailWhen": settings_store.voicemail_policy(cfg),
        "callLabel": call_button_label(cfg, persona_name),
        "askFeedback": bool(cfg.get("ask_call_feedback")),
        # Per-door (operator's ask): the text line and the machine each read
        # their own switch rather than inheriting the call's.
        "askChatFeedback": bool(cfg.get("ask_chat_feedback")),
        "askVmFeedback": bool(cfg.get("ask_vm_feedback")),
        # An embed sits flush by default; this is the opt-back-in outline.
        "embedOutline": bool(cfg.get("embed_card_outline")),
    }
