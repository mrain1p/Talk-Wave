# Changelog

Release notes for operators. One entry per push to `main`; the full
commit-by-commit detail is in git history.

## 0.9.147

### The sound board

- **Three bundled clips ship in the image** — two dial-up handshakes and
  the Wilhelm scream — as WAV, so the server-played beep can use them too.
- Call sounds gains a **board**: bundled clips and uploads in one table,
  each playable, with its length and an **editable category** — file your
  shelf however you like, a soft sound pack. Every sound dropdown offers
  the bundled clips alongside uploads, and links to good free sources
  (freesound CC0, Pixabay) sit under the table.

### Wording

- A **Wording section** under The call card: every fixed string — Ringing,
  Answering, On the line, Recording, Hang up, Leave a message, the talk
  bar, the closed-line labels — overridable in the station's own voice.
- All of them, plus the Call button's custom label and the voicemail
  greeting, take **{station} {dj} {show} {track} {tagline}**, filled live;
  an empty or unknown placeholder simply disappears.

## 0.9.146

### Transmission modes

- One section now holds the line's doors: **Take live calls**, **Enable
  voicemail**, and **push to talk per surface** (moved in from the player
  matrix), with a plain statement of what each combination adds up to —
  phone, phone with a machine, voicemail-only, closed — and where the open
  line's talk-over behaviour is decided.

### The conversation, tidied

- Turn-taking sits **between Greeting and Closing the call** and leads with
  "Let the caller talk over the DJ". The **hard ceiling joins Closing the
  call**; the one-field Call length section retires.
- Station awareness is one deliberate block: what's always known (DJ card,
  show write-up and episode angle, current track, station name), what's
  fetched live — and why the takeover permission can name another show even
  with "Know the rest of the line-up" off: **tools look things up for
  themselves**; the dials only shape the prompt.
- Speech hygiene gains the **em-dash toggle** — a breath (default), a plain
  " - ", or leave them — beside a cleaned-up row layout.

### Voice effects

- Three new colours: **AM radio, megaphone, underwater** — the intensity
  dial applies to all six.
- The test can **borrow any DJ's voice** and run **clean or through the
  effect**, side by side.

### Player & embed

- Who's on air leads with the face: DJ photo, show name, tagline, now
  playing, then the photo's shape beside them.
- The embed section says plainly there is **nothing to add by hand**, and
  the snippet follows two real choices — starting look and captions style.
- "Can I leave a message for the DJ?" joins the caller-asks reference,
  tiered like everything else.

## 0.9.145

### The panel stops crying wolf

- **No more phantom "6 unsaved changes" on a fresh load** — the save bar
  now appears only after a real edit; repaints during load briefly disagree
  with themselves and no longer flash it.
- **/settings works as the panel's address** (easier to remember); /panel
  keeps working, and every fetch behind the page is untouched.

### Finding and running things

- **Voice effects moved under Running the line**, between Call sounds and
  Call transcripts, with its intensity dial and test button.
- The dashboard splits the night into **Calls** and **Voicemails** tiles,
  each a jump to its records, and the **On air tile wears the DJ's photo**.
- **Run the full check** moved into the Diagnostics header and now runs
  everything there: pipeline, speed test, recent calls, server logs.

### Saying what things are

- The **MCP endpoint** sits in the open under Station API with a "derived
  automatically" note instead of hiding behind Advanced; the station's four
  buttons share one row; **Test access** drops the "admin".
- Ears says it plainly: **"Built-in Whisper — local, no key (default)"**.
- A **"How the doors work"** reference card leads Permissions & safety: the
  three tiers and the layers behind them — PBKDF2, fail2ban-style lockouts,
  write-only keys, signed tokens, the tool allowlist.
- Guest-code expiry says it runs **per device**.

## 0.9.144

### The line, said plainly

- **Voicemail gets a master switch** — Enable voicemail, then when it
  answers; the 'never' option had been carrying the on/off job invisibly. A
  stored policy migrates to the right switch position.
- **Live calls is its own section**, not a row inside Voicemail — the two
  switches together are the line's mode, and neither depends on the other.
- **Tune-in splits in two**: counting the caller as a listener (what makes
  requests work) and piping the broadcast audibly into the call are now
  separate switches — volume 0 had been carrying the second job as a trick.
- Renames: **On-air ducking** (was Sharing the microphone), **Tune the
  caller into the station**, **Back-to-air commentary** — whose help now
  says plainly it is NOT the mid-call announcements, which run under their
  own permissions.

### The card, held to its shape

- **Hang up is structurally alone** while a call is up — a CSS rule owned
  by the connect/end pair, so no repaint can put "On the line" beside it
  again. The meters band gets width floors: no more MIC OFF folded into a
  tower with the DJ meter squeezed out.

### Recent calls

- Rows wear the diagnostics' own type, each carries the **caller's tier**
  and chips naming the **tools used**, and the problems filter is the same
  toggle style as the thumbs.

### Sounds & sundries

- One blurb carries the formats, limits and WAV-preferred rules; every
  sound row has its own one-line note; "How many transcripts to keep" says
  so; a greeting with no staged clip falls back to the station clip, then a
  random one — not alphabetically to whoever sorts first.

## 0.9.143

- **The conversation reads top-down**: Station awareness first (what the DJ
  knows), then House style (how it speaks — Answering above Conversation),
  then Greeting, Closing the call, Turn-taking.
- Station awareness says what the numbers count — **Recently played songs,
  Coming-up songs** — and now states what the DJ always knows without a
  dial: its own DJ card, the show's write-up and episode angle, the current
  track, the station's name. The library stays search-on-demand.
- Checkbox help joins its **own line** ("Ask the caller's name", "Let the
  caller talk over the DJ") instead of a band underneath.
- The **duplicate Signing-off box is gone** — 0.9.141 moved it into Closing
  the call but left the original in House style, and the visible one was
  the copy with no placeholder.

## 0.9.142

### Voicemail behaves like an answering machine

- **The mic only opens after the beep.** The worker announces the beep to
  the card, which holds the caller's microphone closed until then — the
  machine cannot hear anyone before it says it is listening.
- **Push to talk applies to voicemail** like any call; the bar reads "Wait
  for the beep…" until the machine is ready.
- **No more hanging up right after the beep**: the nobody-spoke window used
  to start counting before the greeting, so it had expired by beep time.
  It starts at the beep now. (Voicemails stay bounded by the same hourly
  and daily caps as calls — that was already true.)

### The panel

- A **search box** above the sections: type, and only the settings whose
  label or help mention it remain, their sections opened; clearing restores
  the panel exactly as it stood.
- The dashboard's **On air tile shows the DJ's photo**, and a new **Recent
  calls tile** counts the night — calls, voicemails, problems, thumbs — and
  jumps to the records.

## 0.9.141

- Usage-control labels grow their noun — **Calls at once / per hour / per
  day**, **Redial wait time** — and **Call length** (now just the hard
  per-call ceiling) joins them under Permissions & safety, one more spend
  limit beside the others. **Speech hygiene** moves there too.
- **Who answers is now Greeting**, and a new **Closing the call** section is
  its mirror: the sign-off steer, the idle check-ins and the earliest
  hang-up, which were scattered across House style and Call length —
  "where are the closing settings" was a fair question.

## 0.9.140

- The Access credential boxes wear the panel's own field styling again —
  moving them onto one line with their buttons had dropped them back to the
  browser's default chrome, which read as a different, broken control.

## 0.9.139

### Finding your way

- **"Take live calls" moved in with Voicemail**, where its other half lives —
  the two switches together are the line's mode (phone / phone with a
  machine / voicemail-only / closed), and the one operator running
  voicemail-only could not find the way back from under Who answers.
- Super-group headers drop their subtext; the sections under them explain
  themselves.

### Diagnostics

- Recent calls can filter on the **caller's own verdict** — thumbs down or
  thumbs up — beside the problems filter, and each rated call shows its
  verdict in the list.
- The server-log level filter is a **dropdown — "All levels" by default**,
  then a floor per severity ("Warnings and up"), replacing the multi-select
  that opened on an ambiguous nothing.

### Access, tightened

- Each credential is one row with a **set / not-set** chip in its heading,
  and the new-password box only appears once **Change password** is pressed.
- Guest-code expiry is now in **hours, default 24**. A stored minutes value
  keeps its real duration, rounded up — nobody's code expires earlier.

### Sounds

- **mp3 / m4a beeps convert in the browser** on upload — the panel decodes
  and re-wraps as WAV before anything travels, so the server-played beep
  works with what you have. A plain WAV is still preferred: it skips the
  conversion untouched. (m4p stays impossible — Apple DRM.)
- The dimmed voicemail-only rows lose their strikethrough — striking out a
  dropdown read as a fault, not a state.

## 0.9.138

### The panel reads at a glance

- Player-matrix help now flows on the **same line as its label** instead of
  a band per row — the operator's ask, and it halves the section's height.
- Section headers carry their state in **colour**: green for on, dimmed for
  off, instead of "on" and "off" in the same grey.
- The panel's theme control is the **same four-stop cycle as the card** —
  light, dark, the station's show colours, match the device — with the same
  icons and the same remembered choice.

### Call sounds

- A **Voicemail beep preview button** joins the other five, playing your
  uploaded WAV or the synthesized tone.
- The beep's dropdown lists **WAV uploads only**, and its Upload button
  refuses anything else up front — an m4a could only ever become the
  fallback tone, silently. Its convertibility verdict now shows here, in
  Call sounds, rather than among the staging results.
- Per-DJ greeting rows are **one line each** — name, state, the editable
  line and the buttons across.

## 0.9.137

- The voice effect gains an **intensity dial** (0–100): full character down
  to a hint of radio. The caller's browser and the panel's **Test with
  effect** button run the same maths, so what you preview is what callers
  hear.

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
