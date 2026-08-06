# agent-worker

The Python half. Two entrypoints, one package. See the root [CLAUDE.md](../CLAUDE.md) for how
the pieces fit together and for the invariants that hold across both processes.

## Where things live

```
main.py            worker entrypoint — LiveKit job handler
token_server.py    the routing table, and nothing else: URL -> handler in api/
api/               the HTTP surface, one module per job
  env.py           what the environment said (LiveKit creds, port)
  wire.py          the HTTP edge — CORS, and who we believe a request is from
  auth.py          the two gates: ADMIN (panel) and GUEST (the phone)
  tokens.py        /token, /call-ended, and the ceilings on minting
  live.py          the call card's payload; live_cache.py is what stales it
  sounds.py        ring/pickup/hold/hangup, bundled and uploaded
  widget.py        serving web-widget/, and how long a browser may keep it
  settings.py      the settings API (the store itself is ../settings.py)
  credentials.py   where a stored secret is allowed to travel
  diagnostics.py   /test/*, /prompt, /logs, /calls
  hooks.py         the station's webhooks, and the warm-ping loop
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

The handler goes in the `api/` module named after its job; the route goes in `build_app()` in
`token_server.py`, which is the only routing table there is. Two tests hold that line:
`TestWidgetServerContract` reads `token_server.py` alone and fails the build if the widget
fetches a path the server does not serve, and `TestTheRoutingTableIsInOnePlace` fails if a
handler exists that nothing routes, if a module registers routes of its own, or if anything
under `api/` imports `token_server` back.
