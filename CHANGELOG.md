# Changelog

Release notes for operators. One entry per push to `main`; the full
commit-by-commit detail is in git history.

## 0.10.43

No behaviour change — a review pass that put tests around the things the last few releases had shipped untested: the quiet-caller nudge lands as one DJ turn, the idle check-in stays silent while the DJ is the one working, and the four embed shapes were verified end to end.

## 0.10.42

Waiting, quiet, and a longer log — the seconds where a line feels alive or dead.

### The call

- **The DJ never asks "are you still there?" while it's the one working.** A request that takes a moment to resolve used to leave the caller in silence and then get asked if they'd left — while they were waiting on the DJ. Now the DJ holds a "working" flag across a search or a resolving request, and says a short line ("let me dig that out") before the lookup so the wait has a voice on it.

### The text line

- **A quiet caller gets one gentle nudge, not dead air.** When the caller has gone quiet with the ball in their court, the DJ sends one short in-persona line to keep the chat feeling like a conversation rather than a turn-based move — never "are you still there?", and never while the DJ is the one still owing a reply. On by default (~15s); switch and interval under **Text line**. (Close-after-turns/seconds and idle-close were already there.)

### The booth

- **Un-like the track on air** — the admin counterpart to the like: a caller signed in as the operator can take the operator's own heart off the current record. Admin only, badged **Station admin** in the panel.

### The panel

- **The server log shows the most recent 20 and pages back** through older lines, instead of dropping the whole buffer onto the settings page.
- Every permission that needs the station admin credentials is clearly badged **Station admin**, and the permissions, the "what callers can ask" list, the station-tools reference and the caller's "?" popup were reconciled so they all describe what the line can actually do.

## 0.10.41

A new caller action, and two ways the DJ handles a music request better.

- **Callers can like the track on air.** "I love this one" adds a like to the record playing now — the same heart a listener taps in the app, so it needs no station credentials and changes no one's audio. Off by default like every action; turn it on under **Caller permissions → Like the track on air**. It likes the current track only (there's no un-like from a call), and the like lands in the transcript with its own ❤️ receipt.
- **"Songs from the movie X" is read as the soundtrack, not the title.** Asked for music from a film, show or game, the DJ now reaches for the actual tracks that were in it rather than queueing a record that merely has the word in its name — and if the only match is a title-word one, it says so instead of passing it off as the soundtrack.
- **On the text line, the DJ says what it's doing before a slow lookup.** A search or a request takes a few seconds; the DJ now types a quick line in its own voice first ("hold on, let me dig through the racks") so a caller isn't left watching the typing dots wondering if anything is happening.

## 0.10.40

Embedding, and the text line.

### Embedding

- **Four off-the-shelf shapes, and the preview wears the one you pick.** Drop the widget in as an inline card, a floating **launcher** pill in a corner, a **docked** bar across the bottom, or an inline **button** that opens the card in a centred pop-up — chosen in the panel's **Embed** section, where the preview now shows the shape itself (press the mock trigger and the card opens where it would on a real page) so you see what you're copying before you paste it. All of them name who answers, or say the line is closed, before they're pressed.

### The text line

- **Ask what's on and the schedule comes back as a table.** The DJ still talks about it in its own voice, but lays the shows out in a compact table the chat renders as a real one, so a roster reads at a glance instead of as a run-on sentence.
- **Actions stay in the conversation.** When the DJ queues a track or changes the show mid-chat, the receipt now lands in the transcript in its own styling and stays there — it used to flash up as a popup outside the chat and vanish.
- **The DJ's replies type at a readable pace.** A long reply used to flash past too fast to follow; it now streams over a few seconds like someone writing it.

## 0.10.39

More of the same polish, closer to the edges — leaving a message, the narrow phone, signing in, embedding on a station page, and the seconds before the DJ speaks.

### On a station page

- **No colour flash when the widget opens on an embed.** It remembers the station's palette and paints in those colours on the first frame, instead of showing the default accent for a beat until the host page's colours arrive.
- **A DJ with no photo shows their initials, not an empty circle** — including when the station serves a 1×1 placeholder image for a persona that has none.

### Signing in

- **The "what can I ask?" list now names the tier you're actually on.** Signing in from guest to operator on the card, without a reload, relabels the menu to "for the operator" and reopens the actions your tier unlocks — it used to keep saying "for guest callers" until you refreshed.

### Voice

- **The caller's microphone is cleaned up before it's heard** — echo cancellation, noise suppression and automatic gain are on by default, so the station playing in a caller's room isn't transcribed over their own speaker, and on a speakerphone the DJ's voice stays out of the caller's transcript. The README and settings now spell out when a cloud STT or TTS is worth the upgrade over the local defaults.

### Voicemail

- **You can see the message you're leaving, and it stays put when you hang up.** Your words appear as you speak them and stack up sentence by sentence, so you can read back the whole message rather than catching only the last line — and when the recording ends the transcript stays on the card, with a note that the DJ will review your request shortly.

### On a phone

- **The controls fit and the mic state is clearer.** Push-to-talk is a bigger target under the thumb; whether you're muted, live, or holding a push-to-talk line reads off a small chip beside your meter instead of a label that ran into the one next to it; the call timer keeps its place on one line; and the level meters no longer crowd each other on a narrow screen.

### The call

- **Quicker to answer, and it ends less abruptly.** A congested station no longer makes prompt assembly re-read the same thing twice before the DJ can speak, so first words come sooner. When a caller's request sets a show takeover in motion, the new DJ and palette now catch up within a few seconds of it airing rather than a poll later. And the DJ is nudged to leave a real next step in the air at the end of a call instead of stopping flat the moment it's done something.

## 0.10.36

Mostly mobile and polish — on a phone, the card now reads like the real thing.

### On a phone

- **Installs and behaves like an app.** Add Wave Talk to a home screen and it opens full-screen as a progressive web app — a call, a text line, or a voicemail, with the DJ's portrait up top, the conversation in the middle, and the actions under your thumb. The keyboard no longer covers the chat, push-to-talk stays held while your finger is down, and a long reply or a long show name wraps to read cleanly instead of trailing off.

### The text line

- **It greets first, types like a person, and tells the truth.** The booth opens the conversation on a fresh chat; the DJ's reply is written out a character at a time rather than appearing whole; and a request or a show change reports what actually happened rather than an invented reason. Pick the opening line — canned, written each time, or off — under **Text line**.

### The call

- **Faster to answer, and it stops talking over itself.** A slow station no longer stacks timeouts onto the ring, and when the broadcast and the call carry the same DJ they no longer double up. “Change the DJ to Wade” is now understood as a takeover to perform, not a request to refuse.

### The card

- One consistent footprint whether you call, text, or leave a message, so an embed never jars between modes. Each door sizes to fit — a worded button takes the room it needs, a bare icon hugs its glyph — and the level meters pair the You and DJ waveforms with volume set aside.

## 0.10.24

Everything since 0.10.15: the text line, callers signing in for more, the
call-quality fixes, and a batch of front-end polish.

### The text line

- **A third way to reach the booth: type to it.** Typed conversation with whoever is on air — the same brain, the same tools, the same receipts as the phone — over a plain WebSocket. No WebRTC, so it works for callers whose networks block call audio, and it keeps working when the media server is down. Turn it on with **Take text chats** on the dashboard, beside Live calls and Voicemail.
- **Resumable, and it ends cleanly.** A chat picks up where it left off in the same browser; an **End** button closes it (writing the transcript to Recent calls as a text chat), and idle/message/age clocks close forgotten ones.
- **A busy call now offers the text line** as a fallback, the way it already offers voicemail — even on surfaces where the permanent “Text the booth” button is off.
- **Its own settings section** holds the clocks and ceilings, including a per-caller reopen wait and a daily cap — a text line is scriptable where a call is not, so it carries the phone's per-IP brakes.

### Callers can sign in for more

- **A “Sign in” corner button** (per surface, off by default) lets a caller enter the guest code or admin password to **unlock the commands you gated above “anyone”** — the way to run per-tier permissions on a line anyone can reach. Give strangers requests and reads; keep announcements or a show takeover for callers who sign in. The button only appears when a code is set and there is a tier left to climb, and the lock icon signs them back out.
- **The “What can I ask?” menu now reflects the caller’s actual tier** — signing in adds the groups it unlocks, signing out removes them — and the menu is **grouped**: just talk / request music / put something on the air / leave a message. Cancelling a takeover and checking a request’s queue position are listed now too; a test pins that every caller permission has an example, so a gated tool can’t ship invisible.

### Every mode

- **A language / instruction guard**, always on: a caller directing the DJ to switch languages, drop its rules, or follow “instructions” quoted at it is testing the line, not making a request — and is treated as such. Mirrors a fix the station itself made after a real raid.

### Call quality, from reading real calls

- **The station’s colours follow the programme.** With “the station’s own colours” chosen, the card repaints to the on-air show’s palette when the show changes, instead of holding the previous one until a reload.
- **The DJ stops talking over its own announcement on a slow station.** An announcement the station is slow to confirm now holds the call DJ quiet until the broadcast actually airs it, rather than counting down from a guess — and stops promising a duration nobody knows.
- **The DJ knows the station’s other shows**, so a caller asking to put another show on air is recognised instead of refused as nonsense.

### Front-end polish

- **The chat text box is visible** — it was rendering transparent on a transparent card (a real bug); it’s now a proper 44px field.
- **The Call / Text / Message buttons line up** at equal width, and wrap tidily in a compact embed instead of overflowing.

## 0.10.15

Covers 0.10.9 through 0.10.15 — the changelog had fallen behind main.

### The call

- **Push to talk is the default on both surfaces.** A fresh install read the old open-mic default as broken — mic hot from pickup, a spacebar that did nothing. Open mic stays a per-surface choice.
- **A request the station matches late gets announced anyway**: a background poll surfaces the resolved track at the DJ's next quiet moment, skips it if the caller already knows, and the record gains a problem line when a title never surfaces at all.
- **The record flags a DJ repeating itself** — the idle ladder once made the model echo its own line three times running, visible only to a human reading the transcript. Now the transcript says so.

### The transcript on the card

- **Speaker labels run inline with the words** — the dead gutter to the left of the speech is gone, and every wrapped line gets the full width of the box.
- **The text stops twitching while anyone talks**: interim speech updates in place instead of replaying the rise-and-fade on every revision, and the italic flicker on unfinished lines is now a quiet colour settling.
- **The standalone card shows four lines of conversation** instead of three. Embeds keep two — that height is a promise to the host page's layout.

### Models on your own endpoint

- **The Brains model list is read from wherever the calls will actually go**: point `llm_base_url` at your own server (llama.cpp, vLLM, LM Studio, llama-swap) and the dropdown lists that endpoint's own models instead of api.openai.com's — paste, reload, pick, save.
- **A model the server does not route gets named alternatives** instead of a bare 404 mid-call.

### Deploying

- **Containers get names a person can read**: `wavetalk-worker`, `wavetalk-web`, `livekit-server`, `wavetalk-caddy` — no more `stack-wavetalk-wavetalk-worker-1` in your GUI.
- **Redeploys stop killing calls mid-shutdown**: `stop_grace_period: 2m` on the worker, because Docker's 10-second default SIGKILLed the transcript write, the slot release and the wrap-up line.
- **When you need the bundled Caddy — and when you don't — is written down**: it exists only because browsers grant the microphone to HTTPS origins; bring your own proxy and it can go, provided you carry over both routes (the widget *and* `/rtc`, the one people forget).
- The README's embed-attribute table renders as a table again, and the docs cover the theme toggle's four looks.

### For anyone hacking on it

- **The repo can finally place its own calls**: `tools/call_harness.py` dials the local stack for real (timings, a spoken line, a recording of the DJ side), and `tools/call_scenarios.py` walks nine call shapes — live, voicemail, fallback, push to talk, timeouts, tool use — on a scratch stack with every on-air path forced off.

## 0.10.8

- **/panel is retired outright.** /settings has been the panel's one address since 0.9.151; the old name kept answering as a redirect, and now it doesn't answer at all — update any bookmark or reverse-proxy rule still pointing at /panel. Every mention in the panel's own help, the docs and the dev tooling now says /settings.

## 0.10.7

- The talk bar tells keyboard users about Space: on devices with a fine pointer it reads *"Tap to talk — or hold Space"*; phones keep the short label, and a custom Talk-bar wording still wins everywhere.

## 0.10.6

- **The dashboard's acting side fills the top**: Transmission on the left with Live calls and Voicemail stacked beside it — each door still paired with its own traffic — and the read-only Station strip full width underneath.
- **The numbers say what they count**: Who-can-call reads *permissions — anyone 3 · guest 9 · admin 9* (tooltip naming the section that decides), and the Live calls door matches.
- **The Voicemail card leads with its mode** — *fallback when the booth can't pick up* or *always on — voicemail-only* — before who may use it and where messages go.

## 0.10.5

- **The thumbs are line icons now** — the rating buttons on the card and the panel's rating filters draw in the theme's own ink instead of the yellow emoji hands, and rating marks in the records use ▲/▼. The one element every theme couldn't touch, gone.
- The harness turned on itself, at the operator's prodding: a new test reads the aggregator against every class under tests/ (two classes existed that had **never run once** — one of them then failed its own first real run), and another sweeps every element shipped hidden against the stylesheet, so a fifth [hidden]-beaten ghost cannot ship. The verify skill learned the ways, not just the things: hiding is proven by visibility, sorts by the order they produce, saves through a repaint, and audio with a long file.
- The README and settings reference catch up with the month: /settings as the panel's address, the four-group dashboard, voicemail, the sound shelf and shipped sets, per-DJ effects, push-to-talk turn commit, and the launcher embed's attributes.

## 0.10.4

### Releasing the talk bar ends the turn

- **Push-to-talk's release now tells the DJ you're done.** It used to only mute — the DJ then waited out its endpointing delay against a mic that was already shut, up to a couple of seconds of dead air after every release. Credit to a beta tester's side-by-side reading for the catch. The widget announces the release; the worker commits the turn — and only when you were actually mid-sentence, so a stray tap never makes the DJ answer silence. An older worker simply ignores the announcement and behaves as before.
- **Voice effect intensity defaults to 60** — colour you can hear with the words still in front. And a stored intensity of 0 now means the clean voice it says, instead of silently becoming full blast.

### The shelf, listening to its first real user

- **"Used for" is "Sound type" now, with its own filter** (Rings, Hang ups, Can't connects…) beside the category one — and sorting the column groups by type instead of shuffling by how many slots happened to use a clip.
- **Real clips lead, set defaults trail**, so the rows you can act on aren't behind a page of read-only defaults.
- **Uploads file under "custom" and declare their own type** — a little type picker on each upload row, saved as picked, feeding the filter and the sort.
- **Blanking a category puts the clip back** to its shipped category (or "custom" for uploads) — a mistyped filing used to be unremovable, which is how a dial-up handshake ended up labelled "test" for good.
- (Removing an upload was already there — the Remove button on its row — it just sat on a later page.)

## 0.10.3

### The floating launcher

- **A third way to wear the widget**: `data-mode="launcher"` turns the embed into a floating call pill in the page corner — support-chat style — that names who answers ("📞 Call Francesca", "Leave a message", or "Line closed") before anyone presses it, and opens the card in a panel above. Collapsing the panel never hangs up a call in progress.
- The embed section's snippet builder gained a **Shape** picker (inline card / floating launcher) — and while wiring it, a real bug fell out: the builder wrote `data-theme` and `data-captions` onto the *script* tag, where embed.js never looks, so those choices in copied snippets have silently done nothing. They land on the div now.

## 0.10.2

### The ring yields at pickup

- **The ring stops (soft fade) the moment the DJ answers** — a long ringback used to keep singing over the hello, because only the ring *timer* stopped and the started file played to its end. A long ring also used to stack copies of itself every 2.6 seconds; now one plays at a time.
- **"Ring yields at pickup"** in Call sounds turns the old behaviour back on if your ring is a jingle you want whole (not recommended). Short one-shots — the pickup click, a beep — are never cut either way.

### The dashboard, four groups

- **Each door lives with its own traffic**: the Live calls toggle sits beside the call records, the Voicemail toggle beside the messages. Transmission keeps The Line and Who-can-call; On air, Station and Brains·Voice·Ears make one Station group. No more toggle in one corner answering for a tile in the other.

### The shelf tells the whole truth

- **The set defaults are ON the shelf now** — five synthesized sounds per set plus the classic beep, playable in place, marked *default*, with a used-for chip when a slot is actually falling through to them.
- **Twelve rows a page** — with the defaults and four packs the shelf passed thirty rows; the pager keeps it a glance, and a filter always lands you back on page one.
- **Every clip says what it was made for** — a dashed *for Can't connect* chip on a busy signal that isn't assigned yet, straight from the catalog's new suggested-use field.

## 0.10.1

### Real recordings on the shelf

- **The Landline set** — seven genuine North American line sounds, recorded not synthesized: dial tone, ringback, two busy signals, touch-tone dialing, the "please hang up and try again" intercept, and the off-hook howl.
- **Loose clips with character**: coins dropping into a metal box, radio static, morse-code SOS — and the actual White House phone recording from the 1981 government shutdown, for a hold message with a story.
- All public-domain or CC0 from Wikimedia Commons, converted to the shelf's WAV shape, with each clip's exact source and licence recorded in the catalog. The shelf now holds 24 clips across four packs and seven categories.

## 0.10.0

The night's work, rolled up: the sound board, the Transmission
dashboard, per-DJ voice effects and two shipped sound sets are enough
new surface to move the minor number. Nothing changes between 0.9.157
and this — the detail lives in the entries below.

## 0.9.157

### The dashboard, two columns

- **Transmission holds the left column**; the six tiles fill the right,
  two across. Live calls and Voicemail wear their own **smaller switches**
  under The Line's big one — off is quiet, on is green, and only a thrown
  kill switch goes coral.
- **Who can call says what each tier gets**: *anyone 5 · guest 9 ·
  admin 12 perms* — the same numbers the Live calls door counts.

### The shelf, round three

- **The ghost box between the cards is gone** — the empty picker menu's
  own display beat its hidden attribute, the same trap as the URL rows
  (and the calls toolbar, fixed while there).
- **Category edits stop un-saving themselves** — the server kept them all
  along; the shelf was repainting from its stale local copy and wiping
  the edit off screen. Saved edits now confirm and refill the filter.
- The shelf's disclosure looks **openable** — advanced doors (the shelf,
  Per-DJ greetings, Per-DJ effects) wear the quiet chip chrome instead of
  a whisper of small caps. The Use for… and category selects wear the
  field skin, and prose under tables gets its breathing room.

### Odds and ends

- **Per-DJ effects rows gain a Test button** — one line rendered in that
  persona's own voice, played through the row's pick, unsaved included.
  (Picks still save the moment they're made — no Apply step to forget.)
- **Find a setting matches section names too** — "sounds" now opens Call
  sounds — and says plainly when nothing matches instead of collapsing
  the page to nothing.

## 0.9.156

- Housekeeping after the night's sweep: the test scaffolding redirects the
  new per-DJ effects store like every other writable path, the URL-row fix
  is pinned by its own test, and the panel design skill learned the
  Transmission group, the slot-card grammar and the per-persona list
  pattern. No behaviour changes.

## 0.9.155

### Per-DJ voice effects

- **Each persona can wear its own colour** — a Per-DJ effects list in Voice
  effects, the staged-greetings shape: pick a colour per DJ, saved on the
  spot; “Shared setting” hands the persona back to the main dropdown. The
  override rides /live, so callers hear it the moment that DJ is on air.

### The shelf grows up, and the packs arrive

- **Two new shipped sets**: *Modern* (marimba ring, warm pop pickup, soft
  hold, falling hang-up, double-buzz can't-connect) and *Rotary* (two-bell
  strike, dial pulses, cradle clunks) — synthesized, like Exchange and
  Handset, so they ship with no licence sheet. Plus one loose novelty:
  the sad trombone. Every clip carries a category and its pack.
- **Find and filter on the shelf** — a search box and a category pick next
  to Upload; one chip per row names the pack (or built-in/upload).
- **Fixed: the six URL rows sat fully visible under the slot cards** — the
  row skin's own display beat the hidden attribute, and the section read
  as duplicated. They now appear only for a slot set to a URL.
- Category headers (Configuration, Permissions & safety…) sit on a
  **darker band** than their sections, so the hierarchy reads at a glance.

### Transmission

- **The dashboard's three controls are one labelled group now** — The Line
  spans the top wearing a real switch (green open, coral paused), with
  Live calls and Voicemail under it. Paused, the two doors dim and warm
  amber: held, not broken — and the action cards finally read apart from
  the read-only tiles.

### Permissions that say who and what, truthfully

- **Caller-requested segments bypass the station's skill cooldowns** — they
  always did (the station's manual trigger is an operator override), but
  the help claimed the opposite. It now says so, names the roster as the
  station's own Skills panel, and leaves Actions per call as the pacing.
- **Search the music library carries "Station admin optional"** — it works
  without credentials; with them the DJ retries phrasing. The unlabelled
  row next to hard-required ones read as an unknown.
- **Every ask in the reference carries a who-chip** — always-available
  rows say *always* instead of nothing.
- **"Seen as" on the reference**: Everything / Anyone / Guest code / Admin
  — preview exactly the menu each tier's caller gets, the same filter the
  card's "?" popup applies for real.
- The card's "?" popup quietly names whose menu it is — *for guest
  callers*, *for the operator* — so a shorter list reads as the door you
  came through, not a fault.

## 0.9.154

### The sound board

- **The six call moments are cards now** — ring, pick up, on hold, hang up,
  can't connect, voicemail beep — each showing what plays today, with ▶ to
  hear it and a press to change it. A missing file or an unplayable beep
  turns its own card red, with the fix one press away.
- **One shelf for every sound the line can play** — built-ins and uploads
  in a single sortable table, each row showing where it's used and taking
  a job straight from **Use for…**. Uploads can be downloaded back out,
  and a file dropped anywhere on the shelf uploads.
- The six-dropdown stack, its eighteen buttons, and most of the prose are
  gone; the WAV rule now lives on the beep alone, which is the only place
  it applies.

## 0.9.153

### The line means what the switch says

- **Pausing the line closes everything — the answering machine included.**
  A paused line used to keep taking messages, which made the dashboard's
  one big switch a lie. The machine still answers through busy and off-air.
- While the line is paused the two mode cards **grey out and stop taking
  presses**; with both modes off, the card says the line is closed instead
  of offering a "Leave a message" button that could only fail. Either way
  the card explains itself: *"The booth isn't taking calls at the moment."*
- Player options the line status overrides are **dimmed with a note saying
  why**, and the preview — a real card — shows the closed face with the
  same explanation, so a paused line doesn't read as a broken preview.

### Voicemail hears the whole message

- **The machine listens from pickup.** Talking over the greeting no longer
  costs the start of the message — real ones arrived as their last two
  words. The beep still plays; it marks where "recording" begins.
- **A pause for thought no longer hangs up** — the quiet window that ends
  a message grew from 3.5 to 6 seconds.
- The "no staged greeting" warning stops firing when a fresh greeting
  succeeded, and the panel now quotes the real derived greeting —
  *You've reached {station}. {dj} is on the air right now — leave a
  request after the beep* — with the working `{dj}` token, not `{DJ}`,
  which the filler silently drops.

### The panel reads faster

- **The dashboard leads the page** under its own band; the settings
  heading, search and a full-width **Jump to** menu sit below it.
- The Live calls card counts what each caller tier **can do**; the
  Voicemail card says who may leave a message and where it goes; the
  Calls tile counts failures and thumbs; the Voicemails tile splits
  passed-on from held.
- **Call logs speak the card's colours** — DJ coral, caller blue — tools
  carry their action's emoji instead of clipping mid-word, the problems
  filter wears its red "!" inside the chip, and the back button matches
  its neighbours.

## 0.9.152

- **The DJ comes back from its own airings.** Sending an announcement or
  segment to air never tripped the step-away watch, so the DJ returned
  from its own broadcast saying nothing — while the caller it had told to
  hold sat waiting. The come-back line fires now, and can nod at what
  just went out.
- **The busy hold fits the words.** One fixed number either reopened the
  gate mid-segment or gagged the call for half a minute over a one-line
  station ID; the hold is now sized from what the station logged as said,
  at the station's own ~140wpm.

## 0.9.151

- **/settings is the panel's one address** — it serves the page itself now,
  and /panel redirects there for old bookmarks. A reverse-proxy allowlist
  belongs in front of /settings.
- Every sound row gets its **own Play button** beside Upload — hearing the
  current pick (set defaults included) no longer means finding the matching
  button in the row above.
- **How the doors work states the lockout numbers**: 5 wrong tries → a
  5-minute cooldown; a second round of 5 → banned until restart.
- Call-in access says plainly that per-feature, per-tier grants live under
  Caller permissions; the caller-asks reference says **examples, not
  commands** — the DJ matches intent, no syntax to learn.

## 0.9.150

### The panel gets a face it chose on purpose

- **Dashboard controls are cards now** — The line / Live calls / Voicemail
  share the status tiles' own grid, height and radius, each with a micro
  label, a state word and a one-line note, colour-edged by state. A 37px
  chip floating over 64px tiles read as two pages sharing a corner.
- **One radius scale, no strays**: 8px controls, 10px contained surfaces,
  12px section bodies — the audit found four unrelated radii in play.
- **Tables are surfaces**: the sound board gains its container — border,
  radius, header band, row hover — instead of bare rows in the section.
- **Button rows stop floating**: a testrow that follows content is the
  section's footer, hairline and all.
- Tiles and section headers **answer the pointer** — a small lift, a quiet
  tint; the page stops feeling inert.

## 0.9.149

- **Transmission modes moved onto the dashboard** — Take live calls and
  Enable voicemail as proper toggle buttons under the kill switch, lit
  green when a door is open, posting the moment they're pressed, with a
  strip saying what the combination amounts to: phone, phone with a
  machine, voicemail-only, or closed.
- The push-to-talk per-surface switches return to **Player settings**,
  where the other per-surface choices live; the Transmission modes section
  retires.

## 0.9.148

- **Effect intensity is back for every effect.** Its visibility rule still
  named only the first three, so picking a newer colour hid the dial — which
  read, fairly, as the volume control disappearing.
- **Four more effects**: stadium PA, intercom squawk, shortwave, lo-fi
  cassette — ten colours now, all under the same intensity dial.
- The **dashboard is a real grid**: three tiles across (two on a narrow
  panel), every row full, every tile the same size.

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
