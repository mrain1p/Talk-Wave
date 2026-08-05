"""Where the caller's browser pulls the broadcast from.

The caller is tuned into the station for the duration of the call, because the
station refuses song requests when nobody is listening and someone on the phone
isn't pulling the stream. That only works if the browser can actually load the
audio, and the derived default — the station's own address with /stream.mp3 on
the end — fails in the two most common deployments:

  * behind TLS, a browser refuses to load an http stream into an https page as
    mixed content, and does it silently, so the call just has no station
    behind it;
  * off-LAN, a 192.168.x.x address is unreachable to the caller entirely.

So `tune_in_url` can be set to the station's public stream. It accepts either
a complete mount (https://live.example.com/stream.mp3) or just the origin
(https://live.example.com), because SubWave publishes its own mount list at
/listen.pls — "the always-served MP3 mount first, appending any enabled
optional mounts" — and an operator shouldn't have to know whether this station
is serving opus as well.

MP3 is ordered first on purpose. It is the one mount SubWave always serves and
the one every browser plays; opus/ogg mounts are real but Safari is unreliable
on them, so they belong in the fallback list rather than at the front of it.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

log = logging.getLogger("callin.tunein")

# Long enough that a call doesn't pay for discovery, short enough that turning
# a mount on in the station shows up without a redeploy.
_CACHE_TTL = 300.0
_cache: dict[str, tuple[float, list[str]]] = {}

_AUDIO_SUFFIXES = (".mp3", ".opus", ".ogg", ".oga", ".aac", ".m4a", ".flac", ".wav")

# What a playlist is allowed to be. A station runs one or two mounts; these
# only bound what an unexpected answer upstream can turn into.
_MAX_MOUNTS = 8
_MAX_SCANNED = 200

# A .pls is `File1=http://...`; an .m3u is bare URLs. Reading both means the
# discovery doesn't care which one the station decided to hand back.
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


def is_a_mount(url: str) -> bool:
    """Has the operator given us a stream, or the station it lives on?"""
    url = (url or "").strip()
    if not url:
        return False
    tail = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()
    return tail.endswith(_AUDIO_SUFFIXES)


def _rank(path: str) -> tuple[int, str]:
    """MP3 first — always served, and the only format every browser plays.

    In practice a station runs one mount at a time, so this rarely has more
    than one thing to sort. It matters on the day someone enables opus
    alongside it and Safari callers would otherwise get the one that doesn't
    play there.
    """
    return (0 if path.lower().split("?", 1)[0].endswith(".mp3") else 1, path)


def _parse_playlist(text: str) -> list[str]:
    """The PATHS a station publishes — never the hosts it publishes them on.

    This is the whole subtlety. A station generates its playlist from its own
    configured address, which is routinely internal: asked over a public
    https origin, one real deployment answered

        #EXTM3U
        #EXTINF:-1,Yosemite FM
        http://192.168.1.245:7700/stream.mp3

    Taking that URL whole would hand the browser the exact unreachable
    plain-http LAN address this setting exists to escape — discovery would
    make things worse than no discovery at all. So the operator's origin
    always wins, and the station only gets to say which path sits on it.
    """
    seen, out = set(), []
    for match in _URL_RE.findall(text or "")[:_MAX_SCANNED]:
        url = match.strip().rstrip(",;")
        path = "/" + url.split("//", 1)[-1].split("/", 1)[-1] if "//" in url else url
        if path in ("/", "") or path in seen or len(path) > 300:
            continue
        seen.add(path)
        out.append(path)
        # A station serves one or two mounts. Whatever answered with hundreds
        # is not a playlist, and every one of these is copied into /live, which
        # every open widget polls — so a bad answer upstream must not become a
        # payload this service repeats to everybody.
        if len(out) >= _MAX_MOUNTS:
            log.info("playlist had more than %d mounts — keeping the first few",
                     _MAX_MOUNTS)
            break
    return sorted(out, key=_rank)


async def discover(origin: str, *, timeout: float = 4.0) -> list[str]:
    """Mounts on the operator's origin, newest answer cached briefly.

    Failure is not worth surfacing: a station that publishes no playlist still
    serves /stream.mp3, which is what the caller gets.
    """
    origin = origin.strip().rstrip("/")
    hit = _cache.get(origin)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return list(hit[1])

    paths: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for name in ("listen.m3u", "listen.pls"):
                try:
                    r = await client.get(f"{origin}/{name}")
                except httpx.HTTPError:
                    continue
                if r.status_code == 200 and r.text:
                    paths = _parse_playlist(r.text)
                    if paths:
                        break
    except Exception as e:                                    # noqa: BLE001
        log.info("stream mount discovery failed for %s: %s", origin, e)

    mounts = [origin + p for p in paths] or [f"{origin}/stream.mp3"]
    _cache[origin] = (time.time(), list(mounts))
    return list(mounts)


async def resolve(cfg: dict, station_base_url: str) -> tuple[str, list[str]]:
    """(what the widget should play, what to fall back to).

    Falling back matters because the mounts a station publishes are not all
    playable everywhere — the browser tries them in order and keeps the first
    that loads.
    """
    configured = str(cfg.get("tune_in_url") or "").strip()

    if configured and is_a_mount(configured):
        return configured, []

    if configured:
        mounts = await discover(configured)
        return mounts[0], mounts[1:]

    # Nothing configured: the historical behaviour, right only on a plain-http
    # LAN deployment.
    derived = station_base_url.rsplit("/api", 1)[0].rstrip("/") + "/stream.mp3"
    return derived, []
