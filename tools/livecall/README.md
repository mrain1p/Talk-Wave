# Placing a real call, without a person

Everything in a run here is genuine except the mouth: real LiveKit, real STT, the real model,
real TTS, the real station. The microphone is a WAV file Chrome is pretending is a capture
device, so a call can be driven end to end from a terminal and the record read back afterwards.

It exists because the ducking could not be diagnosed any other way. The on-air hold only
happens when the DJ actually goes on air, which needs a real call, a real request and a real
station — and the interesting part is *timing*, so a human on the line changes the thing being
measured. Every ducking fix from 0.10.121 to 0.10.124 was sized from a run of this.

## What you need

- Chrome (the harness talks to it over CDP, not through a driver).
- `websocket-client` in the venv.
- The front door and guest code in the environment. **They are not in this file** — the repo is
  public. `.claude/OPERATOR.local.md` is gitignored and holds the real values.

```bash
export TALKWAVE_URL="https://<your front door>/" TALKWAVE_GUEST_CODE="<guest code>"
```

## Making the caller talk

`say.ps1` drives Windows' own speech synthesiser — no API key, no network, and the format is
already what Chrome's fake device wants (16 kHz, mono, 16-bit). Edit the lines, run it, then
stitch the clips together with the silence that makes the pacing realistic:

```bash
powershell -ExecutionPolicy Bypass -File tools/livecall/say.ps1
```

**The silence is the point, not padding.** A caller who talks straight through never leaves the
DJ room to act, and a run with no gap after the request never produces a hold to measure. The
run that found the widget bug had 75 seconds of silence after "give a shout out for my friend" —
long enough to sit through the whole duck without saying a word over it.

Chrome wants one WAV, so concatenate:

```python
import wave
RATE = 16000
def silence(s): return b"\x00\x00" * int(RATE * s)
def clip(n):
    w = wave.open(f"audio/{n}.wav"); d = w.readframes(w.getnframes()); w.close(); return d

out = wave.open("audio/caller.wav", "wb")
out.setnchannels(1); out.setsampwidth(2); out.setframerate(RATE)
for part in [silence(16), clip("d1"), silence(75), clip("d2"), silence(8)]:
    out.writeframes(part)
out.close()
```

The leading 16 seconds matter too: the greeting now waits for clear air, so a caller who speaks
at second two is talking over it.

## Running it

Chrome has to be launched with the fake device — the flags are load-bearing and three of them
have cost time:

```bash
chrome --remote-debugging-port=9333 --remote-allow-origins=* \
  --user-data-dir=/tmp/callprofile \
  --use-fake-ui-for-media-stream --use-fake-device-for-media-stream \
  --use-file-for-fake-audio-capture="$PWD/audio/caller.wav%noloop" \
  --autoplay-policy=no-user-gesture-required --no-first-run about:blank
```

- **`--remote-allow-origins=*`** or the CDP websocket is refused with a 403 that names the flag.
- **`%noloop`** or the caller repeats their lines forever and the DJ hears an increasingly
  strange conversation.
- **`--autoplay-policy=no-user-gesture-required`**, because the widget tunes the caller into the
  station and a blocked autoplay means the call runs with no broadcast behind it — which is
  exactly the audio the duck is about.

Then:

```bash
python tools/livecall/place_call.py
```

It seeds the guest code before the page reads it (the run is not meant to be testing the
sign-in form), presses Call, taps the talk bar once after the greeting — push-to-talk is a
toggle, not a hold — and prints the widget's state every five seconds until the call ends.

## Reading it back

The call record carries the ducking timeline (`call/air_log.py`), which is the part worth
looking at:

```
+   0.0s  station voice.queued   durSecs 30.4, bufSecs 22.0, audibleIn 23.0
+   0.0s  hold opened            why='we put something on air', forSecs 33.5
+   1.3s  station voice.start
+  31.5s  station voice.end
+  35.7s  hold closed            heldSecs 35.7
```

The panel renders the same rows under the transcript. What to look for: a hold that opens long
before `voice.start`, one that closes before `voice.end`, or `heldSecs` far larger than
`durSecs` — the three shapes the reports have taken.

## Two warnings

**It really broadcasts.** A run that asks for a shoutout puts one on the air, in front of
whoever is listening. Ask the operator first, every time — a standing yes is not a thing here.

**Don't ask it to skip, take over, or run a segment.** Every station-wide permission is open on
the operator's deployment, so a test caller asking for a skip really does cut the record its
listeners are hearing.
