# agent-worker

The Python half. Two entrypoints, one package. See the root [CLAUDE.md](../CLAUDE.md) for how
the pieces fit together and for the invariants that hold across both processes.

## Where things live

```
main.py            worker entrypoint — LiveKit job handler
token_server.py    aiohttp app on :8100 — widget host + settings/admin API (2.5k lines)
call/
  session.py       the call object: prepare() -> start() -> greet()
  lifecycle.py     behaviours attached to a live session (dead air, timeout, quiet caller)
  tools/           what the DJ may do mid-call — registry.py enforces the allowlist
  record.py        the call transcript written to CALLS_PATH
  providers.py     STT/LLM/TTS construction
brain/             system-prompt assembly (assemble / briefing / conduct)
settings.py        layered config, re-read every call
secrets_store.py   API keys — write-only from the browser's point of view
admin_auth.py      PBKDF2 password store, ADMIN + GUEST
station.py         read-only SUB/WAVE REST client
station_config.py  mirrors the station's DJ/TTS config
tts_adapter.py     pluggable TTS, configured by tts-adapters/*.json
```

## Tests

`test_sidecar.py` is the whole suite — 200+ tests. It gates CI: no image reaches `:latest`
without it green.

```bash
LOG_TO_FILE=0 SETTINGS_PATH=/tmp/t.json SECRETS_PATH=/tmp/s.json ADMIN_AUTH_PATH=/tmp/a.json CALLS_PATH=/tmp/calls python -m unittest test_sidecar -v
```

House rules for tests here, and they are firm:

- **Stdlib only** (`unittest`, `tempfile`). The venv needs nothing new to run the suite.
- **Never touch the network.** Fake the client, don't reach for the station.
- `os.environ["LOG_TO_FILE"] = "0"` is set *before* any module that calls `log_setup.setup()`
  is imported, or the run pollutes `data/logs/worker.log`.
- Inherit from `_TempStores` when a test touches settings, secrets or auth — it redirects every
  writable path into a temp dir.
- Class names read as the claim they defend: `TestStoredKeysStayHome`,
  `TestFirstRunIsNotOpenToTheWeb`, `TestJoinTokensExpire`. Not `TestSecretsStore2`.
- When a test exists because something broke on air, say so in a comment. Several already do,
  and that context is why they survive refactors.

## Adding an HTTP route

Routes are registered in one block near the bottom of `token_server.py`. Anything the widget
calls must be registered there — `TestWidgetServerContract` in the suite fails the build if
`app.js` fetches a path the server does not serve.
