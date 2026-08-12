"""What the environment told us.

The LiveKit credentials and the port are read by more than one route module,
and they must be read after .env is loaded — so the load happens here, once,
and everything else imports the values rather than re-reading os.environ in
its own order.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

PORT = int(os.environ.get("TOKEN_SERVER_PORT", "8100"))


def _keys_from_livekit_yaml() -> tuple[str, str]:
    """The keypair straight from livekit.yaml, so ONE file holds the secret.

    It used to live in two places that had to match by hand — livekit.yaml
    for the media server, .env for these processes — and a fresh install
    tripping over that dance is what prompted this (0.10.72 era). The compose
    mounts livekit.yaml read-only into both python services; when .env
    supplies nothing, the pair is read from here. Parsed with the stdlib on
    purpose: pyyaml is not a dependency of the suite or the app, and the one
    shape LiveKit documents —

        keys:
          <name>: <secret>

    — does not need one.
    """
    path = Path(os.environ.get("LIVEKIT_CONFIG_PATH") or "/etc/livekit.yaml")
    if not path.is_file():
        # A source checkout / run-local.ps1: the repo root's own copy.
        path = Path(__file__).parent.parent.parent / "livekit.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:                                         # noqa: BLE001
        return "", ""
    in_keys = False
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        if not line[:1].isspace():
            in_keys = stripped.strip() == "keys:"
            continue
        if in_keys and ":" in stripped:
            name, _, secret = stripped.strip().partition(":")
            name, secret = name.strip(), secret.strip().strip("'\"")
            if name and secret:
                return name, secret
    return "", ""


def rtc_flags() -> dict | None:
    """The rtc flags from the same livekit.yaml the keypair comes from, or
    None when no yaml is readable (a dev box without one).

    Parsed so the pipeline check can say WHICH flag is wrong instead of
    guessing: a real deployment removed --node-ip from the compose (the
    callers-from-anywhere variant) while livekit.yaml still said
    use_external_ip: false — LiveKit advertised its container address,
    signalling worked, and media had nowhere to flow (0.10.88). The stdlib
    parse, same reasoning as the keypair reader above.
    """
    path = Path(os.environ.get("LIVEKIT_CONFIG_PATH") or "/etc/livekit.yaml")
    if not path.is_file():
        path = Path(__file__).parent.parent.parent / "livekit.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:                                         # noqa: BLE001
        return None
    flags: dict = {"use_external_ip": False, "node_ip": ""}
    in_rtc = False
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        if not line[:1].isspace():
            in_rtc = stripped.strip() == "rtc:"
            continue
        if in_rtc and ":" in stripped:
            key, _, value = stripped.strip().partition(":")
            key, value = key.strip(), value.strip().strip("'\"")
            if key == "use_external_ip":
                flags["use_external_ip"] = value.lower() == "true"
            elif key == "node_ip":
                flags["node_ip"] = value
    return flags


def apply_livekit_keys() -> None:
    """Push the yaml keypair into the environment when .env didn't supply
    one. The SDK and every route read os.environ, so this must run before
    either — it does, at this module's import, and main.py imports it early
    for the worker's sake."""
    if os.environ.get("LIVEKIT_API_SECRET"):
        return
    name, secret = _keys_from_livekit_yaml()
    if name and secret:
        os.environ["LIVEKIT_API_KEY"] = name
        os.environ["LIVEKIT_API_SECRET"] = secret
        return
    # No secret anywhere: the worker will retry-loop on 401s and the token
    # server will mint tokens LiveKit refuses, both of which look like
    # anything but a missing mount. Say the fix ONCE, here, where the gap is
    # known — a real deployment's adapted compose was missing the mounts and
    # the operator diagnosed it from raw 401 logs (0.10.86).
    import logging

    # "Missing" and "present but unreadable" are different fixes, and saying
    # the wrong one sends the operator to the compose file when the problem
    # is a Synology ACL (a file can show rwxrwxrwx+ on the host and still
    # refuse uid 1000 — exactly how this deployment's mounted livekit.yaml
    # failed, 0.10.87).
    path = Path(os.environ.get("LIVEKIT_CONFIG_PATH") or "/etc/livekit.yaml")
    if path.is_file():
        why = (f"{path} is mounted but this process cannot READ it — on a "
               f"Synology an ACL can refuse uid 1000 while ls shows rwx for "
               f"everyone. On the host: chmod 644 livekit.yaml (and "
               f"synoacltool -del livekit.yaml if that alone doesn't take)")
    else:
        why = (f"no livekit.yaml at {path} — mount it into this container: "
               f"./livekit.yaml:/etc/livekit.yaml:ro under BOTH talkwave "
               f"services, as the shipped docker-compose.yaml does")
    logging.getLogger("callin.env").error(
        "no LiveKit keypair: LIVEKIT_API_SECRET is unset and %s. Or set the "
        "keypair in .env. Until then LiveKit refuses every token with a 401.",
        why,
    )


apply_livekit_keys()

LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
# What the *browser* connects to — not the internal docker hostname.
LIVEKIT_PUBLIC_URL = os.environ.get("LIVEKIT_PUBLIC_URL", "ws://localhost:7880")
