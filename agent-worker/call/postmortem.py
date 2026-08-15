"""What the record learns once the call is over.

Six checks, and every one exists because a real call went wrong in a way that
left NO trace anywhere else — no exception, no log line, nothing an operator
could point at. A caller whose audio never arrived; a voice that started on
time and fell further behind every sentence; a model that took the whole budget
to answer; the DJ showing the caller the door; two of its own turns racing; an
ask that went nowhere. Each is invisible from inside the call and obvious in
one line of the transcript afterwards.

Split from session.py at 0.10.147, when it crossed the length ceiling. The seam
is clean and one-way: everything here READS a finished call and writes into its
record, and none of it is consulted while the call is running.
"""

from __future__ import annotations

import logging

from log_setup import describe

log = logging.getLogger("callin.agent")


def write_notes(call, duration: float, final: list) -> None:
    """Every end-of-call check, in the order an operator would want them.

    The caller-side facts first (was anyone there, did they ask for something),
    then the machinery (was it slow, did it trip over itself) — a record read
    top to bottom should say what happened before it says how well it ran.
    """
    _note_if_the_door_was_held_open(call)
    _note_if_two_turns_wanted_the_floor(call)
    _note_if_an_ask_went_unanswered(call)
    _note_if_nothing_was_heard(call, duration, final)
    _note_if_the_model_kept_the_caller_waiting(call)
    _note_if_the_voice_fell_behind(call)


def _note_if_the_door_was_held_open(call) -> None:
    """Say so, in the record, when the DJ had to be steered off the door.

    The correction itself is silent to the caller and to the operator, and a
    silent fix is one nobody can tell is load-bearing — or failing. A call
    that needed telling once is the mechanism working; a call that needed
    telling three times is the prompt still pulling the other way, and that
    is the number the next prompt cut should be argued from.
    """
    if not call.record or not call.door.corrections:
        return
    n = call.door.corrections
    call.record.problem(
        f"The DJ ended {n} turn{'s' if n != 1 else ''} by asking whether the "
        "caller wanted anything else, before they had said they were "
        "finished — the line was steered off it on the following turn each "
        "time. One is ordinary. Several means the closing rules in the "
        "prompt are not landing on this model."
    )

def _note_if_two_turns_wanted_the_floor(call) -> None:
    """Say so when two of the DJ's own turns tried to start at once.

    Recorded rather than acted on: the lock already stops the overlap,
    and the NUMBER is what says whether this narrow guard was worth
    having. If a month of calls records none, it can go.
    """
    if not call.record or not call.floor.collisions:
        return
    call.record.problem(
        f"Two of the DJ's own turns wanted to start at once "
        f"{call.floor.collisions} time(s)"
        + (f", and {call.floor.given_up} gave up waiting"
           if call.floor.given_up else "")
        + ". They were serialised rather than spoken over each other. "
        "Expected to be rare; several on one call means two behaviours "
        "are firing on the same cue."
    )

def _note_if_an_ask_went_unanswered(call) -> None:
    """Say so when the caller asked for something and nothing ever ran.

    The one failure this whole stream was created to investigate, and until
    now it could not be seen in a record at all — there are turns, there are
    tools, and nothing joins them. Detection only: the DJ is never told and
    no turn is generated. If the archive fills with these, a director has a
    case; if it does not, the turn-by-turn shape is fine.
    """
    if not call.record:
        return
    dropped = call.asks.unanswered(call.actions.taken_at)
    if not dropped:
        return
    call.record.problem(
        f"The caller asked for something that needed a tool and no action "
        f"was recorded afterwards ({len(dropped)}): "
        + "; ".join(f'"{d}"' for d in dropped[:3])
        + ". Either the DJ never acted, or it was refused and nothing "
        "landed — the transcript says which. This is the shape a request "
        "takes when it gets dropped mid-call."
    )

def _note_if_nothing_was_heard(call, duration: float, final: list) -> None:
    """Say so, in the record, when a call produced no caller audio.

    An off-LAN caller whose media path never establishes looks exactly like
    a healthy call from in here: the room is joined, the agent starts, the
    greeting plays, and then the line drops around fifteen seconds later
    with nothing received. That is what the first outside caller hit, and
    NOTHING in our own logs said so — the failure was only visible in
    LiveKit's ICE candidates and the caller's browser console.

    This can't distinguish a broken media path from a silent caller, so it
    doesn't pretend to. It records the shape and names the candidates in
    order of likelihood, which is what a future investigation needs.
    """
    if call.heard["n"] or not call.record:
        return

    dj_spoke = any(who == "dj" and text.strip() for who, text in final)
    log.warning(
        "no caller audio received room=%s duration=%.0fs dj_spoke=%s — "
        "media path, blocked microphone, or a silent caller",
        call.ctx.room.name, duration, dj_spoke,
    )
    call.record.problem(
        f"No audio was ever received from the caller ({duration:.0f}s on the "
        f"line, the DJ {'did' if dj_spoke else 'did not'} speak). Three "
        "things look like this from the booth: the caller was off-LAN and "
        "the media path never established, their microphone was blocked, "
        "or they genuinely said nothing. If they reported \"Could not "
        "connect\" after about fifteen seconds of ringing, it is the first "
        "— see off-LAN calling in the README."
    )

def _note_if_the_voice_fell_behind(call) -> None:
    """Say so, in the record, when the TTS could not keep up with playback.

    This is the failure that has no symptom from in here. Time to first
    audio was measured at a healthy 1.5s while the same backend ran at
    1.6-2.3x realtime, so the DJ started speaking on cue and then fell
    further behind with every sentence — audible to the caller as gaps and
    drag, invisible in the transcript, and nothing anywhere errored. The
    operator could only report that calls "felt laggy", which is not
    something anyone can act on.
    """
    tts = getattr(call.session, "tts", None)
    report = getattr(tts, "pace_report", None)
    if not call.record or not callable(report):
        return
    try:
        said = report()
    except Exception as e:                                # noqa: BLE001
        log.debug("could not read the TTS pace (harmless): %s", describe(e))
        return
    if said:
        log.warning("%s", said)
        call.record.problem(said)

def _note_if_the_model_kept_the_caller_waiting(call) -> None:
    """Say so, in the record, when the caller waited on the model.

    The companion to the check above, one leg earlier. Both exist because
    the same class of failure — everything works, slowly enough to ruin the
    call — leaves no trace anywhere else: no exception, no line in the
    transcript, nothing an operator can point at. See llm_pace.
    """
    if not call.record:
        return
    said = call.think.report()
    if said:
        log.warning("%s", said)
        call.record.problem(said)
