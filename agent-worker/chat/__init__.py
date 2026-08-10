"""The station's text line: typed conversations with whoever is on air.

The third mode beside live calls and voicemail, and deliberately the most
transport-boring of the three — no LiveKit, no room, no worker job. A chat is
a WebSocket on the token server driving the same brain, the same tool
wrappers and the same record archive the phone uses. That is a feature twice
over: it works for callers whose networks block WebRTC media, and it keeps
working when the media server is down.
"""
