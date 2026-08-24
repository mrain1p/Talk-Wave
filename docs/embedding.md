# Embedding

The call card on your own page.

[← back to the README](../README.md)

---

The two-line version:

```html
<div id="subwave-callin"></div>
<script src="https://your-host/embed.js"></script>
```

Renders the compact card in an iframe with `allow="microphone"` set (its absence is the classic silent embed failure). The panel never ships in an embed.

**What the card shows in an embed is answered separately** from what it shows on the standalone page — every element block on the panel's **Players** page carries a Page column and an Embed column. The host page usually has its own show heading and now-playing line, and a second copy of both inside the frame is noise.

The settings gear is the one thing never offered here at any setting: an embed does not load the panel's code, so it would open nothing.

| Attribute | Effect |
|---|---|
| `data-theme="light\|dark\|inherit"` | The widget *starts* on this theme — `inherit` matches the host page's background (resolved before the frame loads, since a cross-origin frame can't see its parent) — but the viewer's toggle still works and their choice is remembered. The toggle cycles light → dark → the station's show colours (when the panel has them on offer) → match the page. Omit for OS preference |
| `data-lock-theme="true"` | Pin `data-theme` outright and remove the toggle, for a page that needs one look |
| `data-captions="ticker\|full\|off"` | Embeds default to `ticker` — latest line only, fading, so the widget stays short |
| `data-height="260px"` | Frame height for tight layouts |
| `data-compact="false"` | Full card instead of the compact one |
| `data-origin` | Widget origin when the script is served from elsewhere |
| `data-mode="launcher\|dock\|button"` | An off-the-shelf shape that *opens on a press* instead of sitting inline: `launcher` is a floating call pill in a page corner, `dock` a slim bar pinned across the bottom, `button` an inline button in the page flow that opens the card in a centred pop-up. All three name who answers (or say the line is closed) before they are pressed, and collapsing one never hangs up a call in progress. Pick one — and preview it — in the panel's **Embed** section |
| `data-position="left"` | Puts the launcher pill in the left corner (right is the default) |

**The station's own colours** are not an embed attribute. Set **Players → Surface → Colours → "The station's own colours"** in the panel, and every surface — embeds included — wears the on-air show's palette live from the station's `/themes`. A host's `data-theme` is only the starting point, so it does not block this.

A host page can also push its own palette *and fonts* into the card over `postMessage` — see `web-widget/HOST-STYLE-GUIDE.md` — which repaints in place without dropping a call.

> **Any page you embed on can mint call tokens**, so treat an embed as publishing the phone. Set a guest code if that isn't what you want.
