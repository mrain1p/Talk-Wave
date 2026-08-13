/* The call page: the phone, and nothing else.

   Served as /call.js and loaded at the end of <body>, so the DOM exists when
   it runs. The settings panel is panel.js and is a separate surface; this file
   knows nothing about it.

   Shared foundation comes from shared.js via the Callin global. */
(function () {
  const {
    $, params, compact, captionsMode, framed, themeForcedByHost, themeDefault,
    ASKS, ASK_GROUPS, NEVER, CALL_KEY, callKey, rememberCallKey, callKeyExpired,
    ctx, pack, playSound, startRinging, stopRinging,
    setSounds, setVolume, getVolume, THEME_ICONS,
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
    set('gearBtn', c.settings !== false && !compact);
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
    set('signinBtn', c.signin !== false && !!(d && d.signinAvailable));
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
    const at = opts.indexOf(opts.includes(cur) ? cur : '');
    const next = opts[(at + 1) % opts.length];
    // Drawn, not typed (shared.js THEME_ICONS): the sun glyph read as a
    // star and the station's asterisk read as nothing at all
    // (operator-reported) \u2014 the station stop wears a transmitter mast,
    // which is what it stands for.
    const G = { light: THEME_ICONS.light, dark: THEME_ICONS.dark,
                station: THEME_ICONS.station, '': THEME_ICONS.device };
    const T = { light: 'light', dark: 'dark', station: "the station's colours",
                '': framed ? 'match the page' : 'follow the device' };
    btn.innerHTML = G[next];
    btn.title = 'Theme — tap for ' + T[next];
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
    // a phone.
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
    document.addEventListener('click', (e) => {
      if (!pop.hidden && !pop.contains(e.target) && e.target !== btn) close();
    });
  }

  const callBtn = $('callBtn'), muteBtn = $('muteBtn'), hangBtn = $('hangBtn');
  const statusText = $('statusText'), dot = $('dot'), capBox = $('captions');

  let room = null, muted = false, live = null;
  let chatOpen = false;                       // the text line, not a call
  let signinMode = false;                     // the gate opened to climb a tier
  let lastCanAsk = null;                       // rebuild the menu only on a tier change
  let djEl = null, rafId = null, streamEl = null;

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
    playFirstWorking(candidates, 0);
  }

  function playFirstWorking(urls, i) {
    if (i >= urls.length) {
      // Was console.info, which meant nobody ever found out. The commonest
      // cause is an http stream on an https page: the browser blocks it as
      // mixed content and the caller hears no station at all.
      console.warn(
        'Talk Wave: could not tune the caller in. Tried:', urls.join(', '),
        '— if these are http:// and this page is https://, the browser blocked ' +
        'them as mixed content. Set the station stream URL in settings.'
      );
      streamEl = null;
      return;
    }
    try {
      // No crossOrigin: the stream is never read through Web Audio, and
      // asking for CORS makes a station that doesn't send the headers fail
      // for no benefit.
      const el = new Audio(urls[i]);
      // Scaled by the caller's own volume from the start — see applyVolume.
      el.volume = stationLevel();
      el.muted = stationLevel() <= 0;
      el.addEventListener('error', () => {
        if (streamEl !== el) return;
        try { el.pause(); } catch (e) {}
        streamEl = null;
        playFirstWorking(urls, i + 1);
      }, { once: true });
      streamEl = el;
      el.play().catch(() => {
        if (streamEl !== el) return;
        streamEl = null;
        playFirstWorking(urls, i + 1);
      });
    } catch (e) {
      streamEl = null;
      playFirstWorking(urls, i + 1);
    }
  }

  function tuneOut() {
    if (!streamEl) return;
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

  async function routeAudio(toSpeaker) {
    onSpeaker = !!toSpeaker;
    let moved = false;

    if (audioSessionSupported()) {
      try {
        // "playback" is the speaker-facing type; "play-and-record" is the one
        // the spec says may be routed to the receiver. The mic keeps
        // capturing either way — the type is a hint about what the page is
        // doing, not a capture permission.
        navigator.audioSession.type = onSpeaker ? 'playback' : 'play-and-record';
        moved = true;
      } catch (e) { /* the platform kept its own answer */ }
    }

    // Only worth asking when the caller wants the loudspeaker: the default
    // output IS the speaker on every platform that implements this, and
    // there is no "earpiece" device id to switch back to.
    if (onSpeaker && djEl && typeof djEl.setSinkId === 'function') {
      try { await djEl.setSinkId(''); moved = true; } catch (e) { /* no */ }
    }

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
          && typeof HTMLMediaElement.prototype.setSinkId === 'function');
  }

  function offerSpeakerButton() {
    return !framed
      && matchMedia('(pointer: coarse)').matches
      && platformCanRoute();
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

  function setStatus(text, state) {
    statusText.textContent = text;
    dot.className = 'dot' + (state ? ' ' + state : '');
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
    mail: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  };
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
    // An ICON-ONLY door is just its glyph — it should hug the icon and give
    // the row's slack to a worded door beside it, not sit at an equal third
    // (operator: "Call Danny Boy" was clipping while two bare icons took the
    // same width). The class lets the CSS size the two cases apart.
    el.classList.toggle('icononly', st.emoji && !st.words);
    if (st.words) fitLabel(el);
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
  function paintOffAir(reason) {
    $('eyebrow').className = 'eyebrow off';
    $('eyebrowText').textContent = reason === 'offline' ? 'Station offline' : 'Off air';
    $('djName').textContent = reason === 'offline' ? 'Unreachable' : 'Nobody on air';
    $('djShow').textContent = '';
    $('djTagline').textContent = reason === 'offline'
      ? 'Cannot reach the station.' : 'No DJ is live right now.';
    $('npTrack').textContent = '';
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
    // The operator can put the machine on the card as its own button,
    // per surface. With the button up, Call never morphs — two clear
    // doors beat one door with a changing sign.
    const vmButton = machineOn && !lineClosedNow
      && !!(framed ? d.embedVmBtn : d.vmBtn) && !needsCode;
    $('vmBtn').hidden = !vmButton;
    if (vmButton) setBtn($('vmBtn'), 'vm', 'mail', word('vm_button', 'Leave a message'));
    // The text line's door, same rules as the machine's: the kill switch
    // outranks it, the door code gates it, and it is per-surface. Never
    // hidden mid-chat — the input row is the conversation.
    const chatButton = !!d.chatEnabled && !lineClosedNow
      && !!(framed ? d.embedChatBtn : d.chatBtn) && !needsCode;
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
      setBtn(callBtn, 'vm', 'mail', word('vm_button', 'Leave a message'));
    } else {
      callBtn.disabled = false;
      setBtn(callBtn, 'call', 'phone', callLabel());
    }
  }

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
      const first = !live;
      live = d;
      // The kiosk clock: a stored code past the operator's ceiling is
      // forgotten before anything else reads it, and the door re-locks.
      if (callKeyExpired(d.guestSessionMinutes)) rememberCallKey('');
      paintLive(preview ? Object.assign({}, d, preview) : d, first);
    } catch (e) {
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
        applyConfiguredTheme(d.theme, d.stationTheme);
        setupAskPopup(d.canAsk);
        applyControls(d);
        paintThemeGlyph();
        // Seeds the palette change-detector so the second poll does not
        // read the load-time palette as a show change. Any tokens it re-sets
        // are the ones the line above just applied.
        followStationPalette(d);
        lastCanAsk = askSignature(d);
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
      if (typeof d.sounds?.volume === 'number' && !room) {
        setVolume(d.sounds.volume);
        $('volSlider').value = getVolume();
        applyVolume();
      }

      if (!d.reachable) { paintOffAir('offline'); return; }
      if (!d.onAir)     { paintOffAir('offair');  return; }

      $('eyebrow').className = 'eyebrow';
      $('eyebrowText').textContent = 'On air now';
      // The NAME is never switchable: a call card that doesn't say who
      // answers isn't a call card. Everything below it is the operator's
      // call, per surface. Emptied rather than hidden — these are text nodes
      // whose parent collapses on its own once they carry nothing.
      const parts = cardParts(d);
      $('djName').textContent = d.name || 'The DJ';
      $('djShow').textContent = parts.show === false ? '' : (d.show || '');
      $('djTagline').textContent = parts.tagline === false ? '' : (d.tagline || '');
      $('npTrack').textContent =
        (parts.track === false || !d.track) ? '' : '♪ ' + d.track;

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

      // One place decides what the Call button says and whether it works.
      // Split across two blocks, the later one silently undid the earlier.
      if (!room) paintIdleButtons(d);
      paintGuestGate();
      updateMicHelp();

      // Several station reads in a row have failed server-side: the card
      // still paints from cache, but the operator should see it's limping
      // rather than discovering thin prompts later.
      if (!room) {
        if (d.degraded) {
          setStatus('Station responding slowly — some info may be stale', 'connecting');
        } else if (lineClosedNow) {
          // A closed line explains itself in a sentence, not just a dead
          // button — "Line closed" alone left callers wondering whose fault
          // it was. Deliberate state, quiet colour, never 'error'.
          setStatus("The booth isn't taking calls at the moment", '');
        } else if (statusText.textContent.startsWith('Station responding slowly')
                   || statusText.textContent.startsWith("The booth isn't taking calls")) {
          // Back to quiet, not back to "Not connected": an idle card with
          // nothing wrong has nothing to say, and the permanent grey sentence
          // read as a fault on every host page it was embedded in.
          setStatus('');
        }
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
  function paintGuestGate() {
    const box = $('guestGate');
    // signinMode keeps the gate open when the caller opened it themselves to
    // climb a tier — otherwise a poll's repaint would snap it shut mid-type.
    if (box) box.hidden = !signinMode && !(live && live.guestRequired && !callKey());
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
    if (msg) msg.textContent = 'Enter the guest code or admin password to '
      + 'unlock more of what you can ask for.';
    if (input) { input.placeholder = 'Guest code or admin password'; input.focus(); }
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
  // — that is a CSS rule keyed on .rig.on, not something painted here.
  const FLOOR = 12;

  // Only the bottom half of the FFT is worth looking at: speech has next to
  // nothing above ~8kHz, and mapping the whole range put six dead segments on
  // the right of every meter.
  const BAND_TOP = 64;

  // Returns the overall level, 0..1, and paints the segments as a side
  // effect. One pass over the buffer for both — the caller needs the level
  // for the avatar glow and the bands for the meter, and reading the analyser
  // twice per frame is how they used to disagree.
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
      // is a style write on every frame — which is the cost that got the
      // spectrum removed the first time.
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
      if ('talkwave.onair' in p.attributes) setOnAir(!!p.attributes['talkwave.onair']);
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

  function renderSaid(el, text) {
    // Fast path: no pipe means no table, so nothing to parse — the overwhelming
    // majority of lines, spoken captions included.
    if (text.indexOf('|') === -1) { el.textContent = text; el.classList.remove('has-table'); return; }
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
    capBox.scrollTop = capBox.scrollHeight;
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
    capBox.scrollTop = capBox.scrollHeight;
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

  function wireEffect(track) {
    const spec = fxSpec();
    if (!spec) return false;
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

  function dropEffect() {
    if (!fx) return;
    try { fx.src.disconnect(); } catch (e) {}
    fx = null;
  }

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
    if (streamEl) {
      const level = stationLevel();
      streamEl.volume = level;
      streamEl.muted = level <= 0;
    }
  }
  $('volSlider').oninput = (e) => { setVolume(+e.target.value); applyVolume(); };
  applyVolume();      // paint the fill at whatever volume we start on

  // Bumped on every startCall AND every endCall, so an async step that
  // resumes after the caller hung up can tell it is stale. The token mint is
  // an await with the Hang up button already live (the card flips to .oncall
  // on the press, no ringing phase) — pressing it there ran endCall to idle
  // and then this function RESUMED, connected the room and opened the mic
  // against a card that said idle: the DJ heard a caller who thought they had
  // hung up. Reviewed 0.10.57.
  let callGen = 0;
  async function startCall(asVoicemail) {
    const myGen = ++callGen;
    vmCall = !!asVoicemail;
    vmBeepHeard = false;

    // A call or a voicemail is a different mode from the text line — you are
    // on the phone now, not typing. If a chat was open, close it and clear
    // its input row, or the card shows a text box AND a call at once (the
    // "END / hi there / SEND" row over a live voicemail, operator-reported).
    if (chatOpen) endChat();
    if ($('chatRow')) $('chatRow').hidden = true;

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
        body: JSON.stringify(vmCall ? { voicemail: true } : {}),
      });
      // 429 = the line is busy or the operator has closed it; 401 = the door
      // code is missing or wrong. Both are answers, not faults — engaged
      // tone, plain wording, and the button comes straight back.
      if (res.status === 429 || res.status === 401) {
        const d = await res.json().catch(() => ({}));
        stopRinging(); tuneOut();
        playSound('failed');
        if (res.status === 401) {
          localStorage.removeItem(CALL_KEY);
          paintGuestGate();
        }
        setStatus(d.error || 'The booth line is tied up — try again shortly.', 'error');
        $('rig').classList.remove('on');
        $('stateChip').hidden = true;
        stopTimer();
        capBox.classList.remove('on');
        callBtn.classList.remove('ringing', 'answering');
        room = null;
        vmCall = false;
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
      room = new LivekitClient.Room({
        adaptiveStream: true, dynacast: true,
        audioCaptureDefaults: {
          echoCancellation: true, noiseSuppression: true, autoGainControl: true,
        },
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
      if (room) { try { await room.disconnect(); } catch (e) {} }
      const denied = err && (err.name === 'NotAllowedError'
        || /permission|not allowed|denied/i.test(err.message || ''));
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
  function startTimer() {
    const max = vmCall
      ? ((live && live.limits && live.limits.voicemailMaxSeconds) || 0)
      : ((live && live.limits && live.limits.maxCallSeconds) || 0);
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
    anYou = anDj = null; djEl = null;
    djOnAir = false; djHasSpoken = false;
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
    muteBtn.textContent = 'Mute';
    muteBtn.classList.remove('on');
    setCardMode('idle');
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
    capBox.scrollTop = capBox.scrollHeight;
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
      + ' line' + (lines === 1 ? '' : 's') + '</span><span class="when">' + t + '</span>';
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

  $('endedBar').onclick = () => {
    const bar = $('endedBar');
    const open = !capBox.classList.contains('on');
    capBox.classList.toggle('on', open);
    bar.classList.toggle('open', open);
    // The line area is three lines while a call is running and nothing is
    // allowed to change that. Reading back a FINISHED call is the one
    // exception, because it is a deliberate click by someone who wants the
    // room — and by then there is no call for the resize to interrupt.
    $('lineBox').classList.toggle('open', open);
    notifyHeight();
  };

  callBtn.onclick = () => {
    if (!room && !previewMode) startCall(callBtn.dataset.vm === '1');
  };
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
    capBox.scrollTop = capBox.scrollHeight;
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
        setStatus(msg.turns && msg.turns.length
          ? 'Back on the text line' : 'Texting the booth — go ahead', 'connected');
        $('chatInput').focus();
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
      setTimeout(() => { capBox.scrollTop = capBox.scrollHeight; }, 350);
    });
  }

  $('vmBtn').onclick = () => { if (!room && !previewMode) startCall(true); };
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
    if (open && djOnAir) {
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
    // The Space hint only where a keyboard is plausible — a phone
    // advertising a key it does not have reads as broken. The operator's
    // own wording (word_ptt) still wins on every device.
    $('pttMain').textContent =
      pttOpen ? "You're live — tap to go quiet"
              : word('ptt', matchMedia('(pointer: coarse)').matches
                  ? 'Tap to talk' : 'Tap to talk — or hold Space');
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
})();

