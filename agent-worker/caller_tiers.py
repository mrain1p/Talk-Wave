"""The caller-tier permission ladder — who may do what on the phone.

Peeled out of settings.py (the maintainability plan, Batch 1): this is the
security-critical half a reviewer must audit for fail-closed behaviour, and it
was buried between the FIELDS table and ~1,300 lines of panel copy. A pure leaf
— every function is pure over its arguments and the constants here; it imports
nothing from settings, so settings imports it (and re-exports these names).

The tier is signed into the room name at mint time by the only code that saw
the password, and read back here; it is never re-derived from anything a caller
controls (architecture invariant 7).
"""
from typing import Any


# ---------------------------------------------------------------------------
# Caller tiers.
#
# Least trusted first, and each one includes everything below it. The tier is
# decided when the token is minted (api/auth.caller_tier) from what the caller
# actually typed, and travels to the worker inside the signed room name — so a
# caller cannot raise their own tier without a token they were not given.
#
#   open   got in without typing anything: the line has no code, or is on auto
#          with none set
#   guest  typed the guest code
#   admin  typed the admin password (which opens the phone as well as the panel)
# ---------------------------------------------------------------------------
TIERS = ("open", "guest", "admin")


TIER_OFF = "off"


# The permissions that carry a tier rather than a yes/no. Everything else in
# the perms group is a modifier — "confirm requests before sending" shapes how
# requests work, it is not a capability anyone is being granted — and asking
# who those apply to would be asking a question with no answer.
TIERED_PERMISSIONS = (
    "allow_voicemail",
    "allow_chat",
    "allow_on_air",
    "allow_requests",
    "allow_library_search",
    "allow_sound_search",
    "allow_exact_queue",
    "allow_album_queue",
    "allow_cancel_queue",
    "allow_favorite",
    "allow_unfavorite",
    "allow_announcements",
    "allow_skills",
    "allow_skip_track",
    "allow_dj_segment",
    "allow_takeover",
    "allow_genre_lock",
    "allow_never_play",
)


# Offered to the panel so the three columns are named in one place.
TIER_CHOICES = [
    (TIER_OFF, "Off"),
    ("open", "Anyone who can call"),
    ("guest", "Callers with the guest code"),
    ("admin", "Admin only"),
]


def tier_reaches(need: Any, have: str) -> bool:
    """Whether a caller at tier `have` clears a permission set to `need`.

    The one ladder. It was spelled out as a dict literal in tokens.py (the
    voicemail gate) and again in api/chat.py — the duplicate that drifts —
    and an unknown `need` fails CLOSED for the same reason normalise_tier
    does: a permission that grants itself on a typo cannot be walked back.

    A MISSING need fails closed too, since 2026-09-02 — it used to read as
    "open", which is the one direction a permission must never default.
    Nothing exploited it: every caller passes `cfg.get("allow_…")` and
    settings.load() always supplies the field's default, so `need` was
    never actually absent. But the player's endpoints read raw settings
    rather than a collapsed session config, and the review that found the
    fail-open default was one refactor away from it mattering. The
    docstring above already claimed this behaviour; now the code agrees.
    """
    if need is None or need == "":
        return False
    ladder = {"open": 0, "guest": 1, "admin": 2}
    need_s = str(need)
    return need_s in ladder and ladder.get(have, 0) >= ladder[need_s]


def normalise_tier(value: Any) -> str:
    """Whatever is stored, as one of off/open/guest/admin.

    Tolerant on purpose: this reads a file an operator can edit by hand, and a
    value it cannot make sense of has to fail CLOSED. A permission that grants
    itself to strangers because the JSON said `"yes"` is the one mistake here
    that cannot be walked back — the caller has already been on air.
    """
    if isinstance(value, bool):
        return "open" if value else TIER_OFF
    text = str(value or "").strip().lower()
    if text in TIERS:
        return text
    if text in ("true", "1", "yes", "on", "all", "everyone"):
        return "open"
    return TIER_OFF


def permission_reaches(setting: Any, tier: str) -> bool:
    """Does a caller at `tier` get this permission?"""
    need = normalise_tier(setting)
    if need == TIER_OFF or tier not in TIERS:
        return False
    return TIERS.index(tier) >= TIERS.index(need)


def tier_from_room(room_name: str) -> str:
    """The caller's tier, read back out of the room the token was signed for.

    `callin-<o|g|a>-<12 hex>`, with an optional `l` behind the tier letter
    (`callin-gl-…`) marking an on-air call — see on_air_from_room. Anything
    else — a probe room, a room minted by a version of the token server that
    predates this, a name from somewhere else entirely — comes back as the
    LEAST trusted tier. Failing closed is the only safe direction: the
    alternative is an unrecognised name handing a stranger the operator's own
    permissions.
    """
    parts = str(room_name or "").split("-")
    if len(parts) >= 3 and parts[0] == "callin":
        for tier in TIERS:
            if parts[1] in (tier[0], tier[0] + "l"):
                return tier
    return "open"


def tier_from_vm_room(room_name: str) -> str:
    """The same reading for a voicemail room: `vm-<o|g|a>-<12 hex>`.

    Its own spelling rather than a branch inside tier_from_room, because the
    two families are minted apart (api/tokens.py) and only a live call carries
    the on-air letter. Same fail-closed doctrine, for the same reason: an
    unrecognised name comes back as the LEAST trusted tier, since the
    alternative is a stranger's message inheriting the operator's permissions.
    """
    parts = str(room_name or "").split("-")
    if len(parts) >= 3 and parts[0] == "vm":
        for tier in TIERS:
            if parts[1] == tier[0]:
                return tier
    return "open"


def on_air_from_room(room_name: str) -> bool:
    """Whether this call was minted as a live-on-air call.

    Rides the room NAME for the same two reasons the tier does: the name is
    inside the signed grant, so a caller cannot put themselves on air without
    a token nobody minted them, and the worker knows it the instant the job
    starts. The flag is one letter behind the tier so tier_from_room's exact
    matching still fails closed on anything unrecognised.
    """
    parts = str(room_name or "").split("-")
    return (len(parts) >= 3 and parts[0] == "callin"
            and len(parts[1]) == 2 and parts[1][1] == "l"
            and parts[1][0] in {t[0] for t in TIERS})


def permissions_for(cfg: dict, tier: str) -> dict:
    """A copy of the settings with every tiered permission collapsed to a
    plain bool for one caller.

    Everything downstream — the tool registry, the prompt, the local wrappers —
    reads `cfg.get("allow_x")` as a truthy value and always has. Resolving here
    means none of it has to learn about tiers, and, more importantly, none of
    it can accidentally read the raw string: `"off"` is truthy, so a consumer
    that missed the change would have switched every permission ON.
    """
    out = dict(cfg)
    for field in TIERED_PERMISSIONS:
        out[field] = permission_reaches(cfg.get(field), tier)
    return out


def guest_door_open(front_access, guest_is_set: bool, guest_tier: bool) -> bool:
    """Whether the guest tier is reachable — the ONE rule the tier resolver, the
    call card and the panel must all agree on.

    Admin-only admits nobody under admin, so no guest can exist. A CODE-GATED
    door is itself the guest tier — everyone inside typed the code, and treating
    them as strangers would be incoherent whatever the switch says. Only on an
    OPEN line is it a real choice, and there it is `guest_tier`: "anyone can
    ring" and "code-holders are their own tier" are two decisions, and inferring
    the second from whether a code happens to exist made deleting the code the
    only way to say no.

    Consolidated at Batch 2 (2026-08-29): auth.caller_tier and live's card each
    spelled this out, and the comment there warned in so many words that "a
    fourth spelling of it is how the card and the panel disagreed by accident".
    One spelling now — pass the three inputs, get the one answer.
    """
    mode = str(front_access or "auto").lower()
    if mode == "admin":
        return False
    if mode == "guest":
        return guest_is_set
    return guest_is_set and guest_tier
