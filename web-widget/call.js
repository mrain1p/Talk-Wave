/* The call page: the phone, and nothing else.

   Served as /call.js and loaded at the end of <body>, so the DOM exists when
   it runs. The settings panel is panel.js and is a separate surface; this file
   knows nothing about it.

   Shared foundation comes from shared.js via the Callin global. */
(function () {
  const {
    $, params, compact, captionsMode, framed, themeForcedByHost, themeDefault,
    applySkin, skinForced, LINK_ICONS,
    ASKS, ASK_GROUPS, NEVER, CALL_KEY, callKey, rememberCallKey, callKeyExpired,
    ctx, resetCtx, pack, playSound, startRinging, stopRinging,
    setSounds, setVolume, getVolume, THEME_ICONS,
    playFirstWorking, readPlayerHandoff, writePlayerHandoff,
  } = window.Callin;


  // ------------------------------------------------- the corner controls
  // Which of the three the card offers is the BACKEND's call, sent with
  // /live, and it is applied identically on the call page and on an embed.
  // It used to be three unrelated mechanisms — the gear hidden by a CSS rule
  // that only existed for embeds, the theme toggle hidden by an inline
  // style set in two different places, the ? driven by whether canAsk came
  // back — which is exactly how the two surfaces ended up offering different
  // controls without anyone deciding they should.
  //
  // The widget only ever SUBTRACTS from what the backend offers, and only
  // for facts the backend cannot know: a host page that forced ?theme= has
  // already decided, and an embed never loads the settings panel at all
  // (app.js returns above it), so a gear there would open nothing.
  //
  // The backend sends BOTH surfaces' answers, because /live is cached across
  // every caller and cannot know which one is asking. `framed` is the test:
  // an embed is a widget in somebody else's iframe, which is the same thing
  // the operator was answering for in the panel's Embed column.
  function surfaceControls(d) {
    if (!d) return {};
    return (framed ? d.embedControls : d.controls) || d.controls || {};
  }

  function applyControls(d) {
    const c = surfaceControls(d);
    // Flush by default in an embed (operator's call, 0.10.51): the card
    // displays in whatever area its host gives it — no border, no sheet —
    // unless the outline is ticked back on. Gated on `compact`, not
    // `framed`: every real embed renders compact, while the panel's Page-tab
    // preview is framed too and must keep showing the real page's card.
    document.querySelector('.card').classList.toggle('bare',
      compact && !(d && d.embedOutline));
    const set = (id, on) => { const b = $(id); if (b) b.hidden = !on; };
    set('helpBtn', c.help !== false && !!(d && d.canAsk));
    set('themeBtn', c.theme !== false && !themeForcedByHost);
    // The player header carries its own copy of the theme toggle (operator's
    // ask, 2026-08-31): the sheet covers the card's corner, and reaching the
    // toggle meant leaving the player. Offered exactly when the card's is.
    set('plThemeBtn', c.theme !== false && !themeForcedByHost);
    // Admin only. `isAdmin` rides the per-request half of /live, so an
    // older worker that does not send it leaves this exactly as it was —
    // `!== false` rather than a truthy test, deliberately.
    // `!d ||` is load-bearing: the offline path calls this with null exactly
    // so the corner controls survive a failed /live, and dereferencing d here
    // threw instead — so the one case this was written for was the one case it
    // did not work. Caught in the preview browser: "Cannot read properties of
    // null (reading 'canOpenSettings')", once per failed read.
    set('gearBtn', c.settings !== false && !compact
        && (!d || d.canOpenSettings !== false));
    // Forget-the-code, shown whenever THIS device holds a stored code and
    // there was a reason to enter one — the line demanded it (kiosks), or
    // sign-in is on offer and the caller climbed a tier. Without the second
    // case a caller who signed in on an open line had no way to sign back
    // out. Forgetting drops them to the tier below on the next /live read.
    set('lockBtn', !!callKey() && (!!(d && d.guestRequired) || c.signin !== false));
    // Sign in for more: the operator's per-surface switch (c.signin), but
    // only when a code exists AND there is a tier to climb to
    // (d.signinAvailable, resolved per-request server-side). A caller already
    // at the top, or a line with nothing gated, never sees it. The
    // `!== false` form matches the other corner controls — show_signin
    // defaults off, so the server always sends an explicit true/false here.
    // …and never NEXT TO the sign-out button. A caller who has already used
    // a code was offered both at once, which reads as two doors when there is
    // only one: you cannot climb from guest to admin without dropping the
    // guest code first anyway, because one field holds one code. So while a
    // code is stored this is hidden and Forget the code stands alone; forget
    // it and Sign in comes back, ready for the admin password (operator's
    // ask, 2026-08-13).
    set('signinBtn', c.signin !== false && !!(d && d.signinAvailable)
        && !callKey());
    // The player head mirrors the PAGE's own corner answers (operator,
    // 2026-09-01: the sheet covers the card's corner, and sign-in is how a
    // key earns the operator side) — the sheet never renders on an embed,
    // so the Page column of the Access matrix already governs it and no
    // new setting exists to drift.
    // What-can-I-ask, on the sheet too (operator, 2026-09-01) — the same
    // corner-control answer the card's own "?" reads, so one switch
    // governs both and there is no second copy to drift.
    set('plHelpBtn', c.help !== false && !!(d && d.canAsk));
    set('plGearBtn', c.settings !== false && !compact
        && (!d || d.canOpenSettings !== false));
    set('plSigninBtn', c.signin !== false && !!(d && d.signinAvailable)
        && !callKey());
    // The guide's header wears the same chips as the player's, for the same
    // reason: it covers the card's corner, so reaching them meant leaving
    // the card you were on (operator, 2026-09-03).
    set('gdHelpBtn', c.help !== false && !!(d && d.canAsk));
    set('gdThemeBtn', c.theme !== false && !themeForcedByHost);
    set('gdGearBtn', c.settings !== false && !compact
        && (!d || d.canOpenSettings !== false));
    set('gdSigninBtn', c.signin !== false && !!(d && d.signinAvailable)
        && !callKey());
    // The operator's link out. `link` is already both gates (the feature and
    // this surface); an address that survived the server's http(s) check is
    // the third — a button that goes nowhere is worse than an empty corner.
    const link = (d && d.cornerLink) || {};
    const linkBtn = $('linkBtn');
    if (linkBtn) {
      // `!== false` like the other corner controls, and the ADDRESS is the
      // second gate: an older worker sends neither key, and then there is no
      // url either, so the button stays away rather than pointing at nothing.
      const ok = c.link !== false && !!link.url;
      if (ok) {
        linkBtn.href = link.url;
        // Drawn if we have it, typed if we don't. An emoji is the one thing on
        // this card no theme and no skin can touch — full colour, rendered by
        // the OS, in a row of three line-drawn controls in the card's own ink
        // (operator-reported). A value that is not one of ours is still shown
        // as text, so a deployment that stored an emoji keeps working.
        const drawn = LINK_ICONS[String(link.icon || '').trim()];
        if (drawn) linkBtn.innerHTML = drawn;
        else linkBtn.textContent = link.icon || '';
        linkBtn.title = link.label || '';
        linkBtn.setAttribute('aria-label', link.label || 'Link');
      }
      linkBtn.hidden = !ok;
    }
    // First-run: the card itself asks for the admin password while none
    // exists (needsSetup rides /live per-request). Never in an embed — a
    // host page's visitors are not the operator.
    set('setupNudge', !framed && !!(d && d.needsSetup));
  }

  // The same /auth/password the panel's nudge uses; an empty `current` is
  // the first set. Success hides the banner for good — needsSetup goes
  // false server-side the moment the store holds a hash.
  if ($('setupPwBtn')) $('setupPwBtn').onclick = async () => {
    const pw = $('setupPw').value || '';
    const msg = $('setupPwMsg');
    if (pw.length < 8) { msg.textContent = 'Use at least 8 characters.'; return; }
    const btn = $('setupPwBtn');
    btn.disabled = true;
    try {
      const r = await fetch('/auth/password', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current: '', new: pw }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        msg.textContent = d.error || 'Could not set it — try the settings page.';
        return;
      }
      $('setupPw').value = '';
      $('setupNudge').hidden = true;
      setStatus('Password set — your settings live at /settings');
      refreshLive();
    } finally { btn.disabled = false; }
  };

  $('lockBtn').onclick = () => {
    rememberCallKey('');
    setStatus('Signed out — the code is forgotten on this device');
    applyControls(shown || live);
    paintGuestGate();
    if (!room) refreshLive();
  };

  // Which lines of the who's-on-air block this surface paints. Same shape and
  // the same reason as the corner controls: an embed sits beside the host
  // page's own show heading and now-playing ticker, and a second copy of
  // either is noise. Missing means show it — a /live from an older server, or
  // a failed one, must not blank the card.
  function cardParts(d) {
    if (!d) return {};
    return (framed ? d.embedCard : d.card) || d.card || {};
  }

  // The operator's theme choice arrives with /live, long after the page has
  // painted, so the bootstrap above handles the immediate cases and this
  // applies the configured one once it is known.
  //
  // "inherit" is resolved by embed.js BEFORE the frame loads — it reads the
  // host page's background and passes ?theme=. A cross-origin frame cannot
  // see the page it sits in, so if inherit reaches us unresolved there is no
  // page to inherit from and auto is the honest answer.
  // The viewer's cycle: light, dark, the station's colours (when the
  // station publishes a palette), and match-the-page/device. Stored like the
  // old toggle; 'station' is new as a VIEWER choice rather than only an
  // operator default.
  function themeOptions() {
    const opts = ['light', 'dark'];
    if (live && live.stationTheme && live.stationTheme.tokens) opts.push('station');
    opts.push('');
    return opts;
  }

  function applyThemeChoice(choice) {
    const root = document.documentElement;
    [...root.style].filter((k) => k.startsWith('--'))
      .forEach((k) => root.style.removeProperty(k));
    if (choice === 'light' || choice === 'dark') {
      root.setAttribute('data-theme', choice);
    } else if (choice === 'station' && live && live.stationTheme
               && live.stationTheme.tokens) {
      root.setAttribute('data-theme',
        live.stationTheme.mode === 'light' ? 'light' : 'dark');
      applyTokens(live.stationTheme.tokens);
    } else if (themeDefault === 'light' || themeDefault === 'dark') {
      root.setAttribute('data-theme', themeDefault);
    } else {
      root.removeAttribute('data-theme');
    }
    paintThemeGlyph();
  }

  function paintThemeGlyph() {
    const btn = $('themeBtn');
    if (!btn) return;
    const opts = themeOptions();
    const cur = localStorage.getItem('callinTheme') || '';
    const here = opts.includes(cur) ? cur : '';
    const next = opts[(opts.indexOf(here) + 1) % opts.length];
    // Drawn, not typed (shared.js THEME_ICONS): the sun glyph read as a
    // star and the station's asterisk read as nothing at all
    // (operator-reported) \u2014 the station stop wears a transmitter mast,
    // which is what it stands for.
    const G = { light: THEME_ICONS.light, dark: THEME_ICONS.dark,
                station: THEME_ICONS.station, '': THEME_ICONS.device };
    const T = { light: 'light', dark: 'dark', station: "the station's colours",
                '': framed ? 'match the page' : 'follow the device' };
    // The glyph shows the theme you are ON; the title names where a tap
    // goes. It used to preview the NEXT stop, which read as the page being
    // wrong about its own state on every surface (operator, 2026-09-01).
    btn.innerHTML = G[here];
    btn.title = 'Theme: ' + T[here] + ' — tap for ' + T[next];
    // The player header's copy wears the same glyph and forwards its press —
    // one cycle, two doors.
    [$('plThemeBtn'), $('gdThemeBtn')].forEach((b) => {
      if (b) { b.innerHTML = G[here]; b.title = btn.title; }
    });
  }

  (function bindThemeCycle() {
    const btn = $('themeBtn');
    if (!btn || themeForcedByHost) return;
    btn.onclick = () => {
      const opts = themeOptions();
      const cur = localStorage.getItem('callinTheme') || '';
      const next = opts[(opts.indexOf(opts.includes(cur) ? cur : '') + 1) % opts.length];
      if (next) localStorage.setItem('callinTheme', next);
      else localStorage.removeItem('callinTheme');
      applyThemeChoice(next);
    };
    [$('plThemeBtn'), $('gdThemeBtn')].forEach((b) => {
      if (b) b.onclick = () => btn.onclick();
    });
  })();

  // "The station's own colours" follow the PROGRAMME — the server resolves
  // the on-air show's palette on every /live — but the theme used to be
  // applied only on the first read, so a show change mid-page left the card
  // wearing the previous show's colours until a reload (operator-reported,
  // 2026-08-09). Repaint on a poll ONLY when a genuinely new palette arrives
  // AND the station look currently governs: the operator default with no
  // viewer override, or the viewer's own explicit 'station' pick. A viewer
  // pinned to light or dark is never repainted — that guard is why the
  // first-read-only rule existed, and it survives here. A palette that goes
  // NULL is a station read failing, not a show losing its colours (the
  // station's default theme backstops `effective`), so nothing is stripped.
  let lastPalette = '';
  function followStationPalette(d) {
    const tokens = d.stationTheme && d.stationTheme.tokens;
    const key = tokens ? JSON.stringify(tokens) : '';
    if (!tokens || key === lastPalette) { if (key) lastPalette = key; return; }
    lastPalette = key;
    const stored = localStorage.getItem('callinTheme') || '';
    if (stored === 'station') {
      applyThemeChoice('station');           // reads the fresh `live`
    } else if (!stored && d.theme === 'station' && !themeForcedByHost) {
      applyConfiguredTheme(d.theme, d.stationTheme);
      paintThemeGlyph();
    }
  }

  function applyConfiguredTheme(choice, palette) {
    if (themeForcedByHost) return;              // the host page has decided
    const root = document.documentElement;
    // A viewer's stored choice — including 'station', which the old
    // light/dark bootstrap cannot apply — beats the operator's default.
    const stored = localStorage.getItem('callinTheme') || '';
    if (stored) {
      applyThemeChoice(stored);
      return;
    }
    if (choice === 'light' || choice === 'dark') {
      root.setAttribute('data-theme', choice);  // forced: applyControls drops
      return;                                   // the toggle to match
    }
    // The station's own colours, resolved server-side into this widget's
    // token names (api/live.station_palette). Same mechanism a host page uses
    // over swtv:theme — the difference is only who did the asking, which
    // matters because the standalone page has no host to ask.
    //
    // `mode` first, then the tokens on top: it decides the handful the
    // station has no counterpart for (the green that means the line is open,
    // the shadow) and what the browser paints its own controls in.
    if (choice === 'station' && palette && palette.tokens) {
      // Not over a viewer who already toggled. The station palette is the
      // operator's default look; the toggle is the viewer's explicit choice,
      // and it clears these tokens to make itself visible (shared.js) — so a
      // poll re-applying them would undo the click within twenty seconds.
      if (localStorage.getItem('callinTheme')) return;
      root.setAttribute('data-theme', palette.mode === 'light' ? 'light' : 'dark');
      applyTokens(palette.tokens);
      return;
    }
    if (!localStorage.getItem('callinTheme')) {
      // The host's soft default fills the gap nothing stronger claimed —
      // removing the attribute here used to wipe it on the first /live.
      if (themeDefault === 'light' || themeDefault === 'dark') {
        root.setAttribute('data-theme', themeDefault);
      } else {
        root.removeAttribute('data-theme');
      }
    }
  }

  // Write a token map onto :root. Shared by the station palette above and the
  // host page's swtv:theme message, which are the same operation arriving by
  // two routes. Only custom-property names, and only values that cannot carry
  // anything but a colour — one of these routes is a message from another
  // origin, and neither should be able to set arbitrary style.
  function applyTokens(tokens) {
    const root = document.documentElement;
    Object.keys(tokens).forEach((k) => {
      if (!/^--[a-z0-9-]+$/i.test(k)) return;
      const v = String(tokens[k]);
      if (v.length < 120 && !/[;{}<>]/.test(v)) root.style.setProperty(k, v);
    });
    // Remember the last palette so the NEXT load can paint in the station's
    // colours on its first frame, instead of flashing the default accent until
    // /live or the host's swtv:theme lands (radio.drearburh.uk, embedded on a
    // SUB/WAVE page that posts its palette after the frame paints — the coral →
    // purple flash reported 2026-08-10). shared.js reads this at boot.
    try { localStorage.setItem('callinPalette', JSON.stringify(tokens)); } catch (e) { /* private mode */ }
  }

  // Station mode (HOST-STYLE-GUIDE §2). The host dresses itself in the on-air
  // show's palette, which changes when the show changes, and sends us the same
  // token map. We repaint IN PLACE — the old mechanism was reloading the
  // frame, and a reload during a call drops the call.
  //
  // Unknown keys are ignored and missing keys fall through to the CSS
  // defaults, so a host that sends three tokens or thirty both work. Only
  // custom-property names are accepted: this is a message from another origin,
  // and nothing here should be able to set arbitrary style.
  addEventListener('message', (e) => {
    if (!framed || e.source !== window.parent) return;
    const msg = e.data;
    if (!msg) return;

    // The settings panel, showing the operator what their unsaved changes
    // would look like. Two gates, and both are required: the frame has to
    // have been opened as a preview (?preview=1), and the message has to
    // come from THIS origin. A station page that embeds the widget is
    // another origin and can never send this — it gets swtv:theme, which is
    // colour only. This one can change what the card offers, so it is for
    // the operator's own page and nothing else.
    //
    // The payload is resolved server-side by /live/preview, so the rules for
    // which control appears and what the Call button says exist once, in
    // api/live.py, rather than once there and once here.
    if (msg.type === 'swtv:preview') {
      if (!previewMode || e.origin !== location.origin) return;
      preview = msg.live || null;
      paintLive(Object.assign({}, live || {}, preview || {}), true);
      return;
    }

    // The panel's hover-highlight: the settings row under the operator's
    // pointer names the card element it controls, and that element wears
    // the accent outline while the pointer stays. Same two gates as
    // swtv:preview — this exists only for the operator's own page.
    if (msg.type === 'swtv:spotlight') {
      if (!previewMode || e.origin !== location.origin) return;
      spotlight(msg.el);
      return;
    }

    // Focusing a line-box wording field in the panel: the status line shows
    // that state's text — typed, or the built-in default — until a null
    // puts the live one back. See lineboxPreview for how the truth is kept.
    if (msg.type === 'swtv:linepreview') {
      if (!previewMode || e.origin !== location.origin) return;
      lineboxPreview(msg.text);
      return;
    }

    // The host answering our request for room to open the ask list in. It is
    // the authority on both the amount and the direction: it can see the
    // page, and we cannot see past our own frame.
    if (msg.type === 'swtv:overlay') {
      const px = Math.max(0, Math.min(2000, Number(msg.px) || 0));
      setOverlay(px, !!msg.up);
      // px 0 is the host confirming it has put the frame back; there is
      // nothing left to show. Otherwise wait for the frame to actually be
      // resized, or the popup gets placed against the height we had a
      // moment ago.
      if (px) requestAnimationFrame(() => { if (askShow) askShow(); });
      else notifyHeight();
      return;
    }

    if (msg.type !== 'swtv:theme' || !msg.tokens) return;
    // An optional mode rides with the tokens, at themeDefault strength: it
    // fills the light/dark answer only while the viewer has not chosen.
    if ((msg.mode === 'light' || msg.mode === 'dark')
        && !localStorage.getItem('callinTheme')) {
      document.documentElement.setAttribute('data-theme', msg.mode);
    }
    applyTokens(msg.tokens);
  });

  // "What can I ask?" — most people meeting a phone-in assume it only takes
  // requests. Built from the shared ASKS list and filtered to the permissions
  // actually switched on, so it can never suggest something the DJ would
  // refuse. What a caller CANNOT do is deliberately left out: that list is
  // for the operator deciding what to allow, not for a stranger on the line.
  // What the ask popup depends on: the permission set AND the tier that names
  // it. A change in either has to repaint it — the tier alone drives the
  // "whose menu this is" chip, and the two do not always move together.
  function askSignature(d) {
    return JSON.stringify([(d && d.callerTier) || null, (d && d.canAsk) || null]);
  }

  function paintAskPopup(canAsk) {
    const host = $('askPopList');
    if (!host) return;
    // Whose menu this is — the server already filtered the list to this
    // caller's tier, and saying so beats a guest wondering why a friend's
    // list looked longer. Quiet: one micro-chip in the head, no room lost.
    const role = $('askRole');
    if (role) {
      const tier = (shown || live || {}).callerTier;
      role.hidden = !tier;
      if (tier) {
        role.textContent = { open: 'for any caller', guest: 'for guest callers',
                             admin: 'for the operator' }[tier] || '';
        role.title = 'What this list offers follows how you got in — the '
          + 'guest code or the operator password can add more.';
      }
    }
    host.innerHTML = '';
    // Grouped: a heading per group that has any offered item, so the reads,
    // the requests and the on-air actions read as three different kinds of
    // thing instead of one long menu.
    ASK_GROUPS.forEach(([key, label]) => {
      const items = ASKS.filter((a) => a.group === key && (!a.need || canAsk[a.need]));
      if (!items.length) return;
      const head = document.createElement('li');
      head.className = 'askhead';
      head.textContent = label;
      host.appendChild(head);
      items.forEach((a) => {
        const li = document.createElement('li');
        li.innerHTML = '<span class="say"></span><span class="why"></span>';
        li.querySelector('.say').textContent = a.say;
        li.querySelector('.why').textContent = a.why;
        host.appendChild(li);
      });
    });
  }

  // Placed in viewport coordinates so it can sit over the page rather than
  // being squeezed into whatever room the card has — on an embed that is
  // almost none, and the list came out squashed and scrolling.
  //
  // Downwards by default, flipping above the button only when there is
  // genuinely more room up there.
  function placeAskPopup(btn, pop) {
    pop.style.maxHeight = '';
    const b = btn.getBoundingClientRect();
    const GAP = 8, EDGE = 12;
    const below = innerHeight - b.bottom - GAP - EDGE;
    const above = b.top - GAP - EDGE;
    const wanted = pop.scrollHeight;

    const goUp = below < wanted && above > below;
    const room = Math.max(140, Math.min(wanted, goUp ? above : below));
    pop.style.maxHeight = room + 'px';

    // Right-aligned to the button, then pulled back inside the viewport.
    const width = pop.getBoundingClientRect().width;
    let left = Math.min(b.right - width, innerWidth - width - EDGE);
    pop.style.left = Math.max(EDGE, left) + 'px';
    pop.style.top = goUp ? Math.max(EDGE, b.top - GAP - room) + 'px'
                         : (b.bottom + GAP) + 'px';
  }

  // The ask list is routinely taller than the whole widget, and inside an
  // embed it was clipped by the frame — a list of eight things a caller can
  // ask, in a 190px window, scrolling. So the frame itself gets out of the
  // way: the widget asks the host for room, embed.js turns the frame into an
  // overlay that reaches past the slot it was given, and the list opens over
  // the host page. Downwards by default; embed.js flips it upwards when the
  // page has no room below, because only the host can see the host.
  //
  // askRoom is the extra px the host granted, 0 when we are not overlaid.
  let askRoom = 0, askWait = null, askShow = null, askClose = null;
  // The current popup + button, and whether the document listeners are wired.
  // setupAskPopup runs on every caller-tier change; it used to add a fresh
  // pair of anonymous document listeners each time and never remove them, so
  // a caller cycling sign-in/lock accumulated stale closures for the life of
  // the page (top-down review, 2026-08-28). One persistent pair now, reading
  // the current popup through these mutable refs.
  let askPop = null, askBtn = null, askDocWired = false;

  function setOverlay(px, up) {
    askRoom = px;
    document.documentElement.style.setProperty('--overlay-px', px + 'px');
    document.body.classList.toggle('overlay-up', px > 0 && up);
  }

  function requestOverlay(px) {
    if (!framed) return;
    parent.postMessage({ type: 'subwave-callin:overlay', px: px }, '*');
  }

  function setupAskPopup(canAsk) {
    const btn = $('helpBtn'), pop = $('askPop');
    if (!btn || !pop) return;
    if (!canAsk) { pop.hidden = true; return; }   // applyControls owns the button
    paintAskPopup(canAsk);

    const close = () => {
      clearTimeout(askWait); askWait = null;
      pop.hidden = true; pop.style.visibility = '';
      btn.setAttribute('aria-expanded', 'false');
      // The card's own offset is NOT dropped here. Opening upwards, the frame
      // is anchored to the bottom of its slot and the card is held down by
      // that offset; dropping it before the host has shrunk the frame back
      // sends the card to the top of a still-tall frame for a frame or two,
      // which reads as the widget jumping as you close the menu. The host
      // echoes px:0 when it is done, and that is what clears it.
      if (askRoom) requestOverlay(0);
    };
    askClose = close;

    const show = () => {
      clearTimeout(askWait); askWait = null;
      placeAskPopup(btn, pop);
      pop.style.visibility = '';
    };
    askShow = show;

    btn.onclick = () => {
      if (!pop.hidden) return close();
      btn.setAttribute('aria-expanded', 'true');
      // Laid out but not yet painted, so scrollHeight is real while the
      // popup cannot be seen sitting in the wrong place for a frame.
      pop.style.visibility = 'hidden';
      pop.hidden = false;
      if (!framed) return show();
      requestOverlay(pop.scrollHeight + 16);
      // A host that framed us without embed.js will never answer. Rather
      // than leaving the list invisible forever, open it inside the frame —
      // which is exactly what it did before any of this existed.
      askWait = setTimeout(show, 250);
    };
    $('askClose').onclick = close;
    // Escape and a click outside, because a popup with only an X is a trap on
    // a phone. Wired ONCE and read through askPop/askBtn/askClose, which this
    // call has just refreshed — so re-running setupAskPopup adds no new
    // listeners.
    askPop = pop; askBtn = btn;
    if (!askDocWired) {
      askDocWired = true;
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && askClose) askClose();
      });
      document.addEventListener('click', (e) => {
        if (askPop && !askPop.hidden && !askPop.contains(e.target)
            && e.target !== askBtn && askClose) askClose();
      });
    }
  }

  const callBtn = $('callBtn'), muteBtn = $('muteBtn'), hangBtn = $('hangBtn');
  const statusText = $('statusText'), dot = $('dot'), capBox = $('captions');

  // THE LINE BOX IS THE SCROLLER, not the transcript inside it. Five places
  // wrote `capBox.scrollTop = capBox.scrollHeight` and every one of them was
  // scrolling an element with no overflow — .captions stopped being a scroller
  // of its own when the box took the job, and nothing updated these. So the
  // newest line landed below the fold and the caller had to scroll by hand to
  // read what had just been said (operator-reported, on a live call).
  //
  // STICKY, not forced: a caller who has deliberately scrolled up to re-read
  // something must not be yanked back every time a word arrives. Within ~40px
  // of the bottom counts as "following", which is where you are unless you
  // went looking.
  // Whether the caller is following the bottom. Maintained from their OWN
  // scrolling, never measured after the fact: the slack was computed once the
  // new line was already in the box, so anything taller than the 40px
  // tolerance — a segment receipt, a skill card, a now-playing card — grew the
  // content past it in one go and the check concluded the caller had scrolled
  // away. They had not. They just had a card land, and from then on every new
  // line arrived below the fold (operator-reported, and worst on exactly the
  // turns that put a card up).
  //
  // A programmatic append fires no scroll event, so this flag survives it and
  // still says what the caller last chose.
  let followingLines = true;
  function watchLineBox() {
    const box = $('lineBox');
    if (!box || box.dataset.followWatched) return;
    box.dataset.followWatched = '1';
    box.addEventListener('scroll', () => {
      followingLines =
        box.scrollHeight - box.scrollTop - box.clientHeight <= 40;
    }, { passive: true });
  }
  function followTranscript() {
    const box = $('lineBox');
    if (!box) return;
    watchLineBox();
    if (!followingLines) return;
    box.scrollTop = box.scrollHeight;
    // A card can grow after it is in the DOM — an image decoding, a font
    // swapping, a receipt laying out — and the scroll above was measured
    // against the height before that. One more pass on the next frame lands
    // on the real bottom.
    requestAnimationFrame(() => {
      if (followingLines) box.scrollTop = box.scrollHeight;
    });
  }
  // …and for the moments where following is the whole point (a message left, a
  // keyboard opening over the box), regardless of where they were.
  function pinTranscript() {
    const box = $('lineBox');
    if (!box) return;
    followingLines = true;             // an explicit pin re-arms following
    box.scrollTop = box.scrollHeight;
    requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
  }

  let room = null, muted = false, live = null;
  let chatOpen = false;                       // the text line, not a call
  let signinMode = false;                     // the gate opened to climb a tier
  let lastCanAsk = null;                       // rebuild the menu only on a tier change
  // The operator's default volume is a DEFAULT, not a rule the poll enforces.
  // Both halves are needed to keep it one: the last value /live carried, so a
  // change the operator makes still reaches an open card, and whether this
  // listener has moved a fader themselves, after which nothing else touches it.
  let lastCfgVolume = null, volTouched = false;
  let djEl = null, rafId = null, streamEl = null;
  // The station player's own element and its health — separate from the
  // call's tune-in bed (streamEl) on purpose: the two are never up at once,
  // but they answer to different volumes and different owners. Ducked while
  // the STUDIO holds the line (see vmDial and applyVolume); declared here
  // because applyVolume reads it at first paint, long before the studio's
  // own block runs.
  let playerEl = null, playerDead = false, playerDucked = false;
  // The sheet's mute — holds the player silent without moving the shared
  // fader. Declared here because applyVolume reads it at first paint, long
  // before the dock's handler is wired.
  let plMuted = false;
  // The player-first auto-open fires once per page load — see the poll.
  let playerStartApplied = false;
  // The DJ's own track, kept so the station can pull the voice into the shared
  // audio graph whichever of the two arrives second. See mixStation.
  let djTrack = null;

  // The now-playing rail: when the record started (unix seconds, from the
  // station) and how long it runs. Both 0 when the station has not said, and
  // the rail then shows a title and nothing else — an empty clock is honest,
  // a guessed one is not.
  let npStart = 0, npLength = 0;
  // The last GOOD record read, held one minute: a station mid-transition can
  // answer a poll with everything but the record — no track, no show — and
  // the card blanked its whole middle for the 20s to the next poll
  // (operator's phone, 2026-08-25). One empty read inside a minute of a good
  // one is forgiven; longer silence is the truth and paints through.
  let npHeld = null;

  // THE IDLE BOARD. What the box says when there is no conversation in it.
  //
  // It has been three things. A permanent grey "Not connected", which
  // restated the eyebrow and the Call button and read as a FAULT on host
  // pages. Then nothing at all, which read as unfinished once the box became
  // the card's whole subject. Then one sentence, which was honest but did not
  // use a box that is now two hundred pixels tall.
  //
  // This is the operator's shape: a rule, the state in the card's own mono
  // voice, a rule, and then WHICH DOORS ARE ACTUALLY OPEN under it — a line
  // per way in, drawn from the same /live flags the buttons are drawn from,
  // so the board cannot advertise a door the card does not offer.
  function paintBoard(d) {
    const box = $('idleBoard');
    if (!box) return;
    // Only when the box has nothing better to do. Any speech, any message,
    // any door — the board is the emptiest state, not an overlay.
    // `.oncall` and not just `room`: the card flips to Hang up the instant the
    // button is pressed, but `room` is only assigned after the token mint, so
    // a board keyed on `room` alone sat over a connected call — the operator
    // saw LINES ARE OPEN above a live transcript at 0:17.
    // `inConversation()` catches what the flags below cannot: the soundbite
    // studio has no room, no chat socket and no .oncall, so the poll painted
    // LINES ARE OPEN over a caller mid-recording (operator's screenshot,
    // 2026-08-17) — the same shape as the 0:17 incident, one surface later.
    const busy = !!room || chatOpen || inConversation()
      || document.querySelector('.card').classList.contains('oncall')
      || (capBox.classList.contains('on') && capBox.children.length)
      || !$('guestGate').hidden || !$('setupNudge').hidden
      || !$('endedBar').hidden;
    if (busy) { box.hidden = true; return; }

    const paused = !!(d && d.callsPaused);
    const dj = (($('djName').textContent) || '').trim();
    const onAir = !!(d && d.onAir);
    // ONE example line for either idle shape below — a two-hundred-pixel
    // box with five words in it read as unfinished (operator, 2026-08-24),
    // and the popup's answer to "what do I even say" hides behind a corner
    // glyph most callers never press. One line, never a list (the operator
    // retired the door list from this board for restating the card); drawn
    // from the same filtered ASKS the popup shows, so it can never suggest
    // what the DJ would refuse. The pick rides the poll, so it rotates.
    const tryLine = () => {
      const can = (d && d.canAsk) || {};
      const says = ASKS.filter((a) => !a.need || can[a.need])
        .map((a) => a.say).filter(Boolean);
      if (!says.length) return null;
      const t = document.createElement('span');
      t.className = 'bdtry';
      t.textContent = 'Try: ' + says[Math.floor(Date.now() / 25000) % says.length];
      return t;
    };
    // The 4c stage message: while the word switch is up, the stage says
    // where the call will go — coral for the broadcast, the cool teal for
    // the private line — instead of listing doors. The switch hides itself
    // through every closed/gated state, so this can never mask one.
    if ($('routeSwitch') && !$('routeSwitch').hidden) {
      box.hidden = false;
      box.innerHTML = '';
      box.appendChild(buildStage(d, dj, paused, tryLine));
      return;
    }
    // Each door twice: is it OFFERED at all, and is it usable right now. The
    // second is what earns the strike-through — a board that lists a way in
    // the card will refuse is worse than one that lists nothing.
    const ways = [];
    if (d && d.liveCalls !== false) {
      ways.push(['Calls', !paused && !!dj && dj !== '…' && onAir]);
    }
    if (d && (framed ? d.embedChatBtn : d.chatBtn) !== false && d.chatEnabled
        && d.chatMine !== false) {
      ways.push(['Texts', !paused]);
    }
    // 'never' is not offered; 'closed' is offered only when the booth is shut,
    // which is exactly when the machine is the point. 'always' is always.
    // A tier the caller doesn't reach is not offered at all — a board that
    // lists a way in the mint will refuse is worse than one listing nothing.
    const vm = d && d.voicemailWhen;
    if (vm && vm !== 'never' && d.voicemailMine !== false) {
      const shut = paused || !onAir;
      ways.push(['Voicemail', vm === 'always' ? true : shut]);
    }

    // The headline follows the doors, not the pause switch alone: a line
    // nobody can use is closed however the switch is set, and one with the
    // machine on is not "closed" just because the booth is empty.
    // ONE LINE, since 0.10.136. The board listed the doors under the headline
    // and named who picks up, and the operator's answer was that the card
    // already says all of it: the doors are the buttons on the action row an
    // inch below, and the DJ's name is at the top of the card in bold beside
    // their photograph. Three ways of saying it is not three times as clear.
    // The doors are still WORKED OUT above, because the headline is derived
    // from them — a line nobody can use is closed however the switch is set.
    const anyLive = ways.some((w) => w[1]);
    const head = anyLive ? 'Lines are open'
      : paused ? 'Lines are closed' : 'Nobody in the booth';
    box.innerHTML = '';
    const rule = () => { const r = document.createElement('span');
      r.className = 'bdrule'; return r; };
    const h = document.createElement('span');
    h.className = 'bdhead' + (paused ? ' shut' : '');
    h.textContent = head;
    box.appendChild(rule()); box.appendChild(h); box.appendChild(rule());
    // Only while a line is actually open — an example under "Lines are
    // closed" would be an invitation the card refuses.
    if (anyLive) {
      const t = tryLine();
      if (t) box.appendChild(t);
    }
    box.hidden = false;
  }

  // THE STAGE (design handoff, 2026-09-03). While the route switch is up,
  // the box under it answers three questions in the order a caller asks
  // them: what does this route COST me, what is the booth actually asking,
  // and what else is this line for. It used to be one centred sentence in a
  // box four hundred pixels tall — the operator's "wasted space", and the
  // reason the phone face read as unfinished beside the other two.
  //
  // Nothing here invents copy: the consequence line is the same wording
  // override it always was, and the middle is only ever drawn from an open
  // line the station has actually announced.
  function buildStage(d, dj, paused, tryLine) {
    const oa = (d && d.onAirCalls) || {};
    const wrap = document.createElement('div');
    wrap.className = 'stage';
    // 1. WHAT THIS ROUTE COSTS. The label names the consequence, the
    // sentence spends it.
    const head = document.createElement('div');
    head.className = 'stagehead';
    head.textContent = onAirPick
      ? word('stage_live_head', 'Goes out on the broadcast')
      : word('stage_priv_head', 'Private line to the booth');
    // With only the voicemail door live, the ON AIR promise narrows to what
    // is actually true: the recording airs, the call would not. And tape
    // mode is its own promise — the conversation airs at hangup, not as you
    // speak — so the stage says which consent is being given.
    const say = document.createElement('div');
    say.className = 'stagesay ' + (onAirPick ? 'live' : 'priv');
    say.textContent = onAirPick
      ? (oa.calls
          ? (oa.mode === 'after'
              ? word('route_tape', 'Broadcast — airs after you hang up')
              : word('route_live', 'Broadcast — live on air'))
          : word('route_vm_live', 'Your recording airs on the station'))
      : word('route_priv', "It's just you and {dj}")
          .replace('{dj}', (dj && dj !== '…') ? dj : 'the DJ');
    const rule = document.createElement('div');
    rule.className = 'stagerule';
    wrap.append(head, say, rule);
    // 2. THE MIDDLE, centred in whatever is left. An open line if the booth
    // has one up; otherwise the example, which is what the box has always
    // offered a caller with nothing to say.
    const mid = document.createElement('div');
    mid.className = 'stagemid';
    const open = openLine(d);
    if (open) {
      const card = document.createElement('div');
      const top = document.createElement('div');
      top.className = 'openhead';
      const pip = document.createElement('span');
      pip.className = 'openpip';
      pip.setAttribute('aria-hidden', 'true');
      const lab = document.createElement('span');
      lab.className = 'openlab';
      lab.textContent = 'Open lines · '
        + (open.dj ? open.dj + ' is asking' : "she's asking");
      top.append(pip, lab);
      if (open.until) {
        const til = document.createElement('span');
        til.className = 'opentil';
        til.textContent = 'until ' + open.until;
        top.appendChild(til);
      }
      const subj = document.createElement('div');
      subj.className = 'opensubj';
      subj.textContent = '\u201C' + open.subject + '\u201D';
      card.append(top, subj);
      mid.appendChild(card);
    } else if (!onAirPick && !paused) {
      // The example rides the PRIVATE stage only: under an on-air line the
      // box is stating a consent, and small talk under a consent muddies it.
      const t = tryLine();
      if (t) mid.appendChild(t);
    }
    wrap.appendChild(mid);
    // 3. WHAT ELSE THE LINE IS FOR, under a rule, quiet — so an open line
    // never reads as the only thing a caller may ring about.
    const foot = document.createElement('div');
    foot.className = 'stagefoot';
    const flab = document.createElement('span');
    flab.className = 'stagefootlab';
    flab.textContent = 'Also on the line';
    const fsay = document.createElement('span');
    fsay.className = 'stagefootsay';
    fsay.textContent = alsoOnTheLine(d);
    foot.append(flab, fsay);
    wrap.appendChild(foot);
    return wrap;
  }

  // The open line the station has up, with the moment it closes resolved to
  // a clock time. THE EARLIER of the line's own expiry and the end of the
  // show it was opened in: a segment cannot outlive its show (the record's
  // own is_live check says so), and a card promising past the hour would be
  // the one thing on the stage that is not true. The show's end comes from
  // the guide the card has already loaded — when there is none, or the
  // station is running something off the schedule (a takeover), the line's
  // own expiry stands alone.
  function openLine(d) {
    const ol = d && d.openLines;
    if (!ol || !ol.live || !ol.subject) return null;
    const ends = ol.expiresAt ? Date.parse(ol.expiresAt) : NaN;
    let at = isNaN(ends) ? null : ends;
    const showEnds = scheduledShowEnd();
    if (showEnds && (at === null || showEnds < at)) at = showEnds;
    return {
      subject: ol.subject,
      dj: ol.dj || '',
      until: at ? new Date(at).toLocaleTimeString([],
        { hour: 'numeric', minute: '2-digit' }) : '',
    };
  }

  // When the show on air is scheduled to end, in local milliseconds — or 0
  // when the card cannot say. Read from the guide payload, which the card
  // already holds whenever the guide face is on; a station running off its
  // own schedule (an override, a takeover) has no scheduled end to report,
  // and saying one would be worse than saying none.
  function scheduledShowEnd() {
    const g = guideData;
    if (!g || !g.grid) return 0;
    if (g.override && g.override.showId) return 0;
    const now = stationNow(g.timezone);
    const runs = echoed(weekRuns(weekSlots(g.grid)));
    const on = onAt(runs, now.abs);
    if (!on) return 0;
    const stationId = (g.onAir && g.onAir.id) || '';
    if (stationId && on.id !== stationId) return 0;      // off schedule
    // The grid is hour-granular — `now.abs` is the hour of the week, not the
    // minute — so the gap is counted from the TOP of the current hour, which
    // is where the run's own boundary sits. Counting from `Date.now()` would
    // put a show's end at 40 minutes past whatever hour it really ends on.
    const d = new Date();
    const top = Date.now() - d.getMinutes() * 60000 - d.getSeconds() * 1000
      - d.getMilliseconds();
    return top + (on.end - now.abs) * 3600000;
  }

  // The doors this line answers besides whatever is up right now, in the
  // card's own voice. Derived from the same permissions the ask menu is
  // filtered by, so it can never name something the DJ would refuse.
  function alsoOnTheLine(d) {
    const can = (d && d.canAsk) || {};
    const bits = [];
    if (can.allow_requests !== false) bits.push('requests');
    if (can.allow_announcements) bits.push('shout-outs');
    if (can.allow_library_search) bits.push('what played earlier');
    if (!bits.length) bits.push('a word with the booth');
    return bits.join(' · ');
  }

  // The card's heart — the same add-only public like the player sheet sends,
  // through the same /player/like relay. Its own state, deliberately not the
  // sheet's: the card must never claim a heart it did not press, and the two
  // surfaces meeting on one record is the harmless case (the station counts
  // both, per its own per-listener limits).
  let cardLiked = false, cardHeartFor = '';
  function paintCardHeart(d) {
    const b = $('npHeart');
    if (!b) return;
    const key = String(d.track || '');
    // `!== false` so a cached /live from an older server keeps the default:
    // the setting ships ON, and absent must not read as off. And never on a
    // still-locked gated line: the relay answers 401 there, so the heart
    // would be a button that silently un-presses itself (measured on the
    // first local render). It appears with the unlock, like the phone.
    const show = d.cardLike !== false && !!key
      && !(d.guestRequired && !callKey());
    b.hidden = !show;
    if (!show) return;
    if (key !== cardHeartFor) { cardHeartFor = key; cardLiked = false; }
    b.classList.toggle('liked', cardLiked);
    b.setAttribute('aria-pressed', cardLiked ? 'true' : 'false');
    b.innerHTML = cardLiked ? '&#9829;' : '&#9825;';
  }
  $('npHeart').addEventListener('click', async () => {
    // A lit heart un-hearts here too when the key clears the permission —
    // the phone page's heart matching the player's (operator, 2026-09-01).
    if (cardLiked) {
      if (!(plAbilities && plAbilities.unlike)) return;
      cardLiked = false;
      paintCardHeart({ track: cardHeartFor, cardLike: true });
      try {
        let song = null;
        try {
          const s = await fetch('/player/like', { headers: keyHeaders() });
          if (s.ok) song = (await s.json()).songId || null;
        } catch (e) { /* no id, no unlike — restore below */ }
        if (!song) throw new Error('no record id to un-heart');
        const r = await fetch('/player/unlike', {
          method: 'POST',
          headers: keyHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ songId: song, title: cardHeartFor }),
        });
        if (!r.ok) throw new Error('refused');
      } catch (e) {
        cardLiked = true;
        paintCardHeart({ track: cardHeartFor, cardLike: true });
      }
      return;
    }
    cardLiked = true;                    // optimistic; walked back on refusal
    paintCardHeart({ track: cardHeartFor, cardLike: true });
    try {
      // Ask which record the station thinks is on FIRST: songId is its
      // stale-tap guard, and a press landing just after a track change
      // would otherwise heart the wrong song. Best-effort — with no answer
      // the station's own current-track fallback applies.
      let song = null;
      try {
        const s = await fetch('/player/like', { headers: keyHeaders() });
        if (s.ok) song = (await s.json()).songId || null;
      } catch (e) { /* fall through to the current-track like */ }
      const r = await fetch('/player/like', {
        method: 'POST',
        headers: keyHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(song ? { songId: song } : {}),
      });
      if (!r.ok) throw new Error('refused');
    } catch (e) {
      cardLiked = false;
      paintCardHeart({ track: cardHeartFor, cardLike: true });
    }
  });

  // A title longer than its row cycles so the whole thing can be read
  // (operator's phone, 2026-08-24 — "I Am — Kamasi…"). The measurement
  // needs a laid-out frame, so it rides rAF; a hidden tab simply keeps the
  // fade until it is looked at. Guarded on the text so the 20s poll does
  // not restart the ride mid-word.
  // `by` is the separator the record's own string uses between the title
  // and the artist. When the line FITS, the tail after it is tinted (the
  // handoff wants the artist a step quieter than the title); when it has to
  // ride, it stays one plain string — a marquee measures a nowrap span, and
  // two of them would ride at two speeds.
  function paintMarquee(el, txt, by) {
    if (!el || el.dataset.mq === txt) return;
    el.dataset.mq = txt;
    el.classList.remove('marq');
    el.textContent = txt;
    if (txt && by) tintTail(el, txt, by);
    if (!txt) return;
    requestAnimationFrame(() => {
      if (el.dataset.mq !== txt) return;          // a newer paint won
      // Measured through the nowrap span, never the element: on a phone the
      // title is a one-line clamp that wraps-and-clips instead of
      // overflowing, so the element's own scrollWidth never exceeds its box
      // however long the record's name is (measured 326/326 with a title
      // visibly cut short). The class goes on for the measurement and comes
      // straight back off when the title fits; rAF runs before the paint,
      // so nothing flashes.
      const span = document.createElement('span');
      span.className = 'mq';
      span.textContent = txt;
      el.textContent = '';
      el.appendChild(span);
      el.classList.add('marq');
      const over = span.scrollWidth - el.clientWidth;
      if (over <= 6) {
        el.classList.remove('marq');
        el.textContent = txt;
        if (by) tintTail(el, txt, by);
        return;
      }
      el.style.setProperty('--marq-d', -(over + 14) + 'px');
      // Reading speed, not a fixed clock: a longer ride takes longer.
      el.style.setProperty('--marq-t', Math.max(7, Math.round(over / 12)) + 's');
    });
  }

  // Point an <img> at a source and let LOADING decide whether it shows.
  //
  // The card polls every twenty seconds and repaints from the same payload,
  // so anything that says `img.hidden = false` on the way past will keep
  // un-hiding a picture that has already failed — which is what put the
  // browser's own broken-image glyph in the station row and on the player's
  // sleeve on a station whose art the browser cannot fetch (operator's
  // phone, 2026-09-04). The load state is the only honest answer, and it
  // survives a repaint: complete + naturalWidth is what a failed image looks
  // like afterwards, and onerror is what it looks like at the time.
  function showWhenLoaded(img, src, onFail) {
    if (!img) return;
    if (!src) { img.hidden = true; return; }
    if (img.getAttribute('src') !== src) {
      img.hidden = true;
      img.onload = () => { img.hidden = false; };
      img.onerror = () => { img.hidden = true; if (onFail) onFail(); };
      img.src = src;
      return;
    }
    // Same source as last time: say nothing new, just do not contradict what
    // the browser already found out.
    if (img.complete) img.hidden = !img.naturalWidth;
  }

  // The artist, a step quieter than the title. textContent first, then the
  // split — so a title carrying the separator inside it can never inject
  // markup, and a line with no separator is simply left alone.
  function tintTail(el, txt, by) {
    const at = txt.indexOf(by);
    if (at < 0) return;
    el.textContent = txt.slice(0, at);
    const rest = document.createElement('span');
    rest.className = 'npby';
    rest.textContent = txt.slice(at);
    el.appendChild(rest);
  }

  // THE STATION ROW (design handoff, 2026-09-03). What is on, as its own
  // band under the identity it belongs to: the record's art, a label, the
  // title, the heart, a level. It was the last line of the identity block,
  // where a thumbnail had nowhere to go and the heart shared a row with a
  // title that was already ellipsising.
  //
  // Assembled HERE rather than in index.html because the markup is shared
  // with the embed, where a 62px band is most of a 320px card — the band
  // collapses back to the one line it always was under body.compact, and
  // that is a stylesheet's job once the nodes exist. Nothing here takes an
  // id that index.html does not already declare except the two the script
  // reads back by name, which it creates itself (the widget contract test
  // reads `.id =` for exactly this).
  function buildStationRow() {
    const who = document.querySelector('.who-row');
    const trackrow = document.querySelector('.trackrow');
    if (!who || !trackrow || document.querySelector('.stationrow')) return;
    // IN THE BOOTH, over the name: the row says whose picture that is
    // before the name has been read.
    const meta = who.querySelector('.meta');
    if (meta) {
      const cap = document.createElement('span');
      cap.className = 'whocap';
      cap.textContent = 'In the booth';
      meta.insertBefore(cap, meta.firstChild);
    }
    const band = document.createElement('div');
    band.className = 'stationrow';
    band.hidden = true;
    const art = document.createElement('span');
    art.className = 'stationart';
    const fill = document.createElement('span');
    fill.className = 'stationfill';
    fill.setAttribute('aria-hidden', 'true');
    const img = document.createElement('img');
    img.id = 'npArt';
    img.alt = '';
    img.hidden = true;
    img.setAttribute('aria-hidden', 'true');
    art.append(fill, img);
    const col = document.createElement('span');
    col.className = 'stationmeta';
    const lab = document.createElement('span');
    lab.className = 'stationcap';
    lab.textContent = 'On the station';
    const heart = $('npHeart');
    col.append(lab, trackrow);
    band.append(art, col);
    // The heart leaves the title's own row for the band, where it is the
    // square the handoff draws rather than a glyph hanging off a title.
    if (heart) band.appendChild(heart);
    const vu = document.createElement('span');
    vu.id = 'npVu';
    vu.className = 'stationvu';
    vu.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < 7; i++) vu.appendChild(document.createElement('i'));
    band.appendChild(vu);
    who.insertAdjacentElement('afterend', band);
  }
  buildStationRow();

  // The station's level. It reports the record's own progress rather than
  // its audio: the card does not decode the stream, and seven bars moving
  // to nothing would be the one piece of furniture on the card that lies.
  // Off a running record the row sits flat.
  function paintStationVu(pct) {
    const vu = $('npVu');
    if (!vu) return;
    // A station that does not report where the record is leaves this with
    // nothing to report either, and fourteen identical stubs read as a
    // broken element rather than a quiet one.
    vu.hidden = !(pct > 0);
    const bars = vu.children;
    for (let i = 0; i < bars.length; i++) {
      // A standing wave, walked along by the elapsed fraction — the same
      // shape every time the same record is at the same place, so it reads
      // as an instrument rather than as noise.
      const phase = (pct / 8) + (i * 0.9);
      const h = pct <= 0 ? 22
        : 30 + Math.round(Math.abs(Math.sin(phase)) * 68);
      bars[i].style.setProperty('--h', h + '%');
    }
  }

  function paintNowPlaying() {
    const clock = $('npElapsed'), rail = $('npRail'), deck = $('playerView');
    const mmss = (n) => Math.floor(n / 60) + ':' + String(n % 60).padStart(2, '0');
    if (!clock || !rail) return;
    const prog = deck && deck.querySelector('.plprog');
    const nbar = rail.querySelector('.npbar');
    if (!npStart) {
      clock.textContent = '';
      paintStationVu(0);
      rail.style.setProperty('--np-progress', '0%');
      if (nbar) nbar.hidden = true;
      const total = $('npTotal');
      if (total) { total.textContent = ''; total.hidden = true; }
      if (deck) {
        $('plElapsed').textContent = '';
        $('plLen').textContent = '';
        if (prog) prog.hidden = true;
        deck.style.setProperty('--pl-progress', '0%');
        }
      return;
    }
    const secs = Math.max(0, Math.floor(Date.now() / 1000 - npStart));
    // Clamped: a station that reports a stale start (a stopped mixer, a clock
    // out of step) would otherwise count on for ever, and a rail reading 94:12
    // is more obviously broken than one that simply stops at the end.
    const shown = npLength ? Math.min(secs, npLength) : secs;
    const pct = npLength
      ? Math.min(100, (shown / npLength) * 100).toFixed(1) + '%' : '0%';
    rail.style.setProperty('--np-progress', pct);
    paintStationVu(npLength ? (shown / npLength) * 100 : 0);
    // The whole cluster — clock, bar, length — lives and dies together,
    // while the record actually runs. The clock used to stay behind after
    // the bar hid, clamped at the track's full length: a frozen "3:52"
    // beside nothing (operator's phone, 2026-08-25). A record whose length
    // the station never sent keeps the counting clock alone — there is no
    // end to be honest about.
    const ticking = npLength ? secs < npLength + 8 : true;
    clock.textContent = ticking ? mmss(shown) : '';
    const total = $('npTotal');
    if (total) {
      total.textContent = (ticking && npLength) ? mmss(npLength) : '';
      total.hidden = !(ticking && npLength);
    }
    if (nbar) nbar.hidden = !(npLength && ticking);
    // The station player's row follows the same figures. The numbers came
    // BACK 2026-08-31 (operator's ask: current time and song end time by
    // the bar) — but only while the record actually runs, so the pinned
    // "3:37 — 3:37" the 2026-08-24 review reported cannot return: past the
    // length plus a grace the whole row hides, and a record whose length
    // the station never sent keeps the counting clock alone, no bar and no
    // end time to be honest about — same deal as the header cluster above.
    if (deck) {
      const running = !!npLength && secs < npLength + 8;
      $('plElapsed').textContent = ticking ? mmss(shown) : '';
      $('plLen').textContent = running ? mmss(npLength) : '';
      const bar = prog && prog.querySelector('.plbar');
      if (bar) bar.hidden = !running;
      if (prog) prog.hidden = !(running || ticking);
      deck.style.setProperty('--pl-progress', running ? pct : '0%');
      // The header's wall clock rides the same tick while the sheet is up.
    }
  }
  // One second is the right cadence for a clock that shows whole seconds, and
  // it costs one text write; the progress hairline has its own CSS transition
  // so it glides between ticks rather than stepping.
  setInterval(paintNowPlaying, 1000);

  // `live` is what the server last said. `shown` is what is on the card,
  // which is `live` with the settings panel's unsaved preview laid over it —
  // the same object in every case except inside the panel's preview frame.
  let shown = null, preview = null;

  // This copy of the card is a picture of a card, inside the settings page.
  // It paints exactly like the real one and does nothing else: pressing Call
  // in a preview would ring the actual DJ from inside the settings form, and
  // the gear would open the panel inside the panel.
  const previewMode = params.get('preview') === '1';

  // Tune the caller into the station for the duration of the call. The station
  // refuses song requests when nobody is listening, and a caller on the phone
  // isn't pulling the stream — so without this, the people most likely to
  // request something are the ones who can't. Muted by default so it doesn't
  // talk over the DJ.
  function tuneIn() {
    const s = live && live.stream;
    if (!s || !s.tuneIn || !s.url || streamEl) return;
    // The station's published mounts, best first. Not every mount plays in
    // every browser — Safari and opus, most often — so a failure moves to the
    // next one rather than leaving the call with no station behind it.
    const candidates = [s.url].concat(s.alternates || []);
    // The bed under a call, which is one of three slots the shared engine
    // fills — the player sheet below is the second, and the settings page's
    // transport is the third.
    playFirstWorking(candidates, 0, {
      get: () => streamEl,
      set: (el) => { streamEl = el; },
      // Scaled by the caller's own volume from the start — see applyVolume.
      level: stationLevel,
      // Into the call's own graph if it will go. Only while a call is up:
      // between calls there is nothing to marry it to, and an element
      // inside an AudioContext cannot be given back.
      onPlaying: (el) => { if (room) mixStation(el); },
      onDead: () => {},
    });
  }

  function tuneOut() {
    if (!streamEl) return;
    unmixStation();
    try { streamEl.pause(); streamEl.src = ''; } catch (e) {}
    streamEl = null;
  }

  // ------------------------------------------------------- which way out
  // A browser with a live microphone hands the call to the platform's
  // voice-call audio session, and that session routes to the EARPIECE. On a
  // desktop nobody notices. On a phone it means music that was playing out
  // loud goes quiet and private the instant the DJ picks up — which is the
  // right behaviour for a phone call and the wrong one for a radio phone-in
  // you are listening to in a car.
  //
  // There is no one API for this, so this tries what each platform has, in
  // order, and reports honestly whether anything took:
  //
  //   navigator.audioSession  the only standardised lever (Audio Session API,
  //                           Safari-only so far). `play-and-record` is
  //                           specified as the one that may route to the
  //                           receiver rather than the speaker, so wanting the
  //                           loudspeaker means NOT asking for it.
  //   setSinkId               real device selection, where it exists — Chrome
  //                           and Firefox. Not implemented in iOS Safari,
  //                           which is exactly the platform that needs it.
  //
  // Neither is guaranteed. iOS Safari publishes nothing that forces the
  // route, so on an iPhone this is a request, not a command — which is why
  // there is a button rather than only a setting: the caller can always ask
  // again, and on the platforms with no API their own Control Centre or the
  // handset's speaker key is the fallback that always works.
  let onSpeaker = true;

  function audioSessionSupported() {
    return !!(navigator.audioSession && 'type' in navigator.audioSession);
  }

  // Which sink id carries the wanted route. '' is the default device — the
  // loudspeaker everywhere this API exists. The earpiece has no reserved id,
  // so it is found by LABEL among the outputs; labels only exist once the
  // mic permission is granted, which is the only time this matters.
  async function sinkFor(wantSpeaker) {
    if (wantSpeaker) return '';
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      const ear = devs.find((d) => d.kind === 'audiooutput'
        && /earpiece|receiver|handset/i.test(d.label || ''));
      return ear ? ear.deviceId : null;
    } catch (e) { return null; }
  }

  async function routeAudio(toSpeaker) {
    const want = !!toSpeaker;
    let moved = false;

    if (audioSessionSupported()) {
      try {
        // "playback" is the speaker-facing type; "play-and-record" is the one
        // the spec says may be routed to the receiver. The mic keeps
        // capturing either way — the type is a hint about what the page is
        // doing, not a capture permission.
        navigator.audioSession.type = want ? 'playback' : 'play-and-record';
        moved = true;
      } catch (e) { /* the platform kept its own answer */ }
    }

    // Both directions, where a sink exists to name. The old shape only ever
    // asked on the way TO the loudspeaker, so on Chromium the earpiece press
    // routed nothing and only relabelled the button (operator's phone,
    // 2026-08-17). A null sink means this platform has no device to name for
    // that direction — nothing is attempted and `moved` stays honest.
    const sink = await sinkFor(want);
    if (sink !== null && djEl && typeof djEl.setSinkId === 'function') {
      try { await djEl.setSinkId(sink); moved = true; } catch (e) { /* no */ }
    }
    // …and the graph, when the voices are married into it — the element is
    // muted then, so routing it alone would move nothing anybody can hear.
    // AudioContext.setSinkId is Chromium-only; elsewhere this is a no-op and
    // the honest answer is the one `moved` already carries.
    if (sink !== null && fx) {
      const c = ctx();
      if (c && typeof c.setSinkId === 'function') {
        try { await c.setSinkId(sink); moved = true; } catch (e) { /* no */ }
      }
    }

    // The label follows the AUDIO, not the press: flipping it on a refused
    // route is the button lying about where the sound is.
    if (moved) onSpeaker = want;
    paintSpeakerBtn();
    return moved;
  }

  // Offered on a PHONE, and nowhere else.
  //
  // The problem it solves only exists on a handset: a device with an earpiece
  // held to your head, and a platform that decides a live microphone means
  // you want that earpiece. A laptop has no earpiece to be moved to, so the
  // button there is a control for a thing that cannot happen. In an embed it
  // is worse than useless — the widget is a card in somebody else's column,
  // and a row of call-handling buttons in it is furniture the host page did
  // not ask for.
  //
  // Then, and only then, the platform has to give us something to pull with.
  // A button that provably cannot move the audio anywhere is worse than no
  // button: the caller presses it, nothing happens, and they conclude the
  // call is broken rather than that their browser is old.
  //
  // Note the split. Whether the platform can be ASKED is one question and
  // whether this surface should show a BUTTON is another — an embed on a
  // phone has exactly the same earpiece problem, so it still gets the
  // loudspeaker by default. It just doesn't get a control for it.
  function platformCanRoute() {
    return audioSessionSupported()
      || (window.HTMLMediaElement
          && typeof HTMLMediaElement.prototype.setSinkId === 'function')
      // The graph's own sink, for the calls where both voices are inside it.
      || (window.AudioContext
          && typeof window.AudioContext.prototype.setSinkId === 'function');
  }

  // Whether the platform will actually MOVE audio, which is a different
  // question from whether the function exists: Chrome on Android ships
  // setSinkId while the Android platform cannot re-route an individual
  // stream (the Chrome team's own words). The tell has two shapes, and the
  // first probe only knew one of them. Listing NO audiooutput devices was
  // caught 2026-08-17; listing exactly ONE (the unnamed "default") slipped
  // through, and that is most Android Chromes — the button showed, the
  // press found no earpiece to name, nothing was attempted, and the honest
  // label rule meant it "did nothing to toggle" (the operator's PWA report,
  // 2026-08-18). A route needs two ends, so the probe now wants TWO named
  // outputs before it believes the platform. Probed at load, re-probed when
  // the devices change (a Bluetooth headset arriving is a routing change)
  // and again once the mic permission lands, because that is the moment
  // device labels become readable at all.
  let canRoute = null;
  async function probeRouting() {
    if (audioSessionSupported()) canRoute = true;
    else if (!platformCanRoute()) canRoute = false;
    else {
      try {
        const devs = await navigator.mediaDevices.enumerateDevices();
        canRoute = devs.filter((d) => d.kind === 'audiooutput').length >= 2;
      } catch (e) { canRoute = false; }
    }
    paintSpeakerBtn();
  }

  function offerSpeakerButton() {
    return !framed
      && matchMedia('(pointer: coarse)').matches
      && canRoute === true;
  }

  function paintSpeakerBtn() {
    const b = $('spkBtn');
    if (!b) return;
    b.hidden = !offerSpeakerButton();
    b.textContent = onSpeaker ? 'Speaker' : 'Phone';
    b.setAttribute('aria-pressed', onSpeaker ? 'true' : 'false');
    // Coloured for the EARPIECE, not the speaker. Loudspeaker is the normal
    // state here and normal states are not warnings; the earpiece is the one
    // worth noticing, because it is the one where a caller who put the phone
    // down can no longer hear the DJ.
    b.classList.toggle('on', !onSpeaker);
  }
  paintSpeakerBtn();
  probeRouting();
  if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
    navigator.mediaDevices.addEventListener('devicechange', () => {
      probeRouting();
      // A device list that changes mid-call is what a Bluetooth profile
      // flip looks like from here — see the route-change recovery ladder.
      checkAudioAlive('devices changed');
    });
  }

  // While the panel is previewing a line-box state (preview frames only),
  // the live status is BANKED rather than painted — every real setStatus
  // lands in the held copy, so blur restores the newest truth instead of
  // whatever was on screen when the operator clicked into the field.
  let lineboxHeld = null;

  function setStatus(text, state) {
    if (lineboxHeld) {
      lineboxHeld.text = text;
      lineboxHeld.dot = 'dot' + (state ? ' ' + state : '');
      return;
    }
    statusText.textContent = text;
    dot.className = 'dot' + (state ? ' ' + state : '');
    // The idle board's example line steps aside while the card is actually
    // saying something — the two share the middle of the box, and a refusal
    // printed into the example's lap read as overlap (operator's phone).
    const box = $('lineBox');
    if (box) box.classList.toggle('saying', !!String(text || '').trim());
  }

  function lineboxPreview(text) {
    const box = $('lineBox');
    if (text == null) {
      if (lineboxHeld) {
        statusText.textContent = lineboxHeld.text;
        dot.className = lineboxHeld.dot;
        if (box) box.classList.toggle('saying', !!lineboxHeld.text.trim());
        lineboxHeld = null;
      }
      return;
    }
    if (!lineboxHeld) {
      lineboxHeld = { text: statusText.textContent, dot: dot.className };
    }
    statusText.textContent = fillWords(String(text));
    if (box) box.classList.toggle('saying', !!statusText.textContent.trim());
  }

  // Which DOM the panel's data-spot names reach. Per-element where the card
  // has one thing to point at, block-level (actions, card) where the row
  // describes a region. The photo has two faces — image, or initials.
  const SPOT_TARGETS = {
    ask: '#helpBtn', theme: '#themeBtn', gear: '#gearBtn', signin: '#signinBtn',
    link: '#linkBtn', photo: '#djAvatar, #djMono', show: '#djShow',
    tag: '#djTagline', now: '#npTrack', line: '#lineBox', ptt: '#pttBtn',
    call: '#callBtn', chat: '#chatBtn', vm: '#vmBtn',
    actions: '.actionrow', card: '.card',
  };

  function spotlight(name) {
    document.querySelectorAll('.spot').forEach((el) => el.classList.remove('spot'));
    const sel = SPOT_TARGETS[String(name || '')];
    if (!sel) return;
    document.querySelectorAll(sel).forEach((el) => el.classList.add('spot'));
  }

  // The other direction: clicking an element on the previewed card tells
  // the panel, which flashes and scrolls to the block that owns it. Capture
  // phase, because most of the card's controls are inert in a preview and
  // never let a click bubble. A real page reports nothing.
  if (previewMode) {
    document.addEventListener('click', (e) => {
      if (!e.target || !e.target.closest) return;
      let hit = 'card';
      for (const k of Object.keys(SPOT_TARGETS)) {
        if (k === 'card') continue;
        if (e.target.closest(SPOT_TARGETS[k])) { hit = k; break; }
      }
      try {
        window.parent.postMessage({ type: 'subwave-callin:spot', el: hit },
          location.origin);
      } catch (err) { /* not framed — nothing to tell */ }
    }, true);
  }

  // What the Call button says at rest. Resolved SERVER-side — the operator
  // may have typed a label, or asked for the live DJ's name, and the rule for
  // which wins belongs in one place rather than in each of the four spots
  // here that put the button back to its resting state.
  // {station}, {dj}, {show}, {track} and {tagline}, filled from the live
  // card state — the same shorthand the voicemail greeting takes, offered
  // everywhere a fixed string can be overridden. Unknown braces vanish.
  function fillWords(text) {
    const d = shown || live || {};
    const map = {
      station: d.station || 'the station',
      dj: d.name || 'the DJ',
      show: d.show || '',
      track: (d.track && d.track.title) || (typeof d.track === 'string' ? d.track : '') || '',
      tagline: d.tagline || '',
    };
    return String(text || '').replace(/\{(\w+)\}/g, (m, k) =>
      Object.prototype.hasOwnProperty.call(map, k.toLowerCase())
        ? map[k.toLowerCase()] : '').replace(/  +/g, ' ').trim();
  }

  function word(key, fallback) {
    const w = ((shown || live || {}).wording) || {};
    return fillWords(w[key] || fallback);
  }

  // Who the transcript says is talking. "DJ" is the generic default; with
  // transcript_dj_name on it is the persona's own name, which reads better on
  // a station whose listeners know the roster and follows the name as the
  // show changes (operator's ask, 2026-08-12). Falls back to DJ whenever the
  // name is not known yet — a label that flickers to blank mid-call would be
  // worse than the generic one.
  function djLabel() {
    const l = shown || live || {};
    if (!l.transcriptDjName) return 'DJ';
    // Same field the card's headline uses (`name`) — one source, so the
    // transcript can never disagree with the name printed above it.
    const name = String(l.name || '').trim();
    return name || 'DJ';
  }

  // What a door shows — its word, its icon, or both — read per feature from
  // /live (callShowWords / callShowEmoji, and the vm/chat pairs). The words
  // themselves are still the wording overrides; this only decides whether an
  // icon rides in front and whether the word shows at all. A door left with
  // NEITHER falls back to its word: a blank button is never the right answer,
  // and an embed that turned both off should still be usable.
  function showParts(feature) {
    const src = shown || live || {};
    let words = src[feature + 'ShowWords'];
    let emoji = src[feature + 'ShowEmoji'];
    if (words === undefined) words = true;      // first paint, before /live
    if (emoji === undefined) emoji = false;
    if (!words && !emoji) words = true;         // never blank
    return { words: !!words, emoji: !!emoji };
  }

  // Line icons in the card's own ink — the same choice the rate buttons and
  // the corner controls already made, and for the same reason the operator
  // gave: an emoji glyph is a colour block the theme cannot touch (a yellow
  // phone on a slate card) and it renders differently on every OS. These
  // inherit currentColor, scale cleanly, and read identically everywhere.
  const BTN_ICONS = {
    phone: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    mail: '<circle cx="6" cy="14" r="4"/><circle cx="18" cy="14" r="4"/><line x1="6" y1="10" x2="18" y2="10"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  };
  // The photograph opens. It is the one image on the card and it is small
  // by necessity — the identity row has three lines of type to carry beside
  // it — so a tap gives it the room the card cannot (operator's ask, every
  // view). Built once, on demand, and closed by anything: the backdrop, the
  // button, or Escape.
  function openPortrait() {
    const img = $('djAvatar');
    if (!img || img.classList.contains('hidden') || !img.src) return;
    let pop = document.getElementById('portraitPop');
    if (!pop) {
      pop = document.createElement('div');
      pop.id = 'portraitPop';
      pop.className = 'portraitpop';
      pop.innerHTML = '<button class="ppclose" aria-label="Close">&times;</button>'
        + '<img alt="" /><span class="ppname"></span>';
      document.querySelector('.card').appendChild(pop);
      pop.addEventListener('click', (e) => {
        if (e.target === pop || e.target.classList.contains('ppclose')) closePortrait();
      });
    }
    pop.querySelector('img').src = img.src;
    pop.querySelector('.ppname').textContent = $('djName').textContent || '';
    pop.hidden = false;
    document.addEventListener('keydown', portraitEsc);
  }
  function closePortrait() {
    const pop = document.getElementById('portraitPop');
    if (pop) pop.hidden = true;
    document.removeEventListener('keydown', portraitEsc);
  }
  function portraitEsc(e) { if (e.key === 'Escape') closePortrait(); }
  ['djAvatar', 'djMono'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('click', openPortrait);
    el.setAttribute('role', 'button');
    el.setAttribute('tabindex', '0');
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPortrait(); }
    });
  });

  function iconSvg(name) {
    return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" '
      + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
      + 'stroke-linejoin="round" aria-hidden="true">'
      + (BTN_ICONS[name] || '') + '</svg>';
  }

  // Paint an idle action button for a feature (call | vm | chat), isolating
  // the icon in its own span so it can be sized independently of the word.
  // The icon markup is a constant from BTN_ICONS, never operator input, so
  // innerHTML on that span is safe; the operator's WORD is a text node and
  // can never become markup. In-call states (Ringing, Hang up) never call
  // this — they stay plain text.
  function setBtn(el, feature, icon, text) {
    if (!el) return;
    const st = showParts(feature);
    el.textContent = '';
    if (st.emoji) {
      const e = document.createElement('span');
      e.className = 'btnemoji';
      e.innerHTML = iconSvg(icon);
      el.appendChild(e);
    }
    if (st.words) {
      el.appendChild(document.createTextNode((st.emoji ? ' ' : '') + text));
    }
    // The NAME survives the operator turning the word off. An icon-only door
    // was reaching a screen reader as "button", with nothing else to go on:
    // the glyph is the only content and it is aria-hidden, deliberately, so
    // dropping the text node dropped the accessible name with it. Two of the
    // three ways into this product — Text the booth and Leave a message —
    // were shipped that way on the deployment (checked 2026-08-16). Always
    // set, not only when icon-only, so the two can never disagree.
    el.setAttribute('aria-label', text);
    // And a tooltip for the sighted half of the same problem: an icon-only
    // door gives a first-time caller no way to find out what it does short of
    // pressing it. Only when the word is gone — a title that repeats a visible
    // label is noise.
    if (st.emoji && !st.words) el.setAttribute('title', text);
    else el.removeAttribute('title');
    // An ICON-ONLY door is just its glyph — it should hug the icon and give
    // the row's slack to a worded door beside it, not sit at an equal third
    // (operator: "Call Danny Boy" was clipping while two bare icons took the
    // same width). The class lets the CSS size the two cases apart.
    el.classList.toggle('icononly', st.emoji && !st.words);
    if (st.words) fitLabel(el);
    // If every door on the row carries a WORD as well as an icon, there is no
    // room for the primary to take two thirds and equal thirds is the honest
    // answer. Decided here rather than in a media query, because what fits is
    // a function of the labels the operator typed. See .actionrow.allworded.
    const row = el.closest('.actionrow');
    if (row) {
      const doors = ['callBtn', 'chatBtn', 'vmBtn']
        .map((id) => document.getElementById(id))
        .filter((b) => b && !b.hidden);
      row.classList.toggle('allworded',
        doors.length === 3 && doors.every((b) => b.textContent.trim().length));
      // The accent belongs to the LEFTMOST door, not to Call wherever it
      // lands (operator: "the one to the left should always be blue not"
      // " just pinned to call the booth"). Marked here because which door
      // is first depends on which are hidden, and CSS cannot ask that of a
      // sibling it has already passed.
      for (const b of doors) b.classList.remove('lead');
      if (doors.length) doors[0].classList.add('lead');
    }
  }

  // Long wording SHRINKS to fit rather than ellipsising away (operator's ask,
  // 2026-08-12: "if their text isn't fitting, shrink the text in their button
  // boxes"). Wrapping was the other option and is wrong here — every door
  // shares one row at a pinned height, so a second line would change the
  // card's geometry, which the compact embed reports to its host page.
  //
  // Measured rather than guessed at with a media query: what overflows is a
  // function of the WORD the operator typed, not of the viewport.
  const LABEL_MIN_PX = 9;
  function fitLabel(el) {
    el.style.fontSize = '';
    // Zero width means the card is not laid out yet (hidden door, first
    // paint) — leave it alone; the next repaint measures for real.
    if (!el.clientWidth) return;
    // Fits as it is: leave no inline size behind, so the stylesheet keeps
    // owning the button and a later theme or width change is free to differ.
    if (el.scrollWidth <= el.clientWidth) return;
    const base = parseFloat(getComputedStyle(el).fontSize) || 13;
    for (let px = base - 0.5; px >= LABEL_MIN_PX; px -= 0.5) {
      el.style.fontSize = px + 'px';
      if (el.scrollWidth <= el.clientWidth) return;
    }
    // Still too long at the floor: the ellipsis in the CSS takes it from
    // here, which is the honest end of the road for pathological wording.
  }

  function callLabel() {
    return fillWords((shown && shown.callLabel) || 'Call the DJ');
  }

  // The card is only ever in ONE mode, and it shows only that mode's
  // controls — the CSS keys every band off `data-mode`. Before this, opening
  // the text line left the call's meters, its push-to-talk bar and the
  // Call/Message buttons all on screen under the input row: the card grew to
  // ~525px and the place to type was buried in the middle of controls for a
  // call nobody was on (operator-reported). Setting the mode here is the one
  // switch; the stylesheet does the hiding.
  //   idle       the doors (the action row); nothing live
  //   call       state · captions · meters · push-to-talk · Hang up
  //   voicemail  state · captions · a level to watch · Hang up — no talk bar
  //   chat       the transcript and the input, and Close — no call controls
  function setCardMode(m) {
    const card = document.querySelector('.card');
    if (card) card.dataset.mode = m;
    // The LISTEN chip belongs to the idle card alone; every mode change is a
    // chance for it to appear or get out of the way.
    paintListenChip();
  }

  // Which way round the three doors sit. Written as flex `order` rather than
  // by moving nodes: the row is a flex container, the buttons are shown and
  // hidden by other rules that would fight a reparent, and an `order` write is
  // idempotent — a poll that repeats it costs nothing and moves nothing. Hang
  // up is not in the list; it takes the whole row on a live call.
  function applyDoorOrder(order) {
    const doors = { call: 'callBtn', chat: 'chatBtn', vm: 'vmBtn' };
    (Array.isArray(order) && order.length ? order : ['call', 'chat', 'vm'])
      .forEach((name, i) => {
        const el = $(doors[name]);
        if (el) el.style.order = String(i);
      });
  }

  // Is somebody actually mid-conversation on this card — a call, a voicemail
  // or an open text line? Read off the mode the card already keeps rather
  // than a second flag: `room` alone would miss the text line, which is the
  // surface the DJ-changed-under-me report came from.
  function inConversation() {
    const card = document.querySelector('.card');
    return !!card && card.dataset.mode !== 'idle';
  }

  // ------------------------------------------------------- embed height
  // A host page sizes its iframe before the widget knows whether it has to
  // ask for a door code, warn about the microphone, or open captions — so a
  // fixed height clips all three. We report our real height and embed.js
  // follows it (unless the host pinned one).
  //
  // Called explicitly from everything that changes the card's height rather
  // than left to the ResizeObserver alone: observer callbacks ride animation
  // frames, which don't run in a background tab, so an embed the visitor
  // isn't looking at would size itself late.
  let lastPosted = 0;
  let measuring = false;

  function notifyHeight() {
    // Silent while the ask list has the frame overlaid: the frame is
    // deliberately taller than the widget just now, and reporting that back
    // would make the host adopt the overlay's height permanently.
    if (!framed || measuring || askRoom) return;
    // The BODY, not the card: the card has an inset around it inside the
    // frame, and reporting the card alone handed back a height 20px short of
    // what the widget actually occupies, so the frame clipped its own bottom
    // edge. Measuring the body keeps that number in the stylesheet where it
    // belongs.
    //
    // Measure the CONTENT height, never the height we were handed. Idle, the
    // card stretches to fill a tall host column so the Call button can sit at
    // the bottom of it — and a stretched card reports the frame's own height
    // straight back to the frame, after which it can only ever grow. The
    // class drops the stretch for one synchronous read; the guard keeps the
    // ResizeObserver that watches this element from re-entering.
    measuring = true;
    document.body.classList.add('measuring');
    const h = Math.ceil(document.body.getBoundingClientRect().height);
    document.body.classList.remove('measuring');
    measuring = false;
    if (h > 0 && h !== lastPosted) {
      lastPosted = h;
      window.parent.postMessage({ type: 'subwave-callin:height', px: h }, '*');
    }
  }

  if (framed) {
    if (window.ResizeObserver) {
      new ResizeObserver(notifyHeight).observe(document.querySelector('.card'));
    }
    addEventListener('load', notifyHeight);
    notifyHeight();
  }



  // Browsers only allow the microphone on HTTPS or localhost. When this page
  // can't capture audio, say so IN PLACE with a clickable way out, instead of
  // a call that dies cryptically. The server tells us where the TLS front
  // door is (live.secureOrigin, derived from its own config).
  function updateMicHelp() {
    const el = $('micHelp');
    if (!el) return;
    const micBlocked = !window.isSecureContext || !navigator.mediaDevices
      || !navigator.mediaDevices.getUserMedia;
    if (!micBlocked) { el.hidden = true; return; }
    const so = (live && live.secureOrigin) || '';
    el.innerHTML = '';
    if (so && so !== location.origin) {
      el.append('Calls need microphone access, which browsers only allow on a secure page. ');
      const a = document.createElement('a');
      a.href = so; a.target = '_top';
      a.textContent = 'Open the secure page: ' + so;
      el.appendChild(a);
      const alt = document.createElement('span');
      alt.className = 'alt';
      alt.textContent = 'First visit shows a one-time certificate screen (Advanced → Proceed). '
        + 'LAN testing alternative: chrome://flags → “Insecure origins treated as secure” → add '
        + location.origin + '.';
      el.appendChild(alt);
    } else if (window.self !== window.top && location.protocol === 'https:') {
      // The widget is on https and STILL not a secure context, which means an
      // ancestor is not: a secure context requires the WHOLE chain, so an
      // https iframe inside an http page is insecure and the microphone is
      // refused. Saying "this page (https://…) is not HTTPS" is both wrong and
      // unactionable — the frame's own URL is the one thing that is fine, and
      // the fix belongs to the page doing the embedding.
      el.append('Calls need microphone access. This widget is on ' + location.origin
        + ', which is fine — but it is embedded in a page served over http://, '
        + 'and a browser only grants the microphone when EVERY page in the chain '
        + 'is secure. Serve the embedding page over https and this clears. '
        + 'See README → Troubleshooting.');
    } else {
      el.append('Calls need microphone access, which browsers only allow on HTTPS or localhost — '
        + 'this page (' + location.origin + ') has neither. See README → Troubleshooting.');
    }
    el.hidden = false;
    notifyHeight();
  }

  // ------------------------------------------------------------- on air card
  // One painter for both homes of the listener count — the header line and
  // the player's Now-playing ribbon. The number is only ever a number
  // (textContent), the mark is static markup, and absent stays hidden.
  function paintListeners(spanId, numId, d) {
    const el = $(spanId);
    if (!el) return;
    const n = d && typeof d.listeners === 'number' && d.listeners >= 0;
    el.hidden = !n;
    if (n) {
      $(numId).textContent = d.listeners;
      el.setAttribute('aria-label', d.listeners + ' listening');
    }
  }

  function paintOffAir(reason) {
    $('eyebrow').className = 'eyebrow off';
    $('eyebrowText').textContent = reason === 'offline' ? 'Station offline' : 'Off air';
    // No count on a quiet or unreachable station — a number next to "Off
    // air" would be counting nobody.
    $('eyebrowListeners').hidden = true;
    $('djName').textContent = reason === 'offline' ? 'Unreachable' : 'Nobody on air';
    $('djShow').textContent = '';
    $('djTagline').textContent = reason === 'offline'
      ? 'Cannot reach the station.' : 'No DJ is live right now.';
    paintMarquee($('npTrack'), '');
    // …and the clock with it. A rail counting up under "Nobody on air" is the
    // card insisting a record is playing while it says the station is dark.
    npStart = 0; npLength = 0; paintNowPlaying();
    $('djAvatar').classList.add('hidden');
    // Off air is exactly what the answering machine is for — but not
    // offline: an unreachable station cannot take delivery either. And not
    // paused: the kill switch closes the machine along with the booth, so
    // a paused off-air line says closed rather than offering the recorder.
    const flags = shown || live || {};
    const paused = !!flags.callsPaused;
    // An un-set-up line has no machine either — the server refuses the
    // voicemail mint along with everything else until a password exists.
    const unset = !!flags.needsSetup;
    const vmButton = vmPolicy() !== 'never' && reason !== 'offline' && !paused
      && !unset && !!(framed ? flags.embedVmBtn : flags.vmBtn);
    $('vmBtn').hidden = !vmButton || !!room;
    callBtn.dataset.vm = '';
    if (reason !== 'offline' && !paused && !unset
        && vmPolicy() !== 'never' && !room && !vmButton) {
      callBtn.disabled = false;
      callBtn.dataset.vm = '1';
      callBtn.textContent = 'Leave a message';
      return;
    }
    callBtn.disabled = true;
    callBtn.textContent = unset && reason !== 'offline' ? 'Line not set up'
      : paused && reason !== 'offline' ? word('closed', 'Line closed')
      : reason === 'offline' ? 'Station offline' : 'Nobody to call';
  }

  // The idle card's buttons, painted from one place — the 20s poll AND the
  // refusal path both land here. The refusal path used to hand-restore the
  // Call button and forget the message button: one failed call on a
  // voicemail-only line left the card stuck on a door that can only fail
  // until the page was reloaded. Operator-reported, from the live page.
  // Whether the card, right now, has NO working door at all — the kill
  // switch is down, or both transmission modes are off. One flag, read by
  // paintIdleButtons and by the idle status line, so the button and the
  // sentence under it cannot tell two different stories.
  let lineClosedNow = false;

  function paintIdleButtons(d) {
    if (room) return;
    // First-run: the server refuses every mint until an admin password
    // exists (0.10.78), so every door here is honestly dead — one disabled
    // button, and the setup ask above it says what opens the line.
    if (d.needsSetup) {
      $('vmBtn').hidden = true;
      if ($('chatBtn')) $('chatBtn').hidden = true;
      callBtn.hidden = false;
      callBtn.disabled = true;
      callBtn.dataset.vm = '';
      callBtn.textContent = 'Line not set up';
      return;
    }
    const needsCode = !!d.guestRequired && !callKey();
    const machineOn = vmPolicy() !== 'never';
    // The kill switch outranks the machine: paused means the booth answers
    // NOTHING — /token refuses the voicemail mint too — so the card says
    // closed instead of offering a door that can only fail. Live calls off
    // with no machine behind them is the same closed line reached through
    // the two mode switches instead of the one big one.
    lineClosedNow = !!d.callsPaused
      || (d.liveCalls === false && !machineOn);
    // The machine answers where a live call cannot: 'closed' turns each
    // refusal below into "Leave a message"; 'always' (or live calls
    // switched off) makes the line voicemail-only.
    const vmOnly = machineOn && !lineClosedNow
      && (vmPolicy() === 'always' || d.liveCalls === false);
    const vmHere = vmOnly
      || (machineOn && !lineClosedNow && vmPolicy() === 'closed' && !d.onAir);
    // The phone-in switch: only when the server says the door is open AND
    // working (setting on, mixer answering its probe), never behind a code
    // gate, and never on a voicemail-only line with no live door for it to
    // modify. Hidden means OFF — a switch the caller cannot see must never
    // stay silently armed from an earlier paint.
    // The two on-air doors, separately: a live CALL needs the mixer, an
    // on-air VOICEMAIL only needs the studio, and each has its own quick
    // kill on the dashboard. The switch stands while EITHER door is open;
    // a door that is shut simply stays private whatever the route says.
    const oaDoors = d.onAirCalls || {};
    const callsLive = !!oaDoors.calls;
    const vmGoesLive = !!oaDoors.voicemail;
    // …and never to a caller whose tier can't open it. The mint always
    // refused (allow_on_air is a tier row); the card offered the switch
    // anyway and the refusal was a dead end with no path to the code —
    // "several times it wouldn't let me" (operator, signed out on their own
    // phone, 2026-08-18). `mine` is per-request truth from /live; absent
    // means an older server, which keeps the old behaviour.
    const onAirHere = (callsLive || vmGoesLive)
      && !lineClosedNow && !needsCode && !vmOnly
      && oaDoors.mine !== false;
    if (!onAirHere) onAirPick = false;
    // The 4c word switch (design handoff, 2026-08-17): route is the single
    // source of truth, and the segment, the stage frame, the stage message
    // and the Call button all derive from it in this one paint.
    if ($('routeSwitch')) {
      $('routeSwitch').hidden = !onAirHere;
      $('routeOn').classList.toggle('on', onAirPick);
      $('routeOn').setAttribute('aria-checked', onAirPick ? 'true' : 'false');
      $('routeOff').classList.toggle('on', !onAirPick);
      $('routeOff').setAttribute('aria-checked', onAirPick ? 'false' : 'true');
      const cardEl = document.querySelector('.card');
      cardEl.classList.toggle('routed', onAirHere);
      cardEl.classList.toggle('route-on', onAirHere && onAirPick);
      cardEl.classList.toggle('route-off', onAirHere && !onAirPick);
      // The CTA only dresses live when a live CALL is actually on offer —
      // an on-air route with only the voicemail door open keeps the booth
      // button private and lets the message door carry the coral.
      cardEl.classList.toggle('cta-live', onAirHere && onAirPick && callsLive);
    }
    // The operator can put the machine on the card as its own button,
    // per surface. With the button up, Call never morphs — two clear
    // doors beat one door with a changing sign.
    const vmButton = machineOn && !lineClosedNow
      && !!(framed ? d.embedVmBtn : d.vmBtn) && !needsCode
      && d.voicemailMine !== false;
    $('vmBtn').hidden = !vmButton;
    if (vmButton) setBtn($('vmBtn'), 'vm', 'mail',
                         onAirHere && onAirPick && vmGoesLive
                           ? word('vm_button_live', 'Record for air')
                           : word('vm_button', 'Leave a message'));
    // The text line's door, same rules as the machine's: the kill switch
    // outranks it, the door code gates it, and it is per-surface. Never
    // hidden mid-chat — the input row is the conversation.
    const chatButton = !!d.chatEnabled && !lineClosedNow
      && !!(framed ? d.embedChatBtn : d.chatBtn) && !needsCode
      && d.chatMine !== false;
    if ($('chatBtn')) {
      $('chatBtn').hidden = !chatButton || chatOpen;
      if (chatButton) setBtn($('chatBtn'), 'chat', 'chat', word('chat_button', 'Text the booth'));
    }
    callBtn.hidden = false;
    callBtn.dataset.vm = '';
    // The text line's send button is written in the markup rather than set
    // per state, so its override lands here — once /live has been read, on
    // every repaint, which is where every other word on the card is decided.
    const sendBtn = $('chatSendBtn');
    if (sendBtn) sendBtn.textContent = word('send', 'Send');
    if (lineClosedNow) {
      // A closed line is a deliberate state, not a fault: one disabled
      // button, and the status line under the card says the booth is not
      // taking calls — before asking for a code that would open nothing.
      callBtn.disabled = true;
      callBtn.textContent = word('closed', 'Line closed');
    } else if (needsCode) {
      callBtn.disabled = true;
      callBtn.textContent = 'Enter the code';
    } else if (vmOnly && vmButton) {
      // Voicemail-only with the machine's own button up: ONE door. A live
      // Call button here can only ring out into a refusal — which is
      // exactly what the operator watched happen.
      callBtn.hidden = true;
    } else if (vmHere && !vmButton) {
      callBtn.disabled = false;
      callBtn.dataset.vm = '1';
      setBtn(callBtn, 'vm', 'mail',
             onAirHere && onAirPick && vmGoesLive
               ? word('vm_button_live', 'Record for air')
               : word('vm_button', 'Leave a message'));
    } else {
      callBtn.disabled = false;
      // The route re-labels and re-dresses the one door rather than adding
      // another: solid coral when the call broadcasts, the cool outline for
      // the private line (the CSS keys off .route-on/.route-off). Without
      // the switch, the operator's own label stands as ever.
      // A HANDSET EITHER WAY. The private route used to swap in the speech
      // bubble, which put a chat glyph on a button that says CALL THE BOOTH
      // — and the text line has its own door on the same row wearing that
      // very icon. Colour is what tells the two routes apart, and it always
      // was; the glyph was saying something the label contradicts.
      setBtn(callBtn, 'call', 'phone',
             onAirPick && callsLive ? word('call_live', 'Call in live')
               : onAirHere ? word('call_offair', 'Call the booth')
               : callLabel());
    }
  }

  // When /live last answered. A phone throttles background timers to
  // nothing, so the 20s poll simply stops while the screen is off — the
  // card then shows whatever record was playing when the phone was locked
  // (operator's report, 2026-08-31: "old songs, many times"). The
  // visibility hook below refreshes the moment the page is looked at again,
  // and this stamp keeps one failed poll from blanking a card that was
  // healthy seconds ago.
  let lastLiveAt = 0;

  async function refreshLive() {
    try {
      // Send the stored code so /live resolves canAsk, callerTier and the
      // sign-in chip for THIS caller's tier — without it a caller holding a
      // guest code still saw only the open-tier menu, because the server
      // resolves tiers from this header and the widget never sent it.
      const r = await fetch('/live', callKey()
        ? { headers: { 'X-Call-Key': callKey() } } : undefined);
      if (!r.ok) throw new Error('unreachable');
      const d = await r.json();
      lastLiveAt = Date.now();
      const first = !live;
      live = d;
      // The kiosk clock: a stored code past the operator's ceiling is
      // forgotten before anything else reads it, and the door re-locks.
      if (callKeyExpired(d.guestSessionMinutes)) rememberCallKey('');
      paintLive(preview ? Object.assign({}, d, preview) : d, first);
    } catch (e) {
      // ONE failed poll against a card that answered seconds ago is a blip —
      // a slow station read, a phone waking its radio — not an outage. Hold
      // the truth we have and let the next poll settle it; blanking to
      // "Station unreachable" on every hiccup was the operator's "sometimes
      // it shows no song at all". Ninety seconds of silence is a real
      // outage and paints as one.
      if (Date.now() - lastLiveAt < 90000 && live) return;
      live = live || {};
      // The controls still have to appear. An unreachable station is the case
      // where the operator most needs the gear, and driving the corner
      // controls off /live means a failed /live would otherwise leave the
      // card with no way into settings at all.
      applyControls(null);
      paintOffAir('offline');
      setStatus('Station unreachable', 'error');
    }
  }

  // The phone throttles background timers to nothing, so the poll stops the
  // moment the screen locks — coming back must not mean reading a record
  // that ended three songs ago for up to 20 more seconds. bfcache restores
  // ride the same hook.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !room) refreshLive();
  });
  window.addEventListener('pageshow', (e) => {
    if (e.persisted && !room) refreshLive();
  });

  // After a call ends the on-air show may have JUST changed: a takeover the
  // caller asked for lands at the next TRACK BOUNDARY, not the moment the tool
  // fires, and the station only pushes a cache-bust (track.play) when the new
  // show actually airs. The ordinary 20s poll can then leave the card on the
  // old DJ and palette for most of a minute — operator-reported, and worst on a
  // short voicemail where the whole interaction is over before the record is.
  // A brief faster poll catches the handover within a few seconds of it airing.
  // Idle-only and self-cancelling, so it never runs during a call or forever.
  let burstTimer = null;
  function burstLive(secs = 40, every = 4000) {
    if (burstTimer) clearInterval(burstTimer);
    const until = Date.now() + secs * 1000;
    burstTimer = setInterval(() => {
      if (room || Date.now() > until) { clearInterval(burstTimer); burstTimer = null; return; }
      refreshLive();
    }, every);
  }

  // A DJ's initials for the no-image ring: first and last word, or the first
  // two letters of a single-word name. "Sergeant Fred Colon" -> "SC".
  function monogram(name) {
    const words = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!words.length) return '';
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return (words[0][0] + words[words.length - 1][0]).toUpperCase();
  }

  function paintLive(d, first) {
    try {
      // What is actually on screen, which is `live` plus any preview overlay
      // on top of it. Everything that asks "what does the card say right now"
      // has to read THIS, not `live` — in the panel's preview those two
      // deliberately differ, and reading the wrong one is how a preview ends
      // up half-applied.
      shown = d;
      // Operator choices that shape the card itself, applied once — /live is
      // polled, and re-running these every few seconds would fight the
      // viewer's own theme toggle and rebuild the popup under their finger.
      // A preview repaint passes first=true deliberately: changing those
      // choices IS what it is for.
      if (first) {
        // The operator's skin, EXPERIMENTAL. Here rather than in the poll for
        // the same reason as the theme: re-applying it every few seconds
        // would restart the idle artefact's animation on every read. A host
        // page that pinned ?skin= has already decided and is left alone.
        if (!skinForced) applySkin(d.skin);
        applyDoorOrder(d.doorOrder);
        applyConfiguredTheme(d.theme, d.stationTheme);
        setupAskPopup(d.canAsk);
        applyControls(d);
        paintThemeGlyph();
        // Seeds the palette change-detector so the second poll does not
        // read the load-time palette as a show change. Any tokens it re-sets
        // are the ones the line above just applied.
        followStationPalette(d);
        lastCanAsk = askSignature(d);
        // The operator can make the player the page's FRONT — it opens
        // without the wipe (this is the starting face, not a transition)
        // and QUIET: PLAY starts the music, never the page turn. ONCE per
        // page load: this block re-runs when the ask-set changes, and it
        // was re-opening the sheet over a caller who had deliberately
        // pulled the phone down — the top ribbon read as dead because
        // every exit was being undone (operator, 2026-09-01).
        if (!playerStartApplied && d.playerStart && playerOffered()
            && !inConversation()) {
          playerStartApplied = true;
          const sheet = $('playerView');
          sheet.classList.add('dragging');    // no slide: this is the start
          openPlayer();
          void sheet.offsetHeight;
          sheet.classList.remove('dragging');
        }
      } else {
        followStationPalette(d);
        // A tier change (signing in or out) changes what this caller may ask
        // and which corner controls apply — rebuild those, but ONLY when the
        // signature actually changed, so an ordinary poll never rebuilds the
        // popup under the caller's finger. This is what makes sign-out drop the
        // on-air group the same way sign-in added it. The signature carries the
        // TIER as well as the menu: an admin whose permissions happen to match
        // a guest's still needs the "for the operator" label, and without the
        // tier in the key a guest→admin switch left the popup saying "for guest
        // callers" until the page was reloaded (operator-reported 2026-08-10).
        const nowCanAsk = askSignature(d);
        if (nowCanAsk !== lastCanAsk) {
          lastCanAsk = nowCanAsk;
          setupAskPopup(d.canAsk);
          applyControls(d);
        }
      }
      // The sound engine lives in shared.js and is fed rather than read from,
      // so the panel can preview a sound without borrowing the call's state.
      setSounds(d.sounds);
      // Seeded on the first read and on an operator change, never on every
      // poll. This ran unconditionally, and /live is polled every 20 seconds
      // while the card is idle: a listener who turned the music down in the
      // player had it snap back to the configured default — usually 100% —
      // within the poll, over and over (operator-reported 2026-08-20). A call
      // was the only thing that stopped it, because `room` was the only guard.
      // Now the listener's own hand is the last word for the session.
      const cfgVolume = typeof d.sounds?.volume === 'number' ? d.sounds.volume : null;
      if (cfgVolume != null && cfgVolume !== lastCfgVolume) {
        lastCfgVolume = cfgVolume;
        // A preview is the operator dragging that very setting, so it lands
        // there whatever the fader in the preview frame has been doing.
        if (!room && (!volTouched || previewMode)) {
          setVolume(cfgVolume);
          $('volSlider').value = getVolume();
          applyVolume();
        }
      }

      // The station player follows every poll: the ribbon and chip appear or
      // go as the operator's switch and the stream come and go, and an open
      // sheet repaints for a record change without the caller doing anything.
      paintListenChip();
      // Only once /live has landed: whether the player is offered at all is
      // the server's answer, and the stream URL arrives with it.
      if (first) resumeFromHandoff();
      // The card heart's un-press and the player's operator side both read
      // the same server answer — fetched once the line is known, not only
      // at sheet-open, so the phone face has it too.
      if (first) fetchAbilities();
      if (playerOpen) { paintPlayer(); fitPlayerArt(); }
      if (playerEl) feedMediaSession();
      castFollowTrack();

      if (!d.reachable) { paintOffAir('offline'); return; }
      if (!d.onAir)     { paintOffAir('offair');  return; }

      $('eyebrow').className = 'eyebrow';
      // The listener count rides the ON AIR line — text on furniture the
      // card already has, so the height promise holds. Zero included: the
      // operator's call (2026-08-18), reversing the old one-listener floor.
      // Absent stays absent: no number is painted when the station won't
      // say or the row is switched off. Its own element with a line-drawn
      // mark since the same day — the emoji-in-text version pushed the
      // number under the player's pull tab on a narrow phone, where it was
      // simply not visible.
      $('eyebrowText').textContent = 'On air';
      paintListeners('eyebrowListeners', 'eyebrowListenersN', d);
      // The NAME is never switchable: a call card that doesn't say who
      // answers isn't a call card. Everything below it is the operator's
      // call, per surface. Emptied rather than hidden — these are text nodes
      // whose parent collapses on its own once they carry nothing.
      const parts = cardParts(d);
      // WHO YOU ARE TALKING TO DOES NOT CHANGE UNDER YOU. A show handover
      // mid-conversation used to rewrite the name, show and tagline on the
      // next poll, so the person you were three lines into a chat with became
      // somebody else while the voice — resolved once when the conversation
      // started — carried on as the DJ you rang. The card was the half that
      // was lying. Identity is repainted only from an idle card; the record,
      // the clock, the palette and everything else keep following the
      // station, which is the operator's ask: the colours may change, the DJ
      // may not. It catches up on the first poll after the line clears.
      // The one-bad-poll grace (see npHeld). Previews are the operator's own
      // fiction and never seed or spend it.
      if (d.track && !previewMode) {
        npHeld = { track: d.track, show: d.show || '', at: Date.now(),
                   start: d.trackStartedAt || 0, secs: d.trackSeconds || 0 };
      }
      const ghost = (!d.track && !previewMode && npHeld
                     && Date.now() - npHeld.at < 65000) ? npHeld : null;
      if (!inConversation()) {
        // The word the screensaver skin bounces. The station does not send its
        // own name, so the SHOW is the closest thing to a station brand the
        // card has — "THE PIAZZA" out of "THE PIAZZA · Golden-Era Pop" — and
        // the DJ's name backs it up when there is no show. Written even when
        // no skin displays it: it costs one assignment and it means turning
        // the skin on never has to wait for the next poll to say anything.
        const word = $('skinWord');
        if (word) {
          word.textContent = String(d.show || d.name || 'ON AIR')
            .split('·')[0].trim().slice(0, 22);
        }
        $('djName').textContent = d.name || 'The DJ';
        $('djShow').textContent = parts.show === false ? ''
          : (d.show || (ghost ? ghost.show : ''));
        $('djTagline').textContent = parts.tagline === false ? '' : (d.tagline || '');
      }
      const rec = d.track ? d : (ghost
        ? { track: ghost.track, trackStartedAt: ghost.start, trackSeconds: ghost.secs }
        : null);
      // No glyph in front of it any more: the band's own label says what
      // this line is, and the note was the only emoji-shaped thing left on
      // the card. The embed keeps it — there the line still has to say what
      // it is on its own.
      const trackTxt = (parts.track === false || !rec) ? ''
        : (compact ? '♪ ' + rec.track : rec.track);
      paintMarquee($('npTrack'), trackTxt, compact ? '' : ' — ');
      const band = document.querySelector('.stationrow');
      if (band) band.hidden = !trackTxt;
      // The record's art, at thumb size, with the sleeve's own bloom. The
      // same /cover proxy the player sheet reads, so a station the browser
      // cannot reach directly still paints.
      // Behind the square either way: the striped fill is what a record with
      // no art the browser can reach is supposed to look like.
      showWhenLoaded($('npArt'),
                     (trackTxt && d.nowPlaying && d.nowPlaying.art) || '');
      // The rail's clock and progress hairline. /live sends WHEN the record
      // started and how long it runs; the elapsed figure is counted here
      // rather than sent, because /live is cached across every caller for a
      // few seconds — a baked-in elapsed would be stale by up to that much
      // and would tick backwards on the next poll.
      npStart = (parts.track === false || !rec) ? 0 : (rec.trackStartedAt || 0);
      npLength = rec ? (rec.trackSeconds || 0) : 0;
      paintNowPlaying();
      // The heart follows the same per-surface visibility as the track line
      // it sits beside: a surface whose operator hid the record shows no
      // heart for it either.
      paintCardHeart(parts.track === false ? { cardLike: false } : d);

      // Shape is one answer for both surfaces — an embed and the page show
      // the same photograph, and nobody has ever wanted it round in one and
      // square in the other. Re-read on every poll rather than once, because
      // it costs an attribute write and it means changing it in the panel
      // shows up on the card within the poll instead of on reload.
      document.querySelector('.card').dataset.avatar =
        d.avatarStyle === 'square' ? 'square' : 'round';
      // Whether this surface runs push to talk. Re-read each poll like the
      // avatar shape — but never mid-call, where flipping the bar out from
      // under a caller would reopen a mic they believe is shut.
      if (!room) document.querySelector('.card').classList.toggle('ptt', pttOn());

      // Which way out the next call starts. Never mid-call: the caller may
      // have pressed the button themselves, and a poll landing 20 seconds
      // later has no business overruling them.
      if (!room) { onSpeaker = d.speakerDefault !== false; paintSpeakerBtn(); }

      // THE PHOTOGRAPH IS IDENTITY TOO. The name, show and tagline have been
      // held for the length of a conversation since 0.10.140, and this block
      // was not — so a show handover mid-chat left the old DJ's name above a
      // NEW DJ's face (operator screenshot, 0.10.145). The initials path had
      // the same fault twice over: monogram() reads the live name, so a new
      // persona with no picture put their initials in the ring beside the
      // previous DJ's name.
      //
      // The whole block, not just the src: which surface shows a photo at all
      // is an operator setting, but it decides it from `parts`, and half a
      // repaint is how the two halves disagreed in the first place. It
      // catches up on the first poll after the line clears, like the rest.
      if (!inConversation()) {
        const img = $('djAvatar'), mono = $('djMono');
        // Initials in the ring when there's no usable image. showMono is also the
        // fallback for the station's 1x1 placeholder: it is a valid PNG, so it
        // LOADS rather than erroring, and without the size check below it stretched
        // one pixel across the whole avatar and read as an empty circle
        // (radio.drearburh.uk, a persona with no image — diagnosed 2026-08-10).
        const showMono = () => {
          img.classList.add('hidden');
          if (mono) {
            const ini = monogram(d.name);
            mono.textContent = ini;
            mono.classList.toggle('hidden', !ini);
          }
        };
        const showImg = () => { if (mono) mono.classList.add('hidden'); img.classList.remove('hidden'); };
        if (parts.avatar === false) {
          // Avatar turned off for this surface — no image AND no initials.
          img.classList.add('hidden');
          if (mono) mono.classList.add('hidden');
        } else if (d.avatar) {
          img.alt = d.name || 'DJ';
          img.onerror = showMono;
          img.onload = () =>
            (img.naturalWidth <= 2 || img.naturalHeight <= 2) ? showMono() : showImg();
          if (img.dataset.pid !== d.personaId) {
            img.dataset.pid = d.personaId || '';
            img.src = d.avatar + '?v=' + (d.personaId || '');  // fires onload/onerror
          } else if (img.complete && img.src) {
            img.onload();   // src unchanged on a poll repaint — re-judge in place
          }
        } else { showMono(); }
      }

      // One place decides what the Call button says and whether it works.
      // Split across two blocks, the later one silently undid the earlier.
      if (!room) paintIdleButtons(d);
      paintGuestGate();
      updateMicHelp();

      // Several station reads in a row have failed server-side: the card
      // still paints from cache, but the operator should see it's limping
      // rather than discovering thin prompts later.
      // `!inConversation()` and not just `!room`: the soundbite studio holds
      // no room, so the poll blanked its instruction line 20s in and painted
      // the idle board over a caller mid-take (operator's screenshot,
      // 2026-08-17). The studio's own status writes are not the poll's to
      // overwrite.
      if (!room && !inConversation()) {
        if (d.degraded) {
          setStatus('Station responding slowly — some info may be stale', 'connecting');
        } else if (lineClosedNow) {
          // A closed line explains itself in a sentence, not just a dead
          // button — "Line closed" alone left callers wondering whose fault
          // it was. Deliberate state, quiet colour, never 'error'.
          setStatus("The booth isn't taking calls at the moment", '');
        } else {
          setStatus('');
        }
        paintBoard(d);
      }
    } catch (e) {
      // A repaint that throws must not take the poll down with it — the next
      // one would never be scheduled and the card would be frozen on
      // whatever it was showing.
      console.warn('Talk Wave: could not paint the card —', e);
    }
  }

  // ---------------------------------------------------------- the door code
  // Optional: set a guest password in Settings → Security and the booth line
  // only opens for people you gave the code to. Deliberately separate from
  // the panel password — this buys you the phone, not the controls.
  // CALL_KEY and callKey() live in shared.js — the panel writes the code this
  // reads, so neither surface can be the one that owns it.

  // Visibility only — refreshLive owns the Call button, so the two can't
  // fight over it.
  // The card knows when the gate is up, as a class CSS can reach: the route
  // switch straddles the stage's top edge, and with the gate open on a phone
  // the keyboard compresses the 100dvh card until the switch sits ON the
  // code input — UNLOCK half-hidden behind ON AIR (operator screenshot,
  // 2026-08-18). While someone is typing a code, the switch stands down.
  function syncGateClass() {
    const card = document.querySelector('.card');
    if (card) card.classList.toggle('gated', !$('guestGate').hidden);
  }

  function paintGuestGate() {
    const box = $('guestGate');
    // signinMode keeps the gate open when the caller opened it themselves to
    // climb a tier — otherwise a poll's repaint would snap it shut mid-type.
    if (box) box.hidden = !signinMode && !(live && live.guestRequired && !callKey());
    // The close button belongs to a VOLUNTARY sign-in only. When the line
    // itself demands a code there is nothing behind the gate to go back to,
    // and an X that reopens on the next poll is worse than no X at all.
    const x = $('guestClose');
    if (x) x.hidden = !signinMode;
    syncGateClass();
    notifyHeight();
  }

  async function submitGuestCode() {
    const input = $('guestPw'), msg = $('guestMsg');
    const pw = input.value.trim();
    if (!pw) return;
    msg.textContent = 'Checking…';
    try {
      const r = await fetch('/auth/guest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: pw }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { msg.textContent = d.error || 'That code is not right.'; return; }
      rememberCallKey(pw);
      // The lock exists exactly when a code is stored — reported missing
      // until reload because nothing re-ran the corner controls here.
      applyControls(shown || live);
      input.value = ''; msg.textContent = '';
      $('guestGate').hidden = true;
      syncGateClass();
      await refreshLive();
    } catch (e) { msg.textContent = 'Could not check that just now.'; }
  }

  // The same code entry, opened deliberately to CLIMB a tier rather than
  // because the line demanded a code to call. Any code the caller has —
  // guest or the admin password — is tried the same way: store it, re-read
  // /live (which resolves the tier from it), and keep it only if the tier
  // actually rose. That accepts both without the widget needing to know
  // which is which. (signinMode is declared with the other call state above.)
  function openSignin() {
    signinMode = true;
    const box = $('guestGate'), input = $('guestPw'), msg = $('guestMsg');
    if (box) box.hidden = false;
    // "The booth line is private" is the LOCKED-OUT wording and it read as a
    // contradiction to someone already mid-conversation with the booth. When
    // this is a voluntary sign-in, say what it is.
    const label = $('guestLabel');
    if (label) label.textContent = 'Sign in for more of what you can ask for.';
    if (msg) msg.textContent = 'Enter the guest code or admin password.';
    if (input) {
      input.placeholder = 'Guest code or admin password';
      input.focus();
      // After the phone keyboard has finished arriving — focus() fires
      // before the viewport resize, and a scroll issued then lands wrong.
      setTimeout(() => {
        try { input.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
        catch (e) { /* an old browser scrolls however it likes */ }
      }, 250);
    }
    // Nothing to close back to when the line itself demands a code — see
    // closeSignin.
    const x = $('guestClose');
    if (x) x.hidden = false;
    syncGateClass();
    notifyHeight();
  }

  // Only ever closes a VOLUNTARY sign-in. A gate the line is demanding (no
  // code, private line) has nothing behind it to go back to, so it has no
  // close button at all — refreshLive puts it straight back up.
  function closeSignin() {
    signinMode = false;
    const box = $('guestGate'), input = $('guestPw'), msg = $('guestMsg');
    if (input) input.value = '';
    if (msg) msg.textContent = '';
    if (box) box.hidden = true;
    syncGateClass();
    notifyHeight();
  }

  async function submitSignin() {
    const input = $('guestPw'), msg = $('guestMsg');
    const pw = input.value.trim();
    if (!pw) return;
    const rank = { open: 0, guest: 1, admin: 2 };
    const before = rank[(live && live.callerTier) || 'open'] || 0;
    msg.textContent = 'Checking…';
    rememberCallKey(pw);                 // store, then let /live judge the tier
    await refreshLive();
    const after = rank[(live && live.callerTier) || 'open'] || 0;
    if (after > before) {
      input.value = ''; msg.textContent = '';
      $('guestGate').hidden = true;
      syncGateClass();
      signinMode = false;
      const tier = (live && live.callerTier) || 'guest';
      setStatus(tier === 'admin'
        ? 'Signed in as admin — more options unlocked'
        : 'Signed in — more options unlocked', 'connected');
      // The ask menu and corner controls were already rebuilt by the
      // refreshLive above (paintLive rebuilds on a canAsk change).
    } else {
      rememberCallKey('');               // a wrong code leaves nothing behind
      await refreshLive();
      msg.textContent = 'That code did not unlock anything — check it and try again.';
    }
  }

  function gateSubmit() { return signinMode ? submitSignin() : submitGuestCode(); }

  if ($('signinBtn')) $('signinBtn').onclick = openSignin;
  // The ask list lives on the CARD (and an embed's host has to make room
  // for it), so the sheet's copy steps aside and presses the real one —
  // no second popup, no second anchoring problem.
  if ($('plHelpBtn')) {
    $('plHelpBtn').onclick = () => {
      closePlayer(true);
      setTimeout(() => { const b = $('helpBtn'); if (b) b.click(); }, 80);
    };
  }
  if ($('gdHelpBtn')) {
    $('gdHelpBtn').onclick = () => {
      showFace('phone');
      setTimeout(() => { const b = $('helpBtn'); if (b) b.click(); }, 80);
    };
  }
  // The player head's copy forwards; the overlay itself lives on the card.
  if ($('plSigninBtn')) {
    $('plSigninBtn').onclick = () => {
      closePlayer(true);
      openSignin();
    };
  }
  if ($('gdSigninBtn')) {
    $('gdSigninBtn').onclick = () => { showFace('phone'); openSignin(); };
  }
  if ($('guestClose')) $('guestClose').onclick = closeSignin;

  if ($('guestBtn')) {
    $('guestBtn').onclick = gateSubmit;
    $('guestPw').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') gateSubmit();
    });
  }

  // ------------------------------------------------------------ level meters
  // A spectrum, not a progress bar. §4.6 had this as one trough and one fill,
  // which is cheaper and honest about level — but a horizontal bar that grows
  // is the shape of a download, and what this has to say is "there is a voice
  // here". The segmented meter says it at a glance.
  //
  // The reason it was collapsed in the first place was cost: a height written
  // per segment per meter per animation frame. paintMeter writes one only when
  // the rounded value actually changed, which in practice is a handful of the
  // sixteen on any given frame.
  // A WAVEFORM per side, 16 bands each, not a level bar.
  //
  // 0.10.131 replaced this with one centre-out bar on the reasoning that the
  // only question the row is asked is "is there a voice here, and whose". The
  // operator put it back and was right about why: the product is called Talk
  // Wave, the waveform IS the thing, and a bar that grows is the shape of a
  // download. A voice moving across sixteen bands reads as a voice; the same
  // voice as one width reads as a progress meter.
  //
  // The cost that got the spectrum removed the FIRST time is paid the way it
  // always was — a segment is written only when its rounded height actually
  // changed, so silence costs nothing.
  const SEGMENTS = 16;
  function buildBars(host) {
    host.innerHTML = '';
    for (let i = 0; i < SEGMENTS; i++) host.appendChild(document.createElement('i'));
  }
  buildBars($('barsYou')); buildBars($('barsDj'));

  function analyserFor(mediaStreamTrack) {
    if (!mediaStreamTrack) return null;
    try {
      const c = ctx();
      const src = c.createMediaStreamSource(new MediaStream([mediaStreamTrack]));
      const an = c.createAnalyser();
      an.fftSize = 256; an.smoothingTimeConstant = 0.75;
      src.connect(an);
      return an;
    } catch (e) { return null; }
  }

  let anYou = null, anDj = null;
  const bufYou = new Uint8Array(128), bufDj = new Uint8Array(128);


  // The floor a silent segment sits at, as a percentage of the meter's
  // height. Not zero: the meter's own shape has to be visible before anyone
  // speaks, or the row looks broken until it isn't. It reads as silence
  // rather than as quiet because off-call the segments are the trough colour
  // — a CSS rule keyed on .rig.on, not something painted here.
  const FLOOR = 12;

  // Only the bottom half of the FFT is worth looking at: speech has next to
  // nothing above ~8kHz, and mapping the whole range put six dead segments on
  // the right of every meter.
  const BAND_TOP = 64;

  // Returns the overall level, 0..1, and paints the bands as a side effect.
  // One pass over the buffer for both — the caller needs the level for the
  // avatar glow and the bands for the meter, and reading the analyser twice
  // per frame is how they used to disagree.
  function paintMeter(host, an, buf, active) {
    const kids = host.children;
    if (!an || !active) {
      for (let i = 0; i < kids.length; i++) {
        if (kids[i].style.height !== FLOOR + '%') kids[i].style.height = FLOOR + '%';
      }
      return 0;
    }
    an.getByteFrequencyData(buf);
    const per = BAND_TOP / SEGMENTS;
    let total = 0;
    for (let s = 0; s < SEGMENTS; s++) {
      let sum = 0;
      const from = Math.floor(s * per), to = Math.floor((s + 1) * per);
      for (let i = from; i < to; i++) sum += buf[i];
      const band = (sum / Math.max(1, to - from)) / 255;
      total += band;
      // Rounded to whole percent before it is compared: the analyser jitters
      // in the third decimal even in silence, and without this every segment
      // is a style write on every frame.
      const h = Math.round(FLOOR + Math.min(1, band * 2.6) * (100 - FLOOR));
      const px = h + '%';
      if (kids[s].style.height !== px) kids[s].style.height = px;
    }
    return Math.min(1, (total / SEGMENTS) * 2.6);
  }

  function tick() {
    paintMeter($('barsYou'), anYou, bufYou, !muted);
    const dj = paintMeter($('barsDj'), anDj, bufDj, true);
    $('djAvatar').classList.toggle('talking', dj > 0.06);
    rafId = requestAnimationFrame(tick);
  }

  function clearMeters() {
    paintMeter($('barsYou'), null, null, false);
    paintMeter($('barsDj'), null, null, false);
  }
  clearMeters();

  // ------------------------------------------------------------- agent state
  const STATE_TEXT = {
    initializing: 'Connecting', idle: 'Idle', listening: 'Listening',
    thinking: 'Thinking', speaking: 'Speaking', reconnecting: 'Reconnecting',
    // Not an SDK state. The DJ on the call and the DJ on the broadcast are
    // the same person, so while the station has the microphone the call DJ
    // waits — and the caller is told that's what the silence is. "On air"
    // was ambiguous on a card whose header already says ON AIR NOW: it read
    // as the station's state, which never changes during a call, rather than
    // as the reason this particular silence is happening.
    onair: 'Working the booth',
  };

  // Before the DJ has said a word, the SDK's own states describe machinery
  // the caller has no use for: "Thinking" and "Connecting" while a line is
  // ringing look like something has stalled. Everything up to first speech is
  // one thing from the caller's side — the phone is ringing somewhere.
  const REACHING = 'Reaching the booth';

  // The worker sets this participant attribute while the broadcast is live.
  // It outranks the SDK's own state: the DJ may well be "listening" as far as
  // the session is concerned, but what the caller needs to know is that it
  // can't answer yet.
  let djOnAir = false, lastAgentState = 'idle', djHasSpoken = false;

  function paintAgentState() {
    const state = djOnAir ? 'onair' : lastAgentState;
    const chip = $('stateChip');
    chip.dataset.state = state || 'idle';
    // The chip, not `room`: it goes up the moment Call is pressed and comes
    // down in endCall before this runs, so it covers the whole window from
    // ringing to first word. `room` is only set once signalling succeeds,
    // which would leave the actual ring showing "Connecting".
    //
    // On air outranks even this: a caller who dials in mid-link is held
    // before the greeting, and "Reaching the booth" would be the wrong story
    // for a silence the broadcast is causing. So does reconnecting, which is
    // a real fault and must not be dressed up as a ring.
    const reaching = !djHasSpoken && !djOnAir && state !== 'reconnecting'
      && !$('stateChip').hidden;
    $('stateText').textContent =
      reaching ? REACHING : (STATE_TEXT[state] || 'Idle');
  }

  function setAgentState(state) {
    lastAgentState = state || 'idle';
    // Latched, not derived: once the DJ has spoken the call is underway for
    // good, and a later thinking pause is a pause rather than a ring.
    if (state === 'speaking') djHasSpoken = true;
    paintAgentState();
    // The first time the DJ actually speaks, the call is properly underway:
    // the button flips from Answering to a green On the line for the rest
    // of the call.
    if (state === 'speaking' && room && !callBtn.classList.contains('live')) {
      callBtn.classList.remove('ringing', 'answering');
      callBtn.classList.add('live');
      callBtn.textContent = word('online', 'On the line');
    }
  }

  function setOnAir(on) {
    if (on === djOnAir) return;
    djOnAir = on;
    paintAgentState();
    document.querySelector('.card').classList.toggle('onair', on);
    // The caller is on hold, and the controls have to say so — the card used
    // to announce the DJ had stepped away while leaving the bar and the Mute
    // button working exactly as before, so a caller kept talking to nobody.
    const bar = $('pttBtn');
    if (bar) bar.disabled = on;
    if (muteBtn) muteBtn.disabled = on;
    // Repaint the bar's own label — it is the control the caller is looking
    // at, so it is the one that has to say "on hold" rather than go on
    // telling them to tap it.
    paintPtt();
    // A hold that never lifts is worse than an overlap. The worker holds the
    // gate for up to 90s waiting for the station to confirm an action it may
    // never log (air.mark_pending_air), which used to mean only that the DJ
    // stayed quiet — since the mic lock landed it means the CALLER cannot
    // speak either, and one real call sat muted until it was abandoned. This
    // is the floor under that: whatever the worker says, the caller gets the
    // microphone back. The card keeps saying the DJ is on air, because it is.
    clearTimeout(holdTimer);
    if (on) {
      // Remember whether they were mid-sentence, so the hold can hand the
      // microphone back in the state it took it. Proven necessary by a real
      // call (2026-08-13): the caller was live, the station spoke, we shut
      // the mic — and when the air cleared 20s later the bar went back to
      // "tap to talk" and the mic stayed SHUT. The DJ spent the rest of the
      // call telling them to check their microphone and then hung up.
      wasLiveBeforeHold = pttOpen;
      holdTimer = setTimeout(() => {
        if (!djOnAir) return;
        holdExpired = true;
        if (bar) bar.disabled = false;
        if (muteBtn) muteBtn.disabled = false;
        paintPtt();
        addSystemLine('🎙', 'Go ahead',
          'The booth is taking a while up there — say your piece and the DJ '
          + 'will catch up.');
      }, MAX_HOLD_MS);
    } else {
      holdExpired = false;
      // The air is clear. Give the microphone back exactly as it was — a
      // caller who was talking when the broadcast cut in is still in the
      // middle of a sentence, and making them notice an unlit bar and tap it
      // again is how a call dies quietly.
      if (wasLiveBeforeHold && room && !pttOpen) setMicOpen(true);
      wasLiveBeforeHold = false;
    }
    if (on) {
      // Shut whatever is open. A caller mid-press when the broadcast takes
      // the mic must not stay open behind the hold.
      if (pttOpen) setMicOpen(false);
      playSound('hold');
      addSystemLine('📻', 'Back on the broadcast',
        'The DJ has the station mic for a moment — you’re on hold, and '
        + 'your call picks up straight after.');
    } else {
      addSystemLine('🎙', 'Back with you', 'The line is yours again.');
    }
  }

  function watchAgentState(r) {
    const read = (p) => {
      if (!p || !p.attributes) return;
      const s = p.attributes['lk.agent.state'];
      if (s) setAgentState(s);
      // Compared to "1" rather than tested for presence. The worker used to
      // clear this by setting it to "", which LiveKit treats as deleting the
      // attribute — so the key disappeared, the `in` test went false, and
      // setOnAir(false) was never called. The card sat in "Working the booth"
      // for the rest of the call while the worker's own log said the air had
      // been clear for eighty seconds. Absent, empty and "0" now all read as
      // off, so this is right against an old worker as well as a new one.
      setOnAir(p.attributes['talkwave.onair'] === '1');
    };
    r.on(LivekitClient.RoomEvent.ParticipantAttributesChanged, (_changed, p) => read(p));
    r.on(LivekitClient.RoomEvent.ParticipantConnected, read);
    r.remoteParticipants.forEach(read);
  }

  // The worker announces an action the moment the station actually accepts
  // it. Worth its own channel: "I've put that in" from the DJ is a claim,
  // this is the receipt.
  function watchActions(r) {
    const decoder = new TextDecoder();
    r.on(LivekitClient.RoomEvent.DataReceived, (payload, _p, _kind, topic) => {
      // The answering machine publishes what it hears on its own topic, so a
      // caller leaving a message SEES their words land instead of talking
      // into a silent card. Rendered as the caller's own transcript line, the
      // same caption box a call uses; the interim/final flag dims it until it
      // settles, exactly like a live call's captions.
      if (topic === 'vm-heard') {
        let m; try { m = JSON.parse(decoder.decode(payload)); } catch (e) { return; }
        if (!m || !m.text) return;
        $('lineBox').classList.add('open');
        capBox.classList.add('on');
        // The machine publishes each SENTENCE on its own, interim then final.
        // Rewriting one fixed line (as this did) showed only the caller's last
        // sentence — so a message read back as a single stray phrase. Instead a
        // finished sentence retires its line and the next one stacks beneath
        // it, and the caller sees the whole message they're leaving. `force`
        // puts it in the box even on a ticker-mode embed: a voicemail has no
        // other transcript, so this is the only sign it's registering a word.
        addCaption('vm-interim', 'you', m.text, m.final !== false, true);
        if (m.final !== false) {
          capNodes.delete('vm-interim');
          delete lastByWho.you;
        }
        return;
      }
      if (topic && topic !== 'talkwave.action') return;
      let msg;
      try { msg = JSON.parse(decoder.decode(payload)); } catch (e) { return; }
      if (!msg || msg.type !== 'action') return;
      addSystemLine(msg.icon || '✅', msg.label || 'Action completed', msg.detail || '');
    });
  }

  // ---------------------------------------------------------------- captions
  const capNodes = new Map();
  const lastByWho = {};   // { who: {node, text, at} }

  // A new line should register as movement, not as text that was simply
  // different when you looked back (HOST-STYLE-GUIDE §5). The forced reflow
  // is load-bearing: without it the class is still present on the second
  // update and the animation never replays. CSS kills this entirely under
  // prefers-reduced-motion.
  function rollIn(el) {
    el.classList.remove('roll');
    void el.offsetWidth;
    el.classList.add('roll');
  }
  // The ticker row is aligned on the baseline of its text, and an EMPTY line
  // box has no baseline — so the row that was reserving two lines' height
  // measured 4px taller empty than it did with speech in it, and the host
  // frame twitched on the first word of every call. A zero-width space is a
  // line box with a baseline and nothing to read.
  const NBSP_LINE = '​';
  function setLine(el, text, animate) {
    const t = text || NBSP_LINE;
    if (!el || el.textContent === t) return;
    el.textContent = t;
    if (text && animate) rollIn(el);              // never animate the blank
  }

  let tickerTimer = null;
  // What the ticker last showed, so a growing interim line can be told from
  // a new turn. The recogniser rewrites the SAME sentence a few times a
  // second while someone talks, and replaying the rise-and-fade on each
  // rewrite made the line flicker for the whole turn — operator-reported as
  // "volatile, keeps flashing". Only a fresh turn moves now; a line that is
  // merely growing updates in place.
  let tickerWho = '', tickerText = '';
  function showTicker(who, text) {
    const t = $('ticker');
    if (!t) return;
    const fresh = who !== tickerWho
      || !(text.startsWith(tickerText) || tickerText.startsWith(text));
    tickerWho = who; tickerText = text;
    t.querySelector('.who').textContent =
      who === 'dj' ? 'DJ' : (who === 'sys' ? '•' : 'You');
    setLine(t.querySelector('.line'), text, fresh);
    t.hidden = false;
    t.classList.add('show');
    t.classList.toggle('sys', who === 'sys');
    if (tickerTimer) clearTimeout(tickerTimer);
    tickerTimer = setTimeout(() => t.classList.remove('show'), 6000);
  }

  // A transcript line is plain text — EXCEPT a Markdown table, which the DJ
  // uses on the text line for a schedule ("what's on?"). A run-on sentence of
  // eleven shows is unreadable; a table is exactly what a schedule is. Built
  // by hand from text nodes only (never innerHTML) — one of the two speakers
  // here is an LLM and its output is never trusted into the DOM as markup.
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
  function splitRow(line) {
    return line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  }
  function isRow(line) { return line != null && line.indexOf('|') !== -1 && line.trim() !== ''; }

  // Transcript text arrives in bursts — a phrase, a sentence, sometimes a
  // whole paragraph at once — and slapping each burst in whole made the
  // transcript visibly jump ("choppy", the operator's word, 2026-08-18). So
  // arriving text is revealed letter by letter instead, at a rate that adapts
  // to the backlog: a big burst drains in about a fifth of a second, a small
  // one trickles, and the reveal can never fall behind the voice. On by
  // default; ?smooth=0 restores the old instant paint, and a system asking
  // for reduced motion gets it too.
  const SMOOTH_CAPTIONS = params.get('smooth') !== '0'
    && !(window.matchMedia
         && matchMedia('(prefers-reduced-motion: reduce)').matches);

  function smoothTo(el, text) {
    if (!SMOOTH_CAPTIONS) { el.textContent = text; return; }
    const shown = el._shown !== undefined ? el._shown : el.textContent;
    if (text === shown) return;
    if (!text.startsWith(shown)) {
      // A rewrite, not a continuation — an interim transcript correcting
      // itself. Animating a correction would type over words the caller can
      // see are wrong; land it whole.
      el.textContent = text; el._shown = text; el._target = text;
      return;
    }
    el._target = text;
    if (el._typing) return;          // the running loop will reach the target
    el._typing = true;
    const step = () => {
      const target = el._target || '';
      const cur = el._shown !== undefined ? el._shown : '';
      if (cur.length >= target.length) { el._typing = false; return; }
      // Drain a twelfth of the backlog per frame, at least one letter — a
      // sentence lands in ~12 frames whatever its length.
      const n = Math.max(1, Math.round((target.length - cur.length) / 12));
      el._shown = target.slice(0, cur.length + n);
      el.textContent = el._shown;
      followTranscript();            // sticky, not forced — see its comment
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function renderSaid(el, text) {
    // Fast path: no pipe means no table, so nothing to parse — the overwhelming
    // majority of lines, spoken captions included.
    if (text.indexOf('|') === -1) { smoothTo(el, text); el.classList.remove('has-table'); return; }
    // The table path rebuilds the node wholesale; the reveal state must not
    // survive into it or the next spoken burst would "continue" from stale text.
    el._shown = undefined; el._target = undefined;
    const lines = String(text).split('\n');
    el.textContent = '';
    let tabled = false;
    let prose = [];
    const flush = () => {
      if (!prose.length) return;
      const span = document.createElement('span');
      prose.forEach((ln, i) => {
        if (i) span.appendChild(document.createElement('br'));
        span.appendChild(document.createTextNode(ln));
      });
      el.appendChild(span);
      prose = [];
    };
    for (let i = 0; i < lines.length; i++) {
      // A table is a header row, a |---|---| separator, then body rows.
      if (isRow(lines[i]) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
        flush();
        const head = splitRow(lines[i]);
        i += 2;
        const body = [];
        for (; i < lines.length && isRow(lines[i]) && !TABLE_SEP.test(lines[i]); i++) {
          body.push(splitRow(lines[i]));
        }
        i--;   // the for-loop's own i++ will step past the last consumed row
        const wrap = document.createElement('div');
        wrap.className = 'tablewrap';
        const table = document.createElement('table');
        table.className = 'chattable';
        const thead = document.createElement('thead');
        const htr = document.createElement('tr');
        head.forEach((c) => { const th = document.createElement('th'); th.textContent = c; htr.appendChild(th); });
        thead.appendChild(htr);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        body.forEach((cells) => {
          const tr = document.createElement('tr');
          for (let c = 0; c < head.length; c++) {
            const td = document.createElement('td');
            td.textContent = cells[c] != null ? cells[c] : '';
            tr.appendChild(td);
          }
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        el.appendChild(wrap);
        tabled = true;
      } else {
        prose.push(lines[i]);
      }
    }
    flush();
    // The .said span is inline; a block table inside it can't lay out until the
    // span becomes a block, so flag it for the stylesheet to switch.
    el.classList.toggle('has-table', tabled);
  }

  function addCaption(id, who, text, final, force) {
    if (!text) return;
    // `force` overrides the caption mode: a voicemail has no DJ captions and no
    // audible reply, so the caller's own words must land in the box even when
    // the card is otherwise in ticker mode — the transcript IS the receipt.
    if (!force && captionsMode !== 'full' && !chatOpen) {
      if (captionsMode === 'ticker') showTicker(who, text);
      return;
    }
    capBox.classList.add('on');
    let node = capNodes.get(id);

    // A turn can arrive as an interim stream and then a final one, each with
    // its own id, which rendered the same sentence twice. If the last thing
    // this speaker said is the same text (or this is a longer version of it)
    // and it was moments ago, treat it as the same turn.
    if (!node) {
      const prev = lastByWho[who];
      const fresh = prev && (Date.now() - prev.at) < 8000
        && prev.node.parentNode === capBox;
      if (fresh && (prev.text === text || text.startsWith(prev.text)
                    || prev.text.startsWith(text))) {
        node = prev.node;
        capNodes.set(id, node);
      }
    }

    if (!node) {
      node = document.createElement('p');
      node.className = 'cap ' + who;
      node.innerHTML = '<span class="who"></span><span class="said"></span>';
      node.querySelector('.who').textContent = who === 'dj' ? djLabel() : 'You';
      capBox.appendChild(node);
      capNodes.set(id, node);
      // Only a NEW turn rises in. An interim transcript rewrites the same
      // node every few hundred ms, and replaying the animation on each of
      // those would shake the line while someone is still speaking.
      rollIn(node);
    }
    renderSaid(node.querySelector('.said'), text);
    node.classList.toggle('interim', !final);
    lastByWho[who] = { node, text, at: Date.now() };
    followTranscript();
    while (capBox.children.length > 40) capBox.removeChild(capBox.firstChild);
  }

  // A system action, not speech: a song going into the queue, a message
  // reaching the air, a segment starting. It gets its own line in the
  // timeline, styled apart from the conversation, because the caller
  // otherwise has only the DJ's word that anything happened.
  function addSystemLine(icon, label, detail, force) {
    if (captionsMode === 'off' && !force) return;
    // In a chat, an action receipt belongs IN the transcript, styled apart —
    // never in the fading ticker. On a ticker-mode embed it used to flash up
    // over the page and vanish a few seconds later ("scheduled…" popping up
    // OUTSIDE the text box and disappearing, operator-reported on a live chat),
    // because this lacked the `chatOpen` guard addCaption already has.
    if (!force && captionsMode !== 'full' && !chatOpen) {
      showTicker('sys', label + (detail ? ' — ' + detail : ''));
      return;
    }
    capBox.classList.add('on');

    const node = document.createElement('p');
    node.className = 'cap sys';
    node.innerHTML = '<span class="ico"></span><span class="said">'
      + '<span class="what"></span><span class="detail"></span></span>';
    node.querySelector('.ico').textContent = icon;
    node.querySelector('.what').textContent = label;
    node.querySelector('.detail').textContent = detail || '';
    capBox.appendChild(node);
    followTranscript();
    // A system line must not be merged into the next spoken turn.
    delete lastByWho.dj;
    while (capBox.children.length > 40) capBox.removeChild(capBox.firstChild);
  }

  function wireCaptions(r) {
    // Newer clients deliver transcripts as text streams; older ones fire an
    // event. Bind ONLY ONE — a client that supports both would otherwise
    // render every line twice under two different ids.
    let streamed = false;
    try {
      r.registerTextStreamHandler('lk.transcription', async (reader, participant) => {
        const local = participant?.identity === r.localParticipant?.identity;
        const who = local ? 'you' : 'dj';
        const id = reader.info?.id || ('t' + Date.now() + Math.random());
        let acc = '';
        for await (const chunk of reader) { acc += chunk; addCaption(id, who, acc, false); }
        addCaption(id, who, acc, true);
      });
      streamed = true;
    } catch (e) { streamed = false; }

    const evt = LivekitClient.RoomEvent.TranscriptionReceived;
    if (!streamed && evt) {
      r.on(evt, (segments, participant) => {
        const local = participant?.identity === r.localParticipant?.identity;
        segments.forEach((s) => addCaption(s.id, local ? 'you' : 'dj', s.text, s.final));
      });
    }
  }

  // -------------------------------------------------------------------- call
  // The slider is the call's volume, not the DJ's. Turning it down used to
  // leave the station playing underneath at its own fixed level — so at the
  // bottom of the range the music was all you could hear. The station keeps
  // its configured proportion of whatever the caller has chosen.
  // ------------------------------------------------------- voice effects
  // A radio colour on the DJ's voice, built from the raw WebRTC track in the
  // caller's own browser — the broadcast never hears it. The element is
  // muted and the processed graph is the only audible path, with its own
  // gain so the volume slider keeps working.
  //
  // Honest caveat, stated in the setting's help too: audio through an
  // AudioContext plays on the default output, so the phone's
  // speaker/earpiece switch has nothing to route while an effect is on.
  let fx = null;

  function voiceEffect() {
    return ((shown || live || {}).voiceEffect) || 'none';
  }

  function distCurve(amount) {
    const n = 512, curve = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const x = (i * 2) / n - 1;
      curve[i] = ((3 + amount) * x * 20 * (Math.PI / 180))
        / (Math.PI + amount * Math.abs(x));
    }
    return curve;
  }

  // freq window + grit per effect. Numbers chosen by ear against the three
  // things people actually mean: a phone line, a CB rig, a handheld.
  const FX = {
    telephone:  { hp: 300, lp: 3400, grit: 0 },
    cb:         { hp: 400, lp: 2500, grit: 26 },
    walkie:     { hp: 500, lp: 2800, grit: 55 },
    am:         { hp: 200, lp: 4800, grit: 12 },
    megaphone:  { hp: 500, lp: 4000, grit: 70 },
    underwater: { hp: 40,  lp: 500,  grit: 0 },
    stadium:    { hp: 300, lp: 5000, grit: 35 },
    intercom:   { hp: 800, lp: 2600, grit: 45 },
    shortwave:  { hp: 600, lp: 2200, grit: 30 },
    lofi:       { hp: 60,  lp: 6500, grit: 8 },
  };

  // The operator's intensity dial, 0-100: 100 is the effect as designed,
  // lower interpolates the filters back toward the clean voice, and 0 is no
  // effect at all — same maths the panel's Test with effect uses.
  function fxSpec() {
    const spec = FX[voiceEffect()];
    if (!spec) return null;
    const lvl = (shown || live || {}).voiceEffectLevel;
    const t = Math.max(0, Math.min(100, lvl == null ? 100 : lvl)) / 100;
    if (t <= 0) return null;
    return {
      hp: spec.hp * t,
      lp: spec.lp + (16000 - spec.lp) * (1 - t),
      grit: Math.round(spec.grit * t),
    };
  }

  // ONE OUTPUT FOR BOTH VOICES.
  //
  // The station is an <audio> element and the DJ is a WebRTC track, and a
  // phone treats those as two different KINDS of sound: the music goes out on
  // the media session at media volume, the call goes out on the voice session
  // at call volume. On a handset that is merely odd. In a car it splits in
  // two — the music on A2DP through the speakers, the DJ through the
  // hands-free profile — at two unrelated levels, which is what the operator
  // heard.
  //
  // The web has no API for "put this element on the voice session", so the
  // only way to marry them is to stop being two players: both sources go into
  // ONE AudioContext and out of one destination. The effect graph already did
  // this for the DJ; this extends it to the station and makes it the path
  // whenever both are audible at once.
  //
  // Two things can refuse. A stream served without CORS headers cannot enter
  // Web Audio at all (createMediaElementSource on a tainted element outputs
  // silence, which would be a call with no station and no error), so the
  // element is loaded with crossOrigin first and retried plain if that fails —
  // and a plain one is never mixed. And AudioContext.setSinkId is Chromium-
  // only, so where it is missing the speaker switch keeps working on the
  // element path instead. Both fall back to exactly what happened before.
  let stationMix = null;      // { src, gain } while the station is in the graph

  function mixStation(el) {
    if (stationMix || !el || el.dataset.cors !== 'ok') return false;
    try {
      const c = ctx();
      const src = c.createMediaElementSource(el);
      const gain = c.createGain();
      gain.gain.value = stationLevel();
      src.connect(gain); gain.connect(c.destination);
      // The element's own volume stops being the lever once it is a source —
      // the gain node is, so applyVolume and the duck both write there.
      el.volume = 1; el.muted = false;
      stationMix = { src, gain };
      // The DJ may already be playing on their own element — the two arrive in
      // whichever order the room gives them. Bring them in now, or the station
      // is in the graph on its own and nothing has been married.
      if (!fx && djTrack && wirePlainVoice(djTrack) && djEl) djEl.muted = true;
      return true;
    } catch (e) {
      console.warn('Talk Wave: the station stays on its own output —', e);
      return false;
    }
  }

  function unmixStation() {
    if (!stationMix) return;
    try { stationMix.src.disconnect(); stationMix.gain.disconnect(); } catch (e) {}
    stationMix = null;
  }

  function wireEffect(track) {
    const spec = fxSpec();
    // No effect, but the station is in the graph: the DJ has to join it, or
    // the two are back on separate outputs and nothing has been fixed.
    if (!spec) return stationMix ? wirePlainVoice(track) : false;
    try {
      const c = ctx();
      const src = c.createMediaStreamSource(
        new MediaStream([track.mediaStreamTrack]));
      const hp = c.createBiquadFilter();
      hp.type = 'highpass'; hp.frequency.value = spec.hp;
      const lp = c.createBiquadFilter();
      lp.type = 'lowpass'; lp.frequency.value = spec.lp;
      const gain = c.createGain();
      gain.gain.value = Math.min(1, getVolume() / 100);
      let node = src;
      const chain = [hp, lp];
      if (spec.grit) {
        const shaper = c.createWaveShaper();
        shaper.curve = distCurve(spec.grit);
        shaper.oversample = '2x';
        chain.push(shaper);
      }
      chain.push(gain);
      chain.forEach((n) => { node.connect(n); node = n; });
      node.connect(c.destination);
      fx = { src, gain };
      return true;
    } catch (e) {
      console.warn('Talk Wave: voice effect unavailable —', e);
      return false;
    }
  }

  // The DJ, through the same context, with no colour on the voice.
  function wirePlainVoice(track) {
    try {
      const c = ctx();
      const src = c.createMediaStreamSource(
        new MediaStream([track.mediaStreamTrack]));
      const gain = c.createGain();
      gain.gain.value = Math.min(1, getVolume() / 100);
      src.connect(gain); gain.connect(c.destination);
      fx = { src, gain };
      return true;
    } catch (e) {
      console.warn('Talk Wave: the DJ stays on their own output —', e);
      return false;
    }
  }

  function dropEffect() {
    if (!fx) return;
    try { fx.src.disconnect(); } catch (e) {}
    fx = null;
  }

  // ------------------------------------------------- route-change recovery
  // The marriage above has one enemy: a phone that changes its audio route
  // mid-call. The worst offender is Bluetooth in a car — the ring plays over
  // the media profile, then the mic engages and the handset drops the link
  // to hands-free. The context that opened at the media rate keeps rendering
  // into a route that no longer exists, and because the graph is the only
  // audible path (the DJ's element is muted the moment the graph takes the
  // voice), the call goes silent exactly as the DJ says hello — ring heard,
  // nothing after (operator's car, 2026-08-31).
  //
  // Recovery is a ladder, cheapest rung first:
  //   1. resume() — covers a plain suspend and iOS's 'interrupted'.
  //   2. rebuild — throw the context away, open a fresh one (born at the NEW
  //      route's rate) and rewire: the DJ through wireEffect, the station by
  //      retuning (a captured element cannot be given back, so it is a new
  //      element or nothing), the meters re-analysed.
  //   3. surrender — unmute the DJ's element and leave the graph out of it.
  //      The element is WebRTC playout, which the platform routes with the
  //      call itself, so it survives any profile the phone lands on. Split
  //      volumes beat a silent DJ.
  //
  // Checked at the moments a flip actually happens: scheduled beats after
  // the mic engages (the flip IS the mic engaging), when the device list
  // changes, and when the page comes back into view. Cheap when nothing is
  // wrong: the probe below only opens when there is a graph to defend.
  let recoverBusy = false;
  let recoverTimers = [];

  function clearRecoveryChecks() {
    recoverTimers.forEach(clearTimeout);
    recoverTimers = [];
  }

  function scheduleRecoveryChecks() {
    clearRecoveryChecks();
    // Three beats, not one: SCO can take a couple of seconds to come up, and
    // a check that runs only during the changeover would rebuild onto a rate
    // that is itself about to change.
    [1500, 4000, 8000].forEach((at) => {
      recoverTimers.push(setTimeout(() => { checkAudioAlive('after pickup'); }, at));
    });
  }

  // Whether the graph's context still matches the hardware it is meant to
  // reach. A stale context after a route flip usually still SAYS 'running' —
  // the honest tell is the rate: a probe context opened now is born at the
  // live route's rate, and a mismatch means ours renders into the void.
  function ctxLooksDead() {
    const c = ctx();
    if (c.state !== 'running') return true;
    try {
      const C = window.AudioContext || window.webkitAudioContext;
      const probe = new C();
      const stale = probe.sampleRate !== c.sampleRate;
      try { probe.close(); } catch (e) { /* probes may linger, harmlessly */ }
      return stale;
    } catch (e) { return false; }
  }

  async function checkAudioAlive(why) {
    if (!room || recoverBusy) return;
    // Element paths route themselves with the platform — only a graph that
    // holds a voice or the station has anything to lose here.
    if (!fx && !stationMix) return;
    if (!ctxLooksDead()) return;
    recoverBusy = true;
    try {
      // Rung 1: ask nicely.
      try { await ctx().resume(); } catch (e) { /* the rebuild is next */ }
      if (!ctxLooksDead()) return;
      console.warn('Talk Wave: audio route changed (' + why + ') — rebuilding the graph');
      // Rung 2: a fresh context at the live rate, everything rewired.
      const hadStation = !!stationMix;
      dropEffect(); unmixStation();
      resetCtx();
      if (djTrack) {
        if (wireEffect(djTrack)) { if (djEl) djEl.muted = true; }
        else if (djEl) { djEl.muted = false; djEl.play?.(); }
        anDj = analyserFor(djTrack.mediaStreamTrack);
      }
      const mic = room && room.localParticipant
        && room.localParticipant.getTrackPublication(LivekitClient.Track.Source.Microphone);
      if (mic && mic.track) anYou = analyserFor(mic.track.mediaStreamTrack);
      if (hadStation) { tuneOut(); tuneIn(); }
      applyVolume();
      routeAudio(onSpeaker);
      // Rung 3: if even the fresh context refuses to run, the element is
      // the way out.
      recoverTimers.push(setTimeout(() => {
        if (!room || !fx) return;
        if (ctx().state === 'running') return;
        console.warn('Talk Wave: the graph will not run here — the DJ takes the element path');
        dropEffect();
        if (djEl) { djEl.muted = false; djEl.play?.(); }
        applyVolume();
      }, 1200));
    } finally {
      recoverBusy = false;
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) checkAudioAlive('page visible');
  });

  function stationLevel() {
    const s = (live && live.stream) || {};
    return Math.min(1, ((s.volume || 0) / 100) * (getVolume() / 100));
  }

  function applyVolume() {
    $('volPct').textContent = getVolume() + '%';
    // The slider draws its own trough and fill (see .vol in style.css) —
    // webkit has no way to style the filled part of a range, so the fill is
    // a gradient stop and this is where the stop comes from.
    $('volSlider').style.setProperty('--vol', getVolume() + '%');
    if (fx) fx.gain.gain.value = Math.min(1, getVolume() / 100);
    else if (djEl) djEl.volume = Math.min(1, getVolume() / 100);
    if (stationMix) {
      // In the graph the gain node is the lever; the element's own volume is
      // ignored once it is a source node.
      stationMix.gain.gain.value = stationLevel();
    } else if (streamEl) {
      const level = stationLevel();
      streamEl.volume = level;
      streamEl.muted = level <= 0;
    }
    if (playerEl) {
      // Full volume, scaled only by the card's own slider: in the player the
      // broadcast is the subject, not the bed under a call. Under the
      // STUDIO it ducks instead — playerLevel carries the factor. The
      // sheet's own mute rides on top of both without moving either.
      const level = playerLevel();
      playerEl.volume = level;
      playerEl.muted = level <= 0 || plMuted;
    }
    // The player's fader is a second handle on the SAME volume — value and
    // drawn fill both, since the fill is a gradient stop, not the browser's.
    const pv = $('plVol');
    if (pv) {
      if (pv.value !== String(getVolume())) pv.value = getVolume();
      pv.style.setProperty('--vol', getVolume() + '%');
    }
  }
  $('volSlider').oninput = (e) => {
    volTouched = true; setVolume(+e.target.value); applyVolume();
  };
  applyVolume();      // paint the fill at whatever volume we start on

  // One small data message to the booth at setup: this page's mic outcome
  // and connection state — the two facts the worker's no-audio postmortem
  // can never see from its side. Caller-authored, so the worker treats it
  // as untrusted and clamps it; best-effort here, the shrug still works.
  function sendSetupNote(mic) {
    if (!room) return;
    try {
      room.localParticipant.publishData(
        new TextEncoder().encode(JSON.stringify(
          { mic: mic, conn: (room.state || '') + '' })),
        { reliable: true, topic: 'talkwave.setup-note' });
    } catch (e) { /* the postmortem falls back to naming candidates */ }
  }

  // Bumped on every startCall AND every endCall, so an async step that
  // resumes after the caller hung up can tell it is stale. The token mint is
  // an await with the Hang up button already live (the card flips to .oncall
  // on the press, no ringing phase) — pressing it there ran endCall to idle
  // and then this function RESUMED, connected the room and opened the mic
  // against a card that said idle: the DJ heard a caller who thought they had
  // hung up. Reviewed 0.10.57.
  let callGen = 0;
  // One teardown per call — see endCall's re-entrancy guard. Cleared when a
  // fresh call starts.
  let callEnded = false;
  // The route chip, for the whole call or recording — a caller must never be
  // able to forget which way they chose ("once you get in you could forget
  // which you picked", operator, 2026-08-18). Both states, voicemail
  // included: ON AIR in coral, OFF AIR in the cool teal — and nothing at all
  // when the card never offered a route, because a switchless deployment has
  // no OFF AIR to reassure anyone about.
  function paintRouteBadge() {
    const b = $('onAirBadge');
    if (!b) return;
    const routed = !!($('routeSwitch') && !$('routeSwitch').hidden);
    const on = vmCall
      ? routed && onAirPick
        && !!(live && live.onAirCalls && live.onAirCalls.voicemail)
      : onAirCall;
    b.hidden = !(on || routed);
    b.classList.toggle('priv', !on);
    const t = $('onAirBadgeText');
    if (t) t.textContent = on ? 'ON AIR' : 'OFF AIR';
  }

  async function startCall(asVoicemail) {
    const myGen = ++callGen;
    callEnded = false;   // a new call may be torn down again
    vmCall = !!asVoicemail;
    // Pinned at the press: the route is only a REQUEST — the server re-gates
    // it at the mint and the worker preflights the transport, so this flag
    // means "asked to be live", never "is live". Only when the CALLS door is
    // actually open: an on-air route with just the voicemail door live must
    // not ask the mint for a broadcast call it would refuse.
    onAirCall = !vmCall && onAirPick
      && !!(live && live.onAirCalls && live.onAirCalls.calls);
    vmBeepHeard = false;

    // A call or a voicemail is a different mode from the text line — you are
    // on the phone now, not typing. If a chat was open, close it and clear
    // its input row, or the card shows a text box AND a call at once (the
    // "END / hi there / SEND" row over a live voicemail, operator-reported).
    if (chatOpen) endChat();
    if ($('chatRow')) $('chatRow').hidden = true;

    // The station player cannot survive a live microphone: on speakers the
    // stream comes straight back in through the caller's mic and gets
    // transcribed as if they had said it — and the call's own tune-in takes
    // over at pickup anyway, at the volume that job calls for.
    //
    // PARKED rather than destroyed, so the element keeps the permission its
    // first tap earned and can actually play again when the line clears —
    // see resumePlayer for why a fresh Audio() could not.
    if (playerEl) playerResume = true;
    parkPlayer();
    closePlayer(true);

    // AFTER any chat teardown (endChat resets the mode to idle) — this is the
    // mode the call runs in. The card switches to the call's own controls,
    // or the recorder's, which hides the push-to-talk bar and mute a
    // voicemail caller has no use for.
    setCardMode(asVoicemail ? 'voicemail' : 'call');

    // Browsers only allow microphone capture on HTTPS or localhost. On a
    // plain http:// LAN address the call would connect and then immediately
    // hang up when mic capture fails — say why up front instead.
    if (!window.isSecureContext || !navigator.mediaDevices
        || !navigator.mediaDevices.getUserMedia) {
      setStatus('This page can\'t use the microphone — see the note below', 'error');
      updateMicHelp();
      return;
    }
    // The end control is on the card from the instant of the press — no
    // button phasing through Ringing -> Answering -> On the line while the
    // caller waits, which the operator asked to be rid of. Hang up (or End
    // message) is the one action during setup, and pressing it cancels a call
    // that has not connected yet. The state chip carries "Connecting…", so the
    // status is not lost by dropping it off the button.
    callBtn.hidden = true;
    callBtn.classList.remove('ringing', 'answering', 'live');
    hangBtn.textContent = asVoicemail ? word('vm_hangup', 'End message')
                                      : word('hangup', 'Hang up');
    hangBtn.hidden = false;
    const card = document.querySelector('.card');
    card.classList.add('oncall');
    // The route light, for the whole call — see paintRouteBadge.
    paintRouteBadge();
    // Repainted HERE, like openChat does — the board is only painted when the
    // card is idle and the /live poll runs every 20 seconds, so LINES ARE OPEN
    // stayed up through connecting, pickup and the DJ's first words. Whoever
    // changes the card's state repaints it.
    paintBoard(live);
    // Voicemail is push-to-talk too (operator's ask): show the bar from the
    // press so the caller has a mic control immediately instead of a dead
    // "MIC OFF" with nothing to hold. Follows the same per-surface PTT switch
    // a call does, which is ON by default.
    card.classList.toggle('ptt', pttOn());
    $('rig').classList.add('on');
    $('stateChip').hidden = false;
    djHasSpoken = false;          // a second call rings like the first one did
    setAgentState('initializing');
    startTimer();
    notifyHeight();
    setStatus(word('connecting', 'Connecting…'), 'connecting');
    $('endedBar').hidden = true;
    $('rateBar').hidden = true;      // last call's verdict, not this one's
    capNodes.clear();
    $('lineBox').classList.remove('open');
    if (captionsMode === 'full') {
      // Emptied and left OFF. The box does not go blank while it waits — the
      // status line underneath it is showing "Connecting…", which is both
      // truer and in the place the caller is already looking. It used to say
      // "Captions will appear here as you talk…" in one box while the header
      // said something about the connection in another.
      capBox.innerHTML = '';
      capBox.classList.remove('on');
    } else if (captionsMode === 'ticker') {
      // Claim the ticker's reserved two lines NOW, empty, so the frame
      // settles once at the start of the call instead of jumping when the
      // first word arrives. From here the height is constant for the rest of
      // the call however long anyone talks.
      const t = $('ticker');
      if (t) { setLine(t.querySelector('.line'), ''); t.hidden = false; }
    }
    notifyHeight();

    ctx();          // unlock audio inside the click gesture
    startRinging();
    // Tune-in happens at PICKUP, not here: the station stream underneath a
    // ringing tone is just noise, and the caller is only a listener once
    // someone actually answers.

    try {
      const res = await fetch('/token', {
        method: 'POST',
        headers: Object.assign(
          { 'Content-Type': 'application/json' },
          callKey() ? { 'X-Call-Key': callKey() } : {}),
        body: JSON.stringify(vmCall ? { voicemail: true }
                             : onAirCall ? { onAir: true } : {}),
      });
      // 429 = the line is busy or the operator has closed it; 401 = the door
      // code is missing or wrong; 403 = a door this caller's tier doesn't
      // open (the on-air ask from a stale tab, mostly — the toggle hides
      // when the door shuts). All answers, not faults — engaged tone, plain
      // wording, and the button comes straight back.
      if (res.status === 429 || res.status === 401 || res.status === 403) {
        const d = await res.json().catch(() => ({}));
        stopRinging(); tuneOut();
        playSound('failed');
        if (res.status === 401) {
          localStorage.removeItem(CALL_KEY);
          paintGuestGate();
        } else if (res.status === 403 && live && live.signinAvailable) {
          // A door this tier doesn't open, from a caller a code could still
          // elevate — a stale tab, or a stored key that expired between
          // paints. The old dead end showed the refusal and nothing else,
          // several times in one evening (2026-08-18); the sign-in row IS
          // the fix for a 403, so open it alongside the message.
          openSignin();
        }
        setStatus(d.error || 'The booth line is tied up — try again shortly.', 'error');
        $('rig').classList.remove('on');
        $('stateChip').hidden = true;
        stopTimer();
        capBox.classList.remove('on');
        callBtn.classList.remove('ringing', 'answering');
        room = null;
        vmCall = false;
        onAirCall = false;
        if ($('onAirBadge')) $('onAirBadge').hidden = true;
        // Back to idle in full: since the card flips to .oncall + Hang up the
        // INSTANT the button is pressed (no ringing phase), a refusal has to
        // undo that here, or .oncall keeps the doors hidden and the card sits
        // on "Hang up" over an engaged-tone message (tester-caught).
        document.querySelector('.card').classList.remove('oncall');
        hangBtn.hidden = true;
        setCardMode('idle');
        // Repaint BOTH buttons from the live state — restoring Call by hand
        // here forgot the message button, and one refused call left the
        // card without its one working door until a reload.
        paintIdleButtons(live || {});
        // And the board with them: the press took it down, so a refusal that
        // did not put it back left an empty box until the next poll.
        paintBoard(live);
        // A busy live line is exactly when the text line earns its keep, so
        // offer it here even if the operator didn't put the permanent button
        // on this surface — the same distinction voicemail draws between its
        // always-on button and its fallback. Not on a 401 (the door code is
        // the fix there) and not when the whole line is paused (chat is shut
        // too). The button is hoisted-visible; tapping it opens the chat.
        if (res.status === 429 && live && live.chatEnabled && !live.callsPaused
            && $('chatBtn')) {
          $('chatBtn').hidden = false;
          setStatus((d.error || 'The booth line is tied up.')
            + ' You can text the booth instead.', 'error');
        }
        if (res.status === 401) {
          callBtn.hidden = false;
          callBtn.disabled = true;
          callBtn.textContent = 'Enter the code';
        }
        return;
      }
      if (!res.ok) throw new Error('token mint failed');
      const { token, url, room: roomName } = await res.json();
      // The caller pressed Hang up while the mint was in flight: endCall
      // already reset the card to idle, so connecting now would be a live
      // call behind an idle face. Release the slot the server just minted
      // and stop here.
      if (myGen !== callGen) {
        fetch('/call-ended', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ room: roomName }), keepalive: true,
        }).catch(() => {});
        return;
      }
      currentRoom = roomName;

      // Echo cancellation, noise suppression and auto-gain set EXPLICITLY, not
      // left to whatever the client library defaults to this version: they are
      // what a phone in a room full of the station's own output needs to be
      // transcribed, and on a speakerphone the echo canceller is the only thing
      // keeping the DJ's voice out of the caller's transcript. Stated here so
      // the README can promise them and mean it.
      //
      // ?mic= exists because only the FIRST of those three has an argument
      // written down. The echo-canceller case is airtight; noise suppression
      // and auto-gain rode in beside it on the same sentence and have never
      // been tested apart. They are not obviously right: browser noise
      // suppression is tuned for steady noise and is known to gate a quiet or
      // distant talker to digital silence, and auto-gain pumping moves the
      // signal that endpointing reads — which is the exact shape of the
      // calls where nothing the caller says is ever heard. A vendor whose
      // whole product is turn-taking quality ships AEC on and both of these
      // OFF.
      //
      // So this is the arm switch for settling it with numbers instead of
      // argument, against call/heard.py's pair. Default is unchanged: no
      // query param means exactly what shipped before.
      //   ?mic=ns-off   ?mic=agc-off   ?mic=clean (both off)
      const micArm = params.get('mic') || '';
      const capture = {
        echoCancellation: true,        // never an arm: the argument holds
        noiseSuppression: !(micArm === 'ns-off' || micArm === 'clean'),
        autoGainControl:  !(micArm === 'agc-off' || micArm === 'clean'),
      };
      if (micArm) {
        console.info('[talkwave] mic arm %s -> NS=%s AGC=%s', micArm,
                     capture.noiseSuppression, capture.autoGainControl);
      }
      room = new LivekitClient.Room({
        adaptiveStream: true, dynacast: true,
        audioCaptureDefaults: capture,
      });

      // Nobody has to answer. If the worker is down, mid-restart, or never
      // gets dispatched, the room connects fine and then nothing happens —
      // and the caller was left ringing indefinitely with no way to tell a
      // slow pickup from a dead line. Every real phone gives up eventually.
      startNoAnswerTimer();

      room.on(LivekitClient.RoomEvent.TrackSubscribed, (track) => {
        if (track.kind !== 'audio') return;
        clearNoAnswerTimer();
        stopRinging();
        playSound('pickup');
        // The machine, not the DJ: no station stream underneath a recording,
        // and the button says what is happening instead of who answered.
        if (vmCall) {
          callBtn.classList.remove('ringing');
          callBtn.classList.add('live');
          callBtn.textContent = word('recording', 'Recording…');
          // The machine records what it hears — and on a push-to-talk card
          // that is whatever the caller sends while the bar is held or
          // latched. The bar STAYS (operator's ask): a tap latches the mic
          // open so even a caller who does not hold still leaves a message,
          // and holding is momentary. Only an open-mic card (PTT switched
          // off) drops the bar and records continuously.
          if (pttOn()) {
            setStatus('Hold the bar — or tap it — and leave your message after the beep',
                      'connected');
          } else {
            document.querySelector('.card').classList.remove('ptt');
            setStatus('The machine is listening — speak after the beep, transcript only',
                      'connected');
          }
        } else if (onAirCall
                   && (((live || {}).onAirCalls) || {}).mode !== 'after') {
          // NO station bed under a live call: the stream at this moment is
          // this very conversation, one stream-buffer ago — a caller hearing
          // their own last exchange under the current one cannot hold a
          // thought. They rejoin the listener count when the line clears.
          // A TAPED call falls through to tune-in below (operator's ask,
          // 2026-08-18): nothing airs until hangup, so the stream under the
          // call is just the station playing, same as any private call.
        } else {
        // Now they're actually on a call: tune them into the station so the
        // station counts them as a listener and accepts their requests.
        tuneIn();
        }
        // Line picked up; the DJ hasn't spoken yet. setAgentState flips this
        // to the green "On the line" at its first spoken word.
        if (!vmCall) {
          callBtn.classList.remove('ringing');
          callBtn.classList.add('answering');
          callBtn.textContent = word('answering', 'Answering…');
        }
        // The bottom row flips: Call (or the machine's door) gives way to
        // Hang up, full width, exactly where a thumb expects it.
        callBtn.hidden = true;
        hangBtn.textContent = vmCall ? word('vm_hangup', 'End message')
                                     : word('hangup', 'Hang up');
        hangBtn.hidden = false;
        // Re-entrant on purpose: a mid-call reconnect re-fires
        // TrackSubscribed, and attaching again WITHOUT tearing down the
        // first element left two playbacks of the same voice running a few
        // ms apart — "the DJ speaking twice, slightly off sync", reported
        // from a live call. Same for the effect graph: fx was overwritten
        // while the old source stayed connected.
        if (djEl) {
          try { djEl.pause(); djEl.srcObject = null; djEl.remove(); }
          catch (e) { /* an orphaned element beats a crashed pickup */ }
        }
        dropEffect();
        djTrack = track;
        djEl = track.attach();
        djEl.volume = Math.min(1, getVolume() / 100);
        if (wireEffect(track)) djEl.muted = true;
        // Without playsinline, iOS takes an audio element it considers
        // "media" full-screen-ish and applies its own routing on top of
        // whatever we asked for.
        djEl.setAttribute('playsinline', '');
        djEl.play?.();
        // The DJ's element only exists from here, so this is the first moment
        // setSinkId has anything to act on. Fire and forget: a platform that
        // refuses is not a reason to interrupt a call that is otherwise up.
        routeAudio(onSpeaker);
        anDj = analyserFor(track.mediaStreamTrack);
        // The DJ may arrive after the pickup beats have run; restart them so
        // a voice just wired into a stranded graph is caught too.
        scheduleRecoveryChecks();
        setStatus('Connected — go ahead, talk', 'connected');
      });
      room.on(LivekitClient.RoomEvent.Disconnected, () => endCall(true));
      room.on(LivekitClient.RoomEvent.Reconnecting, () => {
        setAgentState('reconnecting');
        setStatus('Connection hiccup - reconnecting...', 'connecting');
      });
      room.on(LivekitClient.RoomEvent.Reconnected, () => {
        setAgentState('listening');
        setStatus('Connected - go ahead, talk', 'connected');
      });
      wireCaptions(room);
      watchAgentState(room);
      watchActions(room);
      room.on(LivekitClient.RoomEvent.DataReceived, (payload, participant, kind, topic) => {
        if (topic !== 'vm-beep' || !vmCall || vmBeepHeard) return;
        // The beep is a cue, not a gate: the mic has been live since
        // pickup, so this only moves the status line to "recording" —
        // never a forced mic-open, which would un-mute a caller who
        // pressed Mute during the greeting.
        vmBeepHeard = true;
        // Voicemail is open-mic, never PTT (see the pickup handler): the beep
        // just moves the status to "recording".
        setStatus('Recording — go ahead, transcript only', 'connected');
      });

      await room.connect(url, token);
      // Enabled first even under push to talk: this is the moment the
      // browser asks the mic permission and the track is created. PTT then
      // closes the line straight away — the first press reopens it without
      // a permission prompt mid-sentence.
      await room.localParticipant.setMicrophoneEnabled(true);
      // The permission just landed = device labels just became readable;
      // the speaker button's probe gets its one honest look (see
      // probeRouting). Fire-and-forget — the call must not wait on it.
      probeRouting();
      // Push-to-talk on a call AND on voicemail (operator's ask): the mic
      // starts closed and the bar opens it. A caller who taps latches it open
      // and leaves a message exactly like an open mic; one who holds is
      // momentary. This once shut the mic on a voicemail card that had NO
      // visible bar (the "MIC OFF, nothing to hold" report) — the bar is
      // present now, so the control the closed mic implies is actually there.
      // Only a card with PTT switched off keeps the historic open mic.
      if (pttOn() && !pttOpen) {
        // Closed only if the caller has not already pressed the bar during
        // the ring — a latch made early is a decision, not a race to lose.
        await setMicOpen(false);
      } else if (pttOn()) {
        paintPtt();
      }

      const mic = room.localParticipant.getTrackPublication(
        LivekitClient.Track.Source.Microphone);
      anYou = analyserFor(mic?.track?.mediaStreamTrack);
      // The mic engaging is the moment a Bluetooth handset drops the link
      // from the media profile to hands-free — the beats below catch the
      // graph if that flip strands it. See the route-change recovery ladder.
      scheduleRecoveryChecks();
      // Tell the booth what this side of the line looks like: the no-audio
      // postmortem could never tell a blocked mic from a dead media path
      // from a silent caller, because the distinction only exists HERE.
      // One small message; the worker records it against the call.
      sendSetupNote('granted');

      // Button stays on Ringing/Answering; setAgentState flips it to the
      // green On the line at the DJ's first word.
      document.querySelector('.card').classList.add('oncall');
      if (!rafId) tick();
      setStatus(word('waiting', 'Connected — waiting for the DJ…'), 'connected');
    } catch (err) {
      console.error(err);
      stopRinging();
      clearNoAnswerTimer();
      playSound('failed');
      // The failure often happens AFTER room.connect() succeeded (a blocked
      // mic, typically). Without this the room stays joined and the agent
      // sits in an empty call.
      tuneOut();
      const denied = err && (err.name === 'NotAllowedError'
        || /permission|not allowed|denied/i.test(err.message || ''));
      // Before the room goes: tell the booth WHY, so the record can name a
      // blocked mic instead of shrugging at three candidates. Best-effort —
      // a room that never joined has nowhere to send it.
      if (denied) sendSetupNote('denied:' + ((err && err.name) || 'mic'));
      if (room) { try { await room.disconnect(); } catch (e) {} }
      // Three failures wore the same "Could not connect" label, and the most
      // common one is the least obvious: the room is joined and signalling is
      // fine, but audio has no route — so it rings for ~15s and then dies.
      // A caller told "could not connect" reasonably assumes the station is
      // down. It isn't, and there is something they can actually try.
      const noMediaPath = !denied && err
        && /pc connection|ice|media|timeout|could not establish/i.test(
          (err.message || '') + ' ' + (err.reason || ''));
      if (noMediaPath) {
        console.warn(
          'Talk Wave: signalling connected but no media path was established. '
          + 'The caller reached the room; audio could not flow. Usually the '
          + 'network cannot reach the media port, or the caller is on an '
          + 'IPv4-only network while the station only publishes IPv6.', err);
      }
      setStatus(
        denied ? 'Microphone blocked — allow mic access'
          : noMediaPath ? 'Reached the studio, but no audio path — try mobile data'
            : 'Could not connect',
        'error');
      // The token was already minted by the time we got here, so a room
      // exists on the server side and this browser holds one of the
      // concurrency slots. Resetting the UI without releasing it left that
      // slot held until it aged out THIRTY MINUTES later — on a two-at-once
      // deployment, two failed connections closed the line to everyone. It
      // also left currentRoom pointing at the dead room, so the NEXT call to
      // end posted /call-ended for the wrong one and offered the caller a
      // thumbs-up against a call that never happened.
      releaseRoom();
      $('rig').classList.remove('on');
      // The same lesson the mint-failure path learned (see the "Back to
      // idle in full" note above): .oncall keeps every idle door
      // display:none, so a connect failure that skipped this left the
      // caller a card with a failure line and NO buttons — no retry, no
      // machine, nothing until a reload (settings review, 2026-08-24; the
      // most common real failure, the ~15s media timeout, lands exactly
      // here because .oncall goes on at the press).
      document.querySelector('.card').classList.remove('oncall');
      $('stateChip').hidden = true;
      stopTimer();
      capBox.classList.remove('on');
      callBtn.classList.remove('ringing', 'answering');
      setBtn(callBtn, 'call', 'phone', callLabel());
      callBtn.disabled = false;
      callBtn.hidden = false;
      hangBtn.hidden = true;
      setCardMode('idle');
      room = null;
      // The route badge outlived the attempt it described: OFF AIR stood in
      // the rail beside the failure line until the next poll swept it
      // (operator round, 2026-08-25). Same flags and painters as the
      // mint-refusal path above — a dead attempt leaves no chips behind,
      // and the board comes back with the buttons rather than by hand.
      vmCall = false;
      onAirCall = false;
      if ($('onAirBadge')) $('onAirBadge').hidden = true;
      paintIdleButtons(live || {});
      paintBoard(live);
    }
  }

  // Tell the server this room is finished with, and forget it. Split out of
  // endCall() because the failed-connect path above needs exactly this and
  // nothing else that endCall does — it has no call to tear down.
  function releaseRoom() {
    if (!currentRoom) return null;
    const ended = currentRoom;
    currentRoom = null;
    // Release the concurrency slot now instead of waiting for it to age out.
    fetch('/call-ended', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room: ended }), keepalive: true,
    }).catch(() => {});
    return ended;
  }

  let currentRoom = null;
  let callStarted = 0, timerId = null;

  // How long the caller rings before we admit nobody is coming. Long enough
  // to cover a cold worker picking up a first call (model load, station
  // reads), short enough that it still feels like a phone.
  const NO_ANSWER_SECS = 40;
  let noAnswerId = null;

  function clearNoAnswerTimer() {
    if (noAnswerId) { clearTimeout(noAnswerId); noAnswerId = null; }
  }

  function startNoAnswerTimer() {
    clearNoAnswerTimer();
    noAnswerId = setTimeout(() => {
      noAnswerId = null;
      if (!room) return;
      console.warn('no answer after ' + NO_ANSWER_SECS + 's — hanging up');
      endCall(false);
      playSound('failed');
      setStatus('No answer — the booth didn’t pick up. Try again in a moment.',
                'error');
    }, NO_ANSWER_SECS * 1000);
  }

  function fmt(sec) {
    const m = Math.floor(sec / 60), r = Math.floor(sec % 60);
    return m + ':' + String(r).padStart(2, '0');
  }

  // Elapsed against the hard limit, so a caller isn't surprised by the cutoff.
  // A voicemail counts against ITS ceiling, not the live call's — the card
  // showed "/ 10:00" on a 30-second machine, which read as the limit being
  // ignored (and the room really did outlive it; the worker closes it now).
  function startTimer(maxOverride) {
    // The studio passes its own ceiling; a call resolves one from /live.
    const max = maxOverride != null ? maxOverride : (vmCall
      ? ((live && live.limits && live.limits.voicemailMaxSeconds) || 0)
      : ((live && live.limits && live.limits.maxCallSeconds) || 0));
    callStarted = Date.now();
    $('timeChip').hidden = false;
    $('timeMax').textContent = max ? '/ ' + fmt(max) : '';
    const tick = () => {
      const el = (Date.now() - callStarted) / 1000;
      $('timeText').textContent = fmt(el);
      const chip = $('timeChip');
      chip.classList.toggle('warn', !!max && max - el <= 60 && max - el > 20);
      chip.classList.toggle('urgent', !!max && max - el <= 20);
    };
    tick();
    timerId = setInterval(tick, 1000);
  }

  function stopTimer() {
    if (timerId) { clearInterval(timerId); timerId = null; }
    $('timeChip').hidden = true;
    $('timeChip').classList.remove('warn', 'urgent');
  }

  function endCall(remote) {
    // Re-entrancy guard: a local Hang up calls endCall(false), which then
    // room.disconnect()s, and the Disconnected event fires endCall(true) a
    // microtask later. The second pass releaseRoom()'d null and hid the
    // feedback bar the first pass had just shown, so a local hang-up never
    // saw the "How was it?" prompt (top-down review, 2026-08-28). One
    // teardown per call; the disconnect echo is a no-op. startCall clears it.
    if (callEnded) return;
    callEnded = true;
    // Any in-flight startCall (e.g. awaiting the token mint) is now stale —
    // its post-await resume checks this and bails. See callGen at startCall.
    callGen += 1;
    const wasVm = vmCall;
    vmCall = false;
    stopRinging();
    clearNoAnswerTimer();
    tuneOut();
    // Kept past the reset below: the feedback buttons post against this
    // call's room, and by the time anyone clicks them currentRoom is long
    // cleared. A room id is minted per call and deleted at the end of one, so
    // holding it here outlives nothing.
    const endedRoom = releaseRoom();
    if (room) { if (!remote) room.disconnect(); playSound('hangup'); }
    room = null; muted = false;
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    dropEffect();
    clearRecoveryChecks();
    anYou = anDj = null; djEl = null; djTrack = null;
    djOnAir = false; djHasSpoken = false;
    // Reset the on-air hold state too, or a call that hung up while its hold
    // had already expired left holdExpired stale-true, and the NEXT call's
    // on-air mic-lock (open && djOnAir && !holdExpired) was defeated — the
    // caller could talk over the broadcast (top-down review, 2026-08-28).
    clearTimeout(holdTimer); holdExpired = false; wasLiveBeforeHold = false;
    document.querySelector('.card').classList.remove('onair');
    clearMeters();
    $('djAvatar').classList.remove('talking');
    $('rig').classList.remove('on');
    $('stateChip').hidden = true;
    stopTimer();
    setAgentState('idle');
    const ticker = $('ticker');
    if (ticker) { ticker.classList.remove('show'); ticker.hidden = true; }
    // A voicemail keeps its transcript OPEN on the ended card — the caller
    // should still see what they submitted, with a line saying it's now the
    // DJ's to read (operator-reported: "when it ends you can't see what you
    // left"). A call folds the transcript into the drawer as before, and only
    // a call offers the verdict buttons — "How was it?" over "Message left"
    // read as the machine fishing for a compliment.
    if (wasVm) {
      showVmReceipt();
      // The machine may ask too — the operator's per-door switch. Only after
      // a message actually left: a thumbs prompt under an empty receipt is
      // the machine fishing twice over.
      offerFeedback(endedRoom, live && live.askVmFeedback
        && capBox.querySelectorAll('.cap.you').length > 0);
    } else {
      collapseTranscript();
      offerFeedback(endedRoom, live && live.askFeedback);
    }
    setBtn(callBtn, 'call', 'phone', callLabel());
    callBtn.classList.remove('live', 'ringing', 'answering');
    callBtn.disabled = false;
    callBtn.hidden = false;
    hangBtn.hidden = true;
    document.querySelector('.card').classList.remove('oncall');
    onAirCall = false;
    if ($('onAirBadge')) $('onAirBadge').hidden = true;
    muteBtn.textContent = 'Mute';
    muteBtn.classList.remove('on');
    setCardMode('idle');
    resumePlayer();
    pttOpen = false;
    const pttBar = $('pttBtn');
    if (pttBar) { pttBar.classList.remove('on'); pttBar.setAttribute('aria-pressed', 'false'); }
    $('meterYou').classList.remove('muted');
    setMicChip('live');
    // The captions box, when it has lines, hides this status line — so on a
    // voicemail with a transcript the review note lives IN the box (see
    // showVmReceipt) and this is the fallback for a message that left no
    // transcribable words.
    setStatus(wasVm ? 'Message received — the DJ will review your request shortly.'
                    : word('ended', 'Call ended'));
    // The card's idle truth — including the second button — comes back from
    // the next /live read rather than being reconstructed by hand here. The
    // burst catches a takeover this call may have set in motion, which airs at
    // the next track boundary and would otherwise show up to a poll late.
    refreshLive();
    burstLive();
    notifyHeight();
  }

  // A voicemail's receipt: keep the caller's own words on screen after the
  // beep, with a line telling them the message is now the DJ's to read. There
  // is no reply and no rating, so the transcript is the whole of what a caller
  // gets to take away — folding it into a collapsed drawer (as a call does)
  // hid the one thing they wanted to check. If nothing was transcribed there is
  // nothing to show, so it falls back to the ordinary collapse and the status
  // line carries the note instead.
  function showVmReceipt() {
    const spoken = capBox.querySelectorAll('.cap.you').length;
    if (!spoken) { collapseTranscript(); return; }
    $('lineBox').classList.add('open');
    capBox.classList.add('on');
    $('endedBar').hidden = true;
    addSystemLine('✓', 'Message received',
                  'The DJ will review your request shortly.', true);
    pinTranscript();
    notifyHeight();
  }

  // Keep the transcript after the call, but out of the way.
  function collapseTranscript() {
    const lines = capBox.querySelectorAll('.cap').length;
    const bar = $('endedBar');
    $('lineBox').classList.remove('open');
    capBox.classList.remove('on');
    if (!lines) { bar.hidden = true; return; }
    bar.hidden = false;
    bar.classList.remove('open');
    const t = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    // "Transcript", not "Call ended". The line area below is already saying
    // the call ended, in a sentence, and a drawer that repeats it is a second
    // announcement of the same fact — which is what made the two of them read
    // as an error message.
    bar.innerHTML = '<span class="chev">▶</span><span>Transcript · ' + lines
      + ' line' + (lines === 1 ? '' : 's') + '</span><span class="when">' + t + '</span>'
      // A way OUT of the transcript, not only into it. Opening it was a
      // labelled click and closing it was the same unlabelled bar again;
      // the × says which way this one goes (operator's ask).
      + '<span class="dclose" role="button" aria-label="Close the transcript"'
      + ' title="Close the transcript">&times;</span>';
    notifyHeight();
  }

  // ------------------------------------------------------ was that any good?
  // Two buttons, offered once per conversation and only when the operator
  // asked for them — per door since 0.10.48: the call, the text line and the
  // machine each carry their own switch, so the caller passes in whether THIS
  // door's switch is on. Deliberately not a modal: a popup over the card the
  // moment a call ends is in the way of the transcript, and the one thing a
  // caller might want after a bad call is to read what was said.
  //
  // The answer lands on that conversation's own transcript, so "find me the
  // bad ones" is a question the panel can answer. Nothing else is collected.
  function offerFeedback(endedRoom, enabled) {
    const bar = $('rateBar');
    if (!bar) return;
    if (!endedRoom || !enabled) { bar.hidden = true; return; }
    $('rateLabel').textContent = 'How was it?';
    $('rateBtns').hidden = false;
    bar.hidden = false;

    const send = (rating) => {
      // Thanked before the request resolves, on purpose. Whether the record
      // was there to write to is the operator's problem, not the caller's —
      // and the worker may still be writing it, which is why the server side
      // retries rather than answering straight away.
      $('rateBtns').hidden = true;
      $('rateLabel').textContent = 'Thanks.';
      notifyHeight();
      fetch('/call-feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ room: endedRoom, rating: rating }),
      }).catch(() => {});
    };
    $('rateUp').onclick = () => send('up');
    $('rateDown').onclick = () => send('down');
    notifyHeight();
  }

  $('endedBar').onclick = (e) => {
    const bar = $('endedBar');
    // The × DISMISSES the drawer — bar and all. It used to merely set the
    // drawer closed, so pressing × on an already-closed drawer changed
    // nothing on screen at all and read as a dead button (operator-reported).
    // An exit that leaves the thing it exits on the card is not an exit.
    if (e.target.classList.contains('dclose')) {
      capBox.classList.remove('on');
      bar.classList.remove('open');
      bar.hidden = true;
      $('lineBox').classList.remove('open');
      paintBoard(live);
      notifyHeight();
      return;
    }
    const open = !capBox.classList.contains('on');
    capBox.classList.toggle('on', open);
    bar.classList.toggle('open', open);
    // The line area is three lines while a call is running and nothing is
    // allowed to change that. Reading back a FINISHED call is the one
    // exception, because it is a deliberate click by someone who wants the
    // room — and by then there is no call for the resize to interrupt.
    $('lineBox').classList.toggle('open', open);
    // Closing the drawer hands the box back to the board — otherwise the
    // card sits on an empty rectangle with a bar at the top of it.
    if (!open) paintBoard(live);
    notifyHeight();
  };

  callBtn.onclick = () => {
    if (!room && !previewMode) startCall(callBtn.dataset.vm === '1');
  };
  // Each cell selects its route (a two-option radio, per the handoff); the
  // pick repaints every derived surface at once, and repaints the BOARD too
  // — the stage message is one of them.
  if ($('routeOn')) {
    const pickRoute = (broadcast) => {
      if (room) return;               // pinned once a call exists
      onAirPick = broadcast;
      paintIdleButtons(live || {});
      paintBoard(live);
    };
    $('routeOn').onclick = () => pickRoute(true);
    $('routeOff').onclick = () => pickRoute(false);
  }
  // ------------------------------------------------- the text line
  // A chat is a WebSocket to /chat/ws and the same caption box the call
  // writes — no LiveKit, no room, no microphone, which is exactly the point:
  // it works where WebRTC cannot, and while the media server is down. The
  // id in localStorage is what makes it resumable per browser; the server
  // holds the transcript and replays it on hello.
  let chatWs = null, chatPend = null, chatText = '', capSeq = 0;
  // The DJ's reply is REVEALED at a human typing pace rather than dumped whole
  // — a caller said an instant wall of text "doesn't feel like a conversation;
  // it'd be better if you were typing while writing". The model streams fast
  // (flash-lite); this buffers the target text and reveals it a few characters
  // at a time, catching up on a long reply so it never lags far behind.
  let chatTarget = '', chatShown = 0, chatDone = false, chatTimer = null;
  // A typing cue asked for while a reveal was still running — see showTyping.
  let typingPending = false;
  // Characters owed but not yet whole — see chatTick.
  let chatCarry = 0;
  // The turn's raw text, and the messages split out of it. chatTarget is
  // always the one piece being revealed right now — see chatResplit.
  let chatRaw = '', chatSegs = [], chatSeg = 0;
  const CHAT_TICK_MS = 30;
  // However slow the pace, a long reply still lands inside this. Eight
  // seconds reads as a person writing a paragraph; much more and a caller is
  // watching a progress bar made of words.
  const CHAT_MAX_SECS = 8;
  // Characters per second per setting. "Normal" is a brisk human typist —
  // deliberately well under the ~33 c/s the fixed 30ms tick used to give.
  const CHAT_PACE = { slower: 11, natural: 19, brisk: 28, instant: 0 };
  function chatCps() {
    const pace = CHAT_PACE[(live && live.chatTypePace) || 'natural'];
    return pace === undefined ? CHAT_PACE.natural : pace;
  }
  // "dots" holds the typing cue and lands the line whole; "typing" reveals it
  // as written. Instant pace is the same arrival with no cue.
  function chatRevealsAsTyped() {
    return ((live && live.chatReveal) || 'typing') === 'typing'
      && chatCps() > 0;
  }
  function chatStopReveal() {
    if (chatTimer) { clearInterval(chatTimer); chatTimer = null; }
    chatTarget = ''; chatShown = 0; chatDone = false; chatCarry = 0;
    chatRaw = ''; chatSegs = []; chatSeg = 0;
  }

  // One TURN can be several messages. The DJ writes in paragraphs and may
  // speak either side of a tool call, and all of it used to pour into ONE
  // caption node — so a reply that was plainly three things read as a single
  // unbroken slab (operator screenshot, 2026-08-12). A blank line is where
  // people break a text, so that is the seam: each piece gets its own row,
  // revealed in turn, exactly as if the booth had sent three messages.
  //
  // Re-split the whole raw buffer each time rather than looking at one
  // delta: a blank line can arrive straddling two chunks, and re-splitting
  // cannot be fooled by where the network happened to cut it. The \r in the
  // class matters too — a model emitting CRLF sends a blank line this would
  // otherwise never see.
  function chatResplit() {
    const parts = chatRaw.split(/\n[ \t\r]*\n+/).map(function (s) {
      return s.trim();
    });
    // Keep empties only at the tail: a trailing blank line is the turn still
    // being written, not a message. Interior blanks are just spacing.
    chatSegs = parts.filter(function (s, i) {
      return s || i === parts.length - 1;
    });
    if (!chatSegs.length) chatSegs = [''];
    chatTarget = chatSegs[chatSeg] || '';
  }
  function chatTick() {
    if (chatShown < chatTarget.length) {
      // It should read as someone writing it live, not a reply that pops in
      // (operator's ask). The pace is now the operator's, in characters per
      // second — a fixed one character per 30ms tick was about 33 c/s, near
      // 400 words a minute, which is nobody typing (operator, 2026-08-12).
      //
      // The floor is what stops a long reply crawling: whatever pace is set,
      // the whole thing lands within CHAT_MAX_SECS. That replaced an older
      // "remaining / 60" catch-up which landed any long reply in under two
      // seconds — too fast to read ("no one could keep up", 2026-08-10).
      const total = chatTarget.length;
      const cps = Math.max(chatCps(), total / CHAT_MAX_SECS);
      // Carry the fraction between ticks, or any pace below one character per
      // tick would floor to zero and never move.
      chatCarry += cps * (CHAT_TICK_MS / 1000);
      const step = Math.floor(chatCarry);
      if (step < 1) return;
      chatCarry -= step;
      chatShown = Math.min(total, chatShown + step);
      if (!chatPend) chatPend = 'chat-' + (++capSeq);
      addCaption(chatPend, 'dj', chatTarget.slice(0, chatShown), false);
    }
    if (chatShown < chatTarget.length) return;
    // This piece is fully out. If another is already waiting behind it then
    // its blank line has been SEEN, so this row is finished — close it and
    // start the next message on a row of its own.
    if (chatSeg < chatSegs.length - 1) {
      addCaption(chatPend || ('chat-' + (++capSeq)), 'dj', chatTarget, true);
      chatPend = null;
      chatSeg += 1;
      chatShown = 0;
      chatCarry = 0;
      chatTarget = chatSegs[chatSeg] || '';
      return;
    }
    if (chatDone) {
      // The last piece, and nothing more is coming. A turn that ended on a
      // blank line leaves an empty tail with no row of its own to write.
      if (chatTarget) {
        addCaption(chatPend || ('chat-' + (++capSeq)), 'dj', chatTarget, true);
      }
      chatPend = null;
      chatStopReveal();
      setStatus('', 'connected');
      // The booth typed a line and then went to work: now the words have
      // landed there is room for the cue that was deferred.
      if (typingPending) { typingPending = false; showTyping(); }
    }
  }

  function chatSay(who, text, final) {
    addCaption('chat-' + (++capSeq), who, text, final !== false);
  }

  // The typing cue: a DJ line whose text is three pulsing dots, appended to
  // the transcript while the booth composes and removed the instant real
  // words or an action card land. One at a time — hidden before shown.
  function showTyping() {
    // A reveal still running IS the "something is happening" signal, so the
    // dots would be a second one — and appending them next to half-written
    // words put "DJ • • •" on the same line as the text the caller was still
    // reading (operator screenshot, 2026-08-12). This happens whenever the
    // booth types a line and THEN reaches for a tool: the server sends
    // `typing` again while the first line is mid-reveal. Defer until the
    // words have landed; chatTick picks it back up.
    if (chatTimer) { typingPending = true; return; }
    hideTyping();
    capBox.classList.add('on');
    const node = document.createElement('p');
    node.className = 'cap dj typing';
    node.id = 'chatTyping';
    node.innerHTML = '<span class="who">DJ</span>'
      + '<span class="said"><span class="typedots" aria-label="typing">'
      + '<i></i><i></i><i></i></span></span>';
    capBox.appendChild(node);
    followTranscript();
    notifyHeight();
  }
  function hideTyping() {
    typingPending = false;
    const n = document.getElementById('chatTyping');
    if (n) n.remove();
  }

  function openChat() {
    if (chatOpen || previewMode) return;
    chatOpen = true;
    // Repainted HERE, not left to the next /live poll. The board is only
    // painted when the card is idle, and the poll runs every 20 seconds — so
    // opening the text line left LINES ARE OPEN sitting behind the first
    // messages for as long as it took the poll to come round (operator saw
    // 5-10 seconds of it). Whoever changes the card's state repaints it.
    paintBoard(live);
    // Start the transcript clean: a chat opened after a previous one closed
    // would otherwise show the old conversation's lines under the new
    // greeting. A resumed chat repaints its own turns from the server's
    // `ready` a moment later, so clearing here loses nothing.
    capBox.innerHTML = '';
    capNodes.clear();
    // The text line owns the card now: only the transcript, the input and
    // Close. The mode hides the meters, the talk bar and the Call/Message
    // doors that used to stack under the input and make the card huge.
    setCardMode('chat');
    $('chatRow').hidden = false;
    $('chatBtn').hidden = true;
    document.querySelector('.linebox').classList.add('open');
    capBox.classList.add('on');
    setStatus('Opening the text line…', 'connected');
    // The card just changed shape — the mode dropped the call's bands and the
    // transcript opened — so an embedded frame has to be told, or it clips the
    // text line it can no longer see the bottom of.
    notifyHeight();
    const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
    chatWs = new WebSocket(scheme + location.host + '/chat/ws');
    chatWs.onopen = () => chatWs.send(JSON.stringify({
      type: 'hello',
      chat: localStorage.getItem('callinChat') || '',
      key: callKey() || '',
    }));
    chatWs.onmessage = (e) => {
      let msg; try { msg = JSON.parse(e.data); } catch (err) { return; }
      if (msg.type === 'ready') {
        localStorage.setItem('callinChat', msg.chat || '');
        (msg.turns || []).forEach((t) => chatSay(t.who === 'dj' ? 'dj' : 'you', t.text));
        // A RESUMED thread goes behind the drawer rather than filling the box.
        // The chat id lives in localStorage so a browser can pick a
        // conversation back up, which is right — but it meant a refresh
        // reopened yesterday's transcript over the card's idle state, and the
        // operator did not want to see it there ("these should not persist a
        // page refresh... shouldn't it have a button to show previous
        // transcript?"). It is one click away, and the click is labelled.
        if (msg.turns && msg.turns.length) collapseTranscript();
        setStatus(msg.turns && msg.turns.length
          ? 'Back on the text line' : 'Texting the booth — go ahead', 'connected');
        // NOT on a touch screen. Focusing the input summons the on-screen
        // keyboard the instant the line opens, which covers half the card
        // before the caller has decided to type anything (operator-reported on
        // a phone). On a pointer device the focus costs nothing and saves a
        // click, so it stays there.
        if (!window.matchMedia('(pointer: coarse)').matches) $('chatInput').focus();
      } else if (msg.type === 'refused') {
        // A refused RESUME usually means the old chat aged out server-side:
        // drop the id and let the next attempt start fresh.
        setStatus(msg.error || 'The text line is closed', 'error');
        if (localStorage.getItem('callinChat')) localStorage.removeItem('callinChat');
      } else if (msg.type === 'typing') {
        // The booth is composing: a moving dot by the DJ's name, so a typed
        // reply that takes a second doesn't read as nothing happening (the
        // "requests go nowhere" feeling). Cleared the moment real words or an
        // action card arrive.
        showTyping();
      } else if (msg.type === 'action') {
        hideTyping();
        addSystemLine(msg.icon || '✅', msg.label || 'Action completed', msg.detail || '');
      } else if (msg.type === 'delta') {
        // In "dots" mode the cue STAYS up and nothing is revealed until the
        // reply is whole — that is the difference between the two settings.
        chatRaw += msg.text || '';
        chatResplit();
        if (chatRevealsAsTyped()) {
          hideTyping();
          // Feed the reveal buffer, don't render straight — the ticker types
          // it out at the operator's pace.
          if (!chatTimer) chatTimer = setInterval(chatTick, CHAT_TICK_MS);
        }
      } else if (msg.type === 'done') {
        hideTyping();
        // The final text wins (it's the authoritative wording); let the ticker
        // finish revealing it, then finalise.
        // The final text is the authoritative wording, so re-split from it
        // rather than from the streamed pieces.
        chatRaw = msg.text || chatRaw;
        chatResplit();
        chatDone = true;
        if (!chatRevealsAsTyped()) {
          // Land them whole: the cue was the wait, so there is nothing left
          // to reveal. Each message still gets its own row.
          chatShown = chatTarget.length;
        }
        if (!chatTimer) chatTimer = setInterval(chatTick, CHAT_TICK_MS);
      } else if (msg.type === 'ended') {
        hideTyping();
        // The server confirmed the close (record written, chat dropped) — so
        // the id is dead. Forget it, exactly as a deliberate End does, or the
        // next open sends a stale id the server can only refuse (0.10.57).
        localStorage.removeItem('callinChat');
        // Fold the card back to idle; the transcript stays in the drawer.
        resetChatUI(word('ended', 'Chat ended'));
      }
    };
    chatWs.onclose = () => {
      // A socket that drops on its own (network, server restart) is
      // recoverable — the transcript is server-side and a resume replays it.
      // A deliberate End cleared chatOpen already, so this says nothing then.
      if (chatOpen) setStatus('Text line dropped — send to reconnect', 'error');
      chatWs = null;
    };
  }

  // End a chat deliberately: tell the server (which writes the record and
  // drops the chat), forget the id so the next open is a fresh conversation,
  // and fold the card back to idle. Safe to call with no socket — the reset
  // is the part that always has to happen.
  function endChat() {
    // Captured before the id is forgotten: the chat's record is filed under
    // the id's own tail (chat/session.py write_record), and /call-feedback
    // matches on that same tail — so the id IS the room for rating purposes.
    const endedChat = localStorage.getItem('callinChat') || '';
    const typed = !!capBox.querySelector('.cap.you');
    if (chatWs && chatWs.readyState === 1) {
      try { chatWs.send(JSON.stringify({ type: 'bye' })); } catch (e) { /* closing anyway */ }
    }
    localStorage.removeItem('callinChat');
    resetChatUI(word('ended', 'Chat ended'));
    // After the reset, which folds the card to idle — the bar sits under the
    // idle card exactly as it does after a call. Only a chat the caller
    // actually typed in: an untouched chat writes no record to rate.
    offerFeedback(endedChat, typed && live && live.askChatFeedback);
  }

  function resetChatUI(note) {
    chatOpen = false;
    if (chatWs) { try { chatWs.close(); } catch (e) { /* already gone */ } chatWs = null; }
    chatPend = null; chatText = '';
    chatStopReveal();   // kill any in-flight typewriter so it can't paint after close
    $('chatRow').hidden = true;
    setCardMode('idle');
    collapseTranscript();
    if (note) setStatus(note);
    // The idle card's doors — Call, and whichever of chat/voicemail the
    // operator offers — come back from the live truth, not by hand.
    paintIdleButtons(live || {});
    notifyHeight();
  }

  function sendChat() {
    const input = $('chatInput');
    const text = (input.value || '').trim();
    if (!text) return;
    if (!chatWs || chatWs.readyState !== 1) { openChatSocket(); return; }
    input.value = '';
    chatSay('you', text);
    setStatus('…', 'connected');
    chatWs.send(JSON.stringify({ type: 'msg', text }));
  }

  // Reopen after a drop without losing what was typed — the hello replays
  // the transcript, then the pending message goes on the next press.
  function openChatSocket() {
    chatOpen = false;
    openChat();
  }

  if ($('chatBtn')) $('chatBtn').onclick = () => { if (!room) openChat(); };
  if ($('chatSendBtn')) $('chatSendBtn').onclick = sendChat;
  if ($('chatEndBtn')) $('chatEndBtn').onclick = endChat;
  if ($('chatInput')) {
    $('chatInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); sendChat(); }
    });
    // When the mobile keyboard opens the viewport shrinks (see the viewport
    // meta) and the transcript flexes down — keep the newest lines in view so
    // the caller reads the conversation they're replying to, not blank space.
    $('chatInput').addEventListener('focus', () => {
      setTimeout(pinTranscript, 350);
    });
  }

  // ------------------------------------------------------- soundbite studio
  // The voicemail door's second flow (live.voicemailFlow === 'studio'):
  // record in the BROWSER, review the transcript and the resolved action,
  // then send to air. No LiveKit and no room — the take is assembled here as
  // a 16 kHz mono WAV (MediaRecorder's webm would need a decoder the server
  // doesn't carry) and uploaded once; playback is the local blob, so
  // re-records cost nothing. The server masters, transcribes, and answers
  // with what sending will actually DO — a resolved track, not a guess — and
  // send executes exactly that record. The caller is the reviewer.
  let vmDraft = null, vmRec = null, vmClip = null, vmPlayer = null, vmBusy = false;
  // Set when a HOLD was released while the mic permission prompt still had
  // the start in flight — the recording honours the lift the moment it lands.
  let vmAbortStart = false;
  // The DJ's staged greeting, playing at pickup; and a session counter so a
  // greeting fetched for a studio the caller already closed never plays.
  let vmGreet = null, vmSession = 0, vmRingTimer = 0, vmDialed = false;
  // True when the MACHINE started the station bed itself (nothing was
  // playing when the line rang) — that bed is the machine's to stop.
  let vmBedOwn = false;

  // The machine's own beep, synthesized — one second of the classic tone
  // between the greeting and the message, because a caller who has ever
  // left a voicemail is waiting for it.
  function vmBeep() {
    try {
      const C = window.AudioContext || window.webkitAudioContext;
      const c = new C();
      const o = c.createOscillator(), g = c.createGain();
      o.frequency.value = 1000;
      g.gain.value = Math.min(0.14, 0.14 * (getVolume() / 100));
      o.connect(g); g.connect(c.destination);
      o.start(); o.stop(c.currentTime + 0.8);
      o.onended = () => { try { c.close(); } catch (e) {} };
    } catch (e) { /* a silent beep never blocks the message */ }
  }

  function vmFlow() {
    // While the Live-on-air switch is on the card it owns this choice too:
    // ON routes the message door to the studio (recorded FOR the air, with
    // the review card saying so), OFF to the classic machine (a private
    // message for the DJ) — whatever the operator's global flow says. One
    // switch, one meaning: does what I do next go out on the station?
    const d = shown || live || {};
    // With the switch up and the VOICEMAIL door open, the caller's route
    // decides. A killed voicemail door means private whatever the route
    // says; no switch at all falls back to the operator's legacy flow.
    if (d.onAirCalls && d.onAirCalls.voicemail && !$('routeSwitch').hidden) {
      return onAirPick ? 'studio' : 'machine';
    }
    if (d.onAirCalls && d.onAirCalls.offered && !$('routeSwitch').hidden) {
      return 'machine';
    }
    return d.voicemailFlow || 'machine';
  }

  function vmCeiling() {
    return ((live && live.limits && live.limits.voicemailMaxSeconds) || 30);
  }

  function keyHeaders(extra) {
    // One X-Call-Key header builder (Batch 7): copy form, so a caller's
    // object is never mutated. Was vmKeyHeaders + an identical plKeyHeaders.
    const h = Object.assign({}, extra || {});
    if (callKey()) h['X-Call-Key'] = callKey();
    return h;
  }

  // One biquad low-pass pass (RBJ, Q .707) — the same filter the server's
  // mastering runs, restated here because the aliasing has to die BEFORE
  // the decimation below, which happens in this file.
  function vmLowpass(x, freq, rate) {
    const w0 = 2 * Math.PI * freq / rate;
    const a = Math.sin(w0) / (2 * 0.707), c = Math.cos(w0), n = 1 + a;
    const b0 = (1 - c) / 2 / n, b1 = (1 - c) / n, b2 = (1 - c) / 2 / n;
    const a1 = (-2 * c) / n, a2 = (1 - a) / n;
    let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    const y = new Float32Array(x.length);
    for (let i = 0; i < x.length; i++) {
      const v = x[i];
      const o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
      x2 = x1; x1 = v; y2 = y1; y1 = o;
      y[i] = o;
    }
    return y;
  }

  // Float32 chunks at the context's own rate -> one 16 kHz mono 16-bit WAV.
  // Linear resample, same judgement as the server's reader: fine for speech
  // — but only once band-limited. Unfiltered, the decimation folds every
  // mic frequency above 8 kHz back INTO the voice as inharmonic grit, and
  // the server's drive then amplifies it: "my voice sounds pretty bad" on
  // the first deployed test. Two low-pass passes at 7 kHz first (~-24
  // dB/oct), mirroring the server-side fix in voicemail/master.py.
  function vmToWav(chunks, rate) {
    let n = 0;
    for (const c of chunks) n += c.length;
    let all = new Float32Array(n);
    let off = 0;
    for (const c of chunks) { all.set(c, off); off += c.length; }
    if (rate > 16000) all = vmLowpass(vmLowpass(all, 7000, rate), 7000, rate);
    const outN = Math.floor(n * 16000 / rate);
    const pcm = new Int16Array(outN);
    for (let i = 0; i < outN; i++) {
      const pos = i * (n - 1) / Math.max(1, outN - 1);
      const lo = Math.floor(pos), hi = Math.min(lo + 1, n - 1), fr = pos - lo;
      const v = all[lo] * (1 - fr) + all[hi] * fr;
      pcm[i] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
    }
    const buf = new ArrayBuffer(44 + pcm.length * 2);
    const dv = new DataView(buf);
    const str = (at, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(at + i, s.charCodeAt(i)); };
    str(0, 'RIFF'); dv.setUint32(4, 36 + pcm.length * 2, true); str(8, 'WAVE');
    str(12, 'fmt '); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
    dv.setUint16(22, 1, true); dv.setUint32(24, 16000, true);
    dv.setUint32(28, 32000, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
    str(36, 'data'); dv.setUint32(40, pcm.length * 2, true);
    new Int16Array(buf, 44).set(pcm);
    return new Blob([buf], { type: 'audio/wav' });
  }

  // The studio drives the SAME state chip a call does — its absence was the
  // operator's report ("i dont see any status chips like when we are in call
  // in or text"). The mapping borrows the call's colours: recording reads as
  // listening (the mic is live), working reads as thinking.
  function vmSetChip(state, text) {
    const chip = $('stateChip');
    if (!chip) return;
    chip.hidden = false;
    chip.dataset.state = state;
    $('stateText').textContent = text;
  }

  function vmDropChip() {
    const chip = $('stateChip');
    if (chip) { chip.hidden = true; chip.dataset.state = 'idle'; }
  }

  // One caption line + one action card: the linebox is the studio's stage
  // (operator's redesign — "i dont think we are using the space well in the
  // transcript area"), so the transcript, the action preview and the send
  // receipt all land there, through the same renderers a call uses.
  function vmLine(id, who, text) {
    $('lineBox').classList.add('open');
    capBox.classList.add('on');
    addCaption(id, who, text, true, true);
  }

  function vmClearBox() {
    capNodes.clear();
    delete lastByWho.you;
    delete lastByWho.dj;
    capBox.innerHTML = '';
  }

  function vmPaintButtons(state) {
    const bar = $('vmRecBtn'), main = $('vmRecMain');
    const rec = state === 'recording';
    bar.setAttribute('aria-pressed', rec ? 'true' : 'false');
    bar.classList.toggle('on', rec);
    // ONE OR TWO WORDS — the state and the clock live in the chips above,
    // and the longer labels overflowed the bar on the operator's phone.
    main.textContent = rec ? word('vm_recording', 'Recording')
      : state === 'door' ? word('vm_dial', 'Leave a voicemail')
      : state === 'greeting' ? word('vm_talkover', 'Hold to talk over it')
      : word('vm_record', 'Hold to record');
    bar.disabled = state === 'busy' || state === 'sending';
    // Record state shows the bar row; review shows the 2×2 verb grid
    // (operator's sketch). Sending keeps the grid, disabled, so the card
    // does not jump while the receipt is on its way.
    $('vmBarRow').hidden = state === 'review' || state === 'sending';
    $('vmGrid').hidden = !(state === 'review' || state === 'sending');
    const busyish = state === 'busy' || state === 'sending';
    $('vmCloseBtn').disabled = busyish;
    ['vmPlayBtn', 'vmRerecBtn', 'vmSendBtn', 'vmCancelBtn'].forEach((id) => {
      $(id).disabled = state === 'sending';
    });
  }

  async function vmStartRec() {
    // A fresh take abandons the old draft — server side too, so the audio
    // never outlives the caller's decision to replace it.
    if (vmDraft) {
      fetch('/voicemail/draft/' + vmDraft.id,
            { method: 'DELETE', headers: keyHeaders() }).catch(() => {});
      vmDraft = null;
    }
    if (vmPlayer) { vmPlayer.pause(); vmPlayer = null; }
    // Talk-over: pressing the bar mid-ring or mid-greeting answers it, like
    // the real machine — and a new take restarts the box's story.
    clearTimeout(vmRingTimer);
    stopRinging();
    if (vmGreet) { vmGreet.pause(); vmGreet = null; }
    vmClearBox();
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      setStatus('Microphone blocked — allow it and try again', 'error');
      return;
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    proc.onaudioprocess = (ev) =>
      chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
    // Through a zero gain, or the caller hears themselves: some engines only
    // run a ScriptProcessor that reaches the destination.
    const sink = ctx.createGain();
    sink.gain.value = 0;
    src.connect(proc); proc.connect(sink); sink.connect(ctx.destination);
    anYou = analyserFor(stream.getAudioTracks()[0]);
    // The YOU meter has to MOVE — the analyser was wired but nothing drove
    // the paint loop outside a call, so the bars sat flat while the caller
    // spoke (operator's report). The same tick a call runs, started here,
    // stopped when the take ends.
    if (!rafId) tick();
    vmRec = { ctx, proc, src, sink, stream, chunks, rate: ctx.sampleRate };
    const secs = vmCeiling();
    vmRec.stopTimer = setTimeout(vmStopRec, secs * 1000);
    vmPaintButtons('recording');
    // Its own state, not 'listening': the tape rolling is worth a pulse the
    // steady mic-live green doesn't give (operator's ask, 2026-08-25).
    vmSetChip('recording', word('vm_chip_rec', 'Recording'));
    // The clock chip carries elapsed against the machine's ceiling, exactly
    // as a call's does — the operator's ask: the state and the time live in
    // the chips, not crammed into the bar's own label.
    startTimer(secs);
    setStatus('Let go, or tap, to stop.', 'connected');
    if (vmAbortStart) {
      // The finger lifted while the permission prompt held the start in
      // flight; honour the lift now rather than recording an empty room.
      vmAbortStart = false;
      vmStopRec();
    }
  }

  async function vmStopRec() {
    const r = vmRec;
    if (!r) return;
    vmRec = null;
    clearTimeout(r.stopTimer);
    stopTimer();
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    clearMeters();
    try { r.proc.disconnect(); r.src.disconnect(); r.sink.disconnect(); } catch (e) {}
    r.stream.getTracks().forEach((t) => t.stop());
    try { r.ctx.close(); } catch (e) {}
    anYou = null;
    if (!r.chunks.length) { vmPaintButtons('idle'); vmSetChip('idle', word('vm_chip_ready', 'Ready')); return; }
    vmClip = vmToWav(r.chunks, r.rate);
    vmBusy = true;
    vmPaintButtons('busy');
    // The transcript arrives AFTER the take here, unlike a live call's
    // streaming captions — there is no room and no streaming STT to ride, so
    // the whole clip is transcribed at once on upload. The operator accepted
    // that ("we can still have it for after the voicemail ends"); the chip's
    // job is to make the wait read as work rather than as a hang.
    vmSetChip('thinking', word('vm_chip_busy', 'Listening back…'));
    setStatus('', 'connecting');
    try {
      const resp = await fetch('/voicemail/draft', {
        method: 'POST',
        headers: keyHeaders({ 'Content-Type': 'audio/wav' }),
        body: vmClip,
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || 'the studio is not answering');
      vmDraft = data;
      // The take and its consequence, in the box — a caption line for what
      // was heard, an action card for what send will DO.
      vmLine('vm-take', 'you', data.transcript
        || '(couldn’t make out words — play it back, or record again)');
      const act = data.action || {};
      addSystemLine(act.kind === 'queue' || act.kind === 'request' ? '♪'
                    : act.kind === 'takeover' ? '📻' : '✉',
                    act.label || 'No station action — the message just plays',
                    '');
      vmPaintButtons('review');
      vmSetChip('idle', word('vm_chip_review', 'Ready to send'));
      setStatus('Play it, send it, or record another take.', 'connected');
    } catch (e) {
      vmPaintButtons('idle');
      vmSetChip('idle', word('vm_chip_ready', 'Ready'));
      setStatus(String(e.message || e), 'error');
    } finally {
      vmBusy = false;
      notifyHeight();
    }
  }

  async function vmSend() {
    if (!vmDraft || vmBusy) return;
    vmBusy = true;
    vmPaintButtons('sending');
    vmSetChip('thinking', word('vm_chip_sending', 'Sending to air…'));
    setStatus('', 'connecting');
    try {
      const resp = await fetch('/voicemail/draft/' + vmDraft.id + '/send',
                               { method: 'POST', headers: keyHeaders() });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        throw new Error(data.receipt || data.error || 'that didn’t go out');
      }
      // The receipt joins the story in the box, over the transcript it
      // belongs to — the same card a call's tool runs render as.
      addSystemLine('📡', word('vm_sent', 'On its way to air'),
                    String(data.receipt || ''));
    } catch (e) {
      vmBusy = false;
      vmPaintButtons('review');
      vmSetChip('idle', word('vm_chip_review', 'Ready to send'));
      setStatus(String(e.message || e), 'error');
      return;
    }
    vmBusy = false;
    vmDraft = null;             // the server deleted it, sent or failed
    vmCloseStudio(true);
  }

  function vmOpenStudio() {
    vmDraft = null; vmClip = null;
    vmSession += 1;
    // The SHEET goes away; the music does not — at the door nothing is
    // recording yet, and the operator wants the station playing right up
    // until the line actually rings (vmDial is where the audio stops).
    closePlayer(true);
    vmClearBox();
    $('vmStudio').hidden = false;
    // The idle board may already be painted in the box, and the poll no
    // longer repaints (or hides) anything while the studio is open — so the
    // hand-off is ours: board away, studio in.
    $('idleBoard').hidden = true;
    setCardMode('vmstudio');
    // The DOOR, not the dial tone: opening the studio rings nothing until
    // the caller presses the bar (operator's correction — the machine was
    // dialling itself the moment the page turned). The bar IS the dial.
    vmDialed = false;
    vmPaintButtons('door');
    vmSetChip('idle', word('vm_chip_ready', 'Ready'));
    setStatus(word('vm_open', 'Press the bar to leave the booth a voicemail.'),
              'connected');
    notifyHeight();
  }

  // The legacy machine's whole theater, in order (operator's ask): it
  // RINGS, the booth picks up, the DJ's greeting plays, the beep — and only
  // then the message. The greeting is FETCHED during the ring (the server
  // may be rendering it in the DJ's voice on demand, which takes real
  // seconds), and the line keeps ringing until it arrives — exactly like a
  // phone nobody has answered yet. Holding the bar answers early.
  function vmDial() {
    if (vmDialed) return;
    vmDialed = true;
    const session = vmSession;
    // The station rides UNDER the machine, the same move tune-in makes at a
    // call's pickup (operator's ask): music already playing ducks to the
    // operator's percentage, and a quiet card gets the station PIPED IN at
    // that level — the caller hears the broadcast throughout either way.
    // Whatever was playing before comes back full when the studio closes;
    // a bed the machine started is the machine's to stop.
    playerDucked = true;
    if (playerEl) {
      applyVolume();
    } else if (playerLevel() > 0
               && (((shown || live || {}).stream) || {}).url) {
      vmBedOwn = true;
      startPlayerAudio();
    }
    vmSetChip('connecting', word('vm_chip_ring', 'Ringing'));
    setStatus(word('vm_ringing', 'Calling the machine…'), 'connecting');
    vmPaintButtons('idle');
    startRinging();
    const minRing = new Promise((res) => {
      vmRingTimer = setTimeout(res, 3400);
    });
    const fetchGreet = (async () => {
      try {
        const ctl = new AbortController();
        const cap = setTimeout(() => ctl.abort(), 20000);
        const r = await fetch('/vm-greeting',
                              { headers: keyHeaders(), signal: ctl.signal });
        clearTimeout(cap);
        if (!r.ok) return { url: '', text: '' };
        let text = '';
        try {
          text = decodeURIComponent(r.headers.get('X-Greeting-Text') || '');
        } catch (e) { /* an undecodable header is just no caption */ }
        return { url: URL.createObjectURL(await r.blob()), text };
      } catch (e) { return { url: '', text: '' }; }
    })();
    Promise.all([minRing, fetchGreet]).then(([, greet]) => {
      if (session !== vmSession || vmRec || cardMode() !== 'vmstudio') {
        if (greet.url) URL.revokeObjectURL(greet.url);
        return;
      }
      stopRinging();
      playSound('pickup');
      // The greeting's WORDS land in the box as the DJ's caption while the
      // voice plays — the operator's ask: the transcript area tells the
      // machine's side of the exchange, not just the caller's.
      if (greet.text) vmLine('vm-greet', 'dj', greet.text);
      vmPlayGreeting(session, greet.url);
    });
  }

  // The answering machine's own voice stays part of the process (operator:
  // "I don't want to lose the voicemail lines from the dj") — the staged
  // greeting the operator rendered per persona plays when the studio picks
  // up, and holding the bar talks over it, exactly like the real machine.
  function vmPlayGreeting(session, blobUrl) {
    // Whatever happened to the greeting — played, missing, refused — the
    // machine still BEEPS and invites the message; the clip is part of the
    // theater, never a gate on it.
    const ready = () => {
      if (session !== vmSession) return;
      if (!vmRec && cardMode() === 'vmstudio' && !vmBusy) {
        vmBeep();
        // The beep gets its card — the moment the machine hands the caller
        // the line, marked in the box where the story is being told.
        addSystemLine('●', word('vm_beep_card',
                                'Beep — hold the bar and say your piece'), '');
        vmPaintButtons('idle');
        vmSetChip('idle', word('vm_chip_ready', 'Ready'));
        setStatus(word('vm_howto', 'Record a message — you can play it back '
          + 'and review it before anything is sent. Once you approve it, '
          + 'the DJ airs it.'), 'connected');
      }
    };
    if (!blobUrl) return ready();          // no clip — straight to the beep
    if (session !== vmSession || vmRec || cardMode() !== 'vmstudio') {
      URL.revokeObjectURL(blobUrl);
      return;
    }
    vmGreet = new Audio(blobUrl);
    vmSetChip('speaking', word('vm_chip_greet', 'Greeting'));
    vmPaintButtons('greeting');
    const done = () => {
      URL.revokeObjectURL(blobUrl);
      if (session !== vmSession) return;
      vmGreet = null;
      ready();
    };
    vmGreet.onended = done;
    vmGreet.onerror = done;
    vmGreet.play().catch(done);
  }

  function vmCloseStudio(sent) {
    vmSession += 1;
    clearTimeout(vmRingTimer);
    stopRinging();
    stopTimer();
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    clearMeters();
    if (vmRec) {                 // mid-recording: stop hardware, keep nothing
      const r = vmRec; vmRec = null;
      clearTimeout(r.stopTimer);
      try { r.proc.disconnect(); r.src.disconnect(); r.sink.disconnect(); } catch (e) {}
      r.stream.getTracks().forEach((t) => t.stop());
      try { r.ctx.close(); } catch (e) {}
      anYou = null;
    }
    if (vmPlayer) { vmPlayer.pause(); vmPlayer = null; }
    if (vmGreet) { vmGreet.pause(); vmGreet = null; }
    if (vmDraft) {
      fetch('/voicemail/draft/' + vmDraft.id,
            { method: 'DELETE', headers: keyHeaders() }).catch(() => {});
      vmDraft = null;
    }
    $('vmStudio').hidden = true;
    vmDropChip();
    setCardMode('idle');
    // The ducked bed comes back up — or, if the machine piped it in itself,
    // goes away with the machine. A player a CALL had silenced (the classic
    // machine flow goes through startCall) resumes.
    playerDucked = false;
    if (vmBedOwn) {
      vmBedOwn = false;
      stopPlayerAudio();
    }
    applyVolume();
    resumePlayer();
    // Sent or cancelled, the box's story goes with the studio — leftover
    // captions were still sitting on the idle card after a send (operator's
    // report; the text line already clears itself this way). The status
    // line carries the receipt.
    vmClearBox();
    capBox.classList.remove('on');
    $('lineBox').classList.remove('open');
    setStatus(sent ? word('vm_sent', 'On its way to air') : '');
    refreshLive();
    notifyHeight();
  }

  // The Record button IS the talk bar, in the studio's costume — the
  // operator's standing rule is push-to-talk on every surface, and the first
  // studio build shipped it as a plain click ("i cant do a push to talk on
  // the voicemail path", operator, 2026-08-17). Same shape as bindPtt: press
  // starts, a hold ends when the finger lifts, a TAP latches the recording
  // on until the next tap. Keyboard activation (Enter/Space fire click with
  // no pointer session) keeps the plain toggle.
  (function bindVmRec() {
    const btn = $('vmRecBtn');
    if (!btn) return;
    let downAt = 0, pressed = false, recBefore = false, viaPointer = false;

    btn.addEventListener('pointerdown', (e) => {
      if (vmBusy) return;
      e.preventDefault();
      // The first press on the bar DIALS — the ring, the greeting, the
      // beep — and only after that does pressing it record.
      if (!vmDialed) { viaPointer = true; vmDial(); return; }
      btn.setPointerCapture?.(e.pointerId);
      pressed = true;
      viaPointer = true;
      downAt = Date.now();
      recBefore = !!vmRec;
      vmAbortStart = false;
      if (!vmRec) vmStartRec();          // press always opens the mic
    });
    const release = () => {
      if (!pressed) return;
      pressed = false;
      const held = Date.now() - downAt >= HOLD_MS;
      if (held || recBefore) {
        // The first press ever pauses on the browser's mic permission
        // prompt, so the recording may not exist yet when the finger lifts
        // — remember the intent and vmStartRec honours it on arrival.
        if (vmRec) vmStopRec(); else vmAbortStart = true;
      }
    };
    btn.addEventListener('pointerup', release);
    btn.addEventListener('pointercancel', release);
    // A long press on mobile otherwise raises the OS context menu, which
    // cancels the pointer mid-hold and stops the take — same swallow as the
    // talk bar, for the same reason.
    btn.addEventListener('contextmenu', (e) => e.preventDefault());
    btn.addEventListener('click', () => {
      if (viaPointer) { viaPointer = false; return; }
      if (vmBusy) return;
      if (!vmDialed) { vmDial(); return; }
      if (vmRec) vmStopRec(); else vmStartRec();
    });
    // Space mirrors the hold, exactly as it does on the talk bar — the
    // keycap on the bar promises it ("it should be similar", operator).
    window.addEventListener('keydown', (e) => {
      if (e.code !== 'Space' || e.repeat || pressed) return;
      if (cardMode() !== 'vmstudio' || vmBusy) return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
      e.preventDefault();
      if (!vmDialed) { vmDial(); return; }   // space answers the door too
      pressed = true;
      downAt = Date.now();
      recBefore = !!vmRec;
      vmAbortStart = false;
      if (!vmRec) vmStartRec();
    });
    window.addEventListener('keyup', (e) => {
      if (e.code !== 'Space' || !pressed) return;
      if (cardMode() !== 'vmstudio') return;
      e.preventDefault();
      release();
    });
  })();
  $('vmPlayBtn').onclick = () => {
    if (!vmClip) return;
    if (vmPlayer) { vmPlayer.pause(); vmPlayer = null; }
    vmPlayer = new Audio(URL.createObjectURL(vmClip));
    vmPlayer.play().catch(() => {});
  };
  $('vmRerecBtn').onclick = () => {
    if (vmBusy) return;
    // Clear any stale abort flag first: a take that auto-stopped at the
    // ceiling while the finger was still down set vmAbortStart=true on the
    // eventual release, and rerec bypasses the pointerdown/keydown resets —
    // so the fresh take instantly aborted and recorded nothing (top-down
    // review, 2026-08-28). This is a deliberate new recording, not a
    // cancelled one.
    vmAbortStart = false;
    vmStartRec();
  };
  $('vmSendBtn').onclick = vmSend;
  $('vmCloseBtn').onclick = () => vmCloseStudio(false);
  $('vmCancelBtn').onclick = () => vmCloseStudio(false);

  $('vmBtn').onclick = () => {
    if (room || previewMode) return;
    if (vmFlow() === 'studio') vmOpenStudio(); else startCall(true);
  };

  // ------------------------------------------------------- station player
  // The ribbon at the card's top edge pulls the station down OVER the phone
  // page — a sheet, wiping down like a shade, swiped back up to reveal the
  // card again (the operator's design, 2026-08-17, replacing a swipe-up
  // card mode whose band never painted). Same stream tune-in plays under a
  // call, but as the SUBJECT: full volume, progress, transport. The point
  // is the installed app on a phone: one card that is the station in your
  // pocket. Full page only — an embed's host page usually IS a player, and
  // two of them would double the audio.
  //
  // The audio outlives the sheet on purpose: pushing it back up keeps the
  // music going (the chip stays green and says so), the way every pocket
  // player keeps playing behind its mini bar. Only a live microphone ends
  // it — see startCall and vmOpenStudio — because on speakers the stream
  // comes straight back in through the mic.

  function cardMode() {
    const card = document.querySelector('.card');
    return (card && card.dataset.mode) || 'idle';
  }

  // The guide face's own state — see THE FACES, further down.
  let guideOpen = false, guideHideTimer = 0, guideData = null, guideAt = 0;
  let playerOpen = false, playerHideTimer = 0;

  // Whether this surface can EVER carry the player, whatever the operator has
  // switched on. Split out from playerOffered because the HANDOFF turns on it:
  // the settings page paints a real call card in an iframe, in the same tab,
  // reading the same sessionStorage — and a copy of this file that can never
  // show a player was clearing the operator's own intent the moment the panel
  // painted its preview. A surface with no player does not get a vote on
  // whether the music continues (found driving it, 2026-08-23).
  const playerSurface = !compact && !framed && !previewMode;
  // LOOKS vs SOUND: the settings preview must SHOW the sheet — the operator
  // had no eyes on any player setting (their report, 2026-09-01) — but must
  // never play audio or touch the cross-page handoff (the original reason
  // preview was fenced out: it cleared the operator's intent). So the
  // visual offer relaxes to any full card, and everything with a side
  // effect keeps gating on playerSurface.
  const playerVisual = !compact;

  function playerOffered() {
    const d = shown || live || {};
    return playerVisual && !!d.swipePlayer && !!(d.stream && d.stream.url);
  }

  // Which face the page OPENS on (operator's "Opens on"): the phone, or
  // the player card already slid in. The other is one swipe away either
  // way; nothing about the gesture flips with the start any more.
  function playerIsHome() {
    const d = shown || live || {};
    return !!d.playerStart && playerOffered();
  }

  // The card's own volume, times the machine's duck while the studio holds
  // the line — 0 is a real value and mutes the bed without stopping it.
  function playerLevel() {
    const duck = playerDucked
      ? Math.max(0, Math.min(100,
          (shown && shown.playerDuck != null) ? shown.playerDuck : 10)) / 100
      : 1;
    return Math.min(1, getVolume() / 100) * duck;
  }

  // Whether the STATION IS WANTED in this tab, which is not the same question
  // as whether it is playing right now. It is what crosses to the settings
  // page and back (see shared.js's handoff), and it is set by the caller's own
  // controls only — the voicemail bed starts the player without anyone asking
  // for the station, and a browser refusing to autoplay is not the caller
  // changing their mind. A refusal that turned this off would lose the music
  // one door at a time.
  let playerWanted = false;
  function wantPlayer(on) {
    playerWanted = !!on;
    // Gated HERE rather than at each caller: three surfaces share this tab's
    // sessionStorage and can never show a player, and one gate is one place to
    // get right. See playerSurface.
    if (playerSurface) writePlayerHandoff(playerWanted, getVolume());
  }

  let playerStopped = false;
  // A live stream resumed from the PHONE's own pause — audio focus lost to
  // another app, the lock screen, a notification — carries on from its
  // buffer: seconds to minutes behind the broadcast. The caller then hears
  // one record while the card names another, because the card follows the
  // station's live edge (the operator heard Placebo under a card that said
  // Such Great Heights, 2026-08-31). Our own buttons already rebuild fresh;
  // this covers the resumes that never pass through them. Reloading the SAME
  // element keeps its play-activation (the parking lesson) and reconnects at
  // the live edge. The threshold is generous: our own duck/park pauses ride
  // through here too, and coming back at the live edge is right for those as
  // well — it is a broadcast, not a podcast.
  let plPausedAt = 0;
  function watchLiveDrift(el) {
    if (el.dataset.driftWatched) return;
    el.dataset.driftWatched = '1';
    el.addEventListener('pause', () => {
      plPausedAt = Date.now();
      paintPlayerButtons();
    });
    el.addEventListener('playing', () => {
      const gap = plPausedAt ? (Date.now() - plPausedAt) / 1000 : 0;
      plPausedAt = 0;
      paintPlayerButtons();
      // NEVER while casting: reassigning src ends the receiver's session,
      // and the TV going quiet mid-resume was reported as "play comes out
      // of my phone afterwards" (operator, 2026-09-01). The receiver holds
      // its own buffer at its own edge; drift is the phone's problem.
      if (gap > 5 && playerEl === el && !playerStopped && !plCasting) {
        try {
          const url = el.currentSrc || el.src;
          if (url) { el.src = url; el.load(); el.play().catch(() => {}); }
        } catch (e) { /* stale audio beats no audio */ }
      }
    });
    watchRemoteSession(el);
  }

  // The one fact the whole cast story hangs on: is THIS element's audio on
  // someone else's speakers right now. Everything that would tear the
  // element down (the stop path, the drift resnap) checks it first, because
  // a torn-down element takes the receiver's session with it.
  let plCasting = false;
  function watchRemoteSession(el) {
    if (!el.remote || el.dataset.remoteWatched) return;
    el.dataset.remoteWatched = '1';
    const sync = () => {
      const was = plCasting;
      // The parked element counts: a call parks the player without ending
      // the receiver's session, and forgetting that here is how the resume
      // path would tear a live cast down.
      const cur = playerEl || playerParked;
      plCasting = cur === el && el.remote.state === 'connected';
      // The receiver paints what the media session says — feed it the
      // moment the session lands so the TV never sits on the browser's
      // generic "Playing Google Chrome" card.
      if (plCasting && !was) feedMediaSession();
      paintPlayerButtons();
    };
    el.remote.addEventListener('connect', sync);
    el.remote.addEventListener('connecting', sync);
    el.remote.addEventListener('disconnect', sync);
  }

  function startPlayerAudio() {
    // A preview sheet is a picture of a player: pressing PLAY inside the
    // settings page must not start the broadcast under the operator's
    // editing (the panel has its own transport for listening).
    if (!playerSurface) return;
    if (playerEl) return;
    const s = ((shown || live || {}).stream) || {};
    if (!s.url) return;
    playerDead = false;
    // BEFORE the chain starts: the first candidate is tried synchronously,
    // and a stale stop from the last press would refuse it on arrival.
    playerStopped = false;
    // Metadata BEFORE first audio: a cast handoff that grabs the element
    // early must find the record already named, or the receiver latches
    // the browser's own "Playing Google Chrome" card.
    feedMediaSession();
    playFirstWorking([s.url].concat(s.alternates || []), 0, {
      get: () => playerEl,
      set: (el) => {
        // A stop that landed while the fallback chain was still walking the
        // mounts refuses the late arrival instead of resurrecting the music.
        if (el && playerStopped) {
          try { el.pause(); el.src = ''; } catch (e) {}
          return;
        }
        playerEl = el;
        if (el) watchLiveDrift(el);
      },
      level: playerLevel,
      onPlaying: () => { playerDead = false; paintPlayerButtons(); feedMediaSession(); },
      onDead: () => {
        playerDead = true;
        paintPlayerButtons();
        if (playerOpen) { paintPlayer(); fitPlayerArt(); }
      },
      // The browser wants its one tap first — the sheet opens quiet with
      // PLAY lit, which is the honest reading, not a dead stream.
      onBlocked: () => { playerDead = false; paintPlayerButtons(); },
    });
    paintPlayerButtons();
  }

  function stopPlayerAudio() {
    playerStopped = true;
    // Pressing STOP is the caller saying they do not want it back, even if a
    // call parked it a moment ago.
    playerResume = false;
    unparkPlayer();
    if (playerEl) {
      const el = playerEl;
      playerEl = null;
      try { el.pause(); el.src = ''; } catch (e) {}
      dropMediaSession();
    }
    paintPlayerButtons();
  }

  // The music a call interrupted comes back when the line clears
  // (operator's ask) — audio only, the sheet stays away, and the chip goes
  // green again to say so. Set by startCall at the moment it silences the
  // player. The STUDIO doesn't stop the music at all — it ducks it
  // (playerDucked, declared with the player's own state up top).
  //
  // THE ELEMENT IS PARKED, NOT DESTROYED (operator-reported: the player came
  // back stopped every time). A media element that a user gesture has played
  // stays allowed to play; a BRAND NEW one does not, and this used to throw
  // the old element away and build a fresh Audio() on the way back. The call
  // ends in a promise callback — LiveKit's disconnect, or the DJ hanging up
  // — which is outside the gesture window whatever the caller pressed, so
  // iOS refused the new element's play() with NotAllowedError and onBlocked
  // swallowed it into a lit PLAY button. Parking keeps the activation.
  //
  // The src IS reassigned on the way back, because a paused live stream
  // resumes from its buffer and would run behind the broadcast for the rest
  // of the session. Reassigning rejoins at the live edge, and an element
  // that has already played once keeps its permission across the change.
  let playerResume = false;
  let playerParked = null;

  // Silence the player for a call without losing the right to restart it.
  function parkPlayer() {
    if (!playerEl) return;
    playerParked = playerEl;
    playerEl = null;
    try { playerParked.pause(); } catch (e) {}
    dropMediaSession();
    paintPlayerButtons();
  }

  function resumePlayer() {
    if (!playerResume) return;
    playerResume = false;
    if (playerEl || !playerOffered()) { unparkPlayer(); return; }
    const el = playerParked;
    playerParked = null;
    if (!el) { startPlayerAudio(); return; }
    const s = ((shown || live || {}).stream) || {};
    if (!s.url) { try { el.pause(); el.src = ''; } catch (e) {} return; }
    playerDead = false;
    playerStopped = false;
    playerEl = el;
    try {
      // Same mount it was on. The fallback chain is not re-walked: this one
      // was playing a moment ago, and a stream that has just died is the
      // rarer case than the browser refusing a new element. While the
      // element is CAST, the src stays untouched — reassigning it ends the
      // receiver's session, and the receiver rejoins its own edge anyway.
      if (!plCasting) el.src = s.url;
      el.volume = playerLevel();
      el.muted = playerLevel() <= 0;
      el.play().then(() => {
        if (playerEl !== el) return;
        playerDead = false;
        paintPlayerButtons();
        feedMediaSession();
      }).catch(() => {
        // Refused even parked, or the mount went away while the call ran.
        // Fall back to the full chain, which ends with PLAY lit if the
        // browser will not have it either way.
        if (playerEl !== el) return;
        playerEl = null;
        try { el.pause(); el.src = ''; } catch (e) {}
        startPlayerAudio();
      });
    } catch (e) {
      playerEl = null;
      startPlayerAudio();
    }
    paintPlayerButtons();
  }

  // Arriving with the station still wanted — from the settings page, or from
  // a reload. Nothing is resumed: the element that was playing died with the
  // other document, so this is a fresh start at the LIVE EDGE, which is where
  // a broadcast should be picked up anyway.
  //
  // The browser may refuse it. That is why the intent is not rewritten here —
  // onBlocked leaves PLAY lit with the intent standing, so pressing it once,
  // or walking back to the other page, tries again.
  let handoffTried = false;
  function resumeFromHandoff() {
    if (handoffTried || !playerSurface) return;
    handoffTried = true;
    const h = readPlayerHandoff();
    if (!h) return;
    playerWanted = true;
    // The fader the operator left it on, unless they have already touched
    // this page's own.
    if (h.volume != null && !volTouched) {
      setVolume(h.volume);
      $('volSlider').value = getVolume();
      applyVolume();
    }
    if (playerEl || !playerOffered() || inConversation()) {
      paintPlayerButtons();
      return;
    }
    startPlayerAudio();
  }

  // On the way out — the gear, the back button, a typed address, the tab
  // being hidden on a phone. pagehide rather than unload: unload is not fired
  // reliably on mobile and forfeits the back-forward cache.
  window.addEventListener('pagehide', () => {
    if (playerSurface) writePlayerHandoff(playerWanted, getVolume());
  });

  // Restored from the back-forward cache. This document is the one that went
  // away, so the answer it left behind may not be the answer any more.
  window.addEventListener('pageshow', (e) => {
    if (!e.persisted || !playerSurface) return;
    const h = readPlayerHandoff();
    // Our own pagehide wrote the intent on the way out, so its ABSENCE now is
    // somebody else's no — the settings page pressed stop while this document
    // sat in the cache. Coming back to music the operator just silenced is the
    // one thing worse than coming back to silence.
    if (!h && playerWanted) { wantPlayer(false); stopPlayerAudio(); return; }
    // A cached element comes back paused on some browsers and playing on
    // others; the intent is what says which it should be.
    if (playerEl && playerEl.paused && playerWanted) {
      playerEl.play().catch(() => { stopPlayerAudio(); startPlayerAudio(); });
      return;
    }
    handoffTried = false;
    resumeFromHandoff();
  });

  // A parked element nobody is coming back for — the caller pressed STOP
  // during the call, or the player stopped being offered.
  function unparkPlayer() {
    if (!playerParked) return;
    const el = playerParked;
    playerParked = null;
    try { el.pause(); el.src = ''; } catch (e) {}
  }

  function openPlayer() {
    if (!playerOffered() || inConversation() || playerOpen) return;
    clearTimeout(playerHideTimer);
    const sheet = $('playerView');
    sheet.hidden = false;
    // The sheet must be RENDERED before the class flips, or there is
    // nothing for the transition to slide — the reflow read is the fence.
    void sheet.offsetHeight;
    document.querySelector('.card').classList.add('playeropen');
    playerOpen = true;
    paintPlayer();
    fitPlayerArt();
    // What this caller's key unlocks (skip, unlike, operator mode) — the
    // server's answer, refreshed at every open so a newly-entered code
    // counts without a reload.
    fetchAbilities();
    // NO music yet — opening shows the deck, and PLAY starts it (operator's
    // correction: the sheet was playing the moment it arrived). Music that
    // was already going keeps going.
    paintListenChip();
  }

  function closePlayer(keepAudio) {
    if (!keepAudio) stopPlayerAudio();
    if (!playerOpen) { $('playerView').hidden = true; paintListenChip(); return; }
    playerOpen = false;
    document.querySelector('.card').classList.remove('playeropen');
    // display:none only after the wipe has left. A timer, not transitionend:
    // transition events never fire in a hidden pane, and a sheet stuck
    // half-rendered would hold the card's overflow clipped for ever.
    clearTimeout(playerHideTimer);
    playerHideTimer = setTimeout(() => {
      if (!playerOpen) $('playerView').hidden = true;
    }, 450);
    paintListenChip();
  }

  // The sheet FITS instead of scrolling (operator's ask, 2026-08-19): the
  // art gives its height back first, the info panels compress onto their own
  // scrollbars second, and only a viewport shorter than everything's minimums
  // ever sees the sheet's own scrollbar. Measured rather than styled because
  // the card's height is config-driven — how many bands the operator has on —
  // and CSS cannot read it: the art's ceiling is whatever space is left once
  // everything fixed has its share.
  function fitPlayerArt() {
    const sheet = $('playerView');
    if (!sheet || sheet.hidden) return;
    const scroll = sheet.querySelector('.plscroll');
    const art = sheet.querySelector('.plartwrap');
    if (!scroll || !art) return;
    sheet.style.removeProperty('--plart-cap');   // measure from natural size
    const over = scroll.scrollHeight - scroll.clientHeight;
    if (over <= 0) return;
    const now = art.getBoundingClientRect().height;
    const cap = Math.max(104, Math.floor(now - over));
    if (cap < now) sheet.style.setProperty('--plart-cap', cap + 'px');
  }
  window.addEventListener('resize', () => { if (playerOpen) fitPlayerArt(); });

  // The last REAL record the station named, and when. The station's
  // /now-playing flaps — a slow read, a track transition — and every flap
  // used to blank the whole sheet to "Live broadcast" until the next good
  // poll (operator, 2026-09-01: "out of nowhere the player drops what is
  // playing... fixes after a while"). Same shape as the call card's
  // lastLiveAt blip tolerance: hold the last known record through a gap,
  // give it up after 90s — by then the silence is a fact, not a flap.
  let plLastNp = null, plLastNpAt = 0;
  function heldNowPlaying(d) {
    const np = d.nowPlaying || {};
    if (np.title) {
      plLastNp = np; plLastNpAt = Date.now();
      return np;
    }
    if (plLastNp && Date.now() - plLastNpAt < 90000) return plLastNp;
    return np;
  }

  // One queue entry is ONE LINE (operator, 2026-09-01): title and artist
  // share it and the tail ellipsizes, so the panel's height budget buys
  // more entries rather than taller ones. Module-level because the Booth
  // tab's log rows wear the same anatomy.
  // `lead` is the row's own gutter — the queue position, or a dash for
  // something already played. One column of them turns a list of records
  // into a list with an order (design handoff, 2026-09-03).
  function queueRow(title, sub, lead) {
    const row = document.createElement('div');
    row.className = 'plrow';
    if (lead) {
      const n = document.createElement('span');
      n.className = 'plnum'; n.textContent = lead;
      row.appendChild(n);
    }
    // The two lines share a column so the artist sits UNDER the title —
    // nesting rather than wrapping, because a wrapped flex line puts the
    // second line back at the row's own left edge and the gutter stops
    // being a gutter.
    const box = document.createElement('span');
    box.className = 'pltext';
    const t = document.createElement('span');
    t.className = 'pltit'; t.textContent = title;
    box.appendChild(t);
    if (sub) {
      const s = document.createElement('span');
      s.className = 'plsub'; s.textContent = sub;
      box.appendChild(s);
    }
    row.appendChild(box);
    return row;
  }

  // The player's hero: the sleeve BESIDE the title rather than over it
  // (design handoff, 2026-09-03). Centred, the title had the sheet's whole
  // width and used four lines of it, and the sleeve pushed the booth's own
  // words off the bottom — the "stack of nested boxes" the redesign was
  // asked to break up. Grouped here for the same reason the station row is:
  // index.html's stack is what an embed and a narrow card still want, and
  // the grouping is a layout, not a payload.
  function buildPlayerHero() {
    const scroll = document.querySelector('.plscroll');
    const art = document.querySelector('.plartwrap');
    if (!scroll || !art || scroll.querySelector('.plartblock')) return;
    const block = document.createElement('div');
    block.className = 'plartblock';
    scroll.insertBefore(block, art);
    const meta = document.createElement('div');
    meta.className = 'plmeta';
    block.append(art, meta);
    ['plTrack', 'plAlbum', 'plTags'].forEach((id) => {
      const el = $(id);
      if (el) meta.appendChild(el);
    });
  }
  buildPlayerHero();

  function paintPlayer() {
    const d = shown || live || {};
    const np = heldNowPlaying(d);
    // The count beside the Now-playing words (operator's ask, 2026-08-18):
    // whoever opened the deck is one of the people this number counts. Same
    // rule as the header's — only a number the station actually gave.
    paintListeners('plListeners', 'plListenersN', d);
    const img = $('plArt'), mono = $('plMono'), glow = $('plGlow');
    const ambient = $('plAmbient');
    // The two blurs travel together: the halo behind the sleeve and the
    // wash across the whole sheet are the same picture at two scales.
    const setBlurs = (src) => {
      for (const layer of [glow, ambient]) {
        if (!layer) continue;
        if (src) {
          if (layer.getAttribute('src') !== src) layer.src = src;
          layer.hidden = false;
        } else {
          layer.hidden = true;
        }
      }
    };
    if (img && mono) {
      // The record's own art, else the DJ's photo, else initials — each step
      // taken only when the one before actually failed to load. The glow is a
      // blurred copy of the SAME image, so it recolors per record.
      //
      // THE POLL MUST NOT UN-DO THE FALLBACK. This said `img.hidden = false`
      // on every repaint, twenty seconds apart, so a station whose art the
      // browser cannot fetch got the initials for an instant and the
      // browser's own broken-image glyph for ever after (operator's phone,
      // 2026-09-04). `dataset.want` is what the chain is being run FOR, so a
      // repaint of the same record leaves the result of that chain alone.
      const want = np.art || d.avatar || '';
      const showMono = () => {
        img.hidden = true;
        setBlurs('');
        mono.textContent = monogram(np.artist || d.name);
        mono.hidden = false;
      };
      if (!want) {
        showMono();
      } else if (img.dataset.want !== want) {
        img.dataset.want = want;
        img.hidden = true; mono.hidden = true; setBlurs('');
        img.onload = () => {
          img.hidden = false; mono.hidden = true;
          setBlurs(img.getAttribute('src'));
        };
        img.onerror = () => {
          // One step down the chain, then the initials.
          if (np.art && img.getAttribute('src') === np.art && d.avatar) {
            img.src = d.avatar;
            return;
          }
          showMono();
        };
        img.src = want;
      } else if (img.complete && !img.naturalWidth) {
        showMono();
      }
    }
    $('plTrack').textContent = np.title || d.track
      || (d.onAir ? 'Live broadcast' : 'Nobody in the booth');
    // The analysis strip the station's own player renders — genre · BPM ·
    // key · mood — as chips. Capped where a full mood list would wrap the
    // strip into a paragraph.
    const tags = [].concat(np.genres || []);
    if (np.bpm) tags.push((Math.round(np.bpm * 10) / 10) + ' BPM');
    if (np.key) tags.push(np.key);
    (np.moods || []).forEach((m) => tags.push(m));
    const row = $('plTags');
    if (row) {
      row.innerHTML = '';
      // SIX. Eight ran the strip to a third row, which is the row the art
      // block does not have — the title grew out of the top of it instead.
      tags.slice(0, 6).forEach((t) => {
        const el = document.createElement('span');
        el.className = 'pill';
        el.textContent = t;
        row.appendChild(el);
      });
    }
    $('plAlbum').textContent =
      [np.artist, np.album, np.year].filter(Boolean).join(' · ');

    // UP NEXT: the station's own queue, ALL of it — the operator wants to
    // see what is coming, not the head of the line; the panel body scrolls
    // when the list outgrows it. The pip only goes live when something is
    // actually queued — a lit pip over an empty panel is the kind of lie
    // the board rules exist to prevent.
    const list = d.upNext || [];
    const nextBody = $('plNextBody');
    if (nextBody) {
      nextBody.innerHTML = '';
      if (list.length) {
        list.forEach((nx, i) => {
          nextBody.appendChild(queueRow(
            nx.title,
            [nx.artist, nx.requestedBy ? 'for ' + nx.requestedBy : '']
              .filter(Boolean).join(' · '),
            String(i + 1)));
        });
      } else {
        nextBody.textContent = 'Nothing queued — send a request below.';
      }
    }
    // JUST PLAYED: the queue's short memory, newest first — the answer to
    // "what was that song?". It shares the queue card, behind its own tab;
    // the tab only offers itself while there is history to stand behind it.
    const past = d.justPlayed || [];
    const pastBody = $('plPastBody');
    if (pastBody) {
      pastBody.innerHTML = '';
      past.forEach((px) => {
        // No number on a played record: its place in a queue is over, and
        // counting backwards from the top would read as one.
        pastBody.appendChild(queueRow(px.title, px.artist || '', '\u2014'));
      });
    }
    plQueueCounts = { next: list.length, past: past.length };
    paintQueueTabs();
    // IN THE BOOTH: what the DJ is SAYING — the newest turn of the live
    // session, straight from the station's own booth feed (the operator's
    // correction: the panel restated the identity header, which is already
    // at the top of the card). The DJ's name rides the sub line; the show
    // stands in only while the booth has said nothing yet.
    const boothBody = $('plBoothBody');
    if (boothBody) {
      // WHO is in the booth, beside the label (operator, 2026-09-01: the
      // sheet never said, and "live" was a word the pip already carries).
      // The NAME rides the header line; the SHOW gets the line under it,
      // always; the booth's own words follow. A real fault outranks the
      // name — a dead stream is worth that room.
      // NAME AND SHOW ON ONE LINE, in the well's own header opposite the
      // label — the handoff's FRANCESCA · THE PIAZZA. They were two rows,
      // and the second of them sat beside the DJ's words rather than above
      // them the moment the well started centring what it carries.
      $('plBoothMeta').textContent = playerDead
        ? 'stream unavailable'
        : (d.onAir
            ? [d.name, String(d.show || '').split('·')[0].trim()]
              .filter(Boolean).join(' · ')
            : 'off air');
      boothBody.innerHTML = '';
      const line = d.booth && d.booth.text;
      if (line) {
        // The words alone — the DJ's name and show were a third statement
        // of what the header already says (operator's cut).
        const q = document.createElement('div');
        q.className = 'plquote'; q.textContent = line;
        boothBody.appendChild(q);
      }
      // Nothing stands in when the booth has said nothing: the panel is
      // the DJ, the show, and their words — the tagline was a fourth line
      // nobody asked for (operator, 2026-09-01). And with no words there is
      // no panel: the well is 132px tall by design, which is right when it
      // carries a line and is an empty box with a caption when it does not
      // (operator's phone, 2026-09-04). The queue below takes the room.
      const well = boothBody.closest('.plpanel');
      if (well) well.hidden = !boothBody.children.length;
    }
    refreshHeart(np.title || '');
    paintPlayerButtons();
    paintSegBtn();
  }

  // The header's right side: the local clock alone. It carried the station's
  // weather too until 2026-08-31 — the operator's call: that corner's room
  // belongs to the cast button now, and a listener already knows the sky.
  // The player head's clock RETIRED 2026-09-01 (operator: "it is not a
  // clock app and just gets in the way") — the element and its painter
  // left together, so nothing reaches an id that no longer exists.

  // --- casting the stream ---------------------------------------------------
  // "Play this on my speakers" from the sheet itself. Two platform doors,
  // tried in the order of their reach: the Remote Playback API (Chromium —
  // Cast devices) and Safari's AirPlay picker. Neither exists = no button,
  // rather than a control that does nothing. The button waits for a playing
  // element because both APIs cast an ELEMENT, and it must be pressed inside
  // the user's own gesture.
  function castSupported() {
    return !!(castFramework() || (window.HTMLMediaElement
      && ('remote' in HTMLMediaElement.prototype
          || 'webkitShowPlaybackTargetPicker' in HTMLMediaElement.prototype)));
  }

  // --- the Cast SDK path (Chromecast / Google TV) --------------------------
  // Remote Playback flings our ELEMENT, and for live audio Chrome shows the
  // receiver a bare "Playing Google Chrome" card no matter what the media
  // session says — proven on the operator's own wall: the phone notification
  // named the record, the TV named the browser (2026-09-01). The framework
  // path loads the stream URL on Google's own media receiver instead: the
  // TV plays the mount itself (the phone can even lock), and the receiver
  // paints title, artist and artwork — re-fed on every record change by a
  // fresh load, which rejoins the live edge during the transition it rides.
  let plCastSess = false, castPlayer = null, castCtl = null;
  let plCastLoadedTitle = null, plCastLoadAt = 0;

  function castFramework() {
    return (window.cast && window.cast.framework
            && window.chrome && window.chrome.cast
            && window.chrome.cast.media) ? window.cast.framework : null;
  }

  window.__onGCastApiAvailable = (ok) => { if (ok) initCastApi(); };

  function initCastApi() {
    const fw = castFramework();
    if (!fw || castCtl) return;
    try {
      fw.CastContext.getInstance().setOptions({
        receiverApplicationId:
          window.chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
        autoJoinPolicy: window.chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED,
      });
      castPlayer = new fw.RemotePlayer();
      castCtl = new fw.RemotePlayerController(castPlayer);
      castCtl.addEventListener(
        fw.RemotePlayerEventType.IS_CONNECTED_CHANGED, () => {
          plCastSess = !!castPlayer.isConnected;
          if (plCastSess) {
            // The TV takes the stream; two copies of the broadcast a
            // half-second apart is the alternative.
            if (playerEl) { wantPlayer(true); stopPlayerAudio(); }
            plCastLoadedTitle = null;
            castLoadMedia();
          }
          paintPlayerButtons();
        });
      castCtl.addEventListener(
        fw.RemotePlayerEventType.IS_PAUSED_CHANGED, paintPlayerButtons);
    } catch (e) { /* the fallback paths stand */ }
  }
  // The async CDN script can win the race against the callback above being
  // read — one late check catches that without polling forever.
  setTimeout(initCastApi, 4000);

  function castLoadMedia() {
    const fw = castFramework();
    const sess = fw && fw.CastContext.getInstance().getCurrentSession();
    if (!sess) return;
    const d = shown || live || {};
    const s = d.stream || {};
    if (!s.url) return;
    const np = heldNowPlaying(d);
    try {
      const C = window.chrome.cast;
      const mi = new C.media.MediaInfo(
        new URL(s.url, location.href).href, 'audio/mpeg');
      mi.streamType = C.media.StreamType.LIVE;
      const md = new C.media.MusicTrackMediaMetadata();
      md.title = np.title || d.track || 'Live broadcast';
      md.artist = np.artist || d.name || '';
      md.albumName = np.album || d.show || '';
      const art = np.art || d.avatar;
      if (art) md.images = [new C.Image(new URL(art, location.href).href)];
      mi.metadata = md;
      sess.loadMedia(new C.media.LoadRequest(mi)).then(() => {
        plCastLoadedTitle = md.title;
        plCastLoadAt = Date.now();
        paintPlayerButtons();
      }, () => {});
    } catch (e) { /* the stream keeps playing with the old card */ }
  }

  // A record change re-feeds the receiver. The default receiver only reads
  // metadata at load, so this is a fresh load — it rejoins the live edge
  // inside the transition between records, where a beat of rejoin is the
  // least audible it will ever be. Debounced so a flapping now-playing
  // cannot saw the stream.
  function castFollowTrack() {
    if (!plCastSess) return;
    // The FIRST load must land whatever state the session connected in —
    // mid-song, before /live answered, whatever. A connect-time load that
    // failed or fed the bare fallback used to stick until the next record
    // ("only kicks in on new songs" — operator, 2026-09-01); now every
    // poll retries until the receiver holds a real title.
    const np = heldNowPlaying(shown || live || {});
    if (!plCastLoadedTitle
        || (np.title && plCastLoadedTitle === 'Live broadcast')) {
      castLoadMedia();
      return;
    }
    if (!np.title || np.title === plCastLoadedTitle) return;
    if (Date.now() - plCastLoadAt < 8000) return;
    castLoadMedia();
  }

  function paintCastBtn() {
    const b = $('plCastBtn');
    if (!b) return;
    // ALWAYS on show while the operator's switch is on (2026-09-01): it
    // used to require a live element, which hid it in exactly the states —
    // paused, parked, stopped — where someone wants the picker back to
    // switch speakers or stop casting. Only a browser with no casting API
    // at all removes it.
    const offered = (shown || live || {}).castButton !== false;
    const on = plCasting || plCastSess;
    b.hidden = !(castSupported() && offered);
    b.classList.toggle('casting', on);
    b.setAttribute('aria-label',
                   on ? 'Casting — change or stop' : 'Cast the stream');
  }

  $('plCastBtn').onclick = async () => {
    // The framework path first: the receiver that can actually SHOW the
    // record. Its own dialog is also where an active session is switched
    // or stopped.
    const fw = castFramework();
    if (fw) {
      try { await fw.CastContext.getInstance().requestSession(); }
      catch (e) { /* dialog closed unchosen — a choice, not a fault */ }
      return;
    }
    // No element yet? Start one inside this same gesture — both pickers
    // cast an ELEMENT, and the first mount is tried synchronously, so the
    // prompt below still counts as user-activated.
    if (!playerEl) { wantPlayer(true); startPlayerAudio(); }
    const el = playerEl || playerParked;
    if (!el) return;
    try {
      if (typeof el.webkitShowPlaybackTargetPicker === 'function') {
        el.webkitShowPlaybackTargetPicker();
      } else if (el.remote && el.remote.prompt) {
        // Always the prompt, connected or not — the picker IS the way to
        // switch devices or stop, and gating it on state was the "can't
        // untoggle" report.
        await el.remote.prompt();
      }
    } catch (e) {
      // The picker closing unchosen rejects; that is a choice, not a fault.
    }
  };

  // --- the segment button on the player's ribbon ---------------------------
  // Shown only when /live says THIS caller may trigger one: a guest or the
  // operator, and only with the operator's own switch on. The flag is the
  // server's answer, never a guess from the tier alone — the setting is half
  // of it, and only the server has seen that.
  function paintSegBtn() {
    const btn = $('plSegBtn');
    if (!btn) return;
    const may = !!((shown || live || {}).openLinesTrigger);
    btn.hidden = !may;
    if (!may) closeSegPop();
  }

  function closeSegPop() {
    const pop = $('plSegPop');
    const btn = $('plSegBtn');
    if (pop) pop.hidden = true;
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  if ($('plSegBtn')) {
    $('plSegBtn').onclick = () => {
      const pop = $('plSegPop');
      const open = pop.hidden;
      pop.hidden = !open;
      $('plSegBtn').setAttribute('aria-expanded', String(open));
      if (open) $('plSegSource').focus();
    };

    $('plSegGo').onclick = async () => {
      const go = $('plSegGo');
      const say = $('plSegSay');
      go.disabled = true;
      say.textContent = 'handing it to the booth…';
      try {
        const r = await fetch('/open-lines/open', {
          method: 'POST',
          headers: keyHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            source: $('plSegSource').value,
            minutes: Number($('plSegMins').value) || 0,
          }),
        });
        const d = await r.json().catch(() => ({}));
        if (r.status === 401) {
          // The setting went off, or the code expired, between the paint and
          // the press. Say which rather than showing a bare failure.
          say.textContent = 'you are not signed in for this any more';
          paintSegBtn();
        } else if (!d.ok) {
          // Every refusal names a gate, and they are all worth reading.
          say.textContent = d.why || 'could not start one';
        } else {
          say.textContent = 'it is on air — ' + (d.premise || 'the DJ has the subject');
          setTimeout(closeSegPop, 2600);
        }
      } catch (e) {
        say.textContent = 'could not reach the booth';
      } finally {
        go.disabled = false;
      }
    };
  }

  // ------------------------------------------- the queue card's two tabs
  // Up next and Just played share one card (the operator's third ruling on
  // this pair: side by side was cramped, stacked spent room the sheet
  // hasn't got). 'next' is the resting face; the past tab only offers
  // itself while there is history, and loses the floor if its history
  // empties under it.
  let plTab = 'next';
  let plQueueCounts = { next: 0, past: 0 };

  function paintQueueTabs() {
    const tn = $('plTabNext'), tp = $('plTabPast'), tb = $('plTabBooth');
    const bn = $('plNextBody'), bp = $('plPastBody'), bb = $('plBoothLogBody');
    if (!tn || !tp || !bn || !bp) return;
    tp.hidden = !plQueueCounts.past;
    // The Booth face is the operator's receipt printer — offered only when
    // the server said this caller's key clears operator mode.
    if (tb) tb.hidden = !(plAbilities && plAbilities.command);
    if ((tp.hidden && plTab === 'past')
        || (!tb || tb.hidden) && plTab === 'booth') plTab = 'next';
    const face = plTab;
    const set = (btn, name) => {
      if (!btn) return;
      btn.classList.toggle('on', face === name);
      btn.setAttribute('aria-selected', face === name ? 'true' : 'false');
    };
    set(tn, 'next'); set(tp, 'past'); set(tb, 'booth');
    bn.hidden = face !== 'next';
    bp.hidden = face !== 'past';
    if (bb) bb.hidden = face !== 'booth';
    // Just the three tab headers on the shoulder (operator, 2026-09-01 —
    // the meta words were noise beside them); the pip alone still says
    // whether something is actually queued.
    $('plNextPip').classList.toggle('live',
                                    face === 'next' && !!plQueueCounts.next);
  }

  $('plTabNext').onclick = () => { plTab = 'next'; paintQueueTabs(); };
  $('plTabPast').onclick = () => { plTab = 'past'; paintQueueTabs(); };
  if ($('plTabBooth')) {
    $('plTabBooth').onclick = () => {
      plTab = 'booth'; paintQueueTabs(); refreshBoothLog();
    };
  }

  function paintPlayerButtons() {
    // The word only — the glyphs beside it are CSS-switched off the sheet's
    // playing class, so writing the button's textContent would erase them.
    // A cast element PAUSED is not playing, and the element survives the
    // pause (tearing it down ends the receiver's session) — so the word
    // reads the element's own state, not just its existence. A framework
    // session outranks both: the TV is the player then.
    const playing = plCastSess
      ? !(castPlayer && castPlayer.isPaused)
      : (!!playerEl && !(plCasting && playerEl.paused));
    const wordEl = $('plPlayWord');
    if (wordEl) {
      wordEl.textContent = playing ? 'Pause' : (playerDead ? 'Try again' : 'Play');
    }
    const pv = $('playerView');
    if (pv) pv.classList.toggle('playing', playing);
    paintCastBtn();
    paintListenChip();
  }

  // …and it stands down beside the faces row (design handoff, 2026-09-03).
  // The chip was the only way to the player before that row existed; with
  // the ribbon up the PLAYER cell is the way, for a mouse and a screen
  // reader alike. It also leaves the now-playing rail empty between calls,
  // which is what lets the rail collapse and the phone face keep the same
  // hero height as the other two.
  function paintListenChip() {
    const chip = $('listenChip');
    if (!chip) return;
    if (!playerOffered()) {
      // The operator can pull the player out from under a caller mid-song —
      // honour it on the next poll rather than playing on with the door gone.
      chip.hidden = true;
      // Nothing to carry next door either: the door the music came through
      // has gone.
      if (playerWanted) wantPlayer(false);
      if (playerEl) stopPlayerAudio();
      if (playerOpen) closePlayer();
      paintFaceBar();
      return;
    }
    const idle = cardMode() === 'idle';
    chip.hidden = !idle || playerOpen || faceList().length > 1;
    chip.classList.toggle('playing', !!playerEl);
    chip.textContent = playerEl ? 'Playing' : 'Listen';
    // The faces row tracks the same state the chip does: which card is
    // showing, and whether the player may be gone to at all right now.
    paintFaceBar();
    guideFollowsTheAir();
  }

  // The lock screen's idea of what is playing, on the platforms that ask.
  // Metadata only plus play/pause — the player is one live stream, so there
  // is nothing honest to say for seek or skip.
  function feedMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const d = shown || live || {};
    // The held record, so a station flap never downgrades the lock screen
    // or a cast receiver to the bare fallback mid-song.
    const np = heldNowPlaying(d);
    const art = np.art || d.avatar;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: np.title || d.track || d.name || 'Live broadcast',
        artist: np.artist || d.name || '',
        album: np.album || d.show || '',
        // sizes/type spelled out: some receivers skip artwork they cannot
        // size ahead of fetching.
        artwork: art ? [{ src: new URL(art, location.href).href,
                          sizes: '512x512', type: 'image/jpeg' }] : [],
      });
      // The lock screen is the caller pressing the same two buttons — and
      // while casting, the same rule as the on-sheet button: a real pause
      // on the same element, never the teardown that ends the session.
      navigator.mediaSession.setActionHandler(
        'play', () => {
          wantPlayer(true);
          if (playerEl && playerEl.paused) playerEl.play().catch(() => {});
          else if (!playerEl) startPlayerAudio();
          paintPlayerButtons();
        });
      navigator.mediaSession.setActionHandler(
        'pause', () => {
          wantPlayer(false);
          if (playerEl && plCasting) playerEl.pause();
          else stopPlayerAudio();
          paintPlayerButtons();
        });
    } catch (e) { /* optional kit; the player works without it */ }
  }

  function dropMediaSession() {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.metadata = null;
      navigator.mediaSession.setActionHandler('play', null);
      navigator.mediaSession.setActionHandler('pause', null);
    } catch (e) {}
  }

  // ---- THE FACES. The phone, the player and the programme guide are
  // cards SIDE BY SIDE (operator, 2026-09-02: "swipe left and right to
  // switch between them, and the small selector row on the bottom").
  // Until then the player was a sheet pulled down over the phone by a
  // ribbon, with a second ribbon, a foot grabber and a travelling curtain
  // for the player-first page — all of it went with that model. The phone
  // is the base; each later card is an overlay one layer above the one
  // before it, sliding in from the right following the finger and back out
  // the same way, and the row at the card's foot names every face on offer
  // and switches on a tap. A face the operator switched off is simply not
  // in the row. Touch, not pointer: on a desktop the row is the way across
  // (and the arrow keys). A drag that starts on a field is typing or the
  // fader, and one that runs mostly up or down is a scroll — the axis is
  // decided in the first eight pixels and then OWNED for the rest of the
  // gesture, so a scroll through the queue never turns into a page turn
  // halfway down.
  // Functions, not consts: paintListenChip runs from the first /live paint,
  // and a const this far down the file would still be unassigned.
  function guideOffered() {
    const d = shown || live || {};
    return playerVisual && !!d.guideCard;
  }
  function faceDefs() {
    return [
      { id: 'phone',  btn: 'facePhone',  el: '',           offered: () => true },
      { id: 'player', btn: 'facePlayer', el: 'playerView', offered: () => playerOffered() },
      { id: 'guide',  btn: 'faceGuide',  el: 'guideView',  offered: () => guideOffered() },
    ];
  }
  function faceList() { return faceDefs().filter((f) => f.offered()); }
  function currentFace() {
    return guideOpen ? 'guide' : playerOpen ? 'player' : 'phone';
  }
  // The player with no slide: the page's own start, or a card laid UNDER
  // the guide before the guide slides away to reveal it.
  function openPlayerInstant() {
    const sheet = $('playerView');
    sheet.classList.add('dragging');
    openPlayer();
    void sheet.offsetHeight;
    sheet.classList.remove('dragging');
  }
  function showFace(id) {
    const now = currentFace();
    if (id === now) return;
    if (id === 'guide') { openGuide(); return; }
    if (id === 'player') {
      if (guideOpen) { openPlayerInstant(); closeGuide(); }
      else openPlayer();
      return;
    }
    if (guideOpen) closeGuide();
    if (playerOpen) closePlayer(true);
  }

  // Where the lit rule sits, in faces: 0 is the first, 1.5 is halfway
  // between the second and third. The swipe writes fractions into it as the
  // finger moves, which is what makes the band read as a pager rather than
  // a row of buttons (operator, 2026-09-03: "less like little chips").
  function paintFaceIndicator(pos) {
    const bar = $('faceBar'), ind = $('faceInd');
    if (!bar || !ind) return;
    const n = faceList().length;
    if (n < 2) return;
    const i = Math.max(0, Math.min(n - 1, pos));
    bar.style.setProperty('--face-n', String(n));
    ind.style.width = (100 / n).toFixed(4) + '%';
    ind.style.transform = 'translateX(' + (i * 100).toFixed(3) + '%)';
  }
  function paintFaceBar() {
    const bar = $('faceBar'), card = document.querySelector('.card');
    if (!bar || !card) return;
    // The operator can switch the guide off under a reader — honour it on
    // the next poll, the way the player's own offer is honoured.
    if (guideOpen && !guideOffered()) closeGuide();
    const list = faceList();
    // Looks, not sound: the settings preview shows the row like it shows
    // the sheet (playerVisual), and a compact card or an embed never has
    // a second face to name.
    const many = playerVisual && list.length > 1;
    bar.hidden = !many;
    // The card's foot makes room only while the row is there — a phone-
    // only card keeps its 16px, and the height the embed reports never
    // moves (an embed is compact, so it never gets the row at all).
    card.classList.toggle('faces', many);
    const now = currentFace();
    const idle = cardMode() === 'idle';
    faceDefs().forEach((f) => {
      const b = $(f.btn);
      if (!b) return;
      dressFace(b, f.id);
      b.hidden = !list.some((x) => x.id === f.id);
      b.classList.toggle('on', f.id === now);
      b.setAttribute('aria-current', f.id === now ? 'true' : 'false');
      // A call is not a page to turn: the other tabs stay in the row (so
      // the foot doesn't jump) but sleep until the line is idle again —
      // openPlayer refuses mid-conversation for the same reason.
      b.disabled = f.id !== 'phone' && now === 'phone' && !idle;
    });
    const ind = $('faceInd');
    if (ind) {
      ind.hidden = !many;
      // Not while the finger owns it — a repaint mid-drag would snap the
      // rule back to the face being left.
      if (!faceDragging) {
        ind.classList.remove('dragging');
        paintFaceIndicator(list.findIndex((f) => f.id === now));
      }
    }
  }
  // Icon over word, in the card's own ink. The row is scanned by shape
  // before it is read — three words at 9.5px in one band read as a caption,
  // not as three doors — and the glyphs are the same line drawings the
  // corner controls use, so the card owns one icon language.
  // Drawn once per button: index.html ships the words (which is what the
  // contract test and a screen reader read), and the mark is added beside
  // them here rather than being three more SVGs in the markup of a page
  // most callers see one face of.
  const FACE_ICONS = {
    phone: ['M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 '
            + '19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 '
            + '2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 '
            + '9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 '
            + '2.81.7A2 2 0 0 1 22 16.92z', 2],
    player: ['M3 12v3M7.5 8v11M12 5v16M16.5 9v9M21 12v3', 2],
    guide: ['M3 4.5h18v16H3zM3 9.5h18M8 4.5V2.5M16 4.5V2.5M7 13.5h5', 1.9],
  };
  function dressFace(btn, id) {
    if (btn.dataset.dressed === id) return;
    const spec = FACE_ICONS[id];
    if (!spec) return;
    btn.dataset.dressed = id;
    const word = (btn.textContent || '').trim();
    btn.textContent = '';
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'facesvg');
    svg.setAttribute('width', '17'); svg.setAttribute('height', '17');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', String(spec[1]));
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', spec[0]);
    svg.appendChild(path);
    const lab = document.createElement('span');
    lab.className = 'facelab';
    lab.textContent = word;
    btn.append(svg, lab);
  }

  let faceDragging = false;
  faceDefs().forEach((f) => {
    const b = $(f.btn);
    if (b) b.onclick = () => showFace(f.id);
  });

  // ---- The programme guide itself: the station's week, in the shape of
  // the operator's own guide page — today's shows hour by hour in a strip,
  // then every show with its DJ, tagline and times, opening in place. The
  // payload is /guide's, already normalised: a 7x24 grid of show ids, the
  // shows, a persona index with avatars proxied through this server, and
  // the station's timezone. Built as nodes, never markup: every word here
  // is the station's, and a show named with an angle bracket is a show,
  // not a tag.
  function openGuide() {
    if (!guideOffered() || guideOpen) return;
    if (cardMode() !== 'idle' && !playerOpen) return;
    clearTimeout(guideHideTimer);
    const el = $('guideView');
    el.hidden = false;
    void el.offsetHeight;                   // rendered before the slide
    document.querySelector('.card').classList.add('guideopen');
    guideOpen = true;
    loadGuide();
    paintFaceBar();
  }
  function closeGuide() {
    if (!guideOpen) { $('guideView').hidden = true; return; }
    guideOpen = false;
    document.querySelector('.card').classList.remove('guideopen');
    clearTimeout(guideHideTimer);
    guideHideTimer = setTimeout(() => {
      if (!guideOpen) $('guideView').hidden = true;
    }, 450);
    paintFaceBar();
  }
  // Put a face away after a slide that did not commit — a timer, not
  // transitionend, for the same reason the player's own hide is one.
  function parkFace(id) {
    if (id === 'player') {
      clearTimeout(playerHideTimer);
      playerHideTimer = setTimeout(() => {
        if (!playerOpen) $('playerView').hidden = true;
      }, 450);
    } else if (id === 'guide') {
      clearTimeout(guideHideTimer);
      guideHideTimer = setTimeout(() => {
        if (!guideOpen) $('guideView').hidden = true;
      }, 450);
    }
  }

  // A guide left OPEN went stale: it paints when opened and holds its
  // read for five minutes, so sitting on it through a show change showed
  // the old one until you left and came back (2026-09-03). The card polls
  // every twenty seconds anyway — so every poll repaints the guide from
  // what it already has, which re-reads the clock, and a poll that says
  // the SHOW has changed throws the read away and asks again.
  let guideLastShow = null;
  function guideFollowsTheAir() {
    const d = shown || live || {};
    const name = d.show || '';
    const changed = guideLastShow !== null && name !== guideLastShow;
    guideLastShow = name;
    if (!guideOpen) return;
    if (changed) loadGuide(true);          // a new show: read it again
    else if (guideData) paintGuide();      // same show: just the clock
  }

  async function loadGuide(force) {
    // The server caches the week for five minutes; so does this, so a
    // reader flicking between cards costs the station nothing.
    if (!force && guideData && Date.now() - guideAt < 300000) {
      paintGuide();
      return;
    }
    try {
      const r = await fetch('/guide', { cache: 'no-store' });
      if (!r.ok) throw new Error('guide ' + r.status);
      guideData = await r.json();
      guideAt = Date.now();
    } catch (e) {
      if (!guideData) guideData = { shows: [], personas: [], grid: {}, timezone: '' };
    }
    paintGuide();
  }

  const GUIDE_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
  const GUIDE_DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const WEEK_H = 168;
  // The station's OWN clock — which weekday and hour it is there, from
  // the timezone the schedule was painted in. A listener two zones away
  // still sees the block that is on air, not the one their wall clock
  // would pick. `abs` is the hour of the week, Monday 0h = 0.
  function stationNow(tz) {
    const opts = { weekday: 'short', hour: 'numeric', hour12: false,
                   month: 'short', day: 'numeric' };
    let parts = null;
    try {
      parts = new Intl.DateTimeFormat('en-US', { timeZone: tz || undefined, ...opts })
        .formatToParts(new Date());
    } catch (e) {
      // An unknown zone: the reader's clock, which is at least a clock.
      parts = new Intl.DateTimeFormat('en-US', opts).formatToParts(new Date());
    }
    const get = (type) => (parts.find((x) => x.type === type) || {}).value || '';
    let dayIndex = GUIDE_DAYS.indexOf(get('weekday').slice(0, 3).toLowerCase());
    let hour = parseInt(get('hour'), 10);
    if (dayIndex < 0) dayIndex = (new Date().getDay() + 6) % 7;
    if (isNaN(hour)) hour = new Date().getHours();
    hour %= 24;
    const longDay = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
                     'Saturday', 'Sunday'][dayIndex];
    return { dayIndex, hour, abs: dayIndex * 24 + hour,
             label: longDay + ', ' + get('month') + ' ' + get('day') };
  }
  function fmtHour(h) {
    h = ((h % 24) + 24) % 24;
    return (h % 12 || 12) + (h < 12 ? ' AM' : ' PM');
  }
  function fmtRange(start, end) { return fmtHour(start) + ' – ' + fmtHour(end); }
  // The week as one flat line of 168 hours, Monday first, so a show that
  // runs past midnight is ONE run (10 PM – 6 AM) and not two halves.
  function weekSlots(grid) {
    const out = [];
    GUIDE_DAYS.forEach((d) => {
      const day = (grid && grid[d]) || [];
      for (let h = 0; h < 24; h++) out.push(day[h] || null);
    });
    return out;
  }
  // Runs over the flat week, { id, start, end } in hours of the week, end
  // exclusive. The week is a circle: a run still going at Sunday's last
  // hour joins the one at Monday's first, so `end` can pass 168.
  function weekRuns(slots) {
    const runs = [];
    let cur = null;
    slots.forEach((id, h) => {
      if (id && cur && cur.id === id && cur.end === h) { cur.end = h + 1; return; }
      cur = id ? { id, start: h, end: h + 1 } : null;
      if (cur) runs.push(cur);
    });
    if (runs.length > 1) {
      const first = runs[0], last = runs[runs.length - 1];
      if (first.start === 0 && last.end === WEEK_H && first.id === last.id) {
        last.end = WEEK_H + first.end;
        runs.shift();
      }
    }
    return runs;
  }
  // Every run, plus its echo a week earlier and a week later, so windows
  // near either end of the week see the runs that cross into them.
  function echoed(runs) {
    const out = [];
    runs.forEach((r) => {
      [-WEEK_H, 0, WEEK_H].forEach((d) => out.push({ id: r.id, start: r.start + d, end: r.end + d }));
    });
    return out;
  }
  function onAt(runs, abs) { return runs.find((r) => r.start <= abs && abs < r.end) || null; }
  // The next time this show starts, from the station's now: "On air now",
  // "Today 6 PM", "Tomorrow 6 AM", "Fri 7 AM".
  // The show's next slot, as PARTS: the guide's rows want the time over the
  // day, and `nextAiring` answers with one string because the hero wants a
  // sentence. `next` marks the soonest airing that has not started, which is
  // the row the listing flags NEXT. `now` says the SCHEDULE has this show on
  // right now — a live row without it is the station running outside its
  // slot, which has no scheduled time to print, and printing the next one
  // put "2 PM / Tomorrow" beside ON AIR NOW. Such a row says Now instead;
  // the hero's own flag says why.
  // UP TODAY: the same rows in the order a listing is read — what is on now
  // first, then whatever is on soonest, then the shows with no airing ahead
  // of them at all. The SET is unchanged (one row per show that airs this
  // week); it is the order that turns a roster into a listing. `next` names
  // the one row that earns the NEXT flag: the soonest airing not yet started.
  function upToday(shows, runs, now, liveId) {
    const slotOf = (s) => {
      if (s.id === liveId) return -1;
      const n = nextSlot(runs, s.id, now);
      return n && typeof n.start === 'number' ? n.start : Infinity;
    };
    const rows = shows.slice().sort((a, b) => slotOf(a) - slotOf(b));
    const next = (rows.find(
      (s) => s.id !== liveId && slotOf(s) !== Infinity) || {}).id;
    return { rows, next };
  }
  function nextSlot(runs, id, now) {
    const mine = runs.filter((r) => r.id === id);
    const on = onAt(mine, now.abs);
    if (on) return { time: fmtHour(on.start), day: 'Today', now: true, next: false };
    const ahead = mine.filter((r) => r.start > now.abs)
      .sort((a, b) => a.start - b.start)[0];
    if (!ahead) return null;
    const days = Math.floor(ahead.start / 24) - now.dayIndex;
    return {
      time: fmtHour(ahead.start),
      day: days === 0 ? 'Today' : days === 1 ? 'Tomorrow'
        : GUIDE_DAY_NAMES[((Math.floor(ahead.start / 24) % 7) + 7) % 7],
      start: ahead.start,
      next: false,
    };
  }
  // The half of a show's full name that is not its title — "THE PIAZZA ·
  // Golden-Era Pop" gives "Golden-Era Pop". The station writes them as one
  // string and the guide wants them as two; `title` is already the head of
  // it, so this is the tail rather than a second parse of the same rule.
  function showGenre(show) {
    const full = String(show.name || ''), head = String(show.title || '');
    if (!full || !head || full === head) return '';
    return full.slice(head.length).replace(/^[\s·—-]+/, '').trim();
  }
  function nextAiring(runs, id, now) {
    const mine = runs.filter((r) => r.id === id);
    if (onAt(mine, now.abs)) return 'On air now';
    const ahead = mine.filter((r) => r.start > now.abs).sort((a, b) => a.start - b.start)[0];
    if (!ahead) return '';
    const days = Math.floor(ahead.start / 24) - now.dayIndex;
    const when = days === 0 ? 'Today' : days === 1 ? 'Tomorrow'
      : GUIDE_DAY_NAMES[((Math.floor(ahead.start / 24) % 7) + 7) % 7];
    return when + ' ' + fmtHour(ahead.start);
  }
  // Which days a show airs, grouped by an identical day's pattern:
  // "Mon–Thu 6 AM – 9 AM", "Sat–Sun 6 AM – 10 AM", the way the
  // operator's own guide lists them. A run past midnight belongs to the
  // day it STARTS on.
  function scheduleGroups(runs, id) {
    const byDay = GUIDE_DAYS.map(() => []);
    runs.filter((r) => r.id === id && r.start >= 0 && r.start < WEEK_H).forEach((r) => {
      const day = Math.floor(r.start / 24);
      byDay[day].push([r.start - day * 24, r.end - day * 24]);
    });
    const groups = [];
    byDay.forEach((ranges, day) => {
      if (!ranges.length) return;
      ranges.sort((a, b) => a[0] - b[0]);
      const key = ranges.map((x) => x.join('-')).join(',');
      let g = groups.find((x) => x.key === key);
      if (!g) { g = { key, days: [], ranges }; groups.push(g); }
      g.days.push(day);
    });
    groups.forEach((g) => {
      // Consecutive days read as a span; the rest as a list.
      const parts = [];
      let i = 0;
      while (i < g.days.length) {
        let j = i;
        while (j + 1 < g.days.length && g.days[j + 1] === g.days[j] + 1) j++;
        parts.push(j - i >= 2
          ? GUIDE_DAY_NAMES[g.days[i]] + '–' + GUIDE_DAY_NAMES[g.days[j]]
          : g.days.slice(i, j + 1).map((d) => GUIDE_DAY_NAMES[d]).join(', '));
        i = j + 1;
      }
      g.label = parts.join(', ');
      g.times = g.ranges.map((x) => fmtRange(x[0], x[1])).join(' · ');
    });
    return groups;
  }

  // A show's own colour, steady across the week and both views. The
  // station names no colour, so it is derived from the id — same show,
  // same tone, every paint. Each tone is mixed in CSS from a palette
  // token, because nothing here may hardcode a colour (the theming
  // contract at the top of style.css).
  // TWELVE, not six: a station with a dozen shows had three of them in
  // the same red, and two blocks side by side in one colour say the same
  // thing about two different shows. Six palette tokens at two strengths
  // each — every one still mixed from a token, never a literal.
  const GUIDE_TONES = 12;
  // Assigned IN ORDER, not hashed: a hash over twelve tones put three of
  // this station's shows in the same red, and two blocks in one colour
  // say the same thing about different shows. Position in the station's
  // own show list is stable between reads, so a show keeps its colour —
  // it can shift when the operator adds or removes a show, which is the
  // price of never colliding inside one week.
  let guideToneOf = {};
  function setToneOrder(ids) {
    guideToneOf = {};
    (ids || []).forEach((id, i) => { guideToneOf[id] = String(i % GUIDE_TONES); });
  }
  // The shows that actually AIR this week, in the order they first do —
  // they get the tones first, so a week inside twelve shows never repeats
  // a colour. A show on the roster but off the air this week takes what
  // is left.
  function airingOrder(runs, shows) {
    const seen = [];
    runs.filter((r) => r.start >= 0 && r.start < WEEK_H)
      .sort((a, b) => a.start - b.start)
      .forEach((r) => { if (!seen.includes(r.id)) seen.push(r.id); });
    (shows || []).forEach((s) => { if (!seen.includes(s.id)) seen.push(s.id); });
    return seen;
  }
  function showTone(id) {
    if (guideToneOf[id] !== undefined) return guideToneOf[id];
    let h = 0;                                   // a show not in the list
    for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
    return String(h % GUIDE_TONES);
  }
  function toneDot(id) {
    const d = document.createElement('span');
    d.className = 'gdtone'; d.dataset.tone = showTone(id);
    d.setAttribute('aria-hidden', 'true');
    return d;
  }
  // Which view the guide is in. Day is the strip, the show on air and the
  // list; Week is the grid a listings page paints (operator, 2026-09-03).
  let guideView = 'day';
  function setGuideView(v) {
    guideView = v === 'week' ? 'week' : 'day';
    const week = guideView === 'week';
    [['guideViewDay', !week], ['guideViewWeek', week]].forEach(([id, on]) => {
      const b = $(id);
      if (!b) return;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    ['guideToday', 'guideHero', 'guideListHead', 'guideList'].forEach((id) => {
      const el = $(id);
      if (el) el.classList.toggle('gdaway', week);
    });
    const grid = $('guideGrid'), spans = $('guideSpans');
    if (grid) grid.hidden = !week;
    if (spans) spans.hidden = !week;
    // The room is only measurable once the grid is on screen.
    if (week) { applyGuideSpan(); repaintGuideGrid(); }
    const sc = $('guideScroll');
    if (sc) sc.scrollTop = 0;
    paintGuideTop();
  }
  (function bindGuideViews() {
    const d = $('guideViewDay'), w = $('guideViewWeek');
    if (d) d.onclick = () => setGuideView('day');
    if (w) w.onclick = () => setGuideView('week');
  })();

  // Seven day rows over one hour ruler. A show that runs past midnight is
  // drawn on the day it STARTS and clipped at the row's end, which is what
  // a listings grid does — the block's own label still carries the true
  // hours, and the Day view shows the run whole.
  // HOW MUCH OF THE DAY IS ON SCREEN AT ONCE. Rotating the phone is not
  // always on offer — a folded phone, a screen in a car — so this is a
  // control rather than a hope (operator, 2026-09-03). It names hours,
  // not a zoom level, because that is the question a reader actually has:
  // six hours reads every name, a whole Day fits with no sideways scroll
  // at all and the key underneath decodes the colours.
  const GUIDE_SPANS = [6, 12, 24];
  const GUIDE_DAY_COL = 42;                    // .gddaycol, in CSS
  // The reader's OWN choice, and nothing else — 0 until they press one.
  // It is deliberately not filled in with the default: the grid is
  // measured to pick that default, and it measures 0 while it is still
  // hidden, so filling it in on the first paint locked a rotated phone to
  // six hours for ever (found at 882x344, 2026-09-03).
  let guideSpanChoice = 0;
  function guideRoom() {
    const grid = $('guideGrid');
    return (grid ? grid.clientWidth : 0) - GUIDE_DAY_COL;
  }
  // Un-chosen, the span is the widest that still reads: a phone gets six
  // hours, a wide card twelve — and it re-decides when the room changes,
  // which is what turning a phone on its side does.
  function effectiveSpan() {
    if (guideSpanChoice) return guideSpanChoice;
    return guideRoom() >= 620 ? 12 : 6;
  }
  // The hour's width follows from the span and the room there is, so the
  // grid always fills its width exactly and never leaves a ragged edge.
  function guideHourPx() {
    const room = guideRoom();
    if (room <= 0) return 56;
    return Math.max(11, room / effectiveSpan());
  }
  function applyGuideSpan() {
    const grid = $('guideGrid');
    if (!grid) return;
    const span = effectiveSpan();
    grid.style.setProperty('--gd-hour', guideHourPx().toFixed(2) + 'px');
    GUIDE_SPANS.forEach((n) => {
      const b = $('guideSpan' + n);
      if (!b) return;
      const on = n === span;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  (function bindGuideSpans() {
    GUIDE_SPANS.forEach((n) => {
      const b = $('guideSpan' + n);
      if (b) b.onclick = () => {
        guideSpanChoice = n; applyGuideSpan(); repaintGuideGrid();
      };
    });
    // The room changes when the window does, and on a phone that includes
    // turning it on its side.
    window.addEventListener('resize', () => {
      if (guideOpen && guideView === 'week') { applyGuideSpan(); repaintGuideGrid(); }
    });
  })();
  // Whether a name FITS its block, in the block's own type: 6.4px is one
  // character of the 9.5px mono the label wears. A name that does not fit
  // is left out rather than shown as a stub.
  function fitsChars(span) {
    return Math.floor((span * guideHourPx() - 14) / 6.4);
  }
  // The grid is painted from the last read, so the span control can
  // repaint it without asking the station again.
  let guideGridArgs = null;
  function repaintGuideGrid() {
    if (guideGridArgs) paintGuideGrid.apply(null, guideGridArgs);
  }
  function paintGuideGrid(byId, runs, now) {
    const grid = $('guideGrid');
    if (!grid) return;
    guideGridArgs = [byId, runs, now];
    applyGuideSpan();
    grid.textContent = '';
    const ruler = document.createElement('div'); ruler.className = 'gdruler';
    ruler.appendChild(document.createElement('span')).className = 'gddaycol';
    const track = document.createElement('div'); track.className = 'gdcells';
    for (let h = 0; h < 24; h += 3) {
      const t = document.createElement('span');
      t.className = 'gdhourlab'; t.style.gridColumn = (h + 1) + ' / span 3';
      t.textContent = fmtHour(h).replace(' ', '');
      track.appendChild(t);
    }
    ruler.appendChild(track);
    grid.appendChild(ruler);
    GUIDE_DAYS.forEach((day, dayIndex) => {
      const row = document.createElement('div'); row.className = 'gdgridrow';
      if (dayIndex === now.dayIndex) row.classList.add('today');
      const lab = document.createElement('span'); lab.className = 'gddaycol';
      lab.textContent = GUIDE_DAY_NAMES[dayIndex];
      row.appendChild(lab);
      const cells = document.createElement('div'); cells.className = 'gdcells';
      const start = dayIndex * 24, end = start + 24;
      // Every run that TOUCHES the day, clipped to it — a show that came
      // over from last night fills this morning rather than leaving it
      // blank, which is what a listings grid does. Its label still carries
      // the true hours, and the Day view shows the run whole.
      runs.filter((r) => r.start < end && r.end > start && byId[r.id])
        .sort((a, b) => a.start - b.start)
        .forEach((r) => {
          const show = byId[r.id];
          const from = Math.max(0, r.start - start);
          const span = Math.max(1, Math.min(24, r.end - start) - from);
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'gdcell';
          b.dataset.tone = showTone(r.id);
          b.style.gridColumn = (from + 1) + ' / span ' + span;
          if (r.start <= now.abs && now.abs < r.end) b.classList.add('on');
          const label = show.title || show.name;
          // NO TIME INSIDE THE BLOCK: where it sits and the ruler above
          // already say when, and the second line was eating the room the
          // name needed. And a name that does not FIT is left out rather
          // than shown as a stub — "U…" tells a reader nothing, while
          // clean colour plus the key underneath does (operator,
          // 2026-09-03). The block keeps its full name on hover, and a
          // press opens the show either way.
          if (label.length <= fitsChars(span)) {
            const n = document.createElement('span'); n.className = 'gdcellname';
            n.textContent = label;
            b.appendChild(n);
          }
          b.title = label + ' · ' + fmtRange(r.start, r.end);
          // A block is a way INTO the show: back to the day view with that
          // show open, which is where its description and DJs live.
          b.onclick = () => { setGuideView('day'); openInList(r.id); };
          cells.appendChild(b);
        });
      if (dayIndex === now.dayIndex) {
        const mark = document.createElement('span');
        mark.className = 'gdnowmark'; mark.setAttribute('aria-hidden', 'true');
        mark.style.left = ((now.hour + 0.5) / 24 * 100).toFixed(3) + '%';
        cells.appendChild(mark);
      }
      row.appendChild(cells);
      grid.appendChild(row);
    });
    // The key. Every show that airs this week, in the order it first
    // does — it decodes the colour-only blocks and doubles as the week's
    // roster. Each entry opens its show, like a block.
    const seen = [];
    runs.filter((r) => r.start >= 0 && r.start < WEEK_H && byId[r.id])
      .sort((a, b) => a.start - b.start)
      .forEach((r) => { if (!seen.includes(r.id)) seen.push(r.id); });
    if (!seen.length) return;
    const key = document.createElement('div'); key.className = 'gdkey';
    const cap = document.createElement('div'); cap.className = 'gdcap';
    cap.textContent = 'On the air this week'; key.appendChild(cap);
    const list = document.createElement('div'); list.className = 'gdkeys';
    seen.forEach((id) => {
      const show = byId[id];
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'gdkeyrow';
      b.appendChild(toneDot(id));
      const n = document.createElement('span'); n.className = 'gdkeyname';
      n.textContent = show.title || show.name;
      b.appendChild(n);
      b.onclick = () => { setGuideView('day'); openInList(id); };
      list.appendChild(b);
    });
    key.appendChild(list);
    grid.appendChild(key);
  }

  function paintGuide() {
    const d = guideData || {};
    const shows = d.shows || [], grid = d.grid || {}, personas = {}, byId = {};
    (d.personas || []).forEach((x) => { personas[x.id] = x; });
    shows.forEach((x) => { byId[x.id] = x; });
    const now = stationNow(d.timezone);
    const runs = echoed(weekRuns(weekSlots(grid))).filter((r) => byId[r.id]);
    setToneOrder(airingOrder(runs, shows));
    const today = $('guideToday'), list = $('guideList');
    const empty = $('guideEmpty');
    const head = $('guideTodayHead'), headMeta = $('guideTodayMeta');
    const hero = $('guideHero'), listHead = $('guideListHead');
    const listMeta = $('guideListMeta');
    if (!today || !list) return;
    // Today, hour by hour: every run that touches today's twenty-four
    // hours, the one on air lit, a run from last night shown from where
    // it started.
    const dayStart = now.dayIndex * 24, dayEnd = dayStart + 24;
    const todays = runs.filter((r) => r.start < dayEnd && r.end > dayStart)
      .sort((a, b) => a.start - b.start);   // 12 AM first, then the day
    today.textContent = '';
    today.hidden = !todays.length;
    if (head) head.hidden = !todays.length;
    let onAir = null;
    todays.forEach((r) => {
      const show = byId[r.id];
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'gdslot';
      if (r.start <= now.abs && now.abs < r.end) { b.classList.add('on'); onAir = r; }
      const n = document.createElement('span'); n.className = 'gdname';
      n.textContent = show.title || show.name;
      const t = document.createElement('span'); t.className = 'gdtime';
      t.textContent = fmtRange(r.start, r.end);
      b.append(n, t);
      b.onclick = () => openInList(show.id);
      today.appendChild(b);
    });
    if (headMeta) headMeta.textContent = now.label;
    // UP TODAY — the head belongs to the LISTING now, not to the hour strip
    // it used to caption, so it says what the column under it is. Built once
    // and left; the head is not cleared between paints.
    if (head && !head.querySelector('.gdtodaycap')) {
      const cap = document.createElement('span');
      cap.className = 'gdtodaycap';
      cap.textContent = 'Up today';
      head.insertBefore(cap, head.firstChild);
    }
    const angle = d.onAir && d.onAir.angle ? d.onAir : null;
    // WHAT IS ON is the station's answer, not the clock's. The grid says
    // what is SCHEDULED, and the two part company the moment the booth
    // takes the air: a takeover pins a show outside its slot, and reading
    // the clock alone would name the scheduled show while a different one
    // is playing — a show listed for a different time, shown as current
    // (operator, 2026-09-03). The clock is the fallback for a station
    // that will not say.
    const stationId = (d.onAir && d.onAir.id) || '';
    const liveId = stationId || (onAir ? onAir.id : '');
    const liveShow = byId[liveId];
    // Off schedule: the station is running something this hour does not
    // hold. Named a takeover when the station says a pin is up.
    const offSchedule = !!(stationId && byId[stationId]
                           && (!onAir || onAir.id !== stationId));
    const pinned = !!(d.override && d.override.showId);
    if (offSchedule) {
      // Nothing in the strip is lit: the scheduled block is not what is
      // playing, and lighting it would be the lie this fixes.
      today.querySelectorAll('.gdslot.on').forEach((b) => b.classList.remove('on'));
    }
    // The show on air, open, under the strip: the angle, the show, the DJ
    // and their soul, and where it sits on the week.
    if (hero) {
      hero.textContent = '';
      hero.hidden = !liveShow;
      if (guideHeroOpen === null) {
        const sc = $('guideScroll');
        guideHeroOpen = !sc || sc.clientHeight >= 420;
      }
      if (liveShow) {
        hero.appendChild(guideHero(
          liveShow, castOf(liveShow, personas), runs, now,
          angle && angle.id === liveShow.id ? angle.angle : '',
          // The "until" is the SCHEDULE's, so it is only true while the
          // station is running the schedule.
          offSchedule ? '' : (onAir ? fmtHour(onAir.end) : ''),
          offSchedule ? (pinned ? 'Takeover' : 'Off schedule') : ''));
      }
    }
    // Then the week: every show, the one on air outlined, each opening
    // in place.
    // THE WEEK FIRST. A show on the roster with no hour on the schedule
    // was listed among the ones actually airing, which reads as a station
    // running seventeen shows when twelve are on (operator, 2026-09-03).
    // Those sit under a quiet heading of their own instead, and the
    // operator can leave them out of the card altogether.
    const airs = new Set(runs.map((r) => r.id));
    const onAirThisWeek = shows.filter((s) => airs.has(s.id));
    const shelved = shows.filter((s) => !airs.has(s.id));
    list.textContent = '';
    if (empty) empty.hidden = shows.length > 0;
    if (listHead) listHead.hidden = !onAirThisWeek.length;
    if (listMeta) {
      listMeta.textContent = onAirThisWeek.length
        + (onAirThisWeek.length === 1 ? ' show' : ' shows') + ' this week';
    }
    const ordered = upToday(onAirThisWeek, runs, now, liveId);
    ordered.rows.forEach((show) => {
      const row = guideRow(
        show, personas, runs, now,
        angle && angle.id === show.id ? angle.angle : '', show.id === liveId);
      if (show.id === ordered.next) {
        const f = row.querySelector('.gdflag');
        if (f && !f.textContent) f.textContent = 'Next';
      }
      list.appendChild(row);
    });
    const shelf = $('guideShelf');
    if (shelf) {
      shelf.textContent = '';
      const wanted = shelved.length && (shown || live || {}).guideShelved !== false;
      shelf.hidden = !wanted;
      if (wanted) {
        // Folded to its heading by default, and the whole section at once
        // (operator, 2026-09-03): these shows are not this week's
        // business, so they cost one line until somebody asks for them.
        shelf.classList.toggle('min', !guideShelfOpen);
        const cap = document.createElement('button');
        cap.type = 'button'; cap.className = 'gdcap gdshelfcap';
        const lab = document.createElement('span');
        lab.textContent = 'Not on the schedule';
        const n = document.createElement('span');
        n.className = 'gdshelfn'; n.textContent = String(shelved.length);
        const chev = document.createElement('span');
        chev.className = 'gdshelfchev'; chev.textContent = '▸';
        chev.setAttribute('aria-hidden', 'true');
        cap.append(lab, n, chev);
        const say = () => {
          const open = !shelf.classList.contains('min');
          cap.setAttribute('aria-expanded', open ? 'true' : 'false');
        };
        cap.onclick = () => {
          guideShelfOpen = shelf.classList.toggle('min') === false;
          say();
        };
        say();
        shelf.appendChild(cap);
        shelved.forEach((show) => shelf.appendChild(
          guideRow(show, personas, runs, now, '', false)));
      }
    }
    // The strip STAYS at midnight: it is the day, read forward, and
    // scrolling it to "now" hid the morning behind the left edge.
    today.scrollLeft = 0;
    paintGuideGrid(byId, runs, now);
    const views = $('guideViews');
    if (views) views.hidden = !shows.length;
    paintGuideTop();
  }
  function castOf(show, personas) {
    return [show.personaId].concat(show.guestPersonaIds || [])
      .filter(Boolean).map((id) => personas[id]).filter(Boolean);
  }
  // Open a show in the list and bring it into view — what the strip's
  // blocks do, and what the hero's own "on the schedule" is beside.
  function openInList(id) {
    const list = $('guideList');
    if (!list) return;
    const row = [...list.children].find((el) => el.dataset.show === id);
    if (!row) return;
    guideOpenRows.add(id);
    row.classList.add('open');
    row.setAttribute('aria-expanded', 'true');
    row.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
  // One picture, or the initials when the station has none.
  function guideFace(p) {
    const mono = () => {
      const m = document.createElement('span');
      m.className = 'gdav mono';
      m.textContent = (p.name || '?').slice(0, 2).toUpperCase();
      return m;
    };
    if (!p.avatar) return mono();
    const img = document.createElement('img');
    // NOT loading="lazy": the rows are built only when the card is
    // opened, so they are deferred by construction already — and a lazy
    // image inside an overlay the browser is not painting never enters a
    // viewport at all, so every face stayed pending (found driving the
    // real station, 2026-09-02).
    img.className = 'gdav'; img.src = p.avatar; img.alt = '';
    // A picture the station won't serve becomes the initials, the way the
    // card's own DJ ring does. And a 1x1 PLACEHOLDER is the same nothing
    // wearing a 200: it loads without erroring and stretches one pixel
    // across the circle (p_50fe86 on the operator's own station).
    const fallback = () => { if (img.parentNode) img.replaceWith(mono()); };
    img.onerror = fallback;
    img.onload = () => { if (img.naturalWidth <= 1) fallback(); };
    return img;
  }
  // A press on a face opens it to a portrait, and again puts it back —
  // the pictures are the point of the booth and 34px of them is a hint
  // (operator, 2026-09-03).
  // The WRAPPER carries the press, never the picture itself: a face that
  // fails swaps the <img> for initials, and a binding on the img went with
  // it — the fallback was a dead circle (found on the real station,
  // 2026-09-03).
  function bindFaceZoom(el, person) {
    el.classList.add('gdzoom');
    el.setAttribute('role', 'button');
    el.tabIndex = 0;
    el.title = person.name || '';
    const toggle = (e) => {
      e.stopPropagation();          // never the row's own open/close
      el.classList.toggle('big');
    };
    el.addEventListener('click', toggle);
    el.addEventListener('keydown', (e) => {
      if (e.code === 'Enter' || e.code === 'Space') { e.preventDefault(); toggle(e); }
    });
    return el;
  }
  function guideFaces(cast, cls) {
    const avs = document.createElement('span');
    avs.className = 'gdavs' + (cls ? ' ' + cls : '');
    cast.slice(0, 4).forEach((p) => avs.appendChild(guideFace(p)));
    return avs;
  }
  // What a show SAYS: the angle when it is on air, the show itself, then
  // the DJ and each guest with their own line and soul. Shared by the
  // hero and the rows, so the two can never say different things.
  function guideBody(show, cast, angle, runs, now, cls) {
    const body = document.createElement('div');
    body.className = 'gdbody' + (cls ? ' ' + cls : '');
    const line = (text, lead, klass) => {
      const l = document.createElement('p');
      l.className = 'gdline' + (klass ? ' ' + klass : '');
      if (lead) { const b = document.createElement('b'); b.textContent = lead; l.append(b); }
      if (text) l.append((lead ? ' — ' : '') + text);
      body.appendChild(l);
    };
    // THE SHOW FIRST, then tonight's angle. It was the other way round, and
    // an angle reads as an aside to a description a reader has not met yet.
    // The description is NAMED because the hero shows this paragraph folded
    // (clamped to three lines) and the rest behind the press — the DJ's soul
    // below is a bare `.gdline` too, so class is the only way to tell them
    // apart.
    if (show.description) line(show.description, '', 'gddesc');
    if (angle) line(angle, "Tonight's angle", 'gdangle');
    if (cast.length) {
      const cap = document.createElement('div'); cap.className = 'gdcap';
      cap.textContent = 'In the booth'; body.appendChild(cap);
    }
    cast.forEach((p, i) => {
      const who = document.createElement('div'); who.className = 'gdperson';
      // NOT THE HOST'S FACE TWICE. The hero already carries it at 62px in
      // its own identity row, and the booth block underneath was showing the
      // same picture again fifteen lines down (operator, 2026-09-04). Guests
      // keep theirs — the hero has room for one face, and it is the host's.
      if (!(cls === 'gdherobody' && i === 0)) {
        const fig = document.createElement('span');
        fig.className = 'gdfig';
        fig.appendChild(guideFace(p));
        who.appendChild(bindFaceZoom(fig, p));
      }
      const m = document.createElement('div'); m.className = 'gdpmeta';
      const role = document.createElement('div'); role.className = 'gdrole';
      role.textContent = i === 0 ? 'Host' : 'Guest';
      const nm = document.createElement('div'); nm.className = 'gdpname';
      nm.textContent = p.name;
      m.append(role, nm);
      if (p.tagline) {
        const tg = document.createElement('div'); tg.className = 'gdptag';
        tg.textContent = p.tagline; m.appendChild(tg);
      }
      who.appendChild(m);
      body.appendChild(who);
      if (p.soul) line(p.soul);
    });
    const groups = scheduleGroups(runs, show.id);
    if (groups.length) {
      const sched = document.createElement('div'); sched.className = 'gdsched';
      const cap = document.createElement('div'); cap.className = 'gdcap';
      cap.textContent = 'On the schedule'; sched.appendChild(cap);
      groups.forEach((g) => {
        const r = document.createElement('div'); r.className = 'gdschedrow';
        const dl = document.createElement('span'); dl.className = 'gddays';
        dl.textContent = g.label;
        if (g.days.includes(now.dayIndex)) {
          const t = document.createElement('span');
          t.className = 'gdtodaytag'; t.textContent = 'Today';
          dl.appendChild(t);
        }
        const tm = document.createElement('span'); tm.className = 'gdhours';
        tm.textContent = g.times;
        r.append(dl, tm);
        sched.appendChild(r);
      });
      body.appendChild(sched);
    }
    return body;
  }
  // The show on air arrives open, and folds away: it is the tallest thing
  // on the card, and reading the week past it meant scrolling (operator,
  // 2026-09-03). The choice is remembered across repaints — a poll, a span
  // change — so it does not spring back open under the reader.
  // null until the first paint decides from the room: open on a normal
  // phone, FOLDED on a short one. A landscape phone leaves about 270px
  // for the week, and the on-air card alone is 639 — so it arrived
  // filling the letterbox with nothing of the week behind it. The
  // reader's own press outranks this from then on.
  let guideHeroOpen = null;
  // The shelved section starts FOLDED — see the shelf below.
  let guideShelfOpen = false;
  // Which shows the reader has opened. Remembered because the guide
  // repaints on every poll now, and a repaint that closed the row someone
  // was reading would be worse than the staleness it fixes.
  const guideOpenRows = new Set();
  // How much of the show is left, as the tail of the "until" line — the
  // figure a reader deciding whether to stay actually wants. Only ever from
  // the SCHEDULE's own end: off schedule there is nothing to count down to,
  // and scheduledShowEnd answers 0 there.
  function minsLeft() {
    const endsAt = scheduledShowEnd();
    if (!endsAt) return '';
    const mins = Math.round((endsAt - Date.now()) / 60000);
    return (mins > 0 && mins < 600) ? ' · ' + mins + ' min left' : '';
  }
  // THE HOST'S OWN FACE beside the show's name (design handoff, 2026-09-03):
  // the guide is a page about people, and the only picture on it was 34px
  // down inside a row nobody had opened yet. The line under the name is the
  // genre the show's full name carries after its title, and who runs it —
  // the tagline stands in where the name has no second half to give.
  function heroId(show, cast) {
    const id = document.createElement('div');
    id.className = 'gdheroid';
    if (cast[0]) {
      const fig = document.createElement('span');
      fig.className = 'gdherofig gdfig';
      fig.appendChild(guideFace(cast[0]));
      id.appendChild(bindFaceZoom(fig, cast[0]));
    }
    const names = document.createElement('div');
    names.className = 'gdheronames';
    const name = document.createElement('div');
    name.className = 'gdheroname'; name.textContent = show.title || show.name;
    names.appendChild(name);
    const line2 = [showGenre(show) || show.tagline,
                   cast[0] ? 'with ' + cast[0].name : '']
      .filter(Boolean).join(' · ');
    if (line2) {
      const t = document.createElement('div');
      t.className = 'gdherotag'; t.textContent = line2;
      names.appendChild(t);
    }
    id.appendChild(names);
    return id;
  }
  // The hero's floor: the way into the rest of it, and where the show sits
  // on the week. Pinned to the bottom of the box by the stylesheet, so the
  // press is in the same place whether the hero is clamped or open.
  function heroFoot(fold, runs, id) {
    const foot = document.createElement('div');
    foot.className = 'gdherofoot';
    foot.appendChild(fold);
    const week = document.createElement('span');
    week.className = 'gdheroweek';
    week.textContent = scheduleGroups(runs, id)
      .map((g) => g.label + ' ' + g.times).join(' · ');
    if (week.textContent) foot.appendChild(week);
    return foot;
  }
  function guideHero(show, cast, runs, now, angle, until, flag) {
    const box = document.createElement('div');
    box.className = 'gdherobox' + (guideHeroOpen ? '' : ' min');
    const top = document.createElement('div'); top.className = 'gdherotop';
    const pip = document.createElement('span');
    pip.className = 'ppip live'; pip.setAttribute('aria-hidden', 'true');
    const lab = document.createElement('span');
    lab.className = 'gdherolab'; lab.textContent = 'On air now';
    top.append(pip, lab);
    // The one place the "until" is said. It used to be in the strip's
    // header as well, beside a header meta and a strip label that both
    // named the same show (operator, 2026-09-03: "a little crazy").
    const tail = document.createElement('span');
    tail.className = 'gdheronext';
    const next = nextAiring(runs, show.id, now);
    // Off schedule, the show's next SLOT is not the story and reads as
    // one: "Takeover · Today 2 PM" looks like the takeover starts at two.
    tail.textContent = until ? 'until ' + until
      : (!flag && next && next !== 'On air now' ? next : '');
    if (until) tail.textContent += minsLeft();
    // THE FLAG FIRST, then the tail — it used to be `insertBefore(f, tail)`,
    // and `tail` is only appended when it has words. Off schedule it has
    // none (the SLOT is not the story then) and the flag is exactly what is
    // set, so the two conditions that had to meet for this to throw were the
    // same one: NotFoundError out of guideHero, and the whole guide painted
    // blank behind one console line. Reached on the stub the moment the
    // clock passed the fixture's scheduled hour (2026-09-03); on a real
    // station, any takeover with no scheduled end.
    if (flag) {
      const f = document.createElement('span');
      f.className = 'gdheroflag'; f.textContent = flag;
      f.title = 'The station is running this outside its scheduled slot';
      top.appendChild(f);
    }
    if (tail.textContent) top.appendChild(tail);
    const fold = document.createElement('button');
    fold.type = 'button'; fold.className = 'gdherofold';
    const say = () => {
      const open = !box.classList.contains('min');
      fold.textContent = open ? 'Less' : 'Read more';
      fold.setAttribute('aria-expanded', open ? 'true' : 'false');
      fold.setAttribute('aria-label', open
        ? 'Show less of what is on air' : 'Read more about what is on air');
    };
    fold.onclick = () => {
      guideHeroOpen = box.classList.toggle('min') === false;
      say();
    };
    say();
    box.append(top, heroId(show, cast));
    box.appendChild(guideBody(show, cast, angle, runs, now, 'gdherobody'));
    box.appendChild(heroFoot(fold, runs, show.id));
    return box;
  }
  // A LISTING ROW, not a card (design handoff, 2026-09-03): WHEN in its own
  // column, then the show's tone as a spine, then the name with its flag and
  // the way in. The row used to lead with the name and bury the time in a
  // "next ·" line under the host, which left a column of shows with no order
  // to read it by — and the moods now belong to the OPEN row, because five
  // chips under every collapsed one was the wall of text this was redrawn to
  // stop being.
  function guideRow(show, personas, runs, now, angle, live) {
    const row = document.createElement('div');
    const wasOpen = guideOpenRows.has(show.id);
    row.className = 'gdrow' + (live ? ' live' : '') + (wasOpen ? ' open' : '');
    row.dataset.show = show.id;
    row.setAttribute('role', 'button'); row.tabIndex = 0;
    row.setAttribute('aria-expanded', wasOpen ? 'true' : 'false');
    const cast = castOf(show, personas);
    const slot = nextSlot(runs, show.id, now);
    const head = document.createElement('div'); head.className = 'gdrowhead';
    const when = document.createElement('span'); when.className = 'gdwhen';
    const onNow = live && !(slot && slot.now);   // see nextSlot
    const wt = document.createElement('span'); wt.className = 'gdwhent';
    wt.textContent = onNow ? 'Now' : (slot ? slot.time : '');
    const wd = document.createElement('span'); wd.className = 'gdwhend';
    wd.textContent = onNow ? '' : (slot ? slot.day : '');
    when.append(wt, wd);
    const spine = document.createElement('span');
    spine.className = 'gdspine gdtone'; spine.setAttribute('aria-hidden', 'true');
    spine.dataset.tone = String(showTone(show.id));
    const metaEl = document.createElement('div'); metaEl.className = 'gdrowmain';
    const title = document.createElement('div'); title.className = 'gdrowtop';
    const name = document.createElement('span'); name.className = 'gdrowname';
    name.textContent = show.title || show.name;
    const end = document.createElement('span'); end.className = 'gdrowend';
    const flag = document.createElement('span'); flag.className = 'gdflag';
    flag.textContent = live ? 'On air now'
      : (slot && slot.next ? 'Next' : '');
    const chev = document.createElement('span'); chev.className = 'gdchev';
    chev.textContent = '\u25B8'; chev.setAttribute('aria-hidden', 'true');
    end.append(flag, chev);
    title.append(name, end);
    const sub = document.createElement('div'); sub.className = 'gdrowsub';
    sub.textContent = showGenre(show) || show.tagline || '';
    if (cast[0]) {
      const h = document.createElement('span'); h.className = 'gdrowhost';
      h.textContent = (sub.textContent ? ' · ' : '') + cast[0].name;
      sub.appendChild(h);
    }
    metaEl.append(title, sub);
    head.append(when, spine, metaEl);
    const body = guideBody(show, cast, angle, runs, now);
    if ((show.moods || []).length) {
      const moods = document.createElement('div'); moods.className = 'gdmoods';
      show.moods.slice(0, 5).forEach((m) => {
        const t = document.createElement('span'); t.textContent = m;
        moods.appendChild(t);
      });
      // A DIRECT CHILD as the reference node, never a querySelector: the
      // schedule block carries a `.gdcap` of its own, so on a show whose
      // first one is that nested heading, insertBefore threw NotFoundError
      // and took the whole guide paint down with it — a blank card and one
      // console line (measured in the stub, 2026-09-03).
      const cap = [...body.children].find((n) => n.classList.contains('gdcap'));
      body.insertBefore(moods, cap || null);
    }
    row.append(head, body);
    // ONE AT A TIME (design handoff, 2026-09-03). Every row carries a
    // paragraph, a mood strip and a DJ, and three of them open at once is
    // the wall of text the listing was redrawn to stop being — the reader
    // scrolls past what they already read to reach what they asked for.
    // The set stays the state (a repaint must not close the row somebody is
    // reading); opening simply empties it first.
    const toggle = () => {
      const open = !row.classList.contains('open');
      if (open) {
        guideOpenRows.clear();
        const list = row.parentElement;
        if (list) {
          list.querySelectorAll('.gdrow.open').forEach((other) => {
            other.classList.remove('open');
            other.setAttribute('aria-expanded', 'false');
          });
        }
        guideOpenRows.add(show.id);
      } else {
        guideOpenRows.delete(show.id);
      }
      row.classList.toggle('open', open);
      row.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    row.onclick = toggle;
    row.addEventListener('keydown', (e) => {
      if (e.code === 'Enter' || e.code === 'Space') { e.preventDefault(); toggle(); }
    });
    return row;
  }
  // The way back up: the guide is the one face long enough to get lost
  // in — seventeen shows, each with a paragraph — so the button surfaces
  // once the week has been scrolled into and stands down at the top.
  function paintGuideTop() {
    const sc = $('guideScroll'), btn = $('guideTop');
    if (!sc || !btn) return;
    btn.hidden = sc.scrollTop < 240;
  }
  (function bindGuideTop() {
    const sc = $('guideScroll'), btn = $('guideTop');
    if (!sc || !btn) return;
    sc.addEventListener('scroll', paintGuideTop, { passive: true });
    btn.onclick = () => {
      const was = sc.scrollTop;
      const smooth = !window.matchMedia
        || !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      try { sc.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'auto' }); }
      catch (e) { sc.scrollTop = 0; }
      // A smooth scroll is animated, and an animation needs frames — a
      // tab the browser is not painting gets none, and the press would
      // do nothing at all. If nothing has moved by now, jump.
      setTimeout(() => {
        if (sc.scrollTop > 0 && sc.scrollTop === was) sc.scrollTop = 0;
      }, 400);
    };
  })();

  // How far an overlay face is shown, 0..1: parked off the right edge at 0.
  function paintFaceProgress(el, shownPct) {
    const pct = Math.min(1, Math.max(0, shownPct));
    el.style.transform = 'translateX(' + ((1 - pct) * 100).toFixed(2) + '%)';
  }

  (function bindFaceSwipe() {
    const card = document.querySelector('.card');
    if (!card) return;
    let sx = 0, sy = 0, st = 0, axis = '', fromId = '', toId = '';
    let moving = null, incoming = false, lastX = 0, lastT = 0, vx = 0;

    card.addEventListener('touchstart', (e) => {
      axis = '';
      if (e.touches.length !== 1) return;
      // The fader, the request box, a select: a finger there is using it.
      if (e.target.closest('input, select, textarea')) return;
      // And a strip that scrolls sideways owns its own horizontal drag —
      // the day's hours were unreachable because every swipe across them
      // turned the page instead (operator, 2026-09-03).
      if (e.target.closest('.gdtoday')) return;
      if (faceList().length < 2) return;
      fromId = currentFace();
      if (fromId === 'phone' && cardMode() !== 'idle') return;
      sx = lastX = e.touches[0].clientX; sy = e.touches[0].clientY;
      st = lastT = Date.now(); vx = 0;
      moving = null;
      axis = 'wait';
    }, { passive: true });

    card.addEventListener('touchmove', (e) => {
      if (!axis || axis === 'v') return;
      const t = e.touches[0];
      const dx = t.clientX - sx, dy = t.clientY - sy;
      if (axis === 'wait') {
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
        // Mostly vertical: a scroll, and it stays one.
        if (Math.abs(dy) > Math.abs(dx)) { axis = 'v'; return; }
        const list = faceList();
        const i = list.findIndex((f) => f.id === fromId);
        const to = list[i + (dx < 0 ? 1 : -1)];
        // Pulling towards a face that isn't there is a scroll too.
        if (!to) { axis = 'v'; return; }
        axis = 'h';
        toId = to.id;
        incoming = dx < 0;
        if (incoming) {
          // The arriving card, rendered under the finger from the first
          // pixel of travel.
          moving = $(to.el);
          if (toId === 'player') {
            clearTimeout(playerHideTimer);
            moving.hidden = false;
            paintPlayer();
            fitPlayerArt();
          } else {
            clearTimeout(guideHideTimer);
            moving.hidden = false;
            loadGuide();
          }
        } else {
          // The leaving card, with the one beneath it laid in place first.
          moving = $(faceDefs().find((f) => f.id === fromId).el);
          if (toId === 'player' && !playerOpen) openPlayerInstant();
        }
        moving.classList.add('dragging');
        faceDragging = true;
        const ind = $('faceInd');
        if (ind) ind.classList.add('dragging');
      }
      const now = Date.now();
      if (now > lastT) {
        vx = (t.clientX - lastX) / (now - lastT);   // px per ms, signed
        lastX = t.clientX; lastT = now;
      }
      const w = moving.getBoundingClientRect().width || 1;
      const shown = incoming
        ? Math.min(1, Math.max(0, -dx / w))
        : 1 - Math.min(1, Math.max(0, dx / w));
      paintFaceProgress(moving, shown);
      // The rule travels with the card: leaving `from` for `to` is the
      // same journey, so it reads the same fraction.
      const list = faceList();
      const from = list.findIndex((f) => f.id === fromId);
      const to = list.findIndex((f) => f.id === toId);
      paintFaceIndicator(from + (to - from) * (incoming ? shown : 1 - shown));
    }, { passive: true });

    const settle = (e) => {
      if (axis !== 'h') { axis = ''; return; }
      axis = '';
      moving.classList.remove('dragging');
      faceDragging = false;
      const ind = $('faceInd');
      if (ind) ind.classList.remove('dragging');
      const t = e.changedTouches && e.changedTouches[0];
      const dx = t ? t.clientX - sx : 0;
      const w = moving.getBoundingClientRect().width || 1;
      // Past a fifth of the width it commits; short of that a quick flick
      // in the same direction commits too, the way a page turn feels.
      const flick = Math.abs(vx) > 0.5 && Date.now() - st < 500;
      const commit = incoming
        ? ((-dx / w) > 0.2 || (flick && vx < 0))
        : ((dx / w) > 0.2 || (flick && vx > 0));
      if (commit) showFace(toId);
      else if (incoming) parkFace(toId);   // released short: slide home, put away
      paintFaceBar();                      // the rule settles where it landed
      // Cleared AFTER the state settles, so the transition animates from
      // wherever the finger left the card rather than snapping first.
      moving.style.transform = '';
    };
    card.addEventListener('touchend', settle, { passive: true });
    card.addEventListener('touchcancel', settle, { passive: true });
  })();

  // The keyboard's swipe: left and right arrows walk the row, unless a
  // field has the focus.
  window.addEventListener('keydown', (e) => {
    if (e.code !== 'ArrowLeft' && e.code !== 'ArrowRight') return;
    const t = e.target;
    if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
    const list = faceList();
    if (list.length < 2) return;
    const i = list.findIndex((f) => f.id === currentFace());
    const next = list[i + (e.code === 'ArrowRight' ? 1 : -1)];
    if (next) showFace(next.id);
  });

  // The VU meter: eleven bars built once, each with its own height, pace
  // and phase, so the strip breathes rather than marches. CSS pauses them
  // whenever .player loses .playing — the meter never dances over silence.
  (function buildVu() {
    const vu = $('plVu');
    if (!vu) return;
    [7, 12, 9, 16, 11, 14, 8, 13, 10, 15, 6].forEach((h, i) => {
      const b = document.createElement('span');
      b.style.setProperty('--h', h + 'px');
      b.style.setProperty('--d', (450 + ((i * 137) % 750)) + 'ms');
      b.style.setProperty('--dl', '-' + ((i * 211) % 900) + 'ms');
      vu.appendChild(b);
    });
  })();

  // The heart. Add-only, matching the station's listener like — un-liking
  // is an operator act there, not a listener one. songId rides along so a
  // track change between paint and press gets the station's 409 instead of
  // the wrong record getting the heart.
  let plLiked = false, plLikeSong = null, plHeartFor = '';
  async function refreshHeart(trackKey) {
    const b = $('plHeartBtn');
    if (!b || !playerOpen) return;
    if (trackKey === plHeartFor) return;   // same record, nothing to re-ask
    plHeartFor = trackKey;
    try {
      const r = await fetch('/player/like', { headers: keyHeaders() });
      const d = await r.json();
      if (!r.ok || d.enabled === false) { b.hidden = true; return; }
      plLiked = !!d.liked; plLikeSong = d.songId || null;
      paintHeart(d.count);
    } catch (e) { b.hidden = true; }
  }
  function paintHeart(count) {
    const b = $('plHeartBtn');
    if (!b) return;
    b.hidden = false;
    b.textContent = plLiked ? '♥' : '♡';
    b.classList.toggle('liked', plLiked);
    b.setAttribute('aria-pressed', plLiked ? 'true' : 'false');
    b.title = count ? count + ' likes' : 'Like this track';
    // The number RETIRED (operator, 2026-09-01: "should just show if it's
    // liked") — the filled heart is the whole answer; the count lives on
    // in the hover title for a desktop that asks.
    const n = $('plLikeCount');
    if (n) n.hidden = true;
  }
  $('plHeartBtn').onclick = async () => {
    // A lit heart UN-hearts when the key clears the permission — the
    // station keeps that as an admin write (the operator's own record),
    // so it goes through /player/unlike, never the public like.
    if (plLiked) {
      if (!(plAbilities && plAbilities.unlike) || !plLikeSong) return;
      plLiked = false; paintHeart();    // optimistic; walked back on refusal
      try {
        const r = await fetch('/player/unlike', {
          method: 'POST',
          headers: keyHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ songId: plLikeSong,
                                title: heldNowPlaying(shown || live || {})
                                  .title || '' }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || 'no');
        paintHeart(d.count);
        if (plTab === 'booth') refreshBoothLog();
      } catch (e) {
        plLiked = true; paintHeart();
        flashOpResult('✗  the un-like did not land', true);
      }
      return;
    }
    plLiked = true; paintHeart();       // optimistic; walked back on refusal
    try {
      const r = await fetch('/player/like', {
        method: 'POST',
        headers: keyHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(Object.assign(
          plLikeSong ? { songId: plLikeSong } : {},
          { title: heldNowPlaying(shown || live || {}).title || '' })),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || 'no');
      paintHeart(d.count);
      if (plTab === 'booth') refreshBoothLog();
    } catch (e) {
      // The heart used to just un-fill: indistinguishable from a press
      // that never registered (front-end review, 2026-09-02).
      plLiked = false; paintHeart();
      flashOpResult('✗  the like did not land', true);
    }
  };

  // --- the operator's side of the sheet (2026-09-01) -----------------------
  // What the caller's key unlocks is the SERVER's answer (/player/abilities)
  // — the widget never guesses from the tier, because only the server has
  // seen the password (the segment button's own rule). Fetched at every
  // sheet open: cheap, and a code entered since last time counts.
  let plAbilities = null, plOpMode = false, plOpChat = '';

  async function fetchAbilities() {
    try {
      const r = await fetch('/player/abilities', { headers: keyHeaders() });
      plAbilities = r.ok ? await r.json() : null;
    } catch (e) { plAbilities = null; }
    paintOperatorSide();
  }

  function paintOperatorSide() {
    const a = plAbilities || {};
    const skip = $('plSkipBtn');
    if (skip) skip.hidden = !a.skip;
    const op = $('plOpBtn');
    if (op) op.hidden = !a.command;
    if (!a.command && plOpMode) setOpMode(false);
    // The remembered face comes back the moment the key still clears it.
    let kept = '';
    try { kept = localStorage.getItem('twOpMode') || ''; } catch (e) {}
    if (a.command && kept && !plOpMode) setOpMode(true);
    paintQueueTabs();
  }

  if ($('plSkipBtn')) {
    $('plSkipBtn').onclick = async () => {
      const b = $('plSkipBtn'), msg = $('plReqMsg');
      b.disabled = true;
      try {
        const r = await fetch('/player/skip',
                              { method: 'POST', headers: keyHeaders() });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || 'the station said no');
        if (msg) msg.textContent = '';
        // A skip ends the record for EVERYONE listening and its only
        // acknowledgement was a 1.5s disabled tint (front-end review,
        // 2026-09-02) — the biggest action on the strip, reported like
        // the smallest. It gets the same receipt every other action does.
        flashOpResult('✓  ⏭  Track skipped');
        if (plTab === 'booth') refreshBoothLog();
      } catch (e) {
        flashOpResult('✗  ' + String(e.message || e), true);
      }
      setTimeout(() => { b.disabled = false; }, 1500);
    };
  }

  function setOpMode(on) {
    plOpMode = !!on;
    // The face survives the visit (operator, 2026-09-01): an operator who
    // lives in Do-it mode should not re-arm it every open.
    try { localStorage.setItem('twOpMode', plOpMode ? '1' : ''); }
    catch (e) { /* private windows */ }
    const op = $('plOpBtn'), input = $('plReqInput'), send = $('plReqSend');
    if (op) {
      op.classList.toggle('on', plOpMode);
      op.setAttribute('aria-pressed', plOpMode ? 'true' : 'false');
    }
    if (input) {
      // Each face names itself and then gives EXAMPLES (operator,
      // 2026-09-01): the old operator line listed sentence stems, which
      // read as syntax to obey rather than things it can do.
      input.placeholder = plOpMode
        ? 'Operator: queue song, create mix, schedule takeover'
        : 'Request: an artist, song, or vibe';
    }
    if (send) send.textContent = plOpMode ? 'Do it' : 'Send';
  }
  if ($('plOpBtn')) $('plOpBtn').onclick = () => setOpMode(!plOpMode);
  // A stuck outcome is dismissed by touching it — the only way it leaves
  // other than being replaced by the next command's own answer.
  if ($('plOpFlash')) {
    $('plOpFlash').onclick = () => {
      const f = $('plOpFlash');
      f.hidden = true; f.classList.remove('stuck');
    };
  }

  // One command, one turn of the text line's own brain — the reply's
  // ACTIONS flash where the words were typed and fade (the operator's
  // shape), and the Booth tab carries the durable record.
  async function plSendCommand() {
    const input = $('plReqInput'), btn = $('plReqSend'), msg = $('plReqMsg');
    const text = (input.value || '').trim();
    if (!text) { input.focus(); return; }
    // An hourglass, not a word: "Working" overflowed the button on a
    // narrow phone (operator, 2026-09-01), and the glyph lets the button
    // stay narrow so the input keeps the room.
    btn.disabled = true; btn.innerHTML = BUSY_ICON;
    msg.textContent = '';
    try {
      const r = await fetch('/player/command', {
        method: 'POST',
        headers: keyHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ text, chat: plOpChat }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || 'the booth did not answer');
      plOpChat = d.chat || plOpChat;
      input.value = '';
      // MECHANICAL feedback only (operator, 2026-09-01: no persona, no
      // questions, nothing that shifts the layout): the receipts flash in
      // the row's own fixed overlay — landed actions as their cards, or
      // the server's one-line status when the command degraded to the
      // request line. The DJ never speaks here.
      const acts = (d.actions || [])
        .map((a) => [a.icon, a.label, a.detail && '— ' + a.detail]
          .filter(Boolean).join(' '))
        .join('   ·   ');
      // A landed action or an accepted hand-off both come back as CARDS
      // and both reach the Requests tab, so they may fade. Only a refusal
      // has nowhere else to live, and it stays.
      if (acts) flashOpResult('✓  ' + acts);
      else flashOpResult('✗  ' + (d.note || 'the booth would not take that'),
                         true);
      if (plTab === 'booth') refreshBoothLog();
    } catch (e) {
      flashOpResult('✗  ' + String(e.message || e), true);
    }
    btn.textContent = plOpMode ? 'Do it' : 'Send';
    btn.disabled = false;
  }

  let plFlashT = 0;
  // `sticky` is for every outcome that is NOT a landed action: a command
  // handed to the request line, a refusal, an error. Those used to fade
  // like a receipt and the operator watched one vanish unread — "it just
  // disappears into oblivion" (2026-09-01). Actions fade because the
  // Requests tab keeps them; everything else stays until the next send or
  // a tap, because nothing else will ever show it.
  function flashOpResult(text, sticky) {
    const f = $('plOpFlash');
    if (!f) return;
    f.textContent = text;
    f.hidden = false;
    f.classList.remove('fade');
    f.classList.toggle('stuck', !!sticky);
    clearTimeout(plFlashT);
    if (sticky) return;
    // Next frame, so the transition actually runs from opaque. Held solid
    // for three and a half seconds and gone by four and a half — long
    // enough to read what was invoked, short enough not to sit on the
    // input (the operator's own 4-5s, 2026-09-01).
    requestAnimationFrame(() => f.classList.add('fade'));
    plFlashT = setTimeout(() => { f.hidden = true; }, 4600);
  }

  async function refreshBoothLog() {
    const body = $('plBoothLogBody');
    if (!body) return;
    try {
      const r = await fetch('/player/booth-log', { headers: keyHeaders() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || 'no');
      body.innerHTML = '';
      const ago = (t) => {
        const m = Math.max(0, (Date.now() / 1000 - t) / 60);
        return m < 60 ? Math.round(m) + 'm ago' : Math.round(m / 60) + 'h ago';
      };
      (d.entries || []).forEach((e) => {
        // The action card's own voice: icon + label lead, the detail and
        // the age trail. A muted detail (a shoutout's words never reach
        // this log) leaves the label carrying the line alone.
        body.appendChild(queueRow(
          [e.icon, e.what || e.label].filter(Boolean).join(' '),
          [e.what ? e.label : '', e.tier, ago(e.t)]
            .filter(Boolean).join(' · ')));
      });
      if (!(d.entries || []).length) {
        body.textContent = 'Nothing yet — what the booth does lands here.';
      }
    } catch (e) {
      body.textContent = 'The log could not be read.';
    }
  }

  // The request row: the station's own listener request box, relayed. The
  // button says SENT for a moment (the mockup's beat); a refusal shows the
  // station's own words — they are written for listeners.
  // The working state's mark, DRAWN not typed: the emoji hourglass came
  // from the system font in full colour, which is the one thing the
  // card's icons never are (operator, 2026-09-01). Same line weight and
  // currentColor as every other glyph on this surface.
  const BUSY_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 2h12M6 22h12M6 2c0 4 3 6.5 6 10-3 3.5-6 6-6 10M18 2c0 4-3 6.5-6 10 3 3.5 6 6 6 10"/></svg>';

  async function plSendRequest() {
    // The row's second face: operator mode sends a command, not a request.
    if (plOpMode) return plSendCommand();
    const input = $('plReqInput'), btn = $('plReqSend'), msg = $('plReqMsg');
    const text = (input.value || '').trim();
    if (!text) { input.focus(); return; }
    btn.disabled = true; btn.innerHTML = BUSY_ICON;
    msg.classList.remove('info');
    msg.textContent = '';
    try {
      const r = await fetch('/player/request', {
        method: 'POST',
        headers: keyHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ text }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.success === false) {
        throw new Error(d.message || d.error || 'the booth did not answer');
      }
      input.value = '';
      // The row says what was invoked, the same way a command does — a
      // request used to vanish into a cleared box with only the button
      // whispering "Sent" (operator, 2026-09-01). The booth's own log
      // keeps the event; this is the moment's receipt.
      flashOpResult('🎵  ' + text);
      btn.textContent = 'Sent';
      setTimeout(() => {
        btn.textContent = plOpMode ? 'Do it' : 'Send';
        btn.disabled = false;
      }, 1600);
    } catch (e) {
      msg.textContent = String(e.message || e);
      btn.textContent = plOpMode ? 'Do it' : 'Send';
      btn.disabled = false;
    }
  }
  $('plReqSend').onclick = plSendRequest;
  $('plReqInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') plSendRequest();
  });

  // The player's own volume, and the card's, are ONE volume — two handles
  // on the same fader, kept in step by applyVolume.
  $('plVol').oninput = (e) => {
    volTouched = true; setVolume(+e.target.value); applyVolume();
  };

  // The mute button went home 2026-09-01 (operator: the transport and the
  // fader cover it). plMuted stays a variable applyVolume reads — always
  // false now — so the level maths upstream needed no rewrite.

  // iOS fires :active on a non-anchor ONLY when the document itself
  // carries a touch listener — without this the card's pressed state
  // (style.css) is an Android-only courtesy. Passive and empty: it
  // exists to be registered, nothing more.
  document.addEventListener('touchstart', () => {}, { passive: true });

  $('listenChip').onclick = () => {
    if (cardMode() === 'idle') openPlayer();
  };
  // The strip's phone square: the one-press road to the phone card,
  // audio untouched — the same move as the row's PHONE tab.
  if ($('plPhoneBtn')) $('plPhoneBtn').onclick = () => closePlayer(true);
  $('plPlayBtn').onclick = () => {
    // A framework session: the transport drives the TV, nothing local.
    if (plCastSess && castCtl) {
      castCtl.playOrPause();
      paintPlayerButtons();
      return;
    }
    const el = playerEl;
    // While casting, the button is a REAL pause on the same element: the
    // stop path clears src, which ends the receiver's session, and the
    // next press then played from the phone's own speakers (operator,
    // 2026-09-01). Paused-but-cast, the TV holds the session and play
    // resumes it there.
    if (el && plCasting) {
      if (el.paused) { wantPlayer(true); el.play().catch(() => {}); }
      else { wantPlayer(false); el.pause(); }
      paintPlayerButtons();
      return;
    }
    if (el) { wantPlayer(false); stopPlayerAudio(); }
    else { wantPlayer(true); startPlayerAudio(); }
  };

  hangBtn.onclick = () => endCall(false);
  $('spkBtn').onclick = () => { routeAudio(!onSpeaker); };

  // ------------------------------------------------------- push to talk
  // The bar is the caller's microphone: lit means the DJ can hear them.
  // Whether this surface uses it is the operator's per-surface answer,
  // carried on /live like the corner controls — the server cannot know which
  // surface is asking, so both travel and `framed` picks.
  //
  // Three ways in, one state out: TAP latches the mic open until tapped
  // again, HOLDING the bar is momentary (open on press, shut on release),
  // and space mirrors the hold for a keyboard. The pointer handlers tell tap
  // from hold by how long the press lasted — a latch that only released on a
  // second tap made every hold leave the mic open, which is the one thing a
  // push-to-talk caller trusts it not to do.
  function pttOn() {
    const d = shown || live || {};
    return !!(framed ? d.embedPtt : d.ptt);
  }

  // ------------------------------------------------------- the machine
  // Whether the answering machine may pick up, and when. The policy rides
  // /live so the card can offer "Leave a message" exactly where it paints a
  // refusal — every closed line used to be a dead end.
  let vmCall = false;
  // The phone-in switch. onAirPick is the idle toggle and is never
  // persisted — a fresh load is a private call until the caller flips it
  // again, so nobody lands on air out of habit. onAirCall pins the choice
  // for the call's whole life the moment the button is pressed.
  let onAirPick = false;
  let onAirCall = false;
  // The worker announces the beep over the data channel. It used to gate
  // the caller's mic; now the mic is live from pickup — the machine hears
  // talk-over, like every answering machine — and the beep only moves the
  // status line from "listening" to "recording".
  let vmBeepHeard = false;
  function vmPolicy() {
    return ((shown || live || {}).voicemailWhen) || 'never';
  }

  let pttOpen = false;
  const HOLD_MS = 300;
  // The longest the broadcast may keep the caller's microphone. A BACKSTOP
  // against a worker that never publishes "clear" — not a timer competing
  // with a legitimate hold.
  //
  // It was 20s, chosen when the worker's own ceiling was 90s and a stuck hold
  // could mute a caller indefinitely. Both halves of that reasoning are gone:
  // the worker's unconfirmed ceiling is 15s since 0.10.113, and a measured
  // voice.end ends a hold on the spot. What 20s did instead was fire in the
  // middle of every NORMAL on-air hold, because a real announcement runs
  // longer than that — measured on a call 2026-08-13: 30.4s of speech, a
  // 35.7s hold, and at 20s the caller was handed the microphone and told "the
  // booth is taking a while up there, say your piece" while the DJ still had
  // fifteen seconds to go on the air. From the caller's seat that is the DJ
  // coming back early, and it is the widget doing it, not the guard.
  //
  // 75s clears the longest thing the DJ can legitimately put on air (a
  // station segment, whose own fallback hold is 60s) and still rescues a
  // caller from a hold that genuinely stuck.
  const MAX_HOLD_MS = 75000;
  let holdTimer = 0, holdExpired = false, wasLiveBeforeHold = false;

  // Every mic switch goes through ONE queue, always driving toward the
  // LATEST intent. Firing setMicrophoneEnabled calls concurrently — a tap
  // during the post-connect close, a fast tap-tap — let them resolve out of
  // order, and the reported bug was exactly that: the bar lit, the mic
  // muted, and the DJ telling a caller mid-press to check their microphone.
  let micOp = Promise.resolve();

  function setMicOpen(open) {
    // The broadcast has the microphone, so the caller does not. Until now the
    // card said the DJ had stepped away and then let the caller carry on
    // talking into a line nobody was listening to — everything said during
    // the hold was transcribed against a DJ that could not answer, and the
    // caller only found out when the reply ignored it (operator-reported).
    // Refused HERE rather than at each of the three call sites because this
    // is the single queue every mic switch already goes through.
    if (open && djOnAir && !holdExpired) {
      setStatus('The DJ is on the station mic — hold on', 'info');
      return micOp;
    }
    const wasOpen = pttOpen;
    pttOpen = !!open;
    paintPtt();
    // Releasing the bar is the caller explicitly saying "your turn" — tell
    // the worker, which commits the turn instead of waiting out its
    // endpointing delay against a mic that is already shut (beta-tester
    // report: mute and unmute was ALL release did). Live calls only: the
    // machine has its own clock, and a plain mute button never comes here.
    if (wasOpen && !pttOpen && room && !vmCall && pttOn()) {
      try {
        room.localParticipant.publishData(
          new TextEncoder().encode('end'),
          { reliable: true, topic: 'talkwave.turn-end' });
      } catch (e) { /* endpointing still ends the turn, just slower */ }
    }
    micOp = micOp.then(async () => {
      if (!room) return;
      try {
        // The state may have changed while queued; apply the current one.
        await room.localParticipant.setMicrophoneEnabled(pttOpen);
        // Re-enabling can mint a fresh capture track (the SDK may stop the
        // old one on mute), so the meter re-reads it or it flatlines and
        // reads as muted while the DJ hears fine.
        if (pttOpen) {
          const pub = room.localParticipant.getTrackPublication(
            LivekitClient.Track.Source.Microphone);
          if (pub && pub.track) anYou = analyserFor(pub.track.mediaStreamTrack);
        }
        // Trust, then verify: if the publication disagrees with the bar,
        // one retry, and if it still disagrees the caller is TOLD instead
        // of finding out from the DJ.
        const pub = room.localParticipant.getTrackPublication(
          LivekitClient.Track.Source.Microphone);
        if (pub && pttOpen && pub.isMuted) {
          await room.localParticipant.setMicrophoneEnabled(true);
          if (pub.isMuted) {
            setStatus('The mic did not open — tap the bar again', 'error');
          }
        }
      } catch (e) {
        console.warn('Talk Wave: could not switch the mic —', e);
      }
    });
    return micOp;
  }

  // One place owns the mic-state chip beside the You meter, so the PTT bar and
  // the Mute button can never leave contradictory text on it. 'live' hides it
  // (a moving meter already says you're heard); 'off' is the PTT resting state
  // and reads dim; 'muted' is a deliberate act and stays loud. The label under
  // it is always just "You" — the state rides on the chip, not the label.
  function setMicChip(state) {
    const chip = $('micChip');
    if (chip) {
      chip.classList.remove('off', 'muted');
      if (state === 'off') { chip.textContent = 'Mic off'; chip.classList.add('off'); chip.hidden = false; }
      else if (state === 'muted') { chip.textContent = 'Muted'; chip.classList.add('muted'); chip.hidden = false; }
      else { chip.hidden = true; }
    }
    const label = $('youLabel');
    if (label) label.textContent = 'You';
  }

  function paintPtt() {
    const bar = $('pttBtn');
    if (!bar) return;
    bar.classList.toggle('on', pttOpen);
    bar.setAttribute('aria-pressed', pttOpen ? 'true' : 'false');
    // No "wait for the beep" state: the machine hears the bar from pickup.
    // ONE line of copy. The Space hint used to be half of it ("Tap to talk —
    // or hold Space"), which made the label explain the control twice; the
    // keycap beside it says the same thing in the shape of the key, and hides
    // itself on a coarse pointer where a phone advertising a key it does not
    // have reads as broken. The operator's own wording (word_ptt) still wins.
    // On hold the bar has to say so itself. Disabling it and leaving "Hold to
    // talk" on the face is an instruction the caller cannot follow — they
    // tap, nothing happens, and the only explanation is a line of status text
    // somewhere else on the card (operator's ask, 2026-08-13).
    $('pttMain').textContent =
      (djOnAir && !holdExpired)
              ? "You're on hold — the DJ is on the station mic"
              : pttOpen ? 'Release to send'
              : word('ptt', 'Hold to talk');
    // The meter tells the same story as the bar, in the vocabulary the mute
    // button already taught it.
    if (room && pttOn()) {
      $('meterYou').classList.toggle('muted', !pttOpen);
      setMicChip(pttOpen ? 'live' : 'off');
    }
  }

  (function bindPtt() {
    const bar = $('pttBtn');
    if (!bar) return;
    let downAt = 0, openBeforePress = false, pressed = false;

    bar.addEventListener('pointerdown', (e) => {
      if (!room) return;             // the queue makes a ring-time press safe
      e.preventDefault();
      bar.setPointerCapture?.(e.pointerId);
      pressed = true;
      downAt = Date.now();
      openBeforePress = pttOpen;
      if (!pttOpen) setMicOpen(true);      // press always opens the line
    });
    const release = () => {
      if (!pressed) return;
      pressed = false;
      const held = Date.now() - downAt >= HOLD_MS;
      // A hold ends when the finger lifts; a tap toggles — which after the
      // press-opens rule above means: leave it open if it was shut, shut it
      // if it was open.
      if (held || openBeforePress) setMicOpen(false);
    };
    bar.addEventListener('pointerup', release);
    bar.addEventListener('pointercancel', release);
    // A long press on mobile otherwise raises the OS text-selection / context
    // menu, which cancels the pointer mid-hold and shuts the mic. Swallow it so
    // holding the bar stays a hold. (touch-action:none in the CSS is the main
    // fix; this covers the browsers that still fire it.)
    bar.addEventListener('contextmenu', (e) => e.preventDefault());

    // Space is the hold, not the latch: down opens, up closes. Ignored while
    // typing (the guest-code box is on this page) and without a call to talk
    // on. keydown repeats while held, hence the `repeat` guard.
    addEventListener('keydown', (e) => {
      if (e.code !== 'Space' || e.repeat || !room || !pttOn()) return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
      e.preventDefault();
      if (!pttOpen) setMicOpen(true);
    });
    addEventListener('keyup', (e) => {
      if (e.code !== 'Space' || !room || !pttOn()) return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
      e.preventDefault();
      setMicOpen(false);
    });
  })();

  muteBtn.onclick = async () => {
    if (!room) return;
    muted = !muted;
    await room.localParticipant.setMicrophoneEnabled(!muted);
    muteBtn.textContent = muted ? 'Unmute' : 'Mute';
    muteBtn.classList.toggle('on', muted);
    $('meterYou').classList.toggle('muted', muted);
    setMicChip(muted ? 'muted' : 'live');
  };

  // ------------------------------------------------------------ installable
  // Only on the standalone page, and only over TLS. An embed installing a
  // service worker for this origin from inside a frame on somebody else's
  // site is a surprise nobody asked for, and the worker would go on serving
  // that origin long after the frame was gone. A failed registration is not
  // worth telling a caller about — the page works exactly as before without
  // one — so it is logged and dropped.
  if ('serviceWorker' in navigator && !framed && window.isSecureContext) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(
        (e) => console.info('Talk Wave: no service worker —', e.message));
    });
  }

  refreshLive();
  setInterval(() => { if (!room) refreshLive(); }, 20000);

  // The gear is a link now, not a toggle. The panel is its own page, so there
  // is nothing on this one to slide open — and a settings save no longer has
  // to reach back here to repaint the card, because the poll above picks the
  // change up within twenty seconds on its own.
  const gear = $('gearBtn');
  // Inert in a preview: the frame is already inside the panel, and following
  // the link would load the settings page into a corner of the settings page.
  if (gear) gear.onclick = () => { if (!previewMode) location.href = '/settings'; };
  [$('plGearBtn'), $('gdGearBtn')].forEach((b) => {
    if (b) b.onclick = () => { if (!previewMode) location.href = '/settings'; };
  });
})();

