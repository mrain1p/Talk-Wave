"""Synthesize the bundled library packs — run once, commit the WAVs.

Every clip here is MADE, not sourced: pure maths through the stdlib `wave`
module, the same philosophy as the widget's Exchange and Handset sets and
the server's answering-machine beep. That is a licensing decision as much
as an aesthetic one — the repo is public, and a shelf of synthesized clips
ships with no attribution sheet and no provenance doubt. Real recorded
clips (CC0) can join the shelf later; they arrive by download + approval,
never silently.

Deterministic on purpose: running this twice writes byte-identical files,
so a diff on assets/ always means somebody changed the RECIPE.

Usage:  python tools/make_library_sounds.py
Writes: assets/sounds/library/*.wav  (catalog.json is maintained by hand —
        a generated file must not clobber operator-curated labels).
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

RATE = 24000
OUT = Path(__file__).resolve().parent.parent / "assets" / "sounds" / "library"


def _samples(secs: float):
    return range(int(RATE * secs))


def _clamp(v: float) -> int:
    return max(-32767, min(32767, int(v * 32767)))


def tone(freqs, secs, gain=0.3, fade=0.01, tremolo=0.0):
    """A chord of sines with edge fades — the building block of everything
    here. `tremolo` (Hz) wobbles the amplitude, which is what makes a bell
    read as a bell rather than a test tone."""
    n = int(RATE * secs)
    edge = max(1, int(RATE * fade))
    out = []
    for i in _samples(secs):
        amp = gain
        if i < edge:
            amp *= i / edge
        elif i > n - edge:
            amp *= (n - i) / edge
        if tremolo:
            amp *= 0.7 + 0.3 * math.sin(2 * math.pi * tremolo * i / RATE)
        v = sum(math.sin(2 * math.pi * f * i / RATE) for f in freqs) / len(freqs)
        out.append(amp * v)
    return out


def decay(freqs, secs, gain=0.4, half_life=0.25):
    """A struck note: full volume at the front, exponential ring-out."""
    out = []
    for i in _samples(secs):
        t = i / RATE
        amp = gain * (0.5 ** (t / half_life))
        v = sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs)
        out.append(amp * v)
    return out


def silence(secs):
    return [0.0] * int(RATE * secs)


def noise_burst(secs, gain=0.25, half_life=0.03, seed=1234):
    """A thump/click: shaped noise from a tiny deterministic LCG — random
    enough for percussion, stable enough to commit."""
    state = seed
    out = []
    for i in _samples(secs):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        r = (state / 0x7FFFFFFF) * 2 - 1
        t = i / RATE
        out.append(gain * (0.5 ** (t / half_life)) * r)
    return out


def write(name: str, pcm) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack("<%dh" % len(pcm), *[_clamp(v) for v in pcm]))
    print("wrote", name, round(len(pcm) / RATE, 1), "s")


def main() -> None:
    # --- the Modern set: a smartphone's soft furniture --------------------
    # Ring: a marimba-ish four-note figure, twice, with air between.
    figure = []
    for f in (523.25, 659.25, 783.99, 1046.5):        # C5 E5 G5 C6
        figure += decay([f, f * 2], 0.28, gain=0.35, half_life=0.18)
    ring = figure + silence(0.5) + figure + silence(0.3)
    write("modern-ring.wav", ring)
    # Pickup: one warm pop.
    write("modern-pickup.wav", decay([880, 1760], 0.22, gain=0.35, half_life=0.06))
    # Hold: two soft alternating notes — patient, not alarmed.
    write("modern-hold.wav",
          decay([659.25], 0.5, gain=0.25, half_life=0.3) + silence(0.15)
          + decay([523.25], 0.6, gain=0.25, half_life=0.35))
    # Hang up: a falling third, closed.
    write("modern-hangup.wav",
          decay([659.25], 0.18, gain=0.3, half_life=0.09)
          + decay([523.25], 0.3, gain=0.3, half_life=0.1))
    # Can't connect: the gentle double-buzz every handset owner knows.
    buzz = tone([392, 396], 0.35, gain=0.28)
    write("modern-failed.wav", buzz + silence(0.18) + buzz)

    # --- the Rotary set: bakelite and bells --------------------------------
    # Ring: a real two-bell strike (slightly detuned pair), twice.
    strike = decay([1568, 1583, 3136], 1.4, gain=0.4, half_life=0.5)
    trem = [v * (0.75 + 0.25 * math.sin(2 * math.pi * 20 * i / RATE))
            for i, v in enumerate(strike)]
    write("rotary-ring.wav", trem + silence(0.4) + trem)
    # Dial: seven pulses of a returning rotary dial.
    click = noise_burst(0.04, gain=0.3, half_life=0.008)
    dial = []
    for _ in range(7):
        dial += click + silence(0.06)
    write("rotary-dial.wav", dial + silence(0.1))
    # Pickup: the handset leaving the cradle — clunk plus a breath of line hum.
    write("rotary-pickup.wav",
          noise_burst(0.09, gain=0.4, half_life=0.02, seed=99)
          + tone([50, 100], 0.25, gain=0.06))
    # Hang up: the cradle taking it back, heavier.
    write("rotary-hangup.wav",
          noise_burst(0.12, gain=0.5, half_life=0.03, seed=7)
          + silence(0.06)
          + noise_burst(0.05, gain=0.2, half_life=0.01, seed=8))

    # --- loose and cheeky ---------------------------------------------------
    # Sad trombone: three descending smears. The fourth wall is a door too.
    def smear(f_from: float, f_to: float, secs: float, gain=0.35):
        out = []
        n = int(RATE * secs)
        phase = 0.0
        for i in range(n):
            t = i / n
            f = f_from + (f_to - f_from) * t
            phase += 2 * math.pi * f / RATE
            amp = gain * min(1.0, (1 - t) * 4) * min(1.0, t * 20 + 0.2)
            # a touch of sawtooth for brass
            v = math.sin(phase) + 0.35 * math.sin(2 * phase) + 0.15 * math.sin(3 * phase)
            out.append(amp * v / 1.5)
        return out

    trombone = (smear(233, 220, 0.5) + smear(220, 208, 0.5)
                + smear(208, 196, 0.45) + smear(196, 175, 1.0))
    write("sad-trombone.wav", trombone)


if __name__ == "__main__":
    main()
