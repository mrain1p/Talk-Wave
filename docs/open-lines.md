# Open Lines

[← back to the README](../README.md)

Everything in [Live on air](on-air.md) is about a listener reaching the booth. This is the booth reaching out: the DJ puts a subject to the audience on the broadcast, and then knows what it asked when somebody arrives — on the phone, on the text line, or on the machine.

**Off by default, and manual by default.** Turning **Open Lines** on (panel → Open Lines) does nothing on its own. A line opens when you press **Open a line now**, or on a cadence you set with *Open one automatically every*, which ships at 0.

**Where the subject comes from.** Either the DJ invents one — from the same material a station segment invents from: who is on air, the show card, tonight's episode, what has just played and what it has said — or it reads **your own list**, one topic per line, in the order you wrote them. Write the subject, not the words: the DJ says it in its own voice.

**What actually airs.** Talk Wave hands the station a *direction* through `/dj/say` in styled mode, and the station writes it in whoever is live. Nothing is stored on the station and no station configuration is touched — this is an action, like every other thing the phone-in can do. What comes back is the sentence that aired, and that is what gets pinned, not the direction that was sent. It matters: with an invented subject the specifics only exist in what the DJ actually said, and a DJ reminded of the instruction instead would invent a second, contradictory version of its own topic.

**When somebody arrives.** The DJ opens by finding out which it is — one light question, near the start, in its own voice. If they came for the topic it takes them seriously and pushes back where it disagrees. **If they did not, it drops the subject completely and never raises it again.** Someone who wants a record played is not a failed contribution.

**How it ends.** After **How long a line stays open** the DJ closes it on air, in character — including when nobody took it up, which is an ordinary thing to happen on radio and sounds like one. **Remind every** raises the subject again mid-window and **Most reminders per topic** is the ceiling that protects the broadcast: a long window with a short interval is how a station ends up asking the same question nine times.

**The two gates.** **Only with at least this many listeners** is checked when a line opens and before each reminder, never in the middle — a topic that vanished because somebody closed a tab would strand whoever was already typing. **Only these DJs** limits it by persona name; not every DJ on a station should be soliciting arguments.

**The address.** **Where to reach you, said on air** is read out with the invitation, exactly as you write it, so write it the way it should sound. Leave it blank when your audience is already looking at the card — a spoken address is for people hearing the stream somewhere else. Because Talk Wave supplies it at compose time, what the DJ reads out is always where Talk Wave actually answers.

An open line belongs to the DJ and the show that opened it, and dies when either changes: the next persona would otherwise be defending an argument it never made.
