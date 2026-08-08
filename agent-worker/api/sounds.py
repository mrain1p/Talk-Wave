"""Ring, pickup, hold, hangup, failed — bundled, uploaded, or synthesized.

Serving them is public by necessity: a caller's browser fetches them mid-call.
Uploading and deleting is not — that writes to the operator's disk and the
result plays to every caller.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from aiohttp import web

import sounds as sound_assets
from api.auth import _write_allowed
from api.live_cache import _live_cache
from api.wire import _cors

log = logging.getLogger("callin.token")


# --- uploaded call sounds --------------------------------------------------
# Somewhere to put your own ring without hosting it yourself. A setting whose
# value is "upload:<name>" resolves to a file here; anything else is passed
# through as a URL, so an externally hosted sound still works exactly as before.
SOUNDS_DIR = Path(
    os.environ.get("SOUNDS_PATH", Path(__file__).parent.parent.parent / "data" / "sounds")
)
SOUND_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
               ".m4a": "audio/mp4", ".aac": "audio/aac", ".webm": "audio/webm"}
MAX_SOUND_BYTES = 2 * 1024 * 1024      # a call sound is a second or two
# ...and a bound on the collection, not just on each file. Per-file was the
# only limit, so nothing stopped the same 2MB being uploaded until the volume
# filled — on a NAS that is the volume the settings, the keys and the call
# records live on. There are five sounds a call can use; twenty files is
# already generous room to keep alternatives around.
MAX_SOUND_FILES = 20
MAX_SOUND_TOTAL_BYTES = 20 * 1024 * 1024
UPLOAD_PREFIX = "upload:"


def _safe_sound_name(name: str) -> str:
    """One flat directory, no traversal, no surprises: keep the stem's word
    characters and a known audio extension, drop everything else."""
    import re

    stem, _, ext = str(name or "").rpartition(".")
    ext = "." + ext.lower()
    if ext not in SOUND_TYPES:
        return ""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:48]
    return f"{stem}{ext}" if stem else ""


def _sound_url(value) -> str:
    """Resolve a stored sound setting to something the browser can fetch."""
    raw = str(value or "").strip()
    if raw.startswith(UPLOAD_PREFIX):
        name = _safe_sound_name(raw[len(UPLOAD_PREFIX):])
        return f"/sounds/{name}" if name else ""
    return raw


def _resolved_sound(cfg: dict, pack: str, kind: str) -> str:
    """Uploaded or configured URL -> bundled pack asset -> "" (synthesized).

    The empty string is meaningful: the widget synthesizes whatever it isn't
    given, which is why the product still works with no audio files anywhere.
    """
    return _sound_url(cfg.get(f"sound_{kind}")) or sound_assets.asset_url(pack, kind)


async def handle_pack_sound(request: web.Request) -> web.FileResponse | web.Response:
    """Serve one bundled sound. Public, like the widget's own assets — these
    ship in the image and a caller's browser has to fetch them mid-call."""
    found = sound_assets.file_for(
        request.match_info.get("pack", ""), Path(request.match_info.get("name", "")).stem
    )
    if not found:
        return _cors(request, web.json_response({"error": "no such sound"}, status=404))
    return web.FileResponse(found, headers={"Cache-Control": "public, max-age=3600"})


async def handle_sound_packs(request: web.Request) -> web.Response:
    """Every pack and the sounds it actually bundles.

    The panel's preview buttons use this so a preview plays what a caller
    would really hear, rather than always demonstrating the synthesized set.
    """
    return _cors(request, web.json_response({
        "packs": [
            {"id": pid, "label": label, "assets": sound_assets.assets_for(pid)}
            for pid, label in sound_assets.packs()
        ],
    }))


def _uploaded_sounds() -> list[str]:
    try:
        return sorted(
            p.name for p in SOUNDS_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in SOUND_TYPES
        )
    except OSError:
        return []


# Operator notes about individual sounds — today just the category each
# upload (or bundled clip) is filed under. data/ so it survives upgrades.
META_PATH = Path(os.environ.get("SOUND_META_PATH",
                                SOUNDS_DIR.parent / "sound-meta.json"))


def _sound_meta() -> dict:
    import json

    try:
        with open(META_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _wav_secs(path: Path):
    import wave

    try:
        with wave.open(str(path), "rb") as w:
            return round(w.getnframes() / float(w.getframerate() or 1), 1)
    except (OSError, wave.Error):
        return None


async def handle_sounds_list(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    meta = _sound_meta()
    library = []
    for entry in sound_assets.library():
        entry = dict(entry)
        override = meta.get(entry["name"]) or {}
        if override.get("category"):
            entry["category"] = override["category"]
        library.append(entry)
    uploads = []
    for name in _uploaded_sounds():
        path = SOUNDS_DIR / name
        uploads.append({
            "name": name,
            "secs": _wav_secs(path) if name.lower().endswith(".wav") else None,
            "category": str((meta.get(name) or {}).get("category") or "upload"),
            "url": f"/sounds/{name}",
        })
    return _cors(request, web.json_response(
        {"sounds": _uploaded_sounds(), "prefix": UPLOAD_PREFIX,
         "library": library, "uploads": uploads}))


async def handle_sound_meta(request: web.Request) -> web.Response:
    """File one sound under a category — the operator's own taxonomy, which
    is what makes the shelf filterable like a soft sound pack."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    import json

    try:
        body = await request.json()
    except Exception:
        body = {}
    name = str((body or {}).get("name") or "")
    known = set(_uploaded_sounds()) | {e["name"] for e in sound_assets.library()}
    if name not in known:
        return _cors(request, web.json_response(
            {"error": "no such sound"}, status=404))
    meta = _sound_meta()
    meta.setdefault(name, {})["category"] = str(
        (body or {}).get("category") or "")[:40] or "misc"
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return _cors(request, web.json_response({"ok": True}))


async def handle_sound_lib(request: web.Request) -> web.StreamResponse:
    """A bundled library clip. Public like /sounds — the widget plays these
    on every caller's page."""
    name = request.match_info.get("name", "")
    path = sound_assets.library_dir() / name
    if ("/" in name or "\\" in name or not name.lower().endswith(".wav")
            or not path.is_file()):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        "Cache-Control": "public, max-age=86400", "Content-Type": "audio/wav"})


async def handle_sound_upload(request: web.Request) -> web.Response:
    """Store an uploaded sound. Operator-only — this writes to disk and the
    result is served to every caller."""
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))

    try:
        reader = await request.multipart()
        field = await reader.next()
        while field is not None and field.name != "file":
            field = await reader.next()
        if field is None:
            return _cors(request, web.json_response({"error": "no file"}, status=400))

        name = _safe_sound_name(field.filename or "")
        if not name:
            return _cors(request, web.json_response(
                {"error": "use an mp3, wav, ogg, m4a, aac or webm file"}, status=400))

        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
        target = SOUNDS_DIR / name
        # Checked before writing, and only for a NEW name — replacing a sound
        # you already have must keep working once the shelf is full, or the
        # only way to fix a bad upload would be to delete something else first.
        existing = _uploaded_sounds()
        if name not in existing:
            used = sum((SOUNDS_DIR / f).stat().st_size for f in existing
                       if (SOUNDS_DIR / f).is_file())
            if len(existing) >= MAX_SOUND_FILES:
                return _cors(request, web.json_response(
                    {"error": f"that would be {len(existing) + 1} uploaded sounds; "
                              f"the limit is {MAX_SOUND_FILES}. Delete one you are "
                              f"no longer using."}, status=413))
            if used >= MAX_SOUND_TOTAL_BYTES:
                return _cors(request, web.json_response(
                    {"error": f"uploaded sounds already use "
                              f"{used // (1024 * 1024)} MB, which is the limit. "
                              f"Delete one you are no longer using."}, status=413))
        tmp = target.with_suffix(target.suffix + ".part")
        size = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_SOUND_BYTES:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    return _cors(request, web.json_response(
                        {"error": "that file is over 2 MB — a call sound only needs "
                                  "a second or two"}, status=413))
                f.write(chunk)
        if not size:
            tmp.unlink(missing_ok=True)
            return _cors(request, web.json_response({"error": "empty file"}, status=400))
        # Explicit modes, as everywhere else under data/: a Synology share
        # creates files and directories with no permission bits at all, which
        # root walks through and a normal user cannot. An uploaded sound that
        # the server can write but not read back is a ring tone that silently
        # never plays.
        for path, mode in ((SOUNDS_DIR, 0o755), (tmp, 0o644)):
            try:
                os.chmod(path, mode)
            except OSError:
                pass
        tmp.replace(target)
    except Exception as e:
        log.warning("sound upload failed: %s", e)
        return _cors(request, web.json_response({"error": str(e)[:140]}, status=400))

    log.info("call sound uploaded: %s (%d bytes)", name, size)
    _live_cache["data"] = None
    return _cors(request, web.json_response(
        {"ok": True, "name": name, "value": UPLOAD_PREFIX + name,
         "sounds": _uploaded_sounds()}))


async def handle_sound_delete(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return _cors(request, web.json_response(
            {"error": request.get("auth_error") or "not allowed",
             "authRequired": bool(request.get("auth_required"))}, status=401))
    name = _safe_sound_name(request.match_info.get("name", ""))
    if name:
        (SOUNDS_DIR / name).unlink(missing_ok=True)
        log.info("call sound removed: %s", name)
    _live_cache["data"] = None
    return _cors(request, web.json_response({"ok": True, "sounds": _uploaded_sounds()}))


async def handle_sound_file(request: web.Request) -> web.StreamResponse:
    """Public: every caller's browser has to be able to fetch these."""
    name = _safe_sound_name(request.match_info.get("name", ""))
    path = SOUNDS_DIR / name if name else None
    if not path or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        "Cache-Control": "public, max-age=3600",
        "Content-Type": SOUND_TYPES.get(path.suffix.lower(), "application/octet-stream"),
    })
