# Changelog

Release notes for operators. One entry per push to `main`; the full
commit-by-commit detail is in git history.

## 0.9.136

- **A voicemail-only line has one door.** With the message button up, the
  Call button hid the fact it could only fail — and a refused call's cleanup
  then forgot the message button, leaving the card stuck without its one
  working door until a reload. The idle buttons now paint from one place,
  on every path.
- **The DJ only speaks once.** A mid-call reconnect re-attached the DJ's
  audio without tearing down the first copy — two playbacks a few
  milliseconds apart, heard as an echo.
- **The embed is just the card.** The 10px inset showed as a white ring on
  hosts whose color-scheme the browser disputed; the card now fills the
  frame edge to edge, square. 308px measured.
- The Voicemail section now **says whether the custom beep can play**,
  tried for real server-side — an unplayable file used to fall back to the
  tone with nothing saying why.

## 0.9.135

- **The custom voicemail beep actually plays.** The worker now converts any
  ordinary WAV — stereo, 8/16/32-bit, any sample rate — to what the line
  plays, instead of silently rejecting a 44.1kHz file and falling back to
  the tone with nothing saying why. Files are capped at 8 seconds; only
  what isn't really a WAV falls back.
- Every sound dropdown now **names its default** — "Default — the Exchange
  set's ring", updating live when the set changes — and the beep's says
  "Classic tone — synthesized", because no set carries it.
- **No thumbs up/down after a voicemail** — there was no conversation to
  rate.

## 0.9.134

- **The embed fits the page it sits on.** The compact card now runs a hard
  height budget — 328px measured, against the 400px a station page's player
  column reserves — so the blank band the frame used to open between the
  sleeve and "Up next" is gone. Volume moved into the meters band (you ·
  vol · DJ) instead of holding a row of its own, the identity block is two
  fixed lines at every width, and every band is pinned: a call cannot change
  the card's height by a pixel — its controls appear inside the same rows.
- The panel's card preview sizes itself from the widget's own height report,
  so the whole card is visible with no scrollbar on either surface tab.
- On a voicemail-only line the Call-button and push-to-talk options show as
  dashed rather than editable — there is no live button for them to shape.
- **Access moved under Permissions & safety**, beside the permissions its
  passwords and door codes guard.
- **The voicemail ceiling really hangs up now.** The recording always stopped
  at the limit, but the room stayed open — the caller sat on a dead line with
  the timer counting. The machine closes the room like a live call ends, and
  the card's timer counts against the machine's own clock ("/ 0:30"), not the
  live call's.
- **The beep is a call sound.** Upload a short WAV under Call sounds →
  Voicemail beep; anything unplayable falls back to the classic tone.

## 0.9.133

- Tests for 0.9.132's seams: the voicemail-only line's refusal, the fresh
  greeting's six-second budget and its fall-back to the staged clip, and the
  station palette staying available to the viewer's theme cycle whatever
  colours the operator chose. The verify skill records the flows to drive.

## 0.9.132

### The line has modes

- A **Take live calls** switch joins the voicemail one, so the line can be a
  phone, a phone with an answering machine, voicemail-only, or closed — and
  the Player preview shows callers what they'll get in each.

### Voicemail

- **Greeting mode**: staged clips (instant, the default) or **fresh each
  call** — a new line written in the persona's own voice at pickup, with the
  staged clip as the backup if it can't make it in time.
- With **nobody on air, the station itself answers** in your default voice
  rather than borrowing a DJ who isn't there. Greetings can use `{station}`,
  `{dj}` and `{show}`, filled in per persona.
- The per-DJ greeting list folds away under its own disclosure, each row
  showing the voice it renders with and its editable line.

### The call card

- The theme control now **cycles four looks**: light, dark, the station's
  show colours, and match-the-page — the icon always shows the next stop.
- The card is rearranged for embeds: state line up top, talk bar and mute
  directly above a bottom-row hang-up, the DJ meter always visible, smaller
  now-playing type, and the mic-hint line is gone. The card reports its
  true content height, so it stops stretching host pages.
- The door-code **lock appears the moment the code is entered**, and it is a
  flat outline rather than a colourful glyph.

### The panel

- The **voice effect moved in with the Voice settings**, where the voice
  lives, and a **Test with effect** button plays the configured voice
  through it.

## 0.9.131

### Voicemail

- A **"Leave a message" button** can sit beside Call, per surface — either
  door, or both. Without it, the Call button still becomes the machine's
  wherever a live call is impossible.
- Each persona's greeting line is editable in place, with Play, Stage and
  Delete per persona. Staging runs one persona at a time with live progress.
- New destination: **triage** — the model reads each message and picks a
  song request, an on-air mention, or a station segment, bounded by the
  caller permissions. A per-tier **"Leave a voicemail"** permission joins
  the matrix.

### The call card

- The theme toggle is a **sun or moon** — the destination, not an abstract
  glyph.
- A **lock button** appears whenever this device holds a door code, and a
  new **guest-code expiry** setting forgets a typed code after a chosen time
  — both for shared and public machines.
- Host pages can now push their **fonts** along with their colours, and a
  page-theme change repaints the card in place instead of reloading it
  mid-call.

### The panel

- A floating **Save / Discard bar** appears the moment anything is unsaved
  and stays until answered.
- The player preview no longer scrolls its own section; each sound's Upload
  button sits beside its dropdown.

### Speech

- "&" is spoken as "and", and em dashes become a natural pause — some
  voices read both literally.

## 0.9.130

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
