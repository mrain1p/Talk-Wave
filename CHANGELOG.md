# Changelog

Release notes for operators. One entry per push to `main`; the full
commit-by-commit detail is in git history.

## 0.9.128

### Voicemail

The line can now take messages when the booth can't pick up. Off by default;
switch it on in the panel's new **Voicemail** section.

- Answer with voicemail never, only when a live call is impossible (paused,
  busy, off air, over the caps), or always.
- The greeting plays in the on-air DJ's own voice. Greetings are staged ahead
  of time from the panel — one render per persona, reused until the text,
  voice or backend changes — with a per-persona report of anything that
  failed to render.
- Messages go where you choose: held in the panel, sent to the station as a
  song request, or handed to the on-air DJ to mention. Every message lands in
  the panel's list regardless, and a delivery the station refuses is held,
  not lost.
- Nothing is recorded as audio. The message is the transcript, captured
  through the speech-to-text already running, with a hard per-message
  ceiling — and the card says so at the beep. Each message also appears in
  Recent calls, labelled as voicemail.
- Leave `Answer with voicemail` off until you have left one test message on
  your own deployment — the worker leg is new in this release.

### The call card

- **Push to talk**: an optional per-surface talk bar — the caller's mic is
  open only while they hold or latch it; space works on a keyboard. The
  quiet-caller check-in knows the difference between a closed bar and a
  broken microphone.
- **Voice effects**: an optional telephone, CB radio or walkie-talkie colour
  on the DJ's voice, applied in the caller's browser only — the broadcast
  never hears it.
- An embedded card's `data-theme` is now its **starting** theme rather than a
  decree: the light/dark toggle stays and the viewer's choice is remembered.
  Hosts that need one fixed look can set `data-lock-theme="true"`.
- The transcript line no longer renders bold and uppercase inside embeds, and
  the card holds one height on every surface — nothing that happens during or
  after a call moves the page it is embedded in.

### The panel

- New **Voicemail** section: policy, greeting, ceiling, destination, greeting
  staging, and the message list.
- Call sounds are picked from dropdowns — set default, an uploaded file, or a
  URL — with upload-and-assign in one step, and download and remove for
  anything uploaded. Limits (2 MB a file, 20 files / 20 MB total) were always
  enforced and are now stated.
- **Sign out** in the top-right corner, beside the theme toggle.

### Providers

- LLM: DeepSeek, Requesty, Vercel AI Gateway, and any OpenAI-compatible
  server (llama.cpp, vLLM, LM Studio) — matching the providers a SUB/WAVE
  station itself offers, so one key serves both.
- TTS adapters for ElevenLabs, Fish Audio, and SUB/WAVE's own Remote `/speak`
  contract, so a TTS server built for the station can carry the call line.

### Fixes

- 0.9.128's widget shipped with a syntax error that froze the call card at
  "Checking…" on every surface. Fixed, and the build now refuses to ship a
  widget that does not parse.
- Embedded cards never show an internal scrollbar, and the embed's Call
  button matches the height of a host page's own action buttons.

- The theme toggle appears when station colours are active — toggling now
  overrides the palette instead of being hidden by it.
- Push-to-talk mic switches are serialized and verified, fixing a lit bar
  over a muted microphone; if the mic still refuses, the caller is told on
  the card.
- A deploy needs the `Caddyfile`; the README's file list now says so.
