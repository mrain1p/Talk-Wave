# How it works

The parts, and the path your voice takes through them.

[← back to the README](../README.md)

---

```
[browser mic] --WebRTC--> [livekit-server] --> [talkwave-worker]
                                                STT -> LLM -> TTS
                                                     |
                                          SUB/WAVE MCP (allowlisted)
```

Your voice reaches a speech-to-text engine, an LLM answers as the DJ who is on air right now, and a text-to-speech voice says it back — a full loop every turn, with the station's own tools attached. Everything runs on **your** hardware and **your** API keys (or fully local with Ollama and the bundled Whisper).

| Component | What it does |
|---|---|
| `livekit-server` | WebRTC media — one room per call |
| `talkwave-worker` | Resolves the persona, builds the prompt, runs STT → LLM → TTS with MCP tools attached |
| `talkwave-web` | Mints join tokens (the browser never sees LiveKit secrets), serves widget and panel, proxies station reads |
| `web-widget` | The call page — installable to a phone's home screen, or a compact embeddable card |

Inside the worker: one call is one `CallSession`; the tool allowlist is declared once, in `registry.py`, and the runtime surface and the panel's reference both derive from it; the prompt is assembled in `agent-worker/brain/`, with what the DJ *knows* and how it *behaves* in separate files; and anything that changes the station is a local wrapper, never a raw MCP call — which is what makes **Actions per call** a real ceiling.

The path one sentence takes — the prompt and what it costs, the tool surface, how a request is triaged, and everything that can make the DJ speak: [How a call actually works](the-call.md).
