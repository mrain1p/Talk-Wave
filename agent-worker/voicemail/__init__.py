"""Voicemail: the second, much smaller kind of call.

Greeting, beep, one caller utterance through the STT that already runs, and
the transcript — never the audio — is the message. docs/VOICEMAIL.md is the
design; the operator's additions (its own settings section, greeting clips
cached against what they were rendered from, the offer appearing exactly
where a live call is refused) are all in it now.

Deliberately NOT inside call/session.py: a voicemail has no agent, no tools,
no OnAirGuard and no idle ladder, and threading a mode flag through the call
object would put a branch in every one of those.
"""
