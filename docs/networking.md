# Calling from outside your network

WebRTC needs a path for the media, not just for the signalling — which is why a call can connect, show every green light, and carry no audio. Three ways to give it one, in increasing order of exposure.

[← back to the README](../README.md)

---

## Calling from outside your network

Signalling rides your reverse proxy on 443, so the page loads for anyone.
**Audio doesn't.** Media goes direct to the address LiveKit advertises; if that
isn't reachable from the caller's network they get about fifteen seconds of
ringing and a dead line.

**Pick one of the three below deliberately.** The failure this section exists to
prevent is not choosing: a config that half-works looks fine from your own
house and drops every caller who isn't on IPv6, with no error anywhere.

### Which am I on right now?

```bash
docker logs <livekit container> 2>&1 | grep "using external IPs"
```

```
using external IPs  ["2600:4040:.../…", "192.168.1.245/192.168.1.245"]
                      ^ public IPv6, reachable    ^ LAN only, not reachable
```

A **public** address on both lines is option 3. A LAN address on the IPv4 line
means IPv4 callers cannot reach you — roughly half the internet, including most
office wifi. The pipeline check's *Browser media path* stage says the same
thing in words.

---

### Option 1 — LAN only

No port forwarding, and nobody on IPv4 outside your network can call.

`livekit.yaml` — see [`livekit.example.yaml`](livekit.example.yaml):

```yaml
rtc:
  udp_port: 7882
  tcp_port: 7881
  use_external_ip: false     # no STUN discovery of your public address
```

#### ⚠️ `use_external_ip: false` is not the same as "unreachable"

**If your machine has a globally routable IPv6 address, callers on IPv6 can
still reach it — from anywhere — with no port forwarding at all.**

`use_external_ip` controls whether LiveKit *discovers* its public address via
STUN. It does not control **host candidates**, which come from enumerating the
network interfaces. A global IPv6 address on your interface is a host
candidate, and IPv6 has no NAT, so it is directly reachable.

You can see this in the ICE log: the IPv6 candidate is labelled `host`, not
`srflx`, meaning it never came from STUN and turning STUN off does not remove
it.

```bash
ip -o addr show <your interface> | grep inet6
```

An address starting `2000::/3` — anything beginning `2` or `3` — is global.
`fe80::` is link-local and harmless. Many home ISPs (Verizon FiOS among them)
hand out a global IPv6 by default, so this is common rather than exotic.

**For genuinely no inbound reachability**, you need one of:

- a host firewall rule dropping inbound UDP 7882 on IPv6, or
- no global IPv6 on that interface, or
- `interfaces.includes` pointing at an interface that has no global IPv6.

Otherwise be honest with yourself that you are on "IPv6 callers can reach me,
IPv4 callers cannot" — which is option 3 with half the audience missing, not
option 1.

---

### Option 2 — LiveKit Cloud carries the media

Everyone can call. **No inbound port**: the media path is outbound to Cloud.
Costs an account, a bill, and the audio leaving your network. It also includes
TURN, which is the only thing that fixes genuinely restrictive corporate
networks — neither of the other options helps there.

Drop the `livekit-server` service from
[`docker-compose.yaml`](docker-compose.yaml) entirely and point the two Python
services at Cloud in `.env`:

```env
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=<from the Cloud dashboard>
LIVEKIT_API_SECRET=<from the Cloud dashboard>
LIVEKIT_PUBLIC_URL=wss://<project>.livekit.cloud
```

Everything else — the station, the panel, your keys, the transcripts — stays on
your machine. Only the audio relay moves.

---

### Option 3 — one forwarded UDP port, self-hosted

Everyone can call and the media stays yours. **One router rule.**

`livekit.yaml`:

```yaml
rtc:
  udp_port: 7882             # ONE muxed port — not a range
  tcp_port: 7881
  use_external_ip: true      # discover the public address via STUN
  # node_ip:                 # MUST stay unset — see below
```

Three things have to be true together, and missing any one of them produces the
silent half-broken state:

1. **`udp_port`, not `port_range_start`/`end`.** A range means one firewall rule
   per port. LiveKit muxes every call over the single port.
2. **`node_ip` unset.** It pins the advertised address and *overrides* what
   `use_external_ip` discovers, so a LAN value works perfectly on your own
   network and breaks every outside caller.
3. **The port actually forwarded.** Without it `use_external_ip` finds your
   public address and then fails to validate it, and LiveKit falls back to
   advertising the LAN address — the exact half-broken state.

#### ⚠️ Read this before opening the port

Forwarding a port is **not reversible by accident** — it stays open until you
remove it, including after you stop caring about this project. Specifically:

- It exposes **LiveKit's media port to the entire internet.** Keeping LiveKit
  patched becomes your job. A vulnerability in its ICE or DTLS handling would
  be reachable before any authentication.
- It answers unsolicited STUN, which makes it a weak (~2–4×) reflector. Not
  useful enough to attract attackers on its own, but it will be scanned.
- **It does not gate who can call.** That is `front_access` and your usage
  limits. An open media port with no guest code and generous limits is an
  invitation to spend your LLM and TTS budget. Set a guest code *first*.
- If you later stop using Talk Wave, **delete the rule.** A forwarded port to
  a host that no longer runs what you think it runs is how home networks get
  into trouble.

It does *not* expose anything else on that machine — forwarding is per-port,
per-protocol, per-destination. And if you already run a reverse proxy on 443,
you are already exposing a much larger surface than this.

#### Before you start

1. **Give the LiveKit host a static or DHCP-reserved IP.** If it is
   `192.168.1.245` today and DHCP moves it tomorrow, the rule silently points
   at nothing and calls fail with no error. Do this in the router's DHCP
   section, not on the machine.
2. **Check you are not behind CGNAT.** Compare the address your router shows as
   its WAN/internet address against what a "what is my IP" site reports. If
   they differ, your ISP is doing carrier-grade NAT and **port forwarding
   cannot work at all** — use option 2 instead. An address in
   `100.64.0.0 – 100.127.255.255` is CGNAT. (Confusingly, `100.0.0.0 – 100.63.x`
   is *not*.)

#### The rule

Router admin pages call this **Port Forwarding**, **Virtual Server**,
**NAT Forwarding**, or **Applications & Gaming** depending on the vendor. Add
one rule:

| Field | Value | Notes |
|---|---|---|
| Name / description | `talkwave-media` | anything; for your own memory |
| Protocol | **UDP** | not TCP, not "Both" |
| External / WAN / public port | `7882` | single port, not a range |
| Internal / LAN / private IP | e.g. `192.168.1.245` | the LiveKit host |
| Internal / private port | `7882` | same as external |
| Source / remote IP | any / blank | callers come from anywhere |
| Enabled | yes | |

Then **save and apply** — many routers stage changes until you do.

Do **not** forward `50000–50100`. That range is what `udp_port: 7882` replaces,
and forwarding it is a hundred needless holes. Do not forward TCP 7881 unless
UDP turns out to be blocked on some caller's network; one rule is easier to
reason about than two.

#### Confirm it worked

Restart LiveKit so it re-runs external-address discovery — a bind-mounted
config is not reloaded by `up -d`:

```bash
docker compose restart livekit-server
docker logs <livekit container> 2>&1 | grep "using external IPs"
```

**Before** the rule, the IPv4 entry is your LAN address, because LiveKit found
the public one and could not validate it:

```
using external IPs  ["2600:4040:.../…", "192.168.1.245/192.168.1.245"]
```

**After**, it should be your public address:

```
using external IPs  ["2600:4040:.../…", "100.33.134.4/192.168.1.245"]
```

If it still shows the LAN address, the rule is not taking effect — check the
internal IP matches the LiveKit host, that the protocol is UDP, and that you
applied the change. Then have someone call from mobile data with wifi off,
which is the only real proof.

**What that exposes.** LiveKit's media port, and nothing else on the host —
forwarding is per-port, per-protocol, per-destination. Getting audio in still
requires ICE credentials and a DTLS fingerprint issued per session over the
authenticated signalling channel, so the port alone grants nothing. The real
costs are that it answers unsolicited STUN (a weak ~2–4× reflector), and that
LiveKit's pre-auth ICE/DTLS parsing becomes reachable from the internet.

Weigh it against what you already expose: if port 443 is open, you are already
running a public HTTP application with authentication, a settings API and file
upload. This is a smaller surface than that.

Pair it with a **guest code** and non-zero usage limits. A public media port,
no guest code and generous limits is an invitation to spend your API budget.
