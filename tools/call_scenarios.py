r"""Call-orchestration scenarios: every shape of call, placed for real.

Where call_harness.py places ONE call against whatever stack is running, this
boots its own stack — livekit, token server, worker — on scratch settings and
walks the line through its modes: live calls, voicemail, the fallback between
them, push to talk, the timeouts, and a read-only tool call. Each scenario
rewrites the scratch settings file and relies on the invariant that settings
are re-read at the start of every call, so one boot serves them all.

    .venv\Scripts\python.exe tools\call_scenarios.py            # all of them
    .venv\Scripts\python.exe tools\call_scenarios.py flow ptt   # just these
    .venv\Scripts\python.exe tools\call_scenarios.py --list

Scenarios: no-answer, flow, ptt, tools, idle, max-length, call-only,
vm-only, vm-fallback.

Safety: the stack is built here and only here — there is no way to point this
at a deployment, and the runner aborts if LiveKit's port is already taken
rather than sharing a room with somebody else's worker. The real data/ is
never touched: settings, auth and call records all live in a scratch
directory (secrets are read from their usual place, the same way a real call
reads them). The station is still the real one from .env, but every scenario
sticks to READ paths — nothing here queues a track, runs a segment, or makes
a sound on air. Not part of test_sidecar: it needs the venv, working
STT/LLM/TTS backends, and several minutes of wall clock.

Run it before and after touching call/, brain/, the widget's call surface, or
any orchestration setting — it is the regression net for exactly the class of
bug the suite cannot hear.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from livekit import rtc

sys.path.insert(0, str(Path(__file__).parent))
from call_harness import GREETING_GAP, LOUD, loudness, play_wav  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PORT = 8199
SERVER = f"http://localhost:{PORT}"

# One spoken line per job, synthesized once with the OS voice. SAPI is
# Windows-only, which is fine: this runner drives the PC stack by design.
LINES = {
    "ask": "Hey! What song is playing right now?",
    "tools": "Can you check if the music library has any songs by the Beatles?",
    "vm": "Hi, this is a test message from the scenario runner. Tell the D J the harness called.",
}


def say_to_wav(text: str, path: Path) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Add-Type -AssemblyName System.Speech; "
         "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
         f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()"],
        check=True, capture_output=True)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class Stack:
    """The three processes, on scratch state, torn down whatever happens."""

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.calls = scratch / "calls"
        self.calls.mkdir(exist_ok=True)
        self.settings = scratch / "settings.json"
        # Start from the operator's real settings so the STT/TTS/LLM wiring
        # matches what a real local call uses; scenarios overlay their own.
        base = REPO / "data" / "settings.json"
        self.base_cfg = json.loads(base.read_text()) if base.exists() else {}
        self.env = {
            "SETTINGS_PATH": str(self.settings),
            "CALLS_PATH": str(self.calls),
            "ADMIN_AUTH_PATH": str(scratch / "no-auth.json"),  # first-run: open
            "TOKEN_SERVER_PORT": str(PORT),
            "LOG_TO_FILE": "0",
        }
        self.livekit: subprocess.Popen | None = None
        self.token: subprocess.Popen | None = None
        self.worker: subprocess.Popen | None = None
        self.worker_log = open(scratch / "worker.log", "w", encoding="utf-8")

    def write_settings(self, overlay: dict) -> None:
        cfg = dict(self.base_cfg)
        cfg.update(overlay)
        # Forced last, above any overlay: the dev .env points at the REAL
        # station, and these are every path by which a call makes sound on
        # air or writes station state. The first scenario run proved the
        # need — callback_enabled rode in from the operator's dev settings
        # and a two-turn scripted call put a real line on real air.
        cfg.update({
            "callback_enabled": False,     # back-to-air handoff = dj_say
            "allow_announcements": "off",  # on-air tools
            "allow_skills": "off",
            "allow_requests": "off",       # queues real tracks
            "allow_takeover": "off",
        })
        self.settings.write_text(json.dumps(cfg))

    def _spawn(self, *args: str, log=None) -> subprocess.Popen:
        import os

        return subprocess.Popen(
            list(args), cwd=str(REPO / "agent-worker"),
            env={**os.environ, **self.env},
            stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT)

    def start(self) -> None:
        if not port_free(7880):
            sys.exit("port 7880 is taken — stop the other stack first "
                     "(run-local.ps1 -Stop); this runner must own the only "
                     "worker or calls get answered by the wrong one")
        self.write_settings({})
        self.livekit = subprocess.Popen(
            [str(REPO / "bin" / "livekit-server.exe"),
             "--config", str(REPO / "livekit.yaml")],
            cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        py = str(REPO / ".venv" / "Scripts" / "python.exe")
        self.token = self._spawn(py, "token_server.py")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"{SERVER}/health", timeout=2).read()
                break
            except Exception:
                time.sleep(0.5)
        else:
            sys.exit("token server never answered /health")
        self.start_worker()

    def start_worker(self) -> None:
        py = str(REPO / ".venv" / "Scripts" / "python.exe")
        # Only the log written from HERE counts. A restarted worker's log
        # still holds the previous "registered worker" line, and matching it
        # declared readiness while the new process was still prewarming —
        # the next scenario then dialled a room nobody was registered for.
        log_path = self.scratch / "worker.log"
        offset = log_path.stat().st_size if log_path.exists() else 0
        self.worker = self._spawn(py, "main.py", "start", log=self.worker_log)
        # 180s to match the worker's own initialize_process_timeout — a cold
        # prewarm is allowed to take that long, so waiting less than the
        # worker's own budget invents failures.
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                if "registered worker" in f.read():
                    return
            if self.worker.poll() is not None:
                break
            time.sleep(1)
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        # Raise, don't sys.exit: a counted failure keeps the scratch dir for
        # diagnosis; SystemExit skipped the keep-on-failure branch and
        # deleted the evidence.
        raise RuntimeError("worker did not register — log tail:\n" + tail)

    def stop_worker(self) -> None:
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            try:
                self.worker.wait(10)
            except subprocess.TimeoutExpired:
                self.worker.kill()
        self.worker = None

    def stop(self) -> None:
        self.stop_worker()
        for proc in (self.token, self.livekit):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.worker_log.close()

    def records(self) -> list[dict]:
        out = []
        for f in sorted(self.calls.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
        return out


class Caller:
    """One scripted caller on the scratch stack."""

    def __init__(self) -> None:
        self.room = rtc.Room()
        self.grant: dict = {}
        self.frames: asyncio.Queue[rtc.AudioFrame] = asyncio.Queue()
        self.data: list[tuple[str, bytes]] = []
        self.answered = asyncio.Event()
        self.closed = asyncio.Event()

        @self.room.on("track_subscribed")
        def _on_track(track, pub, participant):
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            self.answered.set()

            async def pump():
                async for ev in rtc.AudioStream(track):
                    await self.frames.put(ev.frame)

            asyncio.ensure_future(pump())

        @self.room.on("data_received")
        def _on_data(packet):
            self.data.append((getattr(packet, "topic", "") or "",
                              bytes(packet.data)))

        @self.room.on("disconnected")
        def _on_gone(*_a):
            self.closed.set()

    def mint(self, voicemail: bool = False) -> int:
        """POST /token; returns the HTTP status. 200 stores the grant."""
        body = json.dumps({"voicemail": True} if voicemail else {}).encode()
        req = urllib.request.Request(f"{SERVER}/token", data=body,
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.grant = json.load(resp)
                return resp.status
        except urllib.error.HTTPError as e:
            self.grant = {"error": e.read().decode("utf-8", "replace")}
            return e.code

    async def connect(self) -> None:
        await self.room.connect(self.grant["url"], self.grant["token"])

    async def wait_loud(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            try:
                frame = await asyncio.wait_for(self.frames.get(), left)
            except asyncio.TimeoutError:
                return False
            if loudness(frame) > LOUD:
                return True

    async def wait_quiet(self) -> None:
        last_loud = time.monotonic()
        while time.monotonic() - last_loud < GREETING_GAP:
            try:
                frame = await asyncio.wait_for(self.frames.get(), GREETING_GAP)
            except asyncio.TimeoutError:
                return
            if loudness(frame) > LOUD:
                last_loud = time.monotonic()

    async def speak(self, wav: Path, ptt: bool = False) -> None:
        with wave.open(str(wav), "rb") as w:
            rate = w.getframerate()
        source = rtc.AudioSource(rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("caller-voice", source)
        await self.room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(
                source=rtc.TrackSource.SOURCE_MICROPHONE))
        await play_wav(source, str(wav))
        if ptt:
            # What releasing the talk bar sends: the explicit "your turn".
            await self.room.local_participant.publish_data(
                b"end", reliable=True, topic="wavetalk.turn-end")

    async def hangup(self) -> None:
        req = urllib.request.Request(
            f"{SERVER}/call-ended",
            data=json.dumps({"room": self.grant.get("room", "")}).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
        await self.room.disconnect()


# --------------------------------------------------------------------------
# The scenarios. Each returns a list of problems; empty means PASS.

async def s_no_answer(stack: Stack, wavs: dict) -> list[str]:
    """Worker down: the caller must simply never be answered (the widget
    plays the engaged tone at 40s; 15s is plenty to prove nobody came)."""
    stack.stop_worker()
    try:
        c = Caller()
        if c.mint() != 200:
            return [f"/token refused: {c.grant}"]
        await c.connect()
        answered = await asyncio.wait_for(
            _wait(c.answered, 15), timeout=20)
        # hangup(), not bare disconnect: the mint took a concurrency slot,
        # and a slot nobody releases sat in _live_calls for the rest of the
        # run — vm-fallback then found "the one line" already occupied by
        # this scenario's ghost.
        await c.hangup()
        return ["a track arrived with no worker running"] if answered else []
    finally:
        stack.start_worker()


async def s_flow(stack: Stack, wavs: dict) -> list[str]:
    """The baseline live call, end to end, with the record checked after."""
    stack.write_settings({})
    problems, timings = [], {}
    c = Caller()
    if c.mint() != 200:
        return [f"/token refused: {c.grant}"]
    t0 = time.monotonic()
    await c.connect()
    if not await _wait(c.answered, 40):
        return ["never answered"]
    timings["answered"] = time.monotonic() - t0
    if not await c.wait_loud(30):
        return ["answered but no greeting audio"]
    timings["greeting"] = time.monotonic() - t0
    await c.wait_quiet()
    await c.speak(wavs["ask"])
    t_spoke = time.monotonic()
    if not await c.wait_loud(30):
        problems.append("no reply to the caller's question")
    else:
        timings["turn"] = time.monotonic() - t_spoke
    await c.wait_quiet()
    await c.hangup()
    rec = await _await_record(stack, c)
    if not rec:
        problems.append("no call record written")
    else:
        if not any(t["who"] == "caller" for t in rec.get("turns", [])):
            problems.append("record has no caller turn — STT leg silent")
        if "hung up" not in str(rec.get("endedBecause", "")):
            problems.append(f"endedBecause={rec.get('endedBecause')!r}")
    print(f"    timings: {_fmt(timings)}")
    return problems


async def s_ptt(stack: Stack, wavs: dict) -> list[str]:
    """Push to talk: the bar-release commit must beat plain endpointing, or
    at least not lose to it. Reported, not thresholded — the number is the
    deliverable. No settings overlay: the talk bar is a widget-surface
    offering (show_push_to_talk); the worker's turn-end handler is always
    armed, so publishing the packet IS the bar release."""
    stack.write_settings({})
    c = Caller()
    if c.mint() != 200:
        return [f"/token refused: {c.grant}"]
    await c.connect()
    if not await _wait(c.answered, 40) or not await c.wait_loud(30):
        return ["no greeting under push to talk"]
    await c.wait_quiet()
    await c.speak(wavs["ask"], ptt=True)
    t_spoke = time.monotonic()
    ok = await c.wait_loud(30)
    turn = time.monotonic() - t_spoke
    await c.wait_quiet()
    await c.hangup()
    if not ok:
        return ["no reply after the turn-end commit"]
    print(f"    ptt turn latency: {turn:.2f}s")
    return []


async def s_tools(stack: Stack, wavs: dict) -> list[str]:
    """A read-only tool call: the DJ searches the library, the record logs
    the tool, and any action card the widget would render is captured."""
    stack.write_settings({"allow_library_search": True})
    c = Caller()
    if c.mint() != 200:
        return [f"/token refused: {c.grant}"]
    await c.connect()
    if not await _wait(c.answered, 40) or not await c.wait_loud(30):
        return ["no greeting"]
    await c.wait_quiet()
    await c.speak(wavs["tools"])
    if not await c.wait_loud(45):                # search + LLM + TTS
        return ["no reply to the library question"]
    await c.wait_quiet()
    cards = [json.loads(d) for t, d in c.data if t == "wavetalk.action"]
    await c.hangup()
    rec = await _await_record(stack, c)
    problems = []
    if not rec:
        problems.append("no call record written")
    elif not rec.get("tools"):
        problems.append("record shows no tool use for a library question")
    else:
        print("    tools in record:",
              ", ".join(t["name"] for t in rec["tools"]))
    print(f"    action cards seen in chat: {len(cards)}"
          + (f" ({cards[0].get('label')})" if cards else ""))
    return problems


async def s_idle(stack: Stack, wavs: dict) -> list[str]:
    """A silent caller gets checked on, not hung up on."""
    stack.write_settings({"idle_prompt_secs": 8})
    c = Caller()
    if c.mint() != 200:
        return [f"/token refused: {c.grant}"]
    await c.connect()
    if not await _wait(c.answered, 40) or not await c.wait_loud(30):
        return ["no greeting"]
    await c.wait_quiet()
    t0 = time.monotonic()
    checked = await c.wait_loud(30)              # say nothing; wait
    await c.hangup()
    if not checked:
        return ["idle_prompt_secs=8 but no check-in within 30s of silence"]
    print(f"    check-in after {time.monotonic() - t0:.1f}s of silence")
    return []


async def s_max_length(stack: Stack, wavs: dict) -> list[str]:
    """The call ceiling: the line must actually close, not just intend to."""
    stack.write_settings({"max_call_seconds": 45, "idle_prompt_secs": 0})
    c = Caller()
    if c.mint() != 200:
        return [f"/token refused: {c.grant}"]
    await c.connect()
    if not await _wait(c.answered, 40):
        return ["never answered"]
    closed = await _wait(c.closed, 45 + 40)      # ceiling + wrap-up grace
    await c.hangup()
    if not closed:
        return ["line still open 40s past a 45s ceiling"]
    rec = await _await_record(stack, c)
    print(f"    endedBecause: {rec.get('endedBecause') if rec else '(no record)'}")
    return []


async def s_call_only(stack: Stack, wavs: dict) -> list[str]:
    """Voicemail off: the machine must refuse while the live line works."""
    stack.write_settings({"voicemail_enabled": False})
    c = Caller()
    vm = c.mint(voicemail=True)
    live = Caller()
    live_status = live.mint()
    if live_status == 200:
        await live.hangup()                      # release the slot politely
    problems = []
    if vm != 403:
        problems.append(f"voicemail token got {vm}, wanted 403")
    if live_status != 200:
        problems.append(f"live token got {live_status}, wanted 200")
    return problems


async def s_vm_only(stack: Stack, wavs: dict) -> list[str]:
    """Voicemail-only line: live refused, the machine answers, beeps,
    takes the message, writes it to the archive, and hangs up by itself."""
    stack.write_settings({"voicemail_enabled": True, "voicemail_when": "always",
                          "live_calls_enabled": False})
    refused = Caller()
    problems = []
    if refused.mint() != 429:
        problems.append("live call was not refused on a voicemail-only line")
    c = Caller()
    if c.mint(voicemail=True) != 200:
        return problems + [f"voicemail token refused: {c.grant}"]
    await c.connect()
    if not await _wait(c.answered, 40):
        return problems + ["the machine never picked up"]
    # The beep cue is the machine saying "now I'm listening".
    for _ in range(120):
        if any(t == "vm-beep" for t, _d in c.data):
            break
        await asyncio.sleep(0.5)
    else:
        problems.append("no vm-beep cue within 60s")
    await c.speak(wavs["vm"])
    closed = await _wait(c.closed, 60)
    if not closed:
        problems.append("machine did not close the line after the message")
    await c.room.disconnect()
    rec = await _await_record(stack, c)
    if not rec:
        problems.append("no voicemail record in the archive")
    elif rec.get("kind") != "voicemail":
        problems.append(f"record kind={rec.get('kind')!r}, wanted voicemail")
    elif not any("harness" in t.get("text", "").lower()
                 for t in rec.get("turns", []) if t["who"] == "caller"):
        problems.append("the message text never reached the record")
    return problems


async def s_vm_fallback(stack: Stack, wavs: dict) -> list[str]:
    """Lines tied up: the exact refusal voicemail exists FOR must let the
    machine through while the live line says busy."""
    stack.write_settings({"voicemail_enabled": True, "voicemail_when": "closed",
                          "max_concurrent_calls": 1})
    first = Caller()
    if first.mint() != 200:
        return [f"could not occupy the one line: {first.grant}"]
    second = Caller()
    live = second.mint()
    vm = Caller()
    vm_status = vm.mint(voicemail=True)
    await first.hangup()
    problems = []
    if live != 429 or "tied up" not in str(second.grant):
        problems.append(f"second live call got {live} ({second.grant})")
    if vm_status != 200:
        problems.append(f"voicemail behind a busy line got {vm_status}, "
                        "wanted 200")
    return problems


SCENARIOS = {
    "no-answer": s_no_answer,
    "flow": s_flow,
    "ptt": s_ptt,
    "tools": s_tools,
    "idle": s_idle,
    "max-length": s_max_length,
    "call-only": s_call_only,
    "vm-only": s_vm_only,
    "vm-fallback": s_vm_fallback,
}


async def _wait(event: asyncio.Event, timeout: float) -> bool:
    try:
        await asyncio.wait_for(event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


def _record_for(stack: Stack, c: Caller) -> dict | None:
    suffix = (c.grant.get("room", "") or "")[-12:]
    for rec in stack.records():
        if suffix and str(rec.get("room", "")).endswith(suffix):
            return rec
    return None


async def _await_record(stack: Stack, c: Caller, timeout: float = 45) -> dict | None:
    """The record is written in the worker's shutdown callback, BEHIND the
    back-to-air handoff and its LLM budget — a fixed short sleep raced it
    and lost. Poll until it lands or the budget is spent."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = _record_for(stack, c)
        if rec:
            return rec
        await asyncio.sleep(1)
    return None


def _fmt(d: dict) -> str:
    return "  ".join(f"{k}={v:.2f}s" for k, v in d.items())


async def main() -> int:
    names = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--list" in sys.argv:
        print("\n".join(SCENARIOS))
        return 0
    if not names:
        names = list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        sys.exit(f"unknown scenario(s): {unknown} — try --list")

    scratch = Path(tempfile.mkdtemp(prefix="wavetalk-scenarios-"))
    wavs = {}
    for key, line in LINES.items():
        wavs[key] = scratch / f"{key}.wav"
        say_to_wav(line, wavs[key])

    stack = Stack(scratch)
    failures = 0
    print(f"scratch: {scratch}")
    try:
        stack.start()
        for name in names:
            print(f"\n== {name}")
            try:
                problems = await SCENARIOS[name](stack, wavs)
            except Exception as e:                            # noqa: BLE001
                problems = [f"crashed: {e!r}"]
            if problems:
                failures += 1
                for p in problems:
                    print(f"    FAIL: {p}")
            else:
                print("    PASS")
    finally:
        stack.stop()
        # A failed run keeps its scratch — the worker log in there is the
        # diagnosis, and deleting it made every failure a rerun.
        if not failures:
            shutil.rmtree(scratch, ignore_errors=True)
        else:
            print(f"\nkept for diagnosis: {scratch}")
    print(f"\n{len(names) - failures}/{len(names)} scenarios passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
