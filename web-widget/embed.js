/**
 * Drop-in embed for the SUB/WAVE call-in widget.
 *
 *   <div id="subwave-callin"></div>
 *   <script src="http://localhost:8100/embed.js"></script>
 *
 * Renders an <iframe> pointing at the call page. Whoever is live on air
 * answers — the host page doesn't choose a persona.
 *
 * Origin is derived from this script's own src, so the same file works in
 * local dev and behind a real domain with no edit. Override per-element with
 * data-origin if you're serving the script from a different host than the
 * widget.
 *
 * The allow="microphone" attribute below is load-bearing: without it
 * getUserMedia fails silently inside the frame and the call button just
 * spins. If you also serve this cross-origin, the widget page must not send
 * X-Frame-Options: DENY or a frame-ancestors CSP that excludes the host.
 */
(function () {
  var self = document.currentScript;
  var DEFAULT_ORIGIN = self ? new URL(self.src).origin : "http://localhost:8100";

  /**
   * Which theme does the host page look like? Walk up from the mount point
   * for the first element with a real (non-transparent) background and judge
   * its brightness; fall back to the page's declared color-scheme, then to
   * the viewer's OS preference. Returns "light" or "dark".
   */
  function hostTheme(el) {
    for (var node = el; node && node.nodeType === 1; node = node.parentElement) {
      var bg = getComputedStyle(node).backgroundColor || "";
      var m = bg.match(/rgba?\(([^)]+)\)/);
      if (!m) continue;
      var parts = m[1].split(",").map(parseFloat);
      if (parts.length > 3 && parts[3] === 0) continue;   // transparent
      // Rec. 601 luma: good enough to tell a dark page from a light one.
      var luma = (0.299 * parts[0] + 0.587 * parts[1] + 0.114 * parts[2]) / 255;
      return luma < 0.5 ? "dark" : "light";
    }
    var declared = getComputedStyle(document.documentElement).colorScheme || "";
    if (/\bdark\b/.test(declared) && !/\blight\b/.test(declared)) return "dark";
    if (/\blight\b/.test(declared) && !/\bdark\b/.test(declared)) return "light";
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  document.querySelectorAll("[id^='subwave-callin']").forEach(function (el) {
    if (el.dataset.callinMounted) return;
    el.dataset.callinMounted = "1";

    var origin = el.getAttribute("data-origin") || DEFAULT_ORIGIN;
    var compact = el.getAttribute("data-compact") !== "false";
    // data-theme:
    //   "light" / "dark"  force it, and hide the widget's own toggle
    //   "inherit"         match the page this is embedded on
    //   omitted           auto — the viewer's OS preference, toggle available
    //
    // "inherit" is resolved HERE rather than in the frame because a
    // cross-origin iframe can't see the host page's styling at all. We read
    // the host's own background and decide light or dark from it, which is
    // what "matches the page" actually means to whoever dropped this in.
    var theme = el.getAttribute("data-theme") || "";
    if (theme === "inherit") theme = hostTheme(el);
    // data-captions="ticker"|"full"|"off". Embeds default to the ticker —
    // only the latest spoken line, fading after a few seconds — so the
    // widget stays short wherever it's dropped.
    var captions = el.getAttribute("data-captions") || "";
    // data-height="260px" overrides the frame height for tight layouts.
    var height = el.getAttribute("data-height") || "";

    var iframe = document.createElement("iframe");
    iframe.src = origin + "/?compact=" + (compact ? "1" : "0")
      + (theme ? "&theme=" + encodeURIComponent(theme) : "")
      + (captions ? "&captions=" + encodeURIComponent(captions) : "");
    iframe.setAttribute("allow", "microphone");
    iframe.setAttribute("title", "Call the SUB/WAVE DJ");
    iframe.style.border = "none";
    iframe.style.width = "100%";
    // An iframe is inline by default, which leaves a few px of baseline gap
    // under it — so the container measures taller than the frame it holds.
    // That difference is invisible until something reads one and writes the
    // other, and then it compounds: the overlay was restoring the frame to
    // its CONTAINER's height, so every open-and-close of the ask list left
    // the widget 3px taller than it started.
    iframe.style.display = "block";
    iframe.style.height = height || (compact ? "190px" : "420px");
    // The widget's own card carries the radius and the frame is transparent
    // around it, so this is belt and braces: it only shows if a host or a
    // station palette ends up painting the frame's background.
    iframe.style.borderRadius = "16px";
    if (theme) iframe.style.colorScheme = theme;

    el.appendChild(iframe);

    // Station theming. The host page calls this when the on-air show changes,
    // passing the same token map it dressed itself in:
    //
    //   document.getElementById("subwave-callin")
    //     .setCallinTheme({ "--pine": "#1b1a2e", "--coral": "#ff7a5c" });
    //
    // The widget repaints in place. Do NOT reload the frame to change a
    // theme — a reload drops whatever call is in progress.
    el.setCallinTheme = function (tokens) {
      if (!tokens || !iframe.contentWindow) return;
      iframe.contentWindow.postMessage({ type: "swtv:theme", tokens: tokens }, origin);
    };

    // "What can I ask?" opens a list that is routinely taller than the whole
    // frame, and a popup clipped by its own iframe is worse than no popup. So
    // for as long as it is open the frame stops being a box in the layout and
    // becomes an overlay: absolutely positioned, taller, above the page, with
    // the container holding the old height so nothing on the host page moves.
    //
    // Direction is decided HERE, not in the widget. Only this side can see
    // the page: the widget asking to open downwards has no idea whether
    // downwards is the bottom of the viewport.
    // The frame's height, and the slot it leaves in the host page. Kept
    // apart deliberately: restoring the frame to the slot's height is the
    // ratchet described above.
    var baseHeight = 0, baseSlot = 0;

    function endOverlay() {
      if (!baseHeight) return;
      el.style.minHeight = "";
      iframe.style.position = "";
      iframe.style.top = "";
      iframe.style.bottom = "";
      iframe.style.left = "";
      iframe.style.zIndex = "";
      iframe.style.height = baseHeight + "px";
      baseHeight = baseSlot = 0;
      // Tell the widget the frame is a box again, so it can let its card
      // settle back to the top and start reporting its height once more.
      iframe.contentWindow.postMessage(
        { type: "swtv:overlay", px: 0, up: false }, origin);
    }

    function beginOverlay(wanted) {
      var box = el.getBoundingClientRect();
      var EDGE = 12;
      var below = window.innerHeight - box.bottom - EDGE;
      var above = box.top - EDGE;
      // Down is the default and only loses it when down genuinely cannot
      // hold the list and up can hold more of it.
      var up = below < wanted && above > below;
      var granted = Math.max(120, Math.min(wanted, up ? above : below));

      if (!baseHeight) {
        baseHeight = Math.round(iframe.getBoundingClientRect().height);
        baseSlot = Math.round(box.height);
      }
      // The container carries the frame's old height so the host page's
      // layout does not shift under the reader while they open a menu.
      if (getComputedStyle(el).position === "static") el.style.position = "relative";
      el.style.minHeight = baseSlot + "px";
      iframe.style.position = "absolute";
      iframe.style.left = "0";
      iframe.style.width = "100%";
      iframe.style.zIndex = "2147483000";
      iframe.style.top = up ? "auto" : "0";
      iframe.style.bottom = up ? "0" : "auto";
      iframe.style.height = (baseHeight + granted) + "px";
      iframe.contentWindow.postMessage(
        { type: "swtv:overlay", px: granted, up: up }, origin);
    }

    window.addEventListener("message", function (e) {
      if (e.source !== iframe.contentWindow) return;
      var msg = e.data;
      if (!msg) return;

      if (msg.type === "subwave-callin:overlay") {
        var wanted = Number(msg.px) || 0;
        if (wanted > 0) beginOverlay(Math.min(wanted, 1400));
        else endOverlay();
        return;
      }

      // Unless the host pinned a height, follow the widget's own. A fixed
      // height is a guess made before the widget knows whether it has to ask
      // for a door code, show a microphone warning, or open captions — and
      // the guess clips all three. Ignored while overlaid: the frame is
      // deliberately the wrong size just now.
      if (msg.type === "subwave-callin:height" && !height && !baseHeight) {
        var px = Number(msg.px);
        // Bounded on purpose: a frame must never be able to take over the
        // host page's layout.
        if (px > 80 && px < 2000) iframe.style.height = px + "px";
      }
    });
  });
})();
