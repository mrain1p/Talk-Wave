/**
 * Drop-in embed for the SUB/WAVE call-in widget.
 *
 *   <div id="subwave-callin"></div>
 *   <script src="http://localhost:8100/embed.js"></script>
 *
 * Renders an <iframe> pointing at the call page. Whoever is live on air
 * answers — the host page doesn't choose a persona.
 *
 * data-mode picks an off-the-shelf shape that opens on a press instead of
 * sitting inline:
 *   "launcher"  a floating pill in a page corner (data-position="left" flips
 *               it) that opens the widget in a fixed panel, support-chat style
 *   "dock"      a slim bar pinned across the bottom that expands upward
 *   "button"    an inline button in the page flow that opens a centred modal
 * All three read the line's state, so they say who answers — or that the line
 * is closed — before they are pressed, and collapsing one does NOT hang up a
 * call in progress.
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
   * The three "opens on a press" shapes, one function because they differ only
   * in the TRIGGER and where the panel sits — the frame, the height/overlay
   * handshake and the state-reading trigger label are identical:
   *
   *   "launcher"  a floating pill in a page corner (support-chat bubble)
   *   "dock"      a slim bar pinned across the bottom, expands upward
   *   "button"    an inline button in the page flow, opens a centred modal
   *
   * The frame is created on FIRST open (a host page shouldn't pay for a widget
   * nobody pressed) and never torn down after: collapsing hides the panel and a
   * call in progress carries on — a phone in the pocket, not one on the hook.
   * The trigger reads /live so it says who answers, or that the line is closed,
   * before it is pressed.
   */
  function mountPanel(el, origin, compact, theme, lockTheme, captions, shape) {
    var left = el.getAttribute("data-position") === "left";
    var isModal = shape === "button";
    var isDock = shape === "dock";
    var Z = "2147483000";

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    var s = trigger.style;
    s.cursor = "pointer"; s.color = "#f2efe9"; s.background = "#191b1f";
    s.border = "1px solid rgba(255,255,255,.18)";
    s.font = "600 13.5px/1.2 system-ui, sans-serif";
    if (isModal) {
      // In the page flow, where the div was dropped — not fixed to a corner.
      s.display = "inline-flex"; s.alignItems = "center"; s.gap = "7px";
      s.padding = "11px 18px"; s.borderRadius = "12px";
      s.boxShadow = "0 2px 10px rgba(0,0,0,.18)";
    } else if (isDock) {
      s.position = "fixed"; s.left = "0"; s.bottom = "0"; s.width = "100%";
      s.zIndex = Z; s.padding = "13px 18px"; s.borderRadius = "0";
      s.borderWidth = "1px 0 0 0"; s.boxShadow = "0 -6px 24px rgba(0,0,0,.28)";
    } else {
      s.position = "fixed"; s.bottom = "18px"; s[left ? "left" : "right"] = "18px";
      s.zIndex = Z; s.padding = "11px 18px"; s.borderRadius = "999px";
      s.boxShadow = "0 8px 28px rgba(0,0,0,.35)";
    }
    trigger.textContent = "📞 Call the DJ";

    // The modal's backdrop dims the page and centres the panel; the other two
    // shapes float the panel next to their trigger with no backdrop.
    var backdrop = isModal ? document.createElement("div") : null;
    if (backdrop) {
      var bd = backdrop.style;
      bd.position = "fixed"; bd.inset = "0"; bd.zIndex = Z; bd.display = "none";
      bd.background = "rgba(0,0,0,.5)";
      bd.alignItems = "center"; bd.justifyContent = "center"; bd.padding = "12px";
    }

    var panel = document.createElement("div");
    var p = panel.style;
    p.zIndex = Z; p.display = "none";
    p.borderRadius = "16px"; p.overflow = "hidden";
    p.width = "min(380px, calc(100vw - 24px))";
    if (isModal) {
      p.position = "relative"; p.width = "min(400px, calc(100vw - 24px))";
      p.boxShadow = "0 24px 64px rgba(0,0,0,.5)";
    } else if (isDock) {
      p.position = "fixed"; p.left = "50%"; p.bottom = "56px";
      p.transform = "translateX(-50%)"; p.width = "min(400px, calc(100vw - 24px))";
      p.boxShadow = "0 18px 48px rgba(0,0,0,.45)";
    } else {
      p.position = "fixed"; p.bottom = "70px"; p[left ? "left" : "right"] = "16px";
      p.boxShadow = "0 18px 48px rgba(0,0,0,.45)";
    }

    var iframe = null, frameHeight = 480, overlaid = false;
    var maxHeight = function () { return Math.max(240, window.innerHeight - (isModal ? 120 : 110)); };

    function applyHeight() {
      if (!iframe) return;
      iframe.style.height = Math.min(frameHeight, maxHeight()) + "px";
    }

    function makeAndWire() {
      iframe = makeIframe(origin, compact, theme, lockTheme, captions);
      applyHeight();
      panel.appendChild(iframe);
      // A modal has a backdrop but no visible trigger behind it, so it carries
      // its own close control; the floating shapes toggle from their trigger.
      if (isModal) {
        var x = document.createElement("button");
        x.type = "button"; x.setAttribute("aria-label", "Close");
        x.textContent = "×";
        var xs = x.style;
        xs.position = "absolute"; xs.top = "6px"; xs.right = "8px"; xs.zIndex = "1";
        xs.width = "28px"; xs.height = "28px"; xs.lineHeight = "26px";
        xs.padding = "0"; xs.borderRadius = "999px"; xs.cursor = "pointer";
        xs.border = "1px solid rgba(255,255,255,.22)"; xs.background = "rgba(0,0,0,.35)";
        xs.color = "#fff"; xs.font = "600 17px/1 system-ui, sans-serif";
        x.onclick = close;
        panel.appendChild(x);
      }
      window.addEventListener("message", function (e) {
        if (!iframe || e.source !== iframe.contentWindow) return;
        var msg = e.data;
        if (!msg) return;
        // The panel is anchored (bottom, or centred), so granting the ask list
        // its room is just growing in place — no direction to negotiate.
        if (msg.type === "subwave-callin:overlay") {
          var wanted = Number(msg.px) || 0;
          overlaid = wanted > 0;
          if (overlaid) {
            var granted = Math.max(120, Math.min(wanted, maxHeight() - frameHeight));
            iframe.style.height = Math.min(frameHeight + granted, maxHeight()) + "px";
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
      // The host's station-theming hook works in every panel shape too.
      el.setCallinTheme = function (tokens) {
        if (!tokens || !iframe || !iframe.contentWindow) return;
        iframe.contentWindow.postMessage({ type: "swtv:theme", tokens: tokens }, origin);
      };
    }

    function open() {
      if (!iframe) makeAndWire();
      if (backdrop) backdrop.style.display = "flex";
      panel.style.display = "block";
      trigger.setAttribute("aria-expanded", "true");
    }

    function close() {
      // Hide, never unmount: tearing the frame down would hang up a live call.
      panel.style.display = "none";
      if (backdrop) backdrop.style.display = "none";
      trigger.setAttribute("aria-expanded", "false");
    }

    trigger.onclick = function () {
      if (panel.style.display === "none") open(); else close();
    };
    if (backdrop) {
      // A click on the dimmed area (not the panel) closes; so does Escape.
      backdrop.onclick = function (e) { if (e.target === backdrop) close(); };
      window.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && backdrop.style.display !== "none") close();
      });
    }

    // What the trigger promises follows the line's real state, so a closed
    // booth never dangles a button that can only refuse. Same resolution order
    // the card uses, reduced to the few words a pill has room for.
    function paintTrigger() {
      fetch(origin + "/live").then(function (r) { return r.json(); })
        .then(function (d) {
          var machineOn = (d.voicemailWhen || "never") !== "never";
          var closed = !!d.callsPaused || (d.liveCalls === false && !machineOn);
          var vmOnly = machineOn && (d.voicemailWhen === "always" || d.liveCalls === false);
          trigger.disabled = closed;
          s.opacity = closed ? ".6" : "";
          trigger.textContent = closed
            ? ((d.wording && d.wording.closed) || "Line closed")
            : vmOnly
            ? "📞 " + ((d.wording && d.wording.vm_button) || "Leave a message")
            : "📞 " + (d.callLabel || "Call the DJ");
        }).catch(function () { /* keep the last label */ });
    }
    paintTrigger();
    setInterval(paintTrigger, 60000);
    window.addEventListener("resize", applyHeight);

    el.appendChild(trigger);
    // Fixed shapes ignore their parent, but a modal backdrop must escape any
    // clipping/overflow the host wrapped the mount div in — park it on <body>.
    if (backdrop) { backdrop.appendChild(panel); document.body.appendChild(backdrop); }
    else { el.appendChild(panel); }
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

    // data-mode picks a shape that OPENS on a press — launcher (corner pill),
    // dock (bottom bar) or button (inline button + centred modal) — instead of
    // the inline card. Everything below this line is the inline card.
    var mode = el.getAttribute("data-mode") || "";
    if (mode === "launcher" || mode === "dock" || mode === "button") {
      mountPanel(el, origin, compact, theme, lockTheme, captions, mode);
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
