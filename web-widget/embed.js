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

  document.querySelectorAll("[id^='subwave-callin']").forEach(function (el) {
    if (el.dataset.callinMounted) return;
    el.dataset.callinMounted = "1";

    var origin = el.getAttribute("data-origin") || DEFAULT_ORIGIN;
    var compact = el.getAttribute("data-compact") !== "false";
    // data-theme="light"|"dark" forces a theme to match the host page and
    // hides the widget's own toggle. Omit it for auto: the viewer's OS
    // preference, with the in-widget toggle available.
    var theme = el.getAttribute("data-theme") || "";
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
    iframe.style.borderRadius = "14px";
    if (theme) iframe.style.colorScheme = theme;

    el.appendChild(iframe);
  });
})();
