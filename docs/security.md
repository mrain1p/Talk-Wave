# Security and privacy

What is exposed, what is enforced rather than advised, and what to check before putting this anywhere the internet can reach.

[← back to the README](../README.md)

---

## Security and privacy

### Exposing this safely — the whole checklist, in one place

The answers used to be scattered across six sections, which is how a real
deployment ended up reachable from the internet with no guest code and no
redial limit — every individual setting was documented and nobody had a list.

Work down it. Each line says what goes wrong if you skip it.

**Before anyone outside your house can reach the page**

- [ ] **Admin password set.** Until one exists the panel is open to same-origin
      requests, which includes anyone who can load the page from a literal
      address. → *Access → Passwords*
- [ ] **Guest code set**, or `front_access` deliberately set to something else.
      `auto` means *open until a code exists* — so no code is an open line, not
      a closed one. → *Access → Passwords*
- [ ] **TLS on the front door.** Passwords and the guest code travel with every
      request; over plain http they are readable on the wire. Browsers also
      refuse microphone access on non-HTTPS origins, so calls cannot work
      anyway.
- [ ] **`CALLIN_ALLOWED_ORIGINS`** set to your real origin(s), or empty. `*`
      lets any page on the internet mint call tokens against you.
- [ ] **Fresh LiveKit secret.** Never the example one.
- [ ] **`CALLIN_ADMIN_KEY`** set as break-glass, so a lockout is recoverable
      without deleting files on the host.

**Money and airtime — the limits that matter once the line is reachable**

- [ ] **`calls_per_hour` non-zero.** 0 is unlimited. The daily cap alone still
      allows a whole day's worth inside one hour.
- [ ] **`calls_per_day` non-zero.** The hard ceiling on what a day can cost.
- [ ] **`caller_cooldown_secs` non-zero.** 0 lets one person redial in a loop.
- [ ] **`max_actions_per_call`** set. Caps requests, segments and on-air
      messages from a single call.
**Who gets what — permissions are a tier, not a switch**

Since 0.9.116 each caller permission is set to the *least trusted caller who
gets it*: **off**, **anyone**, **guest code**, or **admin**. The tier is
decided from what the caller typed at the door, travels to the worker inside
the signed room name, and is resolved before the DJ's tool list is built — a
caller cannot raise their own.

The practical consequence, and the reason it exists: you no longer have to
choose between a public line and being able to do anything useful from your
own phone. Put the far-reaching ones on **admin** and they are yours alone
while the line stays open to everybody else.

Upgrading changes nothing — the old `true` meant "anyone who got through the
door", which is exactly **anyone**, and that is what it migrates to.

- [ ] **`allow_announcements`** — understand it before granting it to
      **anyone**. It lets a caller hand the on-air DJ a line to read *to
      everyone listening*. Off by default since 0.9.89. The tool allowlist and
      the conduct prompt push back, but that is a model declining, not a gate
      refusing.
- [ ] **`allow_skip_track` / `allow_dj_segment`** — both reach every listener
      rather than the caller. Off by default; **admin** is the tier to use if
      you want them at all on a station with an audience.
- [ ] **`allow_takeover`** — the furthest-reaching switch here, and the only
      one whose effect outlives the call: it pins a show ahead of the weekly
      schedule, so a different DJ is on air for an hour (longer if the caller
      asks) and keeps going after they hang up. It also lets a caller cancel a
      takeover *you* set from the station's own admin page. Off by default.
      Needs station admin credentials, and Actions per call is the only thing
      pacing it.
- [ ] **Check the columns you cannot fill.** Granting something to **guest**
      with no guest code set would grant it to a tier nobody can be. The panel
      greys those cells out and says why rather than letting you save a
      setting that never applies.

**Privacy — what you keep about people who call**

- [ ] **`record_calls`.** On by default, because it is how a bad call gets
      diagnosed. It writes both sides of a stranger's conversation to
      `data/calls/`. Turn it off if you do not want that; `record_keep`
      controls how long the ones you keep survive.
- [ ] **`ask_caller_name`** is off by default. A volunteered name is still used
      and still ends up in the transcript.
- [ ] **Transcripts are plain JSON on disk**, owner-readable only. So is
      `data/secrets.json`. Protect the volume; never commit `data/`.
- [ ] **Tell callers.** Nothing in the widget says a call is recorded. If you
      keep transcripts and the line is public, that is your disclosure to make.

**The network**

- [ ] Decide which connectivity option you are on — see [Calling from outside
      your network](#calling-from-outside-your-network) — rather than
      discovering it from a failed call.
- [ ] If you forwarded a port, **delete the rule** when you stop running this.

### What is enforced rather than advised

Worth knowing which of these the software will hold for you:

- Passwords are PBKDF2 hashes; the store refuses a guest code equal to the
  admin one; **an unreadable password file counts as configured**, so a file
  permission fault locks the panel and the phone rather than opening them.
- A stored API key is only ever sent to the host it is saved for — testing a
  draft URL withholds it and says so.
- A caller's address comes from the socket, not a header they control, unless
  the connection came from a trusted proxy.
- Destructive station tools are not on the call line at all, whatever the
  settings say.
- Join tokens last two minutes.

### Two passwords, two jobs

Both under *Access → Passwords*, and the store refuses to let them match.

**Admin** protects the panel, API keys and test buttons. Whoever holds it
controls the application and can spend your API keys. Until one is set the
panel stays open with a standing nudge — fine on a trusted LAN, but a
deliberate choice.

**Guest** is optional and protects only the phone: the Call button, `/token`,
and every embed. There's no username; the code is the whole thing. Admin is
accepted as a guest code, so an operator carries one password.

**Who can call the booth** is its own setting beside them, not something
inferred from whether a guest code happens to exist:

| | |
|---|---|
| **Automatic** (default) | open until you set a guest code, then required |
| **Open** | anyone who loads the page can call |
| **Guest code** | the code you hand out, or the admin password |
| **Admin only** | the phone is closed to callers — useful while setting up |

Choosing *Guest code* or *Admin only* without having set that password refuses
every call, and the panel says so, rather than falling open. The panel itself
is admin-only in all four.

Lockout is 5 failures per address → 5-minute cooldown, a second round → banned
until restart, with guest failures counted separately. Locked out? Set
`CALLIN_ADMIN_KEY` (always accepted) or restart. Passwords travel with each
request, so beyond your LAN use the HTTPS front door — over plain http they are
readable on the wire.

**Which address** is the address the connection came from, not one the caller
can name. `X-Forwarded-For` is a list the client starts, so it is only read
when the connection arrived from a reverse proxy you trust, and then only the
entry that proxy appended — see `CALLIN_TRUSTED_PROXIES` in `.env.example`. It
defaults to any loopback or private peer, which covers the normal deployment: a
reverse proxy reaching the container over the docker bridge. Set it explicitly
if this port is reachable directly from an untrusted network as well as through
the proxy; otherwise the lockout is a header away from meaning nothing, in
either direction. This reads one hop — the entry your proxy appended. With a
CDN in front as well, that entry is the CDN's address rather than the caller's,
so per-caller limits collapse into one shared bucket.

**Stored keys only travel to the host they're saved for.** The panel can test a
URL before saving it, and a test builds the real provider — so a URL supplied
in a request could otherwise make this service post your OpenAI key, your TTS
key or the station's admin password to whatever host was named, which is the
one thing keeping keys server-side is meant to prevent. A draft URL is still
tested; the key just stays home, and the result says so.

**Join tokens last two minutes.** The guest code and the usage limits are
checked when a token is minted, so a long-lived token is a line that can be
reopened without passing either again.

**Before exposing beyond your LAN:**

1. `CALLIN_ALLOWED_ORIGINS` — **empty by default since 0.9.77**, which is
   same-origin only and is what most deployments want: the widget on this
   service's own page needs no entry. Set it only to embed the widget on
   another site, and then to that site's origin. `*` lets *any* page on the
   internet embed the widget and mint call tokens against you — it used to be
   the default, and both processes now warn at startup if you choose it. This
   is the *embed* permission and nothing more: it does not open the settings
   panel.
2. Set the admin password, and a guest code if the page is public. Do this
   before you reach the panel by hostname — with no password set, the panel
   accepts a same-origin request only from a literal address, because a *name*
   can be pointed at this box by someone else and the browser would present
   that as same-origin. If you need the named origin during setup, put it in
   `CALLIN_PANEL_ORIGINS` (not the embed list — the two permissions are
   different sizes and are kept apart).
3. Fresh LiveKit keypair. Never deploy the example key.
4. Real TLS on the front door, so visitors see no certificate warnings.
5. Keep usage limits non-zero — every call spends real money. Set **calls per
   day** and **actions per call**, not just the hourly limit: an hourly cap
   alone still permits 24× that in a day.
6. Know what's plaintext: `data/secrets.json` holds API keys unencrypted.
   Protect the volume; never commit `.env` or `data/`.

### Upgrading to 0.9.77: `CALLIN_ALLOWED_ORIGINS` defaults to empty

It used to default to `*` — any page on the internet could embed the widget and
mint call tokens against your service, spending your LLM and TTS budget. Empty
now, which is same-origin only.

**Breaking only if you embed the widget on another site and never set the
variable.** Set it to that site's origin and embeds work exactly as before. If
you only ever open the widget on this service's own page, there is nothing to
do — that is same-origin and needs no entry.

Taken pre-1.0 deliberately: shipping the convenient default into 1.0 would have
meant living with it.

### Upgrading to 0.9.65 or later: the container is no longer root

`data/` holds your API keys and password hashes as plain files, and it is bind
mounted from the host — so the container running as root meant root on those
files, and on anything else that mount could reach. It now runs as uid 1000.

**Fix ownership *and* modes before you pull.** Both, not just the first:

```
chown -R 1000:1000 /path/to/wave-talk/data
chmod -R u+rwX     /path/to/wave-talk/data
```

The chmod is not belt-and-braces. Some filesystems — Synology shares among them
— create files with **no permission bits at all**, mode `000`. Root ignores
that and reads them anyway, so it never showed; a normal user cannot, even as
the owner, so chowning alone leaves the app unable to read its own settings.
`u+rwX` gives the owner read/write and marks directories traversable without
opening anything to anyone else. From 0.9.66 the app sets modes explicitly on
everything it writes, so this is a one-time repair of files already on disk.

Do it while the old container is still running — root ignores the mode bits, so
the running deployment carries on unaffected and the new one comes up able to
read its own files. If your host share uses a different uid, build with
`--build-arg APP_UID=… --build-arg APP_GID=…` instead.

Skip it and the app comes up unable to read its settings, keys or password
store. That fails *shut*: an unreadable password file counts as "a password is
set that nothing can satisfy", never as "no password set", so a permissions
mistake cannot leave the panel open (0.9.64). `CALLIN_ADMIN_KEY` is the way
back in, and both processes log the directory, the uid and the exact chown at
startup.
