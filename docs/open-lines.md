# Open Lines

[← back to the README](../README.md)

Everything in [Live on air](on-air.md) is about a listener reaching the booth. This is the booth reaching out: the DJ puts a subject to the audience on the broadcast, and then knows what it asked when somebody arrives — on the phone, on the text line, or on the machine.

**Off by default, and manual by default.** Turning **Open Lines** on (panel → Open Lines) does nothing on its own. A line opens when you press **Open a line now**, or on a cadence you set with *Open one automatically every*, which ships at 0.

The same two presses sit on the **dashboard**, under Transmission: *Make one up* and *Off the shelf* while nothing is up, *Close it* while a line stands. Which kind of subject to put up is a decision per topic, so it does not need a trip to the settings page.

**Where the subject comes from.** Either the DJ invents one — from the same material a station segment invents from: who is on air, the show card, tonight's episode, what has just played and what it has said — or it takes one **off your shelf**.

**The shelf** is your own list of subjects, each aimed at whichever DJs suit it. Add with **+ Add**, then pick the DJs on that row; leave none picked and it is open to all of them. That aim is the point of the shelf: an argument that lands in one persona's mouth is wrong in another's, and a single shared list made the DJ allowlist do a job it could not do. The **least recently used** one goes up next, so a subject you have just typed is the next one out.

Write the SUBJECT, not the words — the DJ says it in its own voice. A line here should read like a note to a presenter, not a script.

**What actually airs.** Talk Wave hands the station a *direction* through `/dj/say` in styled mode, and the station writes it in whoever is live. Nothing is stored on the station and no station configuration is touched — this is an action, like every other thing the phone-in can do. What comes back is the sentence that aired, and that is what gets pinned, not the direction that was sent. It matters: with an invented subject the specifics only exist in what the DJ actually said, and a DJ reminded of the instruction instead would invent a second, contradictory version of its own topic.

**When somebody arrives.** The DJ opens by finding out which it is — one light question, near the start, in its own voice. If they came for the topic it takes them seriously and pushes back where it disagrees. **If they did not, it drops the subject completely and never raises it again.** Someone who wants a record played is not a failed contribution.

**Reporting back.** With **Report back on air when somebody answers** on, the DJ returns to the broadcast after a conversation about the topic ends and tells the room what came of it. Without this the loop is open at one end: a listener hears a question, somebody answers it in a private conversation, and nobody listening ever learns the question was real or that anyone replied — so nobody else joins in.

What airs is the **position**, not the person and not their words:

- never a name or a handle, and the DJ is told not to invent one — handed *"someone argued X"*, a model will cheerfully attribute it to a caller named Dave from Fresno, which is a real person being given words and a hometown they never offered;
- never a quotation — the DJ says what was argued, in its own voice;
- nothing personal that came up on the way past;
- and if the conversation was not actually about the subject, **nothing airs at all**. Most conversations while a line is open are requests, and a DJ announcing *"someone asked for a Beatles record"* as though it were a contribution is worse than silence.

It fires only once a conversation has **ended** — a record on disk is a finished conversation — so the DJ is never reporting on somebody who is still typing. One per pass, so two arriving close together reach the room a minute apart as two moments rather than stacked into one breath. At most three per topic, which is not a setting: the toggle is the decision, and a cap an operator can raise is a cap that ends up raised.

**It never outlasts the programme.** The station publishes its week as an hour grid, so Talk Wave knows when the DJ currently on air gives way to the next one, and a line is cut to that boundary. This is honesty rather than new behaviour: an open line already died when the show changed, because a premise opened by one DJ must not survive into another's show — it just used to claim otherwise to everyone looking at the countdown. In the last few minutes of a show nothing is shortened at all, since cutting a line that fine would air its invitation and its sign-off back to back.

**How it ends.** After **How long a line stays open** the DJ closes it on air, in character — including when nobody took it up, which is an ordinary thing to happen on radio and sounds like one. **Remind every** raises the subject again mid-window and **Most reminders per topic** is the ceiling that protects the broadcast: a long window with a short interval is how a station ends up asking the same question nine times.

**The two gates.** **Only with at least this many listeners** is checked when a line opens and before each reminder, never in the middle — a topic that vanished because somebody closed a tab would strand whoever was already typing. **Only these DJs** limits it by persona name; not every DJ on a station should be soliciting arguments.

**The address.** **Where to reach you, said on air** is read out with the invitation, exactly as you write it, so write it the way it should sound. Leave it blank when your audience is already looking at the card — a spoken address is for people hearing the stream somewhere else. Because Talk Wave supplies it at compose time, what the DJ reads out is always where Talk Wave actually answers.

An open line belongs to the DJ and the show that opened it, and dies when either changes: the next persona would otherwise be defending an argument it never made.
