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
    iframe.style.height = height || (compact ? "190px" : "420px");
    // Square. The rounded frame was the outermost part of the "pasted-on
    // card" look — the host page has no rounded corners anywhere.
    iframe.style.borderRadius = "0";
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

    // Unless the host pinned a height, follow the widget's own. A fixed
    // height is a guess made before the widget knows whether it has to ask
    // for a door code, show a microphone warning, or open captions — and the
    // guess clips all three.
    if (!height) {
      window.addEventListener("message", function (e) {
        if (e.source !== iframe.contentWindow) return;
        var msg = e.data;
        if (!msg || msg.type !== "subwave-callin:height") return;
        var px = Number(msg.px);
        // Bounded on purpose: a frame must never be able to take over the
        // host page's layout.
        if (px > 80 && px < 2000) iframe.style.height = px + "px";
      });
    }
  });
})();
