---
name: wavetalk-deploy
description: Deploy or update the Wave Talk stack — first-time setup, pulling a new image, or fixing a deployment that came up wrong. Covers the permissions, HOST_IP and mixed-content traps that break deployments silently. Use when asked to deploy, update the stack, or when a deployed instance misbehaves after an upgrade.
---

# Deploy Wave Talk

Four services, defined in `docker-compose.yaml`: `livekit-server`, `agent-worker`,
`token-server`, `caddy`. The two Python services are **one image in two containers** —
`ghcr.io/mrainone7p/wave-talk`.

The operator manages stacks through a **GUI (Portainer-style "update the stack")** and prefers
inline `environment:` entries in compose over `.env` files on the host. Ship fixes as tagged
images they pull; put config in compose env or the settings panel. Prefer that over asking for
shell steps.

## Routine update

```bash
docker compose pull && docker compose up -d
```

Then confirm **both** Python containers report the same version — they ship as one image but run
as two containers, and a redeploy that recreates one and not the other leaves them skewed:

```bash
curl -s http://<host>:8100/health
```

**Then confirm the worker actually registered.** A container that started is not a stack that
works — see the bind-mount trap below:

```bash
docker logs <worker> 2>&1 | grep -E "registered worker|401|invalid"
```

## Changing anything in livekit.yaml: restart LiveKit explicitly

`docker compose up -d` hashes **service definitions**, not the contents of bind-mounted files.
Edit `livekit.yaml` and compose reports `livekit-server Running` and moves on, so the process
keeps serving the config it loaded at boot.

This bites hardest when rotating the API secret, because the two halves live in different
places: the secret is in `livekit.yaml` (read once, at start) and in both Python services' env
(read on recreate). Rotate both, run `up -d`, and you get the Python side on the new secret and
LiveKit still on the old one:

```
worker:   401, message='Invalid response status', url='ws://.../agent'
livekit:  invalid token: ... token signature is invalid
```

Nothing crashes. Both containers are "up". Every call fails until someone looks.

```bash
docker compose restart livekit-server
```

The worker's retry ladder (16 attempts, backing off) covers the gap, so fixing it inside a
couple of minutes costs nothing. Observed 2026-08-05 during a secret rotation.

The same applies to any bind-mounted config a service reads once at startup.

## First deploy — the checklist that actually matters

1. `livekit.example.yaml` → `livekit.yaml` with a **fresh keypair**, `use_external_ip: true`.
2. `.env.example` → `.env`. `LIVEKIT_PUBLIC_URL` must be **what the browser reaches** (the
   `wss://` URL through the reverse proxy), not the internal service name.
3. **Set `HOST_IP`** to the LAN address of the machine running the stack. It drives the LiveKit
   media address, the browser URL and the webhook callback — one variable configures everything.
4. Make `./data` usable by the container's user (see permissions below).
5. Reverse proxy in front of :8100, `CALLIN_ALLOWED_ORIGINS` set to real origins, and
   `CALLIN_ADMIN_KEY` set, **before** it is reachable beyond the LAN.
6. Set `SUBWAVE_STREAM_URL` to the station's **public https** stream.

## The three failures that look like something else

**Hangs at "Ringing" while every server check passes.** LiveKit is advertising an address the
browser cannot reach. Set `HOST_IP` and recreate. The same cause shows up as webhooks arriving
on a `172.x` address. Firewall needs **UDP 7882** and **TCP 7881**.

**The DJ is there but there is no music.** An `http://` stream on an `https://` page is blocked
as mixed content — *silently*. Set the station stream URL to an https one. Note the station's
own `/listen.m3u` advertises its **internal** address, so mount discovery must take the path
only and keep the operator's origin.

**The panel has forgotten everything, or login says a file cannot be read.** From 0.9.65 both
Python services run as **uid 1000**, not root. Run this on the host once, *before* pulling — it
is safe against the still-running root container:

```bash
chown -R 1000:1000 ./data && chmod -R u+rwX ./data
```

**Both commands.** Ownership alone is not enough: some filesystems (Synology shares among them)
create files with mode `000`, which root reads straight through and an owner cannot. Nothing is
lost when this happens — the data just isn't being read. Both processes print the exact fix at
startup, naming the files. `CALLIN_ADMIN_KEY` is the way back in meanwhile.

This fails **shut**, deliberately: an unreadable password store counts as "a password is set
that nothing can satisfy", never as "no password set", so it cannot leave the panel open.

## Notes

- The widget must be opened over **HTTPS** — browsers only allow microphone capture on a secure
  origin. Plain :8100 is for same-machine dev and the station webhook callback.
- The 141MB `faster-whisper-base.en` model is a **runtime download into the container's
  writable layer**, not baked into the image, so every container recreate re-downloads it.
  Prewarm covers it, but it is a real redeploy cost.
- `data/` holds settings, API keys and call transcripts, and is bind-mounted into both Python
  services. Both must see the same directory or panel changes never reach the worker.
