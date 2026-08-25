# Calling from outside your network

WebRTC needs a path for the **media**, not just the signalling — which is why a call can connect, show every green light, and carry no audio. Three ways to give it one, in increasing order of exposure.

[← back to the README](../README.md)

---

> **Pick one deliberately.** A config that half-works looks fine from your own house and drops every caller who isn't on IPv6, with no error anywhere.

| | Who can call | Inbound port | Media stays yours | Costs |
|---|---|---|---|---|
| **1. LAN only** | your own network | none | yes | — |
| **2. LiveKit Cloud** | everyone, including restrictive corporate networks | none | **no** | an account and a bill |
| **3. One forwarded UDP port** | everyone | one UDP rule | yes | a router rule you must remember to remove |

Option 2 is the only one that includes TURN, which is the only thing that fixes genuinely restrictive corporate networks.

## Which am I on right now?

```bash
docker logs livekit-server 2>&1 | grep "using external IPs"
```

```
using external IPs  ["2001:db8:.../…", "192.168.1.10/192.168.1.10"]
                      ^ public IPv6, reachable    ^ LAN only, not reachable
```

- **A public address on both lines** — you are on option 3.
- **A LAN address on the IPv4 line** — IPv4 callers, roughly half the internet, cannot reach you.

The pipeline check's *Browser media path* stage says the same thing in words.

---

## Option 1 — LAN only

No port forwarding; nobody outside your network can call over IPv4. In `livekit.yaml` set `use_external_ip: false`, and keep the compose's `--node-ip` line.

> **One honesty check.** `use_external_ip: false` does **not** remove host candidates. A globally routable IPv6 on the machine — anything starting `2` or `3` in `ip -o addr show | grep inet6`, common on home fiber — is reachable from anywhere, with no forwarding at all.

If you want genuinely no inbound reach, firewall inbound UDP 7882 on IPv6 or restrict `interfaces.includes`. Otherwise you are really on *"IPv6 can reach me, IPv4 cannot"*.

## Option 2 — LiveKit Cloud carries the media

Everyone can call, with **no inbound port** — the media path is outbound to Cloud.

- **What it costs:** an account, a bill, and the audio leaving your network.
- **What stays home:** the station, the panel, the keys, and the transcripts.

Delete the `livekit-server` service from the compose, and point both Talk Wave services at Cloud in `.env`:

```env
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=<from the Cloud dashboard>
LIVEKIT_API_SECRET=<from the Cloud dashboard>
LIVEKIT_PUBLIC_URL=wss://<project>.livekit.cloud
```

## Option 3 — one forwarded UDP port, self-hosted

Everyone can call and the media stays yours. **One router rule.**

### The config

In `livekit.yaml`:

```yaml
rtc:
  udp_port: 7882             # ONE muxed port — not a range
  tcp_port: 7881
  use_external_ip: true      # discover the public address via STUN
  # node_ip:                 # MUST stay unset — it overrides the discovery
```

…and in the compose, **swap the two `command:` lines under `livekit-server`** so `--node-ip` is gone. It pins the advertised address to the LAN and defeats everything above.

> **Three things must be true together**, or you get the silent half-broken state:
>
> 1. `udp_port` is a single port, not a range
> 2. `node_ip` is unset
> 3. the port is actually forwarded
>
> Without the forward, LiveKit finds your public address, fails to validate it, and falls back to advertising the LAN.

### Before you start

- **Give the LiveKit host a DHCP-reserved IP.** A moved lease silently points the rule at nothing.
- **Check you are not behind CGNAT.** If your router's WAN address differs from what a "what is my IP" site says, forwarding cannot work — use option 2.

### The rule

Router UIs call it Port Forwarding, Virtual Server, or NAT Forwarding.

| Field | Value | Notes |
|---|---|---|
| Protocol | **UDP** | not TCP, not "Both" |
| External / WAN port | `7882` | single port, not a range |
| Internal IP | e.g. `192.168.1.10` | the LiveKit host |
| Internal port | `7882` | same as external |
| Source | any | callers come from anywhere |

- Do **not** forward `50000–50100` — that range is what the muxed port replaces.
- Skip TCP 7881 unless UDP proves blocked for some caller.

### Confirm it worked

```bash
docker compose restart livekit-server
```

A bind-mounted config is not reloaded by `up -d`, so it needs the restart. Then check that `using external IPs` shows your public address instead of the LAN one.

**The only real proof is a call from mobile data with wifi off.**

This config serves **LAN and off-LAN callers together** — removing `node_ip` does not trade one for the other. LAN traffic reaches the advertised public address by looping through your own router (NAT hairpin, which nearly every home router does). After the mobile-data proof call, make one from the LAN too; if that one fails, your router is the rare one that refuses hairpin, and pinning `node_ip` back is the LAN-only fallback.

<details>
<summary><b>What the rule exposes, honestly</b></summary>

**What it opens:** LiveKit's media port, and nothing else. Forwarding is per-port, per-protocol, per-destination, and audio still requires per-session ICE credentials issued over authenticated signalling.

**What it costs:**

- LiveKit's pre-auth packet parsing becomes internet-reachable — keeping it patched becomes your job.
- It answers unsolicited STUN, which makes it a weak reflector.
- The rule outlives your interest in the project. **Delete it when you stop running this.**

**The port does not gate who may call.** That is the guest code and the usage caps, so set those first. And if 443 is already open on this box, you are exposing far more than this.

</details>

## The TLS front door — one public name for the page and the signalling

Whichever option carries the media, the **signalling** works best as a single public name. Give the widget and LiveKit one hostname behind your reverse proxy — the bundled Caddy already does this:

| Route on `https://call.example.com` | Goes to | Notes |
|---|---|---|
| `/` (everything else) | `talkwave-web:8100` | the widget, the panel, the API |
| `/rtc` | `livekit-server:7880` | **WebSocket support ON** — this is signalling |

Then set `LIVEKIT_PUBLIC_URL=wss://call.example.com` in the stack environment and redeploy. With a real certificate there is no certificate screen at all, and because the page and the signalling share an origin, the pipeline check's browser-media stage passes by construction.

> **The `/rtc` half is the one people forget.** Without it the page loads, the token mints, and the call dies at *"could not establish signal connection"*.

Verify it from outside your LAN:

```bash
curl -s https://call.example.com/rtc/validate
```

- **LiveKit answering** *"no permissions to access the room"* proves the route — a 401 is correct, since you sent no token.
- **A 404 or a TLS error** means the proxy route is missing, or has no certificate.

Media never rides the proxy — it flows straight to the forwarded UDP port — so the front door adds no media latency.

Two names (the page on one, signalling on another) also works, but buys nothing and costs a second certificate and an origin-mismatch warning. **One name is the shape to pick.**
