# Feature request: make banter something you can aim — a skill, or a line you feed it

**Repo:** perminder-klair/subwave
**Filed from:** Talk Wave (call-in sidecar), where the gap showed up

## Where this came from

A caller asked the call-in DJ for "a banter break about Lt. Surge". The DJ had
`dj_segment` available and deliberately did not use it, because nothing in the
banter path can carry a subject:

```
POST /dj/segment { type: 'banter' }
  -> runBanter()                       // takes no arguments
     -> settings.getOnAirRoster()
     -> getFullContext()
     -> dj.generateBanter({ host, guests, show, current, context, recap, recentOpeners })
     -> queue.announceExchange(lines, 'banter')
```

Firing it would have produced a good exchange about something else, so the DJ
wrote the material itself and sent it through `/dj/say` — which airs as one
voice. The content was right; it just could not become a conversation.

Banter is the best thing the booth does and it is currently the one thing
nothing can point at. Three asks, smallest first.

## 1. Announcements can be banterable

`/dj/say` already picks how the text is treated:

```
POST /dj/say { text, kind?, mode?: 'raw' | 'styled', sfx? }
  raw    -> the DJ speaks `text` verbatim
  styled -> `text` is an instruction; the LLM writes it in persona, then speaks
```

Add a third mode:

```
  banter -> `text` is the SUBJECT; the on-air cast talks about it as an exchange
```

Internally that is the existing banter path with the text as its topic —
`generateBanter({ ..., topic: text })` then `queue.announceExchange(lines, 'banter')`
instead of a single rendered voice. Falls back to `styled` when the show has no
guests, so a caller's line is never unairable.

This is the one that fixes the case above, and it is the most useful single
change for any companion app: anything fed to the booth can arrive as a
conversation rather than an announcement.

## 2. Skills can be marked banterable, with a wheel for how often

A lot of skills are better as an exchange than as one voice reading a result —
the co-hosts reacting to the weather, arguing about the news item, riffing on
the album anniversary. But not *every* time, or the format wears out.

So: per-skill, two properties in `SKILL.md` frontmatter beside the existing
`label` / `cooldown` / `context` / `tags` / `cronOnly`:

```yaml
banter: true      # this skill CAN air as an exchange
banterChance: 35  # ...and does, roughly this often (0-100)
```

`banterChance` is the wheel — a dial in the skill editor next to the cooldown.
At 0 it never banters (same as today), at 100 it always does when guests are on,
and in between the booth mixes it up on its own so the show does not settle into
one rhythm. When a skill is due and the wheel comes up banter, its generated
content becomes the topic for `generateBanter` instead of a solo delivery;
otherwise it airs exactly as it does now.

Guest-less shows always fall back to solo, so turning this on can never make a
skill unairable — it just does nothing until there are co-hosts.

## 3. Invoke it directly

Both of the above should be callable on demand, not only scheduled:

```
POST /dj/segment { type: 'banter', topic?: string }   # free-form subject
POST /dj/segment { type: 'banter', skill?: string }   # run the skill, banter its content
```

An explicit press ignores `banterChance` and fires the exchange, the same way
every manual trigger already bypasses `shouldFire` and the skill cooldowns. That
gives an operator (and a companion app, through the allowlisted MCP surface) a
way to say "talk about this, now".

## Shape of the change

All three land on the same primitive — `generateBanter` gaining an optional
`topic`, and `runBanter` gaining arguments to pass it. Scheduled banter with no
topic is completely unchanged, so nothing existing moves.

Happy to send a PR for #1 and #3 if useful — they are small and additive. #2 is
more yours, since it touches the skill schema and the editor UI.
