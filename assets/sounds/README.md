# Bundled call sounds

Drop audio files here and they ship inside the image. This is the middle tier
of three:

```
operator upload / URL   →   bundled asset (here)   →   synthesized in the browser
```

Nothing here has to exist. With this folder empty the widget synthesizes every
sound, which is how Talk Wave has always worked and is worth keeping — a
deployment with no audio files still rings, picks up and hangs up.

## Adding a pack

A pack is a folder. Its name is the value stored in `sound_pack`, and it
appears in **Call sounds → Sound set** in the panel automatically.

```
assets/sounds/
  vintage/
    label.txt      optional: "Vintage — 1950s exchange"
    ring.mp3
    pickup.mp3
    hold.mp3
    hangup.mp3
    failed.mp3
```

Without `label.txt` the dropdown shows the folder name, tidied — `vintage`
becomes "Vintage", `old-bell` becomes "Old Bell".

**A pack can be partial.** Any sound the folder doesn't provide falls back to
the synthesized one for that pack, so a folder containing only `ring.mp3` is a
perfectly good pack.

## The five sounds

| File | When it plays |
|---|---|
| `ring` | While the call is connecting, repeating until answered |
| `pickup` | The DJ answers |
| `hold` | The DJ steps onto the broadcast mid-call |
| `hangup` | The call ends |
| `failed` | Engaged tone — line busy, limit reached, or couldn't connect |

Extensions are tried in this order: `.mp3`, `.m4a`, `.aac`, `.ogg`, `.wav`,
`.webm`. Prefer **mp3** — it is the one format every browser plays.

Keep them short and quiet. These play over a live phone call, and anything
long or loud competes with the DJ. A second or two is plenty.

## The two built-in names

`classic` and `phone` are the synthesized packs ("Exchange" and "Handset").
A folder with one of those names doesn't create a new pack — it supplies files
for that one, sound by sound. So `assets/sounds/classic/ring.mp3` replaces the
synthesized ring for Exchange and leaves its other four alone.

## Operator uploads are separate

Uploads through the panel go to `data/sounds/` and override everything here —
they're per-deployment, these are per-build.

## Where the library clips came from

Every clip in `library/` is either synthesized by `tools/make_library_sounds.py` (Modern, Rotary, the sad trombone — pure maths, no provenance to track) or a public-domain/CC0 recording from Wikimedia Commons, converted to mono 16-bit WAV. The recorded ones carry a `source` field in `catalog.json` naming the exact Commons file and its licence — if a clip ever needs defending, that field is the receipt. Nothing here requires attribution, but the receipts stay anyway.
