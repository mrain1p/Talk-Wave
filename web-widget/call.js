/* The call page: the phone, and nothing else.

   Served as /call.js and loaded at the end of <body>, so the DOM exists when
   it runs. The settings panel is panel.js and is a separate surface; this file
   knows nothing about it.

   Shared foundation comes from shared.js via the Callin global. */
(function () {
  const {
    $, params, compact, captionsMode, framed, themeForcedByHost, themeDefault,
    ASKS, NEVER, CALL_KEY, callKey, rememberCallKey, callKeyExpired,
    ctx, pack, playSound, startRinging, stopRinging,
    setSounds, setVolume, getVolume,
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
    const set = (id, on) => { const b = $(id); if (b) b.hidden = !on; };
    set('helpBtn', c.help !== false && !!(d && d.canAsk));
    set('themeBtn', c.theme !== false && !themeForcedByHost);
    set('gearBtn', c.settings !== false && !compact);
    // Not the operator's switch: the lock exists exactly when this DEVICE
    // holds a door code a passer-by could use. Kiosks are why.
    set('lockBtn', !!(d && d.guestRequired) && !!callKey());
  }

  $('lockBtn').onclick = () => {
    rememberCallKey('');
    setStatus('Door code forgotten on this device');
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
    const G = { light: '\u2600', dark: '\u263e', station: '\u2733', '': '\u25a6' };
    const T = { light: 'light', dark: 'dark', station: "the station's colours",
                '': framed ? 'match the page' : 'follow the device' };
    btn.textContent = G[next];
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
    ASKS.filter((a) => !a.need || canAsk[a.need]).forEach((a) => {
      const li = document.createElement('li');
      li.innerHTML = '<span class="say"></span><span class="why"></span>';
      li.querySelector('.say').textContent = a.say;
      li.querySelector('.why').textContent = a.why;
      host.appendChild(li);
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
        'Wave Talk: could not tune the caller in. Tried:', urls.join(', '),
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

  function callLabel() {
    return fillWords((shown && shown.callLabel) || 'Call the DJ');
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
    const vmButton = vmPolicy() !== 'never' && reason !== 'offline' && !paused
      && !!(framed ? flags.embedVmBtn : flags.vmBtn);
    $('vmBtn').hidden = !vmButton || !!room;
    callBtn.dataset.vm = '';
    if (reason !== 'offline' && !paused
        && vmPolicy() !== 'never' && !room && !vmButton) {
      callBtn.disabled = false;
      callBtn.dataset.vm = '1';
      callBtn.textContent = 'Leave a message';
      return;
    }
    callBtn.disabled = true;
    callBtn.textContent = paused && reason !== 'offline'
      ? word('closed', 'Line closed')
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
    if (vmButton) $('vmBtn').textContent = word('vm_button', 'Leave a message');
    callBtn.hidden = false;
    callBtn.dataset.vm = '';
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
      callBtn.textContent = word('vm_button', 'Leave a message');
    } else {
      callBtn.disabled = false;
      callBtn.textContent = callLabel();
    }
  }

  async function refreshLive() {
    try {
      const r = await fetch('/live');
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

      const img = $('djAvatar');
      if (parts.avatar === false) { img.classList.add('hidden'); }
      else if (d.avatar) {
        if (img.dataset.pid !== d.personaId) {
          img.dataset.pid = d.personaId || '';
          img.src = d.avatar + '?v=' + (d.personaId || '');
        }
        img.alt = d.name || 'DJ';
        img.classList.remove('hidden');
        img.onerror = () => img.classList.add('hidden');
      } else { img.classList.add('hidden'); }

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
      console.warn('Wave Talk: could not paint the card —', e);
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
    if (box) box.hidden = !(live && live.guestRequired && !callKey());
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

  if ($('guestBtn')) {
    $('guestBtn').onclick = submitGuestCode;
    $('guestPw').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submitGuestCode();
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
    if (on) {
      playSound('hold');
      addSystemLine('📻', 'Back on the broadcast',
        'The DJ has the station mic for a moment — your call picks up straight after.');
    }
  }

  function watchAgentState(r) {
    const read = (p) => {
      if (!p || !p.attributes) return;
      const s = p.attributes['lk.agent.state'];
      if (s) setAgentState(s);
      if ('wavetalk.onair' in p.attributes) setOnAir(!!p.attributes['wavetalk.onair']);
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
      if (topic && topic !== 'wavetalk.action') return;
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

  function addCaption(id, who, text, final) {
    if (!text) return;
    if (captionsMode !== 'full') {
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
      node.querySelector('.who').textContent = who === 'dj' ? 'DJ' : 'You';
      capBox.appendChild(node);
      capNodes.set(id, node);
      // Only a NEW turn rises in. An interim transcript rewrites the same
      // node every few hundred ms, and replaying the animation on each of
      // those would shake the line while someone is still speaking.
      rollIn(node);
    }
    node.querySelector('.said').textContent = text;
    node.classList.toggle('interim', !final);
    lastByWho[who] = { node, text, at: Date.now() };
    capBox.scrollTop = capBox.scrollHeight;
    while (capBox.children.length > 40) capBox.removeChild(capBox.firstChild);
  }

  // A system action, not speech: a song going into the queue, a message
  // reaching the air, a segment starting. It gets its own line in the
  // timeline, styled apart from the conversation, because the caller
  // otherwise has only the DJ's word that anything happened.
  function addSystemLine(icon, label, detail) {
    if (captionsMode === 'off') return;
    if (captionsMode !== 'full') {
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
      console.warn('Wave Talk: voice effect unavailable —', e);
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

  async function startCall(asVoicemail) {
    vmCall = !!asVoicemail;
    vmBeepHeard = false;

    // Browsers only allow microphone capture on HTTPS or localhost. On a
    // plain http:// LAN address the call would connect and then immediately
    // hang up when mic capture fails — say why up front instead.
    if (!window.isSecureContext || !navigator.mediaDevices
        || !navigator.mediaDevices.getUserMedia) {
      setStatus('This page can\'t use the microphone — see the note below', 'error');
      updateMicHelp();
      return;
    }
    callBtn.disabled = true;
    $('vmBtn').hidden = true;
    callBtn.textContent = word('ringing', 'Ringing…');
    callBtn.classList.add('ringing');
    $('rig').classList.add('on');
    $('stateChip').hidden = false;
    djHasSpoken = false;          // a second call rings like the first one did
    setAgentState('initializing');
    startTimer();
    notifyHeight();
    setStatus('Connecting…', 'connecting');
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
        // Repaint BOTH buttons from the live state — restoring Call by hand
        // here forgot the message button, and one refused call left the
        // card without its one working door until a reload.
        paintIdleButtons(live || {});
        if (res.status === 401) {
          callBtn.hidden = false;
          callBtn.disabled = true;
          callBtn.textContent = 'Enter the code';
        }
        return;
      }
      if (!res.ok) throw new Error('token mint failed');
      const { token, url, room: roomName } = await res.json();
      currentRoom = roomName;

      room = new LivekitClient.Room({ adaptiveStream: true, dynacast: true });

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
          setStatus('The machine is listening — transcript only, no audio is kept',
                    'connected');
          // A worker from before the vm-beep topic never sends it; after
          // the longest plausible greeting, treat it as beeped so the
          // status stops waiting on a beep that already sounded. The mic
          // is live either way — the beep stopped being a gate.
          setTimeout(() => {
            if (vmCall && !vmBeepHeard && room) {
              vmBeepHeard = true;
              paintPtt();
            }
          }, 15000);
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
        hangBtn.textContent = word('hangup', 'Hang up');
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
        if (pttOn()) {
          paintPtt();
          setStatus('Recording — hold the bar and speak', 'connected');
        } else {
          setStatus('Recording — go ahead, transcript only', 'connected');
        }
      });

      await room.connect(url, token);
      // Enabled first even under push to talk: this is the moment the
      // browser asks the mic permission and the track is created. PTT then
      // closes the line straight away — the first press reopens it without
      // a permission prompt mid-sentence.
      await room.localParticipant.setMicrophoneEnabled(true);
      // A voicemail mic is live from PICKUP, exactly like a call: the worker
      // wires STT before the greeting on purpose, because talking over an
      // answering machine is how people use answering machines. The widget
      // holding the mic shut until the beep threw all of that away — real
      // messages arrived as their last two words. The beep is a cue now.
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
      setStatus('Connected — waiting for the DJ…', 'connected');
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
          'Wave Talk: signalling connected but no media path was established. '
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
      callBtn.textContent = callLabel();
      callBtn.disabled = false;
      callBtn.hidden = false;
      hangBtn.hidden = true;
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
    collapseTranscript();
    // No verdict buttons after a voicemail — there was no conversation to
    // rate, and "How was it?" over "Message left" read as the machine
    // fishing for a compliment. Operator-reported.
    if (!wasVm) offerFeedback(endedRoom);
    callBtn.textContent = callLabel();
    callBtn.classList.remove('live', 'ringing', 'answering');
    callBtn.disabled = false;
    callBtn.hidden = false;
    hangBtn.hidden = true;
    document.querySelector('.card').classList.remove('oncall');
    muteBtn.textContent = 'Mute';
    muteBtn.classList.remove('on');
    pttOpen = false;
    const pttBar = $('pttBtn');
    if (pttBar) { pttBar.classList.remove('on'); pttBar.setAttribute('aria-pressed', 'false'); }
    $('meterYou').classList.remove('muted');
    setStatus(wasVm ? 'Message left — it gets passed on.' : 'Call ended');
    // The card's idle truth — including the second button — comes back from
    // the next /live read rather than being reconstructed by hand here.
    refreshLive();
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
  // Two buttons, offered once per call and only when the operator asked for
  // them. Deliberately not a modal: a popup over the card the moment a call
  // ends is in the way of the transcript, and the one thing a caller might
  // want after a bad call is to read what was said.
  //
  // The answer lands on that call's own transcript, so "find me the bad ones"
  // is a question the panel can answer. Nothing else is collected.
  function offerFeedback(endedRoom) {
    const bar = $('rateBar');
    if (!bar) return;
    if (!endedRoom || !(live && live.askFeedback)) { bar.hidden = true; return; }
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
          { reliable: true, topic: 'wavetalk.turn-end' });
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
        console.warn('Wave Talk: could not switch the mic —', e);
      }
    });
    return micOp;
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
      $('youLabel').textContent = pttOpen ? 'You' : 'You — mic off';
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
    $('youLabel').textContent = muted ? 'You — muted' : 'You';
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
        (e) => console.info('Wave Talk: no service worker —', e.message));
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

