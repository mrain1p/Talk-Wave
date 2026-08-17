"""Mastering a caller's clip for the air: trim, phone-band, drive, level.

Why this module exists at all: the first caller clip ever aired (2026-08-17)
went out around -22 dBFS RMS against a music bed near -15 — the mixer decoded
it end to end, the log said it played, and the one listener on the stream heard
nothing. Speech straight off a microphone has a crest factor near 18 dB, so its
peaks say "loud" while its body sits under the programme. The chain here is the
one that was then heard clearly on the same station the same night: band-pass
to the telephone octaves, a gentle tanh drive to pull the body up, and a peak
normalise — measured at -14.3 dBFS active RMS, which the voice channel's own
mic_chain then carries the rest of the way.

The band-pass is also the costume: 300-3400 Hz is what a phone line sounds
like, and a caller on a radio show is SUPPOSED to sound like a phone line —
full-bandwidth caller audio reads as a second studio mic, which is a small lie
about where the voice is coming from.

Everything here is stdlib on purpose (wave/struct/math), same as capture.py:
the suite needs no new dependency and neither does the image. The loops run at
review time on a bounded clip, not on the audio path of a live call.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

# The output contract: what local Whisper wants and twice what the band-pass
# keeps, so nothing here argues with the STT that reads the same file.
TARGET_RATE = 16000

# The telephone octaves. Below 300 Hz is the rumble that fights the music's
# low end and spends all the headroom saying nothing; above 3400 Hz is hiss
# the drive would amplify as hard as the voice.
BAND_LO_HZ = 300.0
BAND_HI_HZ = 3400.0

# The drive that was actually audible on air. 6.0 was needed to survive the
# sfx bed's 0.7 gain; on the voice channel that much saturation is just
# distortion, and 1.6 was the take the operator heard clearly.
DRIVE = 1.6

# Peak headroom either side of the drive. Not -0.1: the station transcodes
# and the mixer resamples, and intersample peaks over full scale come out the
# other side as crackle.
PEAK = 0.90

# What counts as silence when trimming, and how much room to leave so the
# first consonant is not clipped off. -40 dBFS is far below speech and above
# ordinary room tone once the band-pass has taken the rumble out.
TRIM_THRESHOLD_DB = -40.0
TRIM_MARGIN_SECS = 0.12


def read_wav_any(path: Path, max_secs: float) -> list[float]:
    """Any ordinary PCM WAV, as mono floats at TARGET_RATE, hard-capped.

    The same conversion capture._wav_as_mono16 does for the beep, re-stated
    here rather than imported because that reader caps at _BEEP_MAX_SECS (8s)
    by design — right for a beep, wrong for a message. The cap here is the
    caller's, from voicemail_max_seconds.
    """
    with wave.open(str(path), "rb") as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.readframes(min(w.getnframes(), int(rate * max_secs)))
    if width == 2:
        samples = list(struct.unpack("<%dh" % (len(frames) // 2), frames))
    elif width == 1:                       # unsigned 8-bit
        samples = [(b - 128) << 8 for b in frames]
    elif width == 4:                       # 32-bit int PCM
        samples = [v >> 16 for v in
                   struct.unpack("<%di" % (len(frames) // 4), frames)]
    else:
        raise ValueError(f"{width * 8}-bit WAV is not PCM this can master")
    if ch > 1:
        samples = [sum(samples[i:i + ch]) // ch
                   for i in range(0, len(samples) - ch + 1, ch)]
    out = [float(v) for v in samples]
    if rate > TARGET_RATE and out:
        # Anti-alias BEFORE decimating. Linear resample with no filter folds
        # everything above 8 kHz back INTO the voice band as inharmonic grit
        # — heard as "my voice sounds pretty bad" on the first live relay
        # test (2026-08-17), on a chain whose drive then amplified exactly
        # that. Two passes of the gentle biquad give ~-24 dB/oct at 7 kHz,
        # which is what makes the linear step below honest for speech.
        out = _lowpass(_lowpass(out, 7000.0, rate), 7000.0, rate)
    if rate != TARGET_RATE and out:
        # Linear resample — good enough for speech ONCE band-limited above.
        n = int(len(out) * TARGET_RATE / rate)
        res = []
        for i in range(n):
            pos = i * (len(out) - 1) / max(1, n - 1)
            lo = int(pos)
            hi = min(lo + 1, len(out) - 1)
            frac = pos - lo
            res.append(out[lo] * (1 - frac) + out[hi] * frac)
        out = res
    return out


def _biquad(x: list[float], b0: float, b1: float, b2: float,
            a1: float, a2: float) -> list[float]:
    y = []
    x1 = x2 = y1 = y2 = 0.0
    for v in x:
        o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, v
        y2, y1 = y1, o
        y.append(o)
    return y


def _highpass(x: list[float], freq: float, rate: int = TARGET_RATE) -> list[float]:
    w0 = 2 * math.pi * freq / rate
    a = math.sin(w0) / (2 * 0.707)
    c = math.cos(w0)
    n = 1 + a
    return _biquad(x, (1 + c) / 2 / n, -(1 + c) / n, (1 + c) / 2 / n,
                   (-2 * c) / n, (1 - a) / n)


def _lowpass(x: list[float], freq: float, rate: int = TARGET_RATE) -> list[float]:
    w0 = 2 * math.pi * freq / rate
    a = math.sin(w0) / (2 * 0.707)
    c = math.cos(w0)
    n = 1 + a
    return _biquad(x, (1 - c) / 2 / n, (1 - c) / n, (1 - c) / 2 / n,
                   (-2 * c) / n, (1 - a) / n)


def _dbfs(x: float) -> float:
    return 20 * math.log10(max(1e-9, x) / 32768)


def trim_silence(samples: list[float], rate: int = TARGET_RATE) -> list[float]:
    """Cut the dead air either side of the speech, leaving a small margin.

    The margin is not politeness: the first take of the harness clip started
    its trim ON the first word's opening consonant and the caller arrived
    saying "-ey" instead of "hey".
    """
    if not samples:
        return samples
    peak = max(abs(v) for v in samples)
    thr = max(peak * 0.04, 32768 * 10 ** (TRIM_THRESHOLD_DB / 20))
    step = max(1, int(rate * 0.02))
    first = last = None
    for i in range(0, len(samples) - step + 1, step):
        if max(abs(v) for v in samples[i:i + step]) > thr:
            if first is None:
                first = i
            last = i + step
    if first is None:
        return []
    margin = int(rate * TRIM_MARGIN_SECS)
    return samples[max(0, first - margin):min(len(samples), last + margin)]


def master(src: Path, dst: Path, max_secs: float) -> dict:
    """The whole chain: read anything, trim, band-pass, drive, write 16k mono.

    Returns the numbers the review card and the call record want. Raises
    ValueError on a clip with nothing in it — the API turns that into "we
    couldn't hear anything, try again" rather than airing eight seconds of
    room tone.
    """
    samples = trim_silence(read_wav_any(src, max_secs))
    if len(samples) < TARGET_RATE * 0.4:
        raise ValueError("the recording contains no audible speech")

    band = _lowpass(_highpass(samples, BAND_LO_HZ), BAND_HI_HZ)

    # Normalise INTO the drive so its knee lands at the same place regardless
    # of how quiet the source was — input level must not change the sound,
    # only the noise floor the caller recorded with.
    k = (PEAK * 32767) / max(1e-9, max(abs(v) for v in band))
    driven = [32767 * math.tanh(v * k / 32767 * DRIVE) for v in band]
    k2 = (PEAK * 32767) / max(1e-9, max(abs(v) for v in driven))
    out = [int(max(-32768, min(32767, v * k2))) for v in driven]

    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(struct.pack("<%dh" % len(out), *out))

    active = [v for v in out if abs(v) > 32768 * 10 ** (TRIM_THRESHOLD_DB / 20)]
    rms = math.sqrt(sum(v * v for v in active) / len(active)) if active else 0.0
    return {
        "seconds": round(len(out) / TARGET_RATE, 2),
        "peakDb": round(_dbfs(max(abs(v) for v in out)), 1),
        "rmsDb": round(_dbfs(rms), 1),
    }


def wav_seconds(path: Path) -> float:
    """How long a finished clip runs — the adapter sizes its wait from this,
    the same way the station sizes voice holds from the WAV header."""
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate() or TARGET_RATE)
