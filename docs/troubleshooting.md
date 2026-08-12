# Troubleshooting

Known limitations, the failures that actually happen, how to read a call back afterwards, and where the logs and tests are.

[← back to the README](../README.md)

---

## Known limitations

- **IPv4-only callers can't connect** without a forwarded port or a relay — see
  [Calling from outside your network](#calling-from-outside-your-network).
  Roughly half of users, and it fails silently from their side.
- **Local TTS may not keep pace with playback.** Above ~1.0× realtime, audio
  gaps mid-sentence. The speed test measures it.
- **One station per deployment** — everything is discovered from a single
  SUB/WAVE instance.
- **API keys are stored unencrypted on disk.** `data/secrets.json` is written
  `0600` where the OS honours it (Windows ACLs don't map cleanly) and kept
  separate from `settings.json`, so settings stay safe to copy or paste. Keys
  are never returned to the browser and never logged — but anyone who can read
  the volume can read them, so the volume is the real protection.
- **Two shared passwords, not user accounts.** One admin, one optional guest,
  each a single secret shared by everyone who has it. No per-person identity,
  so nothing attributes an action to a particular operator.
- **Recent calls keeps the newest 100 (the default).** A diagnostic aid, not an archive.
- **The panel is not built for hostile exposure.** It assumes an operator on a
  trusted network who has set a password.

## Troubleshooting

Run the **full pipeline check** first; it walks every leg in call order and
names the fix. The classics:

- **Hangs at "Ringing" while server checks pass** — LiveKit is advertising an
  address the browser can't reach. Set `HOST_IP` and recreate the container;
  the same cause shows as webhooks on a `172.x` address. Check the firewall
  allows **UDP 7882** and TCP 7881.
- **"This page can't use the microphone"** — the page is on plain
  `http://<lan-ip>`, where browsers refuse capture. Use the TLS page.
- **…but the widget IS on https and still says so** — then the page *embedding*
  it is not. A secure context requires **every page in the chain** to be
  secure, so an `https` iframe inside an `http` page is insecure and the
  microphone is refused. The widget's own URL is fine; serve the host page over
  https. Common when testing an embed from a LAN address like
  `http://192.168.1.245:8090` while the iframe points at your real domain.
- **The DJ is there, the music isn't** — an `http://` stream on an `https://`
  page is blocked as mixed content, silently. Set **Station stream URL** to an
  https one; the *Station stream* stage says so outright.
- **Every save says "cross-origin request blocked"** — you are reaching the
  panel by a HOSTNAME before any admin password exists. Pre-password the
  panel only trusts a literal address (a name can be pointed at your box by
  someone else; an IP cannot). Open the panel once at the box's own IP, set
  the admin password there, and the hostname works from then on — or name
  the origin in `CALLIN_PANEL_ORIGINS`.
- **The call button says "Line not set up"** — no admin password exists yet,
  and until one does every door refuses: calls, texts and voicemail alike, in
  every access mode. The call page itself asks for the password on a fresh
  install; *Permissions & safety → Access* is the other way in.
- **Locked out of the panel** — set `CALLIN_ADMIN_KEY`, or restart to clear
  bans. To remove the password entirely, delete `data/admin-auth.json`.
- **The panel has forgotten everything, or the login says a file cannot be
  read** — `data/` is not readable by the container's user, which is uid 1000
  from 0.9.65. Nothing is lost; it just isn't being read. Fix owner *and*
  modes on the host — `chown -R 1000:1000 data && chmod -R u+rwX data` — and
  recreate. Both processes print the same instruction at startup, naming the
  files. Ownership alone is not enough on filesystems that create files with
  mode `000` (Synology shares do). `CALLIN_ADMIN_KEY` gets you in meanwhile.
- **Voice test 400s on local TTS** — the voice id doesn't exist on that server
  (cloud names and local ids aren't interchangeable). *Reload voice list* after
  switching backend.
- **Works on the LAN, not outside** — see [Calling from outside your
  network](#calling-from-outside-your-network). Chrome may also ask LAN
  visitors to "connect to devices on your local network"; that's Private
  Network Access, one-time and harmless.

## Diagnosing a call

**Start with Recent calls**, under *Diagnostics*. Each call writes one file as
it ends — both sides, every tool with its result, the config it ran under, and
anything that failed.

That file is a transcript of a stranger's conversation sitting on your disk, so
it is a choice: **Keep call transcripts** under *Call behaviour* turns it off
entirely, and **Transcripts to keep** decides how long the ones you do keep
stick around (100 by default, deleted oldest-first as new calls land). With it
off, nothing is written and Recent calls only shows what is already there — you
are then diagnosing from the container logs, which is what this section exists
to stop you doing.

```
2026-08-04 23:34:36  Dalia  ·  136s  ·  6 caller turns
  google/gemini-3.1-flash-lite  stt=local/base.en  tts=local
  ⚠ station 503 on /request
23:34:45 DJ      You're through to the booth — what's on your mind?
23:34:48 CALLER  Can you play something fun?
23:34:49   tool  subwave_request_song → Added to the queue
```

The tool column is the point: the DJ saying it did something is a claim, that
line is the receipt. The config line ties a bad call to the setting that caused
it. Files live in `data/calls/`, the newest **Transcripts to keep** of them (100 by default).

The other rows: **Full pipeline check** names the first thing that would break;
**Speed test** reports time to first audio per leg (over ~1.5s sounds laggy);
**Server logs** shows recent activity.

## Logs and tests

Local runs write rotating logs to `data/logs/`. Under Docker the same lines go
to container stdout, where the worker logs its version at startup and every
call as `heard:` / `said:` / `tool:` lines. `/health` reports the running
version — check both containers match, since they ship as one image but run as
two.

```bash
cd agent-worker && LOG_TO_FILE=0 SETTINGS_PATH=/tmp/t.json SECRETS_PATH=/tmp/s.json ADMIN_AUTH_PATH=/tmp/a.json CALLS_PATH=/tmp/calls LISTENERS_PATH=/tmp/l.json LISTENER_SAMPLE_INTERVAL=0 python -m unittest test_sidecar
```

**Those environment variables are not optional.** Most test classes redirect
their own paths, but `admin_auth` and the call record fall back to the real
`data/` directory, so a bare run can write into your actual auth file and call
transcripts. They point every writable path away from the checkout; CI sets the
same set.

The suite covers the speech filter, settings precedence, secrets and passwords,
the lockout ladder, usage limits, the tool registry, prompt assembly, sound
packs, the call record and the call's lifecycle seams — plus the security
posture itself: that a stored key only travels to a saved host, that a caller
cannot name their own address, that an unreadable password store closes the
panel and the phone rather than opening them, and that files written into
`data/` set their own permissions. CI runs it before building an image, so a
failing suite never reaches `:latest`.

Several checks derive what they test from the source tree rather than from a
hand-maintained list, so they cover code that does not exist yet: that the
widget only calls routes the token server actually serves and only reads
elements that exist, that every writing station method is muzzled in the
conduct harness, that every settings field has a control in the panel, and that
every module is reached by the suite at all.
