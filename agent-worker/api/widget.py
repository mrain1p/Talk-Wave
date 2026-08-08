"""Serving web-widget/ — the call page, and how long a browser may keep it.

The html is never cached, so an image update is picked up immediately. The
100KB of js and css behind it is cached for a year, because its URL carries a
tag that changes exactly when the file does.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
from pathlib import Path

from aiohttp import web

from version import APP_VERSION

log = logging.getLogger("callin.token")

# Not in the stdlib's table, and aiohttp's static handler answers with
# application/octet-stream for anything it cannot name. A manifest served as
# a binary download is not read as a manifest, so the page is silently not
# installable — with a passing suite and nothing in the log.
mimetypes.add_type("application/manifest+json", ".webmanifest")

WIDGET_DIR = Path(
    os.environ.get("WIDGET_DIR", Path(__file__).parent.parent.parent / "web-widget")
)


# name -> (cache key, rendered html). Keyed per page because index.html and
# panel.html reference different scripts and must not share a slot.
_page_cache: dict = {}


def asset_tag(name: str) -> str:
    """The cache key for one widget file: its own modification time.

    Keyed on the FILE, not on APP_VERSION. That distinction is load-bearing.
    These are served `immutable` for a year, so a version-keyed URL means any
    change to app.js without a version bump — a hotfix, an edit in a running
    container, anyone working on the widget — leaves every browser holding the
    old copy until the cache expires. Keying on mtime means the URL changes
    exactly when the bytes do, which is the actual promise `immutable` makes.
    """
    try:
        return str(int((WIDGET_DIR / name).stat().st_mtime))
    except OSError:
        return APP_VERSION


def _versioned_page(name: str) -> str:
    """One of our html pages, with ?v=<tag> on every script and stylesheet.

    The page itself must never be cached — that is what stopped operators
    seeing a stale interface after an image update. But the js and css behind
    it can be cached forever *if the URL changes when they do*. So the html
    stays fresh and the heavy assets stop being re-downloaded on every load.

    Re-read when the file changes, so an edit shows up without a restart.
    """
    path = WIDGET_DIR / name
    html = path.read_text(encoding="utf-8")

    # Whatever the page actually references, rather than names spelled out
    # here. When app.js was split into shared/call/panel a hardcoded list
    # would have kept tagging a file that no longer existed and silently left
    # the three real ones uncached — the failure this whole mechanism exists
    # to prevent, reintroduced by the very change that split the file. The
    # same reasoning is why this takes the page as an argument: index.html and
    # panel.html do not load the same scripts and must not share a cache slot.
    assets = sorted(set(re.findall(r'(?:src|href)="/([\w.-]+\.(?:js|css))"', html)))
    tags = {asset: asset_tag(asset) for asset in assets}

    # Keyed on the html AND every tag it embeds — cache it on its own mtime
    # alone and an edit to one of the assets would keep serving the previous
    # tag, which is the exact bug this is here to prevent.
    key = (path.stat().st_mtime, tuple(sorted(tags.items())))
    cached = _page_cache.get(name)
    if not cached or cached[0] != key:
        for asset, tag in tags.items():
            html = html.replace(f'"/{asset}"', f'"/{asset}?v={tag}"')
        _page_cache[name] = (key, html)
    return _page_cache[name][1]


async def handle_index(request: web.Request) -> web.Response:
    return web.Response(text=_versioned_page("index.html"),
                        content_type="text/html")


# /panel — the operator page's address until 0.9.151, then a redirect —
# is RETIRED outright as of 0.10.8, by the operator's word: one name,
# /settings, and the old one answers 404 like any other unknown path. A
# proxy allowlist belongs in front of /settings (which serves the page for
# text/html and the admin-gated JSON for everything else).


# Compressible and worth compressing. Audio, images and fonts are already
# compressed formats — running deflate over them costs CPU and saves nothing.
_TEXTY = (".html", ".js", ".css", ".json", ".svg", ".map")


@web.middleware
async def _assets(request: web.Request, handler):
    """Cache policy and compression for everything the browser downloads.

    Two things were wrong here. Nothing was compressed — not even when the
    browser asked — so every load pulled ~170KB of text off the wire. And
    everything carried `no-cache`, which exists for a real reason: after an
    image update people saw the OLD interface until a hard refresh, with
    nothing to tell them why.

    Both are now true at once. The html is still never cached, so a new
    version is picked up immediately. It points at `/app.js?v=<version>` and
    `/style.css?v=<version>`, and *those* are immutable for a year — a URL
    that changes whenever its contents do can safely be cached forever. Ask
    for them without the version (an old page, a direct link) and you get the
    old revalidating behaviour, which is correct rather than merely safe.
    """
    resp = await handler(request)
    path = request.path

    if path == "/" or path.endswith(".html"):
        resp.headers["Cache-Control"] = "no-cache"
    elif path.endswith((".js", ".css")):
        # Immutable only when the tag matches the file as it is on disk right
        # now. A stale tag gets revalidation instead, so an old page can never
        # pin a copy that has since changed.
        if request.query.get("v") == asset_tag(path.lstrip("/")):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"

    # Negotiated: aiohttp only compresses when the client said it could, and
    # skips it for a 304, which carries no body anyway.
    if path == "/" or path.endswith(_TEXTY):
        try:
            resp.enable_compression()
            # Because the body now depends on Accept-Encoding, and these are
            # served with a year of `immutable`. Without this a shared cache
            # is entitled to hand a compressed body to a client that never
            # asked for one — which reads as a corrupt file.
            resp.headers["Vary"] = "Accept-Encoding"
        except (AttributeError, RuntimeError):
            pass    # already sent, or a response type that cannot compress
    return resp
