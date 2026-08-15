"""
The token server: one place that says which URL reaches which handler.

Every handler lives in `api/`, one module per job. This file is the map, and
it is deliberately the only place the routing table exists — the widget's
contract test reads the block below to check that every path app.js fetches is
a path this server actually serves, so a route registered anywhere else would
be invisible to it.

Also serves web-widget/ so the call page and embed.js have a real origin.

Run: python token_server.py   (or via run-local.ps1)
"""

from __future__ import annotations

import logging

from aiohttp import web

import settings as settings_store
from api import wire
from api.diagnostics import (
    handle_calls,
    handle_clear_calls,
    handle_delete_call,
    handle_clear_logs,
    handle_logs,
    handle_mark_call,
    handle_prompt_preview,
    handle_speed_test,
    handle_test_admin,
    handle_test_env,
    handle_test_llm,
    handle_test_station,
    handle_test_tts,
)
from api.env import LIVEKIT_PUBLIC_URL, PORT
from api.stats import handle_stats_listeners, sample_listeners
from api.hook_receiver import handle_hooks_recent, handle_station_hook
from api.hooks import handle_hooks_test, keep_station_warm
from api.auth import handle_guest_login, handle_set_password
from api.live import (
    handle_avatar,
    handle_health,
    handle_live,
    handle_live_preview,
)
from api.voicemail import (
    handle_voicemail_clear,
    handle_voicemail_clip,
    handle_voicemail_clip_delete,
    handle_voicemail_messages,
    handle_voicemail_override,
    handle_voicemail_stage,
    handle_voicemail_status,
)
from api.settings import (
    handle_get_settings,
    handle_post_secrets,
    handle_post_settings,
    handle_settings_options,
    handle_voice_effect_set,
    handle_voice_effects,
)
from api.sounds import (
    handle_pack_sound,
    handle_sound_delete,
    handle_sound_file,
    handle_sound_packs,
    handle_sound_upload,
    handle_sounds_list,
    handle_sound_lib,
    handle_sound_meta,
)
from api.chat import handle_chat_ws
from api.tokens import handle_call_ended, handle_call_feedback, handle_token
from api.widget import WIDGET_DIR, _assets, handle_index
from api.wire import handle_options

import log_setup

log_setup.setup("token-server")
log = logging.getLogger("callin.token")


def build_app() -> web.Application:
    app = web.Application(middlewares=[_assets])
    app.router.add_post("/token", handle_token)
    app.router.add_post("/call-ended", handle_call_ended)
    app.router.add_post("/call-feedback", handle_call_feedback)
    app.router.add_get("/chat/ws", handle_chat_ws)
    app.router.add_post("/hooks/station", handle_station_hook)
    app.router.add_get("/hooks/recent", handle_hooks_recent)
    # Asks the station to push at us and waits for it to arrive — the only
    # thing that can tell "the station accepted our row" from "the station can
    # actually reach this box".
    app.router.add_post("/hooks/test", handle_hooks_test)
    app.router.add_options("/hooks/test", handle_options)
    app.router.add_get("/logs", handle_logs)
    app.router.add_get("/calls", handle_calls)
    # The ACTIVITY strip's listener curve — the one series /calls can't carry.
    app.router.add_get("/stats/listeners", handle_stats_listeners)
    # Operator housekeeping: the transcripts are a caller's words, so removing
    # them must not depend on enough new calls arriving to age them out.
    app.router.add_delete("/calls", handle_clear_calls)
    # One record rather than all of them — see handle_delete_call.
    app.router.add_delete("/calls/{rid}", handle_delete_call)
    # The operator's own verdict on a call, beside the caller's.
    app.router.add_post("/calls/{rid}/mark", handle_mark_call)
    app.router.add_delete("/logs", handle_clear_logs)
    app.router.add_options("/call-ended", handle_options)
    app.router.add_options("/call-feedback", handle_options)
    app.router.add_options("/token", handle_options)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/live", handle_live)
    # What the card WOULD look like with these settings. Admin only, writes
    # nothing — it exists so the panel's preview resolves the look through the
    # same code a real caller does instead of reimplementing it in JS.
    app.router.add_post("/live/preview", handle_live_preview)
    app.router.add_options("/live/preview", handle_options)
    app.router.add_get("/settings", handle_get_settings)
    app.router.add_post("/settings", handle_post_settings)
    app.router.add_options("/settings", handle_options)
    app.router.add_post("/settings/secrets", handle_post_secrets)
    app.router.add_options("/settings/secrets", handle_options)
    app.router.add_post("/auth/password", handle_set_password)
    app.router.add_options("/auth/password", handle_options)
    app.router.add_post("/auth/guest", handle_guest_login)
    app.router.add_options("/auth/guest", handle_options)
    app.router.add_get("/settings/options", handle_settings_options)
    app.router.add_get("/settings/voice-effects", handle_voice_effects)
    app.router.add_post("/settings/voice-effects", handle_voice_effect_set)
    app.router.add_options("/settings/voice-effects", handle_options)
    app.router.add_get("/voicemail/status", handle_voicemail_status)
    app.router.add_post("/voicemail/stage", handle_voicemail_stage)
    app.router.add_get("/voicemail/messages", handle_voicemail_messages)
    app.router.add_delete("/voicemail/messages", handle_voicemail_clear)
    app.router.add_get("/voicemail/greeting/{persona_id}", handle_voicemail_clip)
    app.router.add_delete("/voicemail/greeting/{persona_id}", handle_voicemail_clip_delete)
    app.router.add_post("/voicemail/greeting/{persona_id}", handle_voicemail_override)
    app.router.add_options("/voicemail/greeting/{persona_id}", handle_options)
    app.router.add_options("/voicemail/stage", handle_options)
    app.router.add_options("/voicemail/messages", handle_options)
    app.router.add_get("/avatar/{persona_id}", handle_avatar)
    app.router.add_get("/settings/sounds", handle_sounds_list)
    app.router.add_post("/settings/sounds", handle_sound_upload)
    app.router.add_options("/settings/sounds", handle_options)
    app.router.add_delete("/settings/sounds/{name}", handle_sound_delete)
    app.router.add_get("/sounds/{name}", handle_sound_file)
    app.router.add_get("/sound-lib/{name}", handle_sound_lib)
    app.router.add_post("/settings/sounds/meta", handle_sound_meta)
    # Bundled packs ship in the image, so unlike uploads they are read-only
    # and need no auth — a caller's browser fetches them mid-call.
    app.router.add_get("/pack-sounds/{pack}/{name}", handle_pack_sound)
    app.router.add_get("/sound-packs", handle_sound_packs)
    app.router.add_post("/test/tts", handle_test_tts)
    app.router.add_options("/test/tts", handle_options)
    app.router.add_post("/test/llm", handle_test_llm)
    app.router.add_options("/test/llm", handle_options)
    app.router.add_get("/test/station", handle_test_station)
    app.router.add_post("/test/admin", handle_test_admin)
    app.router.add_options("/test/admin", handle_options)
    app.router.add_get("/prompt", handle_prompt_preview)
    app.router.add_post("/test/env", handle_test_env)
    app.router.add_post("/test/speed", handle_speed_test)
    app.router.add_options("/test/speed", handle_options)
    app.router.add_options("/test/env", handle_options)
    app.router.add_get("/", handle_index)
    # The operator's page on its own URL, so a reverse proxy can put a rule in
    # front of the admin surface that it could never put in front of the phone.
    app.router.add_static("/", WIDGET_DIR, show_index=False, name="widget")
    app.cleanup_ctx.append(keep_station_warm)
    app.cleanup_ctx.append(sample_listeners)
    return app


def warn_if_open_to_the_web() -> None:
    """`*` means any page anywhere may mint a call token against this service.

    No longer the default — 0.9.77 changed it to empty, which is same-origin
    only — but it is still a value an operator can choose, and choosing it
    deserves saying out loud: someone else's site can put your Call button on
    their page and spend your API budget.
    """
    if "*" in wire.allowed_origins():
        log.warning(
            "the embed allowlist is '*' — any page on the internet may embed "
            "this widget and mint call tokens against it, which spends your "
            "LLM and TTS budget. Set it to your own origin(s) in the panel "
            "(Embed on another page) or CALLIN_ALLOWED_ORIGINS; leave it "
            "empty if you do not embed the widget anywhere else."
        )


# aiohttp's default access line repeats a timestamp our own formatter has
# already written, then spends 120 characters on the referer and the browser's
# full user-agent — so in the panel's log viewer every request wrapped onto
# three lines and buried the events worth reading. Client, request, status,
# bytes: everything an operator diagnoses with, nothing they don't.
ACCESS_LOG_FORMAT = '%a "%r" %s %b'


if __name__ == "__main__":
    log.info("call-in widget + token server on http://localhost:%s", PORT)
    warn_if_open_to_the_web()
    log.info("browser will be told to connect to %s", LIVEKIT_PUBLIC_URL)
    settings_store.check_data_dir()
    web.run_app(build_app(), port=PORT, print=None,
                access_log_format=ACCESS_LOG_FORMAT)
