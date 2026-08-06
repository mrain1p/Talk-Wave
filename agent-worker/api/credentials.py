"""Where a stored secret is allowed to travel.

Its own module because it is a rule, not a helper: both the settings panel's
option lookups and every test endpoint have to obey it, and the failure it
prevents is silent.
"""

from __future__ import annotations

import logging

log = logging.getLogger("callin.token")


# --- where a stored secret is allowed to travel ---------------------------
# The panel can preview a URL before saving it — type a new TTS server in the
# box, press Test, see whether it works. That override reaches the code that
# builds the real provider, and that code attaches whatever key is stored for
# it. So a URL supplied in a REQUEST could make this process post the stored
# OpenAI key, the TTS key, or the station's admin password to any host the
# requester named. Every one of them came back in the clear against a test
# host, which turns the panel password into the plaintext of every key —
# exactly what storing them server-side is supposed to prevent.
#
# The rule now: a stored secret only ever goes to the host that secret is
# already configured for. A draft URL is still tested, just without the key
# attached, and the answer says so. Save the URL and the key travels again.


def _host_of(url: str) -> str:
    """Scheme-less host:port, for comparing two configured URLs."""
    from urllib.parse import urlparse

    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    return (parsed.netloc or "").lower()


def _is_saved_host(url: str, *saved: str) -> bool:
    """True when `url` points at a host the operator has already configured.

    Several saved values are compared because one field can legitimately have
    more than one home — the MCP endpoint is derived from the station base URL
    when it isn't set explicitly.
    """
    host = _host_of(url)
    if not host:
        return True                 # nothing supplied: the saved value is in use
    return any(host == _host_of(s) for s in saved if s)


def _credentials_travel_to(url: str, *saved: str) -> tuple[bool, str]:
    """(may the stored secret go here, note for the operator if not)."""
    if _is_saved_host(url, *saved):
        return True, ""
    log.warning("withholding stored credentials from unsaved host %s", _host_of(url))
    return False, (
        f"Tested without your stored credentials: {_host_of(url)} is not the "
        "address in your saved settings, and a stored key is only ever sent to "
        "the host it is configured for. Save this URL first to test it for real."
    )
