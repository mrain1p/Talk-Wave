# Quick start

From nothing to a working call in about ten minutes.

[← back to the README](../README.md)

---

## What you need

- A machine running **Docker** with compose (a NAS is fine — that is where this was built).
- A running **[SUB/WAVE](https://github.com/perminder-klair/subwave)** station. Talk Wave is its companion phone line, not a standalone radio.
- **One LLM API key** (OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Requesty, Vercel AI Gateway) — or your own box, which needs none: Ollama, an OpenAI-compatible server, or the station's locca. Speech-to-text ships in the box; everything else has a working default. Which model and voice actually carry a call: [what to run](models.md).

## The one-command version

```bash
curl -fsSL https://raw.githubusercontent.com/mrain1p/Talk-Wave/main/install.sh | bash
```

Fetches the stack, generates the LiveKit secret, detects your LAN address, prepares `data/`, and starts everything — then tells you the address to open. It refuses to touch a folder that already holds a deployment. If you'd rather see every move, the same steps by hand:

## Install

Make a folder, grab four files from this repo, create one empty directory:

```bash
mkdir talk-wave && cd talk-wave
wget https://raw.githubusercontent.com/mrain1p/Talk-Wave/main/docker-compose.yaml
wget https://raw.githubusercontent.com/mrain1p/Talk-Wave/main/Caddyfile
wget -O .env https://raw.githubusercontent.com/mrain1p/Talk-Wave/main/.env.example
wget -O livekit.yaml https://raw.githubusercontent.com/mrain1p/Talk-Wave/main/livekit.example.yaml
mkdir data && chown -R 1000:1000 ./data && chmod -R u+rwX ./data
```

The `chown` matters: the services run as uid 1000, and a `data/` they can't read means no setup ask and a locked panel — the login gate and the logs name it, but never seeing it is better.

Then two edits:

1. **`livekit.yaml`** — paste a fresh secret under `keys:` (the file shows the one-line generator). The app reads the keypair from here too, so it lives in one file.
2. **`.env`** — set `HOST_IP` to this machine's LAN address.

That is the whole configuration surface on disk. Everything else — model, voice, permissions, the lot — is set later in the settings panel and applies to the next call without a restart.

## Start it

```bash
docker compose up -d
```

Open **`https://<HOST_IP>:8443`**. The first visit shows a one-time certificate screen (self-signed TLS — HTTPS is required for the microphone); proceed past it, then:

1. **Set the admin password** — the page asks before anything else, because until one exists the panel is open to whoever can reach it and the line itself answers nobody: no calls, texts or voicemail until the password is set.
2. Open the settings (the gear), go to **Configuration**, point **SUB/WAVE Station** at your station and add your **LLM key** under Brains.
3. Run **the pipeline check** (Diagnostics page) — twelve stages that walk every leg of a real call in order and name the first thing that would break.
4. **Press Call.**

## Where things live

Everything the deployment owns is in that one folder, and only `data/` ever changes — the app fills it as you use things (settings, keys, transcripts, uploads). **Backing up = copying `data/` + `.env` + `livekit.yaml`.** The two Docker volumes hold only re-downloadable state.

## Next steps

- **A real domain and a trusted certificate** (no certificate screen): [networking — the TLS front door](networking.md).
- **Callers from outside your network**: [networking](networking.md) — one router rule plus one compose line.
- **Every setting explained**: [settings](settings.md). **Hardening**: [security](security.md). **When a call goes wrong**: [troubleshooting](troubleshooting.md).
