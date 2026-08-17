# Security and privacy

What is exposed, what is enforced rather than advised, and what to check before putting this anywhere the internet can reach.

[← back to the README](../README.md)

---

## Exposing this safely — the checklist

Work down it. Each line says what goes wrong if you skip it.

**Before anyone outside your house can reach the page**

- [ ] **Admin password set.** Until one exists the panel is open to whoever can load the page — and every line is locked: no calls, texts or voicemail in any access mode until the password is set. → *Access*
- [ ] **Call-in access chosen deliberately.** A fresh install starts *Admin only* — opening the line to a guest code or to anyone is a decision you make, not a state you inherit. On deployments from before 0.10.80 the old *Automatic* rule still runs: open until a code exists — no code is an open line, not a closed one. → *Access*
- [ ] **TLS on the front door.** Passwords travel with every request, and browsers refuse the microphone on plain http anyway.
- [ ] **Open the panel over the TLS door (`:8443`), not `:8100`.** The plain-http port is published alongside Caddy and is a *live admin surface* — reach the panel over `http://…:8100` and the admin password and every API key you type cross the LAN in cleartext, sniffable by anything on the wire. Type them at the `:8443` door. If your own reverse proxy terminates TLS, don't publish `:8100` to the LAN at all — the station's webhook is the only thing that needs it, and that can go through the proxy too.
- [ ] **The embed allowlist** (*Players → The frame → Allowed origins*) set to your real origin(s), or empty. `*` lets any page on the internet mint call tokens against you; the server warns at startup if you choose it. Empty is same-origin only — the widget's own page needs no entry.
- [ ] **Fresh LiveKit secret.** Never the example one.
- [ ] **`CALLIN_ADMIN_KEY`** set as break-glass, so a lockout is recoverable without deleting files on the host.
- [ ] **Reach the panel by hostname only after a password exists.** Before one is set, the panel accepts same-origin requests only from a literal address (a *name* can be pointed at your box by someone else); `CALLIN_PANEL_ORIGINS` is the escape hatch during setup.

**Money and airtime — every call spends your API keys**

- [ ] **`calls_per_hour`, `calls_per_day`, `caller_cooldown_secs` non-zero.** 0 means unlimited; the hourly cap alone still permits 24× that in a day.
- [ ] **`max_actions_per_call`** set — caps requests, segments and on-air messages from one call.

**Who gets what — permissions are a tier, not a switch**

Each caller permission is granted to the *least trusted caller who gets it*: **off**, **anyone**, **guest code**, or **admin**. The tier is decided at the door, travels inside the signed room name, and is resolved before the DJ's tool list is built — a caller cannot raise their own. Put the far-reaching ones on **admin** and they are yours alone while the line stays open to everybody else.

- [ ] **`allow_announcements`** hands the on-air DJ a line to read *to everyone listening*. Guest tier by default on a fresh install — only callers you handed the code to — and off on anything upgraded from before 0.10.80.
- [ ] **`allow_skip_track` / `allow_dj_segment`** reach every listener, not the caller. Admin tier by default on a fresh install (your own phone, nobody else's); off on upgrades.
- [ ] **`allow_cancel_queue`** takes a not-yet-aired track back out of the queue. The queue is shared, so it can cancel a record a *different* caller asked for — which is exactly why the station exposes no listener-facing cancel of its own. Off by default, and worth leaving there on an open line.
- [ ] **`allow_sound_search`** is a pair of READS (a “sounds like” search, and the neighbours of the track on air). They queue nothing, change nothing and are not counted against Actions per call — the risk is disclosure of your library's contents, the same as library search, not action.
- [ ] **`allow_takeover`** pins a different show on air and keeps going after the caller hangs up. Admin tier by default on a fresh install, off on upgrades; needs station admin credentials either way.
- [ ] **`allow_genre_lock`** holds the whole station to a genre for up to twelve hours, and like a takeover it outlives the call. Quieter than one, which is the risk: a pinned show announces itself on air in a voice listeners recognise, a narrowed playlist does not. Off by default, and it needs a SUB/WAVE new enough to have the control at all — older stations answer that they can't rather than failing.
- [ ] **`allow_never_play`** — **the furthest-reaching switch here, and the only one with no expiry.** It puts the record on air onto the station's never-play list: out of the queue, out of the fallback playlist, never selected again. Nothing goes out on air to say it happened, so an unwanted ban is found by noticing a record has stopped coming round. Off by default, admin tier, station admin credentials required. The same switch lets a caller *lift* a ban, including one you set yourself — deliberately, so a mistake made from the phone has a way back that doesn't depend on you spotting it.

**Privacy — what you keep about people who call**

- [ ] **`record_calls`** is on by default (it is how a bad call gets diagnosed) and writes both sides of a stranger's conversation to `data/calls/`. `record_keep` controls retention; turn it off if you don't want it.
- [ ] **Transcripts and `data/secrets.json` are plain files on disk**, owner-readable only. Protect the volume; never commit `.env` or `data/`.
- [ ] **Tell callers.** Nothing in the widget says a call is recorded — if you keep transcripts on a public line, that disclosure is yours to make.
- [ ] If you forwarded a port, **delete the rule** when you stop running this.

## What is enforced rather than advised

- Passwords are PBKDF2 hashes; the store refuses a guest code equal to the admin one; an **unreadable password file counts as configured**, locking rather than opening.
- A stored API key is only ever sent to the host it is saved for — testing a draft URL withholds it and says so.
- A caller's address comes from the socket, not a header they control, unless the connection came from a proxy named in `CALLIN_TRUSTED_PROXIES`.
- Destructive station tools are not on the call line at all, whatever the settings say.
- Join tokens last two minutes — the guest code and the usage limits are re-checked at every mint.

## Two passwords, two jobs

Both under *Access*, and the store refuses to let them match.

**Admin** protects the panel, API keys and test buttons — and opens the phone, so an operator carries one password. **Guest** is optional and protects only the phone; the code is the whole thing, no username.

**Who can call** is its own setting beside them:

| | |
|---|---|
| **Admin only** (default on a fresh install) | the phone answers only the admin password — a new line starts closed and is opened as a decision |
| **Open** | anyone who loads the page can call — the guest door is off and the code does not elevate; the admin password still opens everything |
| **Guest code** | the code you hand out, or the admin password |
| **Automatic** (default on deployments from before 0.10.80) | open until you set a guest code, then required |

*Open* and *Guest code* are one choice apiece, not a cascade. Choosing a code-gated mode without having set that password refuses every call and the panel says so, rather than falling open. And every mode — *Open* included — waits for the admin password to exist first: an unconfigured deployment refuses all calls until it has an owner.

**Lockout**: 5 wrong tries per address → 5-minute cooldown; a second round → banned until restart, guest failures counted separately. Locked out? `CALLIN_ADMIN_KEY` is always accepted, or restart. The lockout keys on the immediate socket peer — which a client cannot choose — and believes a forwarded address only when `CALLIN_TRUSTED_PROXIES` names the proxy, so set that whenever a reverse proxy is in front.
