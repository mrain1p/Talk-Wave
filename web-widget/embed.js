/**
 * Drop-in embed for the SUB/WAVE call-in widget.
 *
 *   <div id="subwave-callin"></div>
 *   <script src="http://localhost:8100/embed.js"></script>
 *
 * Renders an <iframe> pointing at the call page. Whoever is live on air
 * answers — the host page doesn't choose a persona.
 *
 * data-mode="launcher" renders a floating pill in the page corner instead
 * (data-position="left" for the other side); pressing it opens the widget
 * in a fixed panel, support-chat style. The pill reads the line's state, so
 * it says who answers — or that the line is closed — before it is pressed.
 * Collapsing the panel does NOT hang up a call in progress.
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

  /**
   * The frame both modes share. One factory, because the microphone allow
   * attribute and the transparent backdrop are load-bearing and were about
   * to exist in two places.
   */
  function makeIframe(origin, compact, theme, lockTheme, captions) {
    var iframe = document.createElement("iframe");
    iframe.src = origin + "/?compact=" + (compact ? "1" : "0")
      + (theme ? (lockTheme ? "&theme=" : "&themeDefault=")
                 + encodeURIComponent(theme) : "")
      + (captions ? "&captions=" + encodeURIComponent(captions) : "");
    iframe.setAttribute("allow", "microphone");
    iframe.setAttribute("title", "Call the SUB/WAVE DJ");
    iframe.style.border = "none";
    iframe.setAttribute("allowtransparency", "true");
    iframe.style.background = "transparent";
    iframe.style.width = "100%";
    iframe.style.display = "block";
    iframe.style.borderRadius = "16px";
    if (theme) iframe.style.colorScheme = theme;
    return iframe;
  }

  /**
   * data-mode="launcher": a floating pill in the page corner — the shape a
   * support-chat bubble taught everyone — that opens the widget in a fixed
   * panel above it. The frame is created on FIRST open (a host page should
   * not pay for a widget nobody pressed) and never torn down after:
   * collapsing hides the panel and the call, if one is up, carries on.
   * The pill reads /live so it can say who answers before anyone commits —
   * or say the line is closed instead of opening a dead door.
   */
  function mountLauncher(el, origin, compact, theme, lockTheme, captions) {
    var left = el.getAttribute("data-position") === "left";
    var Z = "2147483000";

    var pill = document.createElement("button");
    pill.type = "button";
    pill.setAttribute("aria-haspopup", "dialog");
    pill.setAttribute("aria-expanded", "false");
    var s = pill.style;
    s.position = "fixed"; s.bottom = "18px"; s[left ? "left" : "right"] = "18px";
    s.zIndex = Z; s.cursor = "pointer";
    s.padding = "11px 18px"; s.borderRadius = "999px";
    s.border = "1px solid rgba(255,255,255,.18)";
    s.background = "#191b1f"; s.color = "#f2efe9";
    s.font = "600 13.5px/1.2 system-ui, sans-serif";
    s.boxShadow = "0 8px 28px rgba(0,0,0,.35)";
    pill.textContent = "📞 Call the DJ";

    var panel = document.createElement("div");
    var p = panel.style;
    p.position = "fixed"; p.bottom = "70px"; p[left ? "left" : "right"] = "16px";
    p.zIndex = Z; p.display = "none";
    p.width = "min(380px, calc(100vw - 24px))";
    p.borderRadius = "16px"; p.overflow = "hidden";
    p.boxShadow = "0 18px 48px rgba(0,0,0,.45)";

    var iframe = null, frameHeight = 480, overlaid = false;
    var maxHeight = function () { return Math.max(240, window.innerHeight - 110); };

    function applyHeight() {
      if (!iframe) return;
      iframe.style.height = Math.min(frameHeight, maxHeight()) + "px";
    }

    function open() {
      if (!iframe) {
        iframe = makeIframe(origin, compact, theme, lockTheme, captions);
        applyHeight();
        panel.appendChild(iframe);
        window.addEventListener("message", function (e) {
          if (!iframe || e.source !== iframe.contentWindow) return;
          var msg = e.data;
          if (!msg) return;
          // The panel is anchored to the bottom, so granting the ask list
          // its room is just growing upward — no direction to negotiate.
          if (msg.type === "subwave-callin:overlay") {
            var wanted = Number(msg.px) || 0;
            overlaid = wanted > 0;
            if (overlaid) {
              var granted = Math.max(
                120, Math.min(wanted, maxHeight() - frameHeight));
              iframe.style.height =
                Math.min(frameHeight + granted, maxHeight()) + "px";
              iframe.contentWindow.postMessage(
                { type: "swtv:overlay", px: granted, up: false }, origin);
            } else {
              applyHeight();
              iframe.contentWindow.postMessage(
                { type: "swtv:overlay", px: 0, up: false }, origin);
            }
            return;
          }
          if (msg.type === "subwave-callin:height" && !overlaid) {
            var px = Number(msg.px);
            if (px > 80 && px < 2000) { frameHeight = px; applyHeight(); }
          }
        });
        // The host's station-theming hook works in this mode too.
        el.setCallinTheme = function (tokens) {
          if (!tokens || !iframe || !iframe.contentWindow) return;
          iframe.contentWindow.postMessage(
            { type: "swtv:theme", tokens: tokens }, origin);
        };
      }
      panel.style.display = "block";
      pill.setAttribute("aria-expanded", "true");
    }

    function close() {
      // Hide, never unmount: tearing the frame down would hang up a live
      // call. Collapsed mid-call, the audio keeps going — a phone in the
      // pocket, not a phone on the hook.
      panel.style.display = "none";
      pill.setAttribute("aria-expanded", "false");
    }

    pill.onclick = function () {
      if (panel.style.display === "none") open(); else close();
    };

    // What the pill promises follows the line's real state, so a closed
    // booth never dangles a button that can only refuse. Same resolution
    // order the card itself uses, reduced to the three words a pill has
    // room for.
    function paintPill() {
      fetch(origin + "/live").then(function (r) { return r.json(); })
        .then(function (d) {
          var machineOn = (d.voicemailWhen || "never") !== "never";
          var closed = !!d.callsPaused
            || (d.liveCalls === false && !machineOn);
          var vmOnly = machineOn
            && (d.voicemailWhen === "always" || d.liveCalls === false);
          pill.disabled = closed;
          s.opacity = closed ? ".6" : "";
          pill.textContent = closed
            ? ((d.wording && d.wording.closed) || "Line closed")
            : vmOnly
            ? "📞 " + ((d.wording && d.wording.vm_button)
                                 || "Leave a message")
            : "📞 " + (d.callLabel || "Call the DJ");
        }).catch(function () { /* keep the last label */ });
    }
    paintPill();
    setInterval(paintPill, 60000);
    window.addEventListener("resize", applyHeight);

    el.appendChild(pill);
    el.appendChild(panel);
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
    // A host's theme is the widget's STARTING point, not a decree: the
    // viewer's own toggle keeps working and their choice is remembered.
    // data-lock-theme="true" restores the old behaviour — theme pinned,
    // toggle gone — for hosts that genuinely need one look.
    var lockTheme = el.getAttribute("data-lock-theme") === "true";
    // data-captions="ticker"|"full"|"off". Embeds default to the ticker —
    // only the latest spoken line, fading after a few seconds — so the
    // widget stays short wherever it's dropped.
    var captions = el.getAttribute("data-captions") || "";
    // data-height="260px" overrides the frame height for tight layouts.
    var height = el.getAttribute("data-height") || "";

    // data-mode="launcher": the floating pill + panel instead of an inline
    // frame. Everything below this line is the inline card.
    if (el.getAttribute("data-mode") === "launcher") {
      mountLauncher(el, origin, compact, theme, lockTheme, captions);
      return;
    }

    var iframe = makeIframe(origin, compact, theme, lockTheme, captions);
    // An iframe is inline by default, which leaves a few px of baseline gap
    // under it (makeIframe sets display:block) — restoring the frame to its
    // CONTAINER's height once compounded 3px per open-and-close of the ask
    // list; see baseHeight/baseSlot below.
    iframe.style.height = height || (compact ? "190px" : "420px");

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
