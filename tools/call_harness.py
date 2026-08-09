r"""A scripted caller. Joins a room against the real local worker, listens for
the DJ, optionally says something, and times every leg of the exchange.

Why this exists: the suite fakes the phone. 400+ tests and not one of them
ever places a call, so the class of bug that lives in real audio — endpointing,
turn timing, a greeting that never arrives — ships invisibly. The push-to-talk
endpointing gap reached a beta tester's fork before anything in this repo
noticed. This is the smallest tool that would have caught it: a real LiveKit
client, the real worker, and a stopwatch.

Usage (local stack running — .\run-local.ps1 from the repo root):

    .venv\Scripts\python.exe tools\call_harness.py
    .venv\Scripts\python.exe tools\call_harness.py --wav ask.wav --record call.wav
    .venv\Scripts\python.exe tools\call_harness.py --guest-code 1234

With no --wav it proves the answer path: token -> dispatch -> prepare ->
greeting audio actually heard, with timings. With --wav (16-bit PCM, the
caller's line) it also measures the full turn: end of caller speech to first
audible DJ reply — the number that decides whether the call feels like a
phone call or a kiosk. --record writes everything the DJ said to a WAV so a
human can hear the call afterwards.

Not part of test_sidecar on purpose: it needs the venv (livekit), a running
stack, and working STT/LLM/TTS backends. The suite stays stdlib-only and
network-free; this is the tool for the layer the suite structurally cannot
see.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import json
import sys
import time
import urllib.parse
import urllib.request
import wave

from livekit import rtc

# A frame is "loud" when its mean absolute int16 amplitude clears this. The
# agent's track carries genuine silence between sentences, so plain presence
# of frames means nothing — only sound does. 300/32767 is about -41 dBFS,
# comfortably above line noise and far below speech.
LOUD = 300

# The greeting is considered finished after this much continuous quiet
# following speech. Matches ordinary sentence gaps: shorter and a mid-greeting
# pause reads as the end, longer and the harness dawdles.
GREETING_GAP = 1.2


def refuse_remote(server: str) -> None:
    """Localhost only, no override flag. Minting a token against a deployed
    instance starts a real agent job, spends real LLM+TTS money and occupies
    the live concurrency slot — the master plan's toolbox rule is 'never', and
    a convenience flag here is how 'never' becomes 'once, by accident'."""
    host = urllib.parse.urlparse(server).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1"):
        sys.exit(f"refusing non-local server {server!r}: this tool places real "
                 "calls and must only ever point at the local stack")


def mint(server: str, guest_code: str) -> dict:
    req = urllib.request.Request(
        f"{server}/token", data=b"{}", method="POST",
        headers={"Content-Type": "application/json",
                 **({"X-Call-Key": guest_code} if guest_code else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.exit(f"/token refused ({e.code}): {detail}")


def call_ended(server: str, room: str) -> None:
    """What the widget does on hangup, so the finished call stops counting
    against the concurrency limit immediately instead of aging out."""
    req = urllib.request.Request(
        f"{server}/call-ended", data=json.dumps({"room": room}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass  # best-effort, same as the widget's sendBeacon


def loudness(frame: rtc.AudioFrame) -> float:
    samples = array.array("h")
    samples.frombytes(bytes(frame.data))
    return (sum(abs(s) for s in samples) / len(samples)) if samples else 0.0


class Stopwatch:
    """Timestamps for each leg, all relative to the token mint — the moment a
    real caller pressed Call."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.marks: dict[str, float] = {}

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.monotonic() - self.t0)

    def show(self) -> None:
        for name, at in self.marks.items():
            print(f"  {at:7.2f}s  {name}")


async def play_wav(source: rtc.AudioSource, path: str) -> None:
    """Push a WAV down the caller's track at real-time pace, then a second of
    genuine silence — the VAD needs to hear the line go quiet or endpointing
    never fires and the DJ waits forever."""
    wf = wave.open(path, "rb")
    if wf.getsampwidth() != 2:
        sys.exit("--wav must be 16-bit PCM")
    sr, ch = wf.getframerate(), wf.getnchannels()
    chunk = sr // 50  # 20ms
    started = time.monotonic()
    sent = 0
    while True:
        raw = wf.readframes(chunk)
        if not raw:
            break
        if ch == 2:  # left channel; averaging buys nothing for a test phrase
            mono = array.array("h")
            mono.frombytes(raw)
            raw = mono[0::2].tobytes()
        n = len(raw) // 2
        frame = rtc.AudioFrame.create(sr, 1, n)
        # frame.data is an int16 ('h') view; bytes export as 'B', so cast the
        # source to match — a plain byte-slice assign raises on the format.
        frame.data[:n] = memoryview(raw).cast("h")
        await source.capture_frame(frame)
        sent += n
        # Pace against the wall clock, not per-chunk sleeps — drift on a long
        # file otherwise stacks up and the tail arrives late.
        ahead = started + sent / sr - time.monotonic()
        if ahead > 0:
            await asyncio.sleep(ahead)
    silence = rtc.AudioFrame.create(sr, 1, chunk)
    for _ in range(50):  # 1s
        await source.capture_frame(silence)
        await asyncio.sleep(0.02)


async def run(args: argparse.Namespace) -> int:
    refuse_remote(args.server)
    watch = Stopwatch()
    grant = mint(args.server, args.guest_code)
    watch.mark("token minted")
    print(f"room {grant['room']}")

    room = rtc.Room()
    audio_frames: asyncio.Queue[rtc.AudioFrame] = asyncio.Queue()
    subscribed = asyncio.Event()

    @room.on("track_subscribed")
    def on_track(track: rtc.Track, pub, participant) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        subscribed.set()

        async def pump() -> None:
            async for event in rtc.AudioStream(track):
                await audio_frames.put(event.frame)

        asyncio.ensure_future(pump())

    recorder = None
    try:
        await room.connect(grant["url"], grant["token"])
        watch.mark("room joined")

        # The widget gives up at 40s with the engaged tone; the harness holds
        # the same standard so "worker down" fails the same way a caller sees.
        try:
            await asyncio.wait_for(subscribed.wait(), args.answer_timeout)
        except asyncio.TimeoutError:
            print(f"FAIL: no agent audio track within {args.answer_timeout:.0f}s "
                  "— the call was never answered")
            return 2
        watch.mark("answered (DJ track subscribed)")

        if args.record:
            recorder = wave.open(args.record, "wb")

        async def next_loud(deadline: float, label: str) -> bool:
            """Consume frames until one carries sound, recording as we go."""
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                try:
                    frame = await asyncio.wait_for(audio_frames.get(), remaining)
                except asyncio.TimeoutError:
                    return False
                if recorder:
                    if recorder.getnframes() == 0:
                        recorder.setnchannels(frame.num_channels)
                        recorder.setsampwidth(2)
                        recorder.setframerate(frame.sample_rate)
                    recorder.writeframes(bytes(frame.data))
                if loudness(frame) > LOUD:
                    watch.mark(label)
                    return True

        async def drain_until_quiet() -> None:
            """Wait for the current DJ speech to end: GREETING_GAP of quiet."""
            last_loud = time.monotonic()
            while time.monotonic() - last_loud < GREETING_GAP:
                try:
                    frame = await asyncio.wait_for(audio_frames.get(), GREETING_GAP)
                except asyncio.TimeoutError:
                    return
                if recorder:
                    recorder.writeframes(bytes(frame.data))
                if loudness(frame) > LOUD:
                    last_loud = time.monotonic()

        if not await next_loud(time.monotonic() + args.greet_timeout,
                               "greeting audible"):
            print(f"FAIL: answered but no audible greeting within "
                  f"{args.greet_timeout:.0f}s — dead air is the A1 bug shape")
            return 3
        await drain_until_quiet()
        watch.mark("greeting finished")

        if args.wav:
            source = rtc.AudioSource(wave.open(args.wav, "rb").getframerate(), 1)
            track = rtc.LocalAudioTrack.create_audio_track("caller-voice", source)
            await room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(
                    source=rtc.TrackSource.SOURCE_MICROPHONE))
            watch.mark("caller speaking")
            await play_wav(source, args.wav)
            watch.mark("caller finished")
            if not await next_loud(time.monotonic() + args.reply_timeout,
                                   "DJ reply audible"):
                print(f"FAIL: no audible reply within {args.reply_timeout:.0f}s "
                      "of the caller finishing")
                return 4
            turn = watch.marks["DJ reply audible"] - watch.marks["caller finished"]
            await drain_until_quiet()
            watch.mark("reply finished")
            print(f"\nturn latency (caller stops -> DJ audible): {turn:.2f}s"
                  f"  {'OK' if turn <= args.turn_budget else 'SLOW'}"
                  f" (budget {args.turn_budget:.1f}s)")

        if recorder:
            print(f"recorded the DJ side to {args.record}")

        print("\ntimeline:")
        watch.show()
        return 0
    finally:
        # Close on every path, not just success — a failed call's recording is
        # exactly the one worth listening to, and an unclosed wave file carries
        # a zero frame count in its header.
        if recorder:
            recorder.close()
        call_ended(args.server, grant["room"])
        await room.disconnect()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--server", default="http://localhost:8100",
                   help="token server (localhost only, enforced)")
    p.add_argument("--guest-code", default="", help="door code, if one is set")
    p.add_argument("--wav", help="16-bit PCM WAV spoken as the caller's line")
    p.add_argument("--record", help="write the DJ's audio to this WAV")
    p.add_argument("--answer-timeout", type=float, default=40.0,
                   help="seconds before an unanswered call fails (widget: 40)")
    p.add_argument("--greet-timeout", type=float, default=30.0)
    p.add_argument("--reply-timeout", type=float, default=30.0)
    p.add_argument("--turn-budget", type=float, default=4.0,
                   help="advisory turn-latency budget; prints SLOW above it")
    args = p.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
