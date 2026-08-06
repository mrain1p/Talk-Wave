/* SUB/WAVE call-in widget logic. Served by token_server as /app.js.
   Loaded at the end of <body>, so the DOM exists when it runs. */
(function () {
  const params  = new URLSearchParams(location.search);
  const compact = params.get('compact') === '1';
  if (compact) document.body.classList.add('compact');

  // How speech is shown: 'full' = the scrolling transcript, 'ticker' = only
  // the latest line, fading out after a few seconds, 'off' = nothing.
  // Embeds default to the ticker so the widget stays small wherever it's
  // dropped; the full page keeps the transcript.
  const captionsMode = params.get('captions') || (compact ? 'ticker' : 'full');

  const $ = (id) => document.getElementById(id);

  // Declared up here because both the embed-height reporting and the ask
  // popup's overlay handshake need it, and they sit at opposite ends of this
  // file.
  const framed = window.parent !== window;

  // Theme: an explicit choice is remembered and beats the OS setting. Embeds
  // can force one with ?theme=light|dark so the widget matches the host page.
  const themeForcedByHost = !!params.get('theme');

  (function theme() {
    const forced = params.get('theme');
    const saved = forced || localStorage.getItem('callinTheme');
    if (saved === 'light' || saved === 'dark') {
      document.documentElement.setAttribute('data-theme', saved);
    }
    const btn = document.getElementById('themeBtn');
    if (!btn || forced) return;
    btn.onclick = () => {
      const now = document.documentElement.getAttribute('data-theme')
        || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
      const next = now === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('callinTheme', next);
    };
  })();

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
  function applyControls(d) {
    const c = (d && d.controls) || {};
    const set = (id, on) => { const b = $(id); if (b) b.hidden = !on; };
    set('helpBtn', c.help !== false && !!(d && d.canAsk));
    set('themeBtn', c.theme !== false && !themeForcedByHost);
    set('gearBtn', c.settings !== false && !compact);
  }

  // The operator's theme choice arrives with /live, long after the page has
  // painted, so the bootstrap above handles the immediate cases and this
  // applies the configured one once it is known.
  //
  // "inherit" is resolved by embed.js BEFORE the frame loads — it reads the
  // host page's background and passes ?theme=. A cross-origin frame cannot
  // see the page it sits in, so if inherit reaches us unresolved there is no
  // page to inherit from and auto is the honest answer.
  function applyConfiguredTheme(choice) {
    if (themeForcedByHost) return;              // the host page has decided
    const root = document.documentElement;
    if (choice === 'light' || choice === 'dark') {
      root.setAttribute('data-theme', choice);  // forced: applyControls drops
      return;                                   // the toggle to match
    }
    if (!localStorage.getItem('callinTheme')) root.removeAttribute('data-theme');
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
    const root = document.documentElement;
    Object.keys(msg.tokens).forEach((k) => {
      if (!/^--[a-z0-9-]+$/i.test(k)) return;
      const v = String(msg.tokens[k]);
      if (v.length < 120 && !/[;{}<>]/.test(v)) root.style.setProperty(k, v);
    });
  });

  // "What can I ask?" — most people meeting a phone-in assume it only takes
  // requests. Built from the shared ASKS list and filtered to the permissions
  // actually switched on, so it can never suggest something the DJ would
  // refuse. What a caller CANNOT do is deliberately left out: that list is
  // for the operator deciding what to allow, not for a stranger on the line.
  function paintAskPopup(canAsk) {
    const host = $('askPopList');
    if (!host) return;
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
  let audioCtx = null, djEl = null, rafId = null, streamEl = null;
  let volume = 100;

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

  function setStatus(text, state) {
    statusText.textContent = text;
    dot.className = 'dot' + (state ? ' ' + state : '');
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

  // ---------------------------------------------------------------- sounds
  // Defaults are synthesized so the widget ships with no audio assets; a
  // configured URL replaces them.
  function ctx() {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    return audioCtx;
  }

  function tone(freqs, start, dur, gain) {
    const c = ctx(), t0 = c.currentTime + start;
    const g = c.createGain();
    g.gain.setValueAtTime(0, t0);
    g.gain.linearRampToValueAtTime(gain * (volume / 100), t0 + 0.02);
    g.gain.setValueAtTime(gain * (volume / 100), t0 + dur - 0.04);
    g.gain.linearRampToValueAtTime(0, t0 + dur);
    g.connect(c.destination);
    freqs.forEach((f) => {
      const o = c.createOscillator();
      o.frequency.value = f; o.type = 'sine';
      o.connect(g); o.start(t0); o.stop(t0 + dur);
    });
  }

  // A short burst of filtered noise — what every mechanical phone sound is
  // actually made of. Oscillators alone can't do a click or a clunk.
  function noise(start, dur, gain, freq, q) {
    const c = ctx(), t0 = c.currentTime + start;
    const frames = Math.max(1, Math.floor(c.sampleRate * dur));
    const buf = c.createBuffer(1, frames, c.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < frames; i++) data[i] = Math.random() * 2 - 1;
    const src = c.createBufferSource(); src.buffer = buf;
    const bp = c.createBiquadFilter();
    bp.type = 'bandpass'; bp.frequency.value = freq; bp.Q.value = q || 1;
    const g = c.createGain();
    g.gain.setValueAtTime(gain * (volume / 100), t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(bp); bp.connect(g); g.connect(c.destination);
    src.start(t0); src.stop(t0 + dur);
  }

  // Two packs. "classic" is the telephone-exchange set the widget shipped
  // with; "phone" is the one asked for — a physical handset in a room, all
  // bell and bakelite rather than app blips. Both are synthesized, so
  // neither needs an audio file to exist.
  const PACKS = {
    classic: {
      // North-American ringback: 440+480Hz, two-second burst.
      ring:   () => { tone([440, 480], 0, 1.1, 0.13); },
      // Line picking up: a short click, then a soft confirmation blip.
      pickup: () => { tone([220], 0, 0.045, 0.16); tone([660], 0.07, 0.10, 0.09); },
      // Hanging up: descending pair.
      hangup: () => { tone([480], 0, 0.14, 0.11); tone([380], 0.15, 0.22, 0.10); },
      // Engaged tone, two beats only — enough to read as "no", not a nag.
      failed: () => { tone([480, 620], 0, 0.35, 0.10); tone([480, 620], 0.5, 0.35, 0.10); },
      // Put on hold: a soft double blip, then the line goes quiet.
      hold:   () => { tone([520], 0, 0.07, 0.07); tone([440], 0.11, 0.09, 0.06); },
    },
    phone: {
      // A bell struck twice, with the hammer noise on each strike and the
      // metal ringing on after it.
      ring: () => {
        for (const at of [0, 0.14]) {
          noise(at, 0.02, 0.10, 2600, 2);
          tone([1180, 1580], at, 0.12, 0.075);
        }
      },
      // Handset lifted off the cradle: a mechanical clunk, then the line
      // opening with a breath of room tone.
      pickup: () => { noise(0, 0.035, 0.20, 900, 1.2); noise(0.05, 0.12, 0.035, 1800, 0.7); },
      // Receiver set back down: the clunk, then the cradle springing.
      hangup: () => { noise(0, 0.05, 0.22, 620, 1.1); noise(0.07, 0.03, 0.11, 1500, 1.6); },
      // Engaged: the old 400Hz burr rather than a two-tone beep.
      failed: () => { tone([400], 0, 0.33, 0.09); tone([400], 0.45, 0.33, 0.09); },
      // Handset set down on the desk beside the phone.
      hold:   () => { noise(0, 0.04, 0.14, 700, 1.1); },
    },
  };

  function pack() {
    const s = (live && live.sounds) || {};
    return PACKS[s.pack] || PACKS.classic;
  }

  let ringTimer = null;
  function playSound(kind) {
    const s = (live && live.sounds) || {};
    if (!s.enabled) return;
    const url = s[kind];
    const builtin = pack()[kind];
    if (url) {
      try {
        const a = new Audio(url);
        a.volume = Math.min(1, volume / 100);
        // A configured file that won't load must not mean silence — the
        // built-in is always there to fall back on.
        a.play().catch(() => builtin && builtin());
        return;
      } catch (e) { /* fall through to built-in */ }
    }
    if (builtin) builtin();
  }

  function startRinging() {
    playSound('ring');
    ringTimer = setInterval(() => playSound('ring'), 2600);
  }
  function stopRinging() {
    if (ringTimer) { clearInterval(ringTimer); ringTimer = null; }
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
    callBtn.disabled = true;
    callBtn.textContent = reason === 'offline' ? 'Station offline' : 'Nobody to call';
  }

  async function refreshLive() {
    try {
      const r = await fetch('/live');
      if (!r.ok) throw new Error('unreachable');
      const d = await r.json();
      const first = !live;
      live = d;
      // Operator choices that shape the card itself, applied once — /live is
      // polled, and re-running these every few seconds would fight the
      // viewer's own theme toggle and rebuild the popup under their finger.
      if (first) {
        applyConfiguredTheme(d.theme);
        setupAskPopup(d.canAsk);
        applyControls(d);
      }
      if (typeof d.sounds?.volume === 'number' && !room) {
        volume = d.sounds.volume;
        $('volSlider').value = volume;
        $('volPct').textContent = volume + '%';
      }

      if (!d.reachable) { paintOffAir('offline'); return; }
      if (!d.onAir)     { paintOffAir('offair');  return; }

      $('eyebrow').className = 'eyebrow';
      $('eyebrowText').textContent = 'On air now';
      $('djName').textContent = d.name || 'The DJ';
      $('djShow').textContent = d.show || '';
      $('djTagline').textContent = d.tagline || '';
      $('npTrack').textContent = d.track ? '♪ ' + d.track : '';

      const img = $('djAvatar');
      if (d.avatar) {
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
      if (!room) {
        const needsCode = !!d.guestRequired && !callKey();
        if (d.callsPaused) {
          // A paused line is a deliberate state, not a fault: say so plainly
          // rather than offering a button that can only fail.
          callBtn.disabled = true;
          callBtn.textContent = 'Line closed';
        } else if (needsCode) {
          callBtn.disabled = true;
          callBtn.textContent = 'Enter the code';
        } else {
          callBtn.disabled = false;
          callBtn.textContent = 'Call the DJ';
        }
      }
      paintGuestGate();
      updateMicHelp();

      // Several station reads in a row have failed server-side: the card
      // still paints from cache, but the operator should see it's limping
      // rather than discovering thin prompts later.
      if (!room) {
        if (d.degraded) {
          setStatus('Station responding slowly — some info may be stale', 'connecting');
        } else if (statusText.textContent.startsWith('Station responding slowly')) {
          setStatus('Not connected');
        }
      }
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

  // ---------------------------------------------------------- the door code
  // Optional: set a guest password in Settings → Security and the booth line
  // only opens for people you gave the code to. Deliberately separate from
  // the panel password — this buys you the phone, not the controls.
  const CALL_KEY = 'callinCallKey';
  const callKey = () => localStorage.getItem(CALL_KEY) || '';

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
      localStorage.setItem(CALL_KEY, pw);
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
  // One trough, one fill (HOST-STYLE-GUIDE §4.6). This was fourteen <i>
  // elements whose heights were rewritten every animation frame — twenty-eight
  // style writes per frame across both meters, and at rest it read as a row of
  // dashes rather than as a level. Now it is one width per meter per frame.
  function buildBars(host) {
    host.innerHTML = '';
    host.appendChild(document.createElement('i'));
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

  function level(an, buf) {
    if (!an) return 0;
    an.getByteFrequencyData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i];
    return Math.min(1, (sum / buf.length) / 96);
  }

  function paintBars(host, lvl, active) {
    const fill = host.firstElementChild;
    if (!fill) return;
    // Idle sits at zero rather than at a token 12%: an empty trough is an
    // honest "nothing is coming through", and the old floor made a dead mic
    // look the same as a quiet one.
    const w = active ? Math.max(0, Math.min(1, lvl * 1.35)) : 0;
    fill.style.width = (w * 100) + '%';
  }

  function tick() {
    const you = muted ? 0 : level(anYou, bufYou);
    const dj  = level(anDj, bufDj);
    paintBars($('barsYou'), you, !muted);
    paintBars($('barsDj'), dj, true);
    $('djAvatar').classList.toggle('talking', dj > 0.06);
    rafId = requestAnimationFrame(tick);
  }

  // ------------------------------------------------------------- agent state
  const STATE_TEXT = {
    initializing: 'Connecting', idle: 'Idle', listening: 'Listening',
    thinking: 'Thinking', speaking: 'Speaking', reconnecting: 'Reconnecting',
    // Not an SDK state. The DJ on the call and the DJ on the broadcast are
    // the same person, so while the station has the microphone the call DJ
    // waits — and the caller is told that's what the silence is.
    onair: 'On air',
  };

  // The worker sets this participant attribute while the broadcast is live.
  // It outranks the SDK's own state: the DJ may well be "listening" as far as
  // the session is concerned, but what the caller needs to know is that it
  // can't answer yet.
  let djOnAir = false, lastAgentState = 'idle';

  function paintAgentState() {
    const state = djOnAir ? 'onair' : lastAgentState;
    const chip = $('stateChip');
    chip.dataset.state = state || 'idle';
    $('stateText').textContent = STATE_TEXT[state] || 'Idle';
  }

  function setAgentState(state) {
    lastAgentState = state || 'idle';
    paintAgentState();
    // The first time the DJ actually speaks, the call is properly underway:
    // the button flips from Answering to a green On the line for the rest
    // of the call.
    if (state === 'speaking' && room && !callBtn.classList.contains('live')) {
      callBtn.classList.remove('ringing', 'answering');
      callBtn.classList.add('live');
      callBtn.textContent = 'On the line';
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
  function setLine(el, text) {
    const t = text || NBSP_LINE;
    if (!el || el.textContent === t) return;      // only animate real changes
    el.textContent = t;
    if (text) rollIn(el);                         // never animate the blank
  }

  let tickerTimer = null;
  function showTicker(who, text) {
    const t = $('ticker');
    if (!t) return;
    t.querySelector('.who').textContent =
      who === 'dj' ? 'DJ' : (who === 'sys' ? '•' : 'You');
    setLine(t.querySelector('.line'), text);
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
    const empty = capBox.querySelector('.capempty');
    if (empty) empty.remove();
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
    const empty = capBox.querySelector('.capempty');
    if (empty) empty.remove();

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
  function stationLevel() {
    const s = (live && live.stream) || {};
    return Math.min(1, ((s.volume || 0) / 100) * (volume / 100));
  }

  function applyVolume() {
    $('volPct').textContent = volume + '%';
    if (djEl) djEl.volume = Math.min(1, volume / 100);
    if (streamEl) {
      const level = stationLevel();
      streamEl.volume = level;
      streamEl.muted = level <= 0;
    }
  }
  $('volSlider').oninput = (e) => { volume = +e.target.value; applyVolume(); };

  async function startCall() {
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
    callBtn.textContent = 'Ringing…';
    callBtn.classList.add('ringing');
    $('rig').classList.add('on');
    $('stateChip').hidden = false;
    setAgentState('initializing');
    startTimer();
    notifyHeight();
    setStatus('Connecting…', 'connecting');
    $('endedBar').hidden = true;
    capNodes.clear();
    if (captionsMode === 'full') {
      capBox.classList.add('on');
      capBox.innerHTML = '<p class="capempty">Captions will appear here as you talk…</p>';
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
        headers: callKey() ? { 'X-Call-Key': callKey() } : {},
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
        callBtn.textContent = res.status === 401 ? 'Enter the code' : 'Call the DJ';
        callBtn.disabled = res.status === 401;
        room = null;
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
        // Now they're actually on a call: tune them into the station so the
        // station counts them as a listener and accepts their requests.
        tuneIn();
        // Line picked up; the DJ hasn't spoken yet. setAgentState flips this
        // to the green "On the line" at its first spoken word.
        callBtn.classList.remove('ringing');
        callBtn.classList.add('answering');
        callBtn.textContent = 'Answering…';
        djEl = track.attach();
        djEl.volume = Math.min(1, volume / 100);
        djEl.play?.();
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

      await room.connect(url, token);
      await room.localParticipant.setMicrophoneEnabled(true);

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
      $('rig').classList.remove('on');
      $('stateChip').hidden = true;
      stopTimer();
      capBox.classList.remove('on');
      callBtn.classList.remove('ringing', 'answering');
      callBtn.textContent = 'Call the DJ';
      callBtn.disabled = false;
      room = null;
    }
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
  function startTimer() {
    const max = (live && live.limits && live.limits.maxCallSeconds) || 0;
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
    stopRinging();
    clearNoAnswerTimer();
    tuneOut();
    if (currentRoom) {
      // Release the concurrency slot now instead of waiting for it to age out.
      fetch('/call-ended', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ room: currentRoom }), keepalive: true,
      }).catch(() => {});
      currentRoom = null;
    }
    if (room) { if (!remote) room.disconnect(); playSound('hangup'); }
    room = null; muted = false;
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    anYou = anDj = null; djEl = null;
    djOnAir = false;
    document.querySelector('.card').classList.remove('onair');
    paintBars($('barsYou'), 0, false); paintBars($('barsDj'), 0, false);
    $('djAvatar').classList.remove('talking');
    $('rig').classList.remove('on');
    $('stateChip').hidden = true;
    stopTimer();
    setAgentState('idle');
    const ticker = $('ticker');
    if (ticker) { ticker.classList.remove('show'); ticker.hidden = true; }
    collapseTranscript();
    callBtn.textContent = 'Call the DJ';
    callBtn.classList.remove('live', 'ringing', 'answering');
    callBtn.disabled = false;
    document.querySelector('.card').classList.remove('oncall');
    muteBtn.textContent = 'Mute';
    muteBtn.classList.remove('on');
    $('meterYou').classList.remove('muted');
    setStatus('Call ended');
    notifyHeight();
  }

  // Keep the transcript after the call, but out of the way.
  function collapseTranscript() {
    const lines = capBox.querySelectorAll('.cap').length;
    const bar = $('endedBar');
    if (!lines) { capBox.classList.remove('on'); bar.hidden = true; return; }
    capBox.classList.remove('on');
    bar.hidden = false;
    bar.classList.remove('open');
    const t = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    bar.innerHTML = '<span class="chev">▶</span><span>Call ended · ' + lines
      + ' line' + (lines === 1 ? '' : 's') + '</span><span class="when">' + t + '</span>';
    notifyHeight();
  }

  $('endedBar').onclick = () => {
    const bar = $('endedBar');
    const open = !capBox.classList.contains('on');
    capBox.classList.toggle('on', open);
    bar.classList.toggle('open', open);
    notifyHeight();
  };

  callBtn.onclick = () => { if (!room) startCall(); };
  hangBtn.onclick = () => endCall(false);
  muteBtn.onclick = async () => {
    if (!room) return;
    muted = !muted;
    await room.localParticipant.setMicrophoneEnabled(!muted);
    muteBtn.textContent = muted ? 'Unmute' : 'Mute';
    muteBtn.classList.toggle('on', muted);
    $('meterYou').classList.toggle('muted', muted);
    $('youLabel').textContent = muted ? 'You — muted' : 'You';
  };

  refreshLive();
  setInterval(() => { if (!room) refreshLive(); }, 20000);

  // Shared by the caller's card and the operator's panel, so the two can
  // never describe the phone differently. Defined above the compact
  // cut-off below because an embed needs it as much as the full page.
  const ASKS = [
    { need: null, say: '“What’s playing right now?”',
      why: 'Reads live station state — always available.' },
    { need: null, say: '“What have you been playing tonight?”',
      why: 'Recent history and what’s queued next.' },
    { need: null, say: '“What’s on after this show?”',
      why: 'The current show always; the rest of the line-up if “Know the rest of the line-up” is on.' },
    { need: 'allow_requests', say: '“Can you play something slower?”',
      why: 'Vague requests work — the station resolves them.' },
    // Deliberately the station's own request-slip vocabulary, so the phone and
    // the request drawer teach callers the same things.
    { need: 'allow_requests', say: '“Something for late-night driving.”',
      why: 'A mood, an occasion or an era goes to the station’s picker, not a name search.' },
    { need: 'allow_requests', say: '“More like this one.” / “Surprise me.”',
      why: 'Follow-ons and open picks are valid requests on their own.' },
    { need: 'allow_requests', say: '“Something from the late seventies?”',
      why: 'An era is a request like any other — no track name needed.' },
    { need: 'allow_requests', say: '“More like this one.” / “Anything similar to Fleetwood Mac?”',
      why: 'The station matches on feel, not just on title.' },
    { need: 'allow_requests', say: '“Can you keep it mellow for the next few?”',
      why: 'A run of requests in one mood — capped by the per-call action limit.' },
    { need: 'allow_library_search', say: '“Have you got any Fleetwood Mac?”',
      why: 'Searches the real library before promising anything.' },
    { need: 'allow_exact_queue', say: '“The second one — the live version.”',
      why: 'Queues that exact recording from the search results, not a re-match.' },
    { need: 'allow_announcements', say: '“Can you say hi to my brother on air?”',
      why: 'Hands a line to the on-air DJ to read in persona.' },
    { need: 'allow_announcements', say: '“Tell everyone what we just talked about.”',
      why: 'Puts the gist of the call on air.' },
    { need: 'allow_skills', say: '“What’s the weather doing?” / “Any news?”',
      why: 'Runs the station’s own weather or news segment.' },
    { need: 'allow_skills', say: '“Give my mate a dedication.”',
      why: 'Runs the dedication or shoutout segment.' },
    { need: 'allow_skills', say: '“Tell us a story about the old days.”',
      why: 'Story time / remembrance segments, in the DJ’s own voice.' },
    { need: null, say: '“Who is this? What’s the story behind this record?”',
      why: 'Answered in character — the DJ knows what’s playing and talks about it.' },
    { need: null, say: '“How long have you been doing the night shift?”',
      why: 'Answered in character from the DJ Card — no tool needed.' },
  ];

  // The other half of the truth: what a caller CANNOT do, and why. Without
  // this the permissions list reads as if anything might be one toggle away.
  const NEVER = [
    ['Skip or stop the current track', 'a stranger could cut off what everyone else is listening to'],
    ['Fire sound effects or stingers', 'nothing to add to a call, plenty to disrupt on air'],
    ['Start or end a show, or hand over to another DJ', 'station-level programming is the operator’s'],
    ['Rebuild the playlist', 'one caller should not reshape the night for everyone'],
  ];


  // =================================================== settings (full page)
  if (compact) return;

  // Every admin request carries the panel password (kept in localStorage so
  // the login persists across visits, until Sign out) as the X-Admin-Key
  // header. Public endpoints — /live, call tokens, /call-ended, /health —
  // never use this.
  function afetch(url, opts) {
    const key = localStorage.getItem('callinAdminKey');
    if (key) {
      opts = Object.assign({}, opts);
      opts.headers = Object.assign({}, opts.headers, { 'X-Admin-Key': key });
    }
    return fetch(url, opts);
  }

  // Field lists come from the server's schema — settings.py is the single
  // source of truth. These start empty and are filled on load.
  let TEXT_FIELDS = [], NUM_FIELDS = [], CHECK_FIELDS = [], SELECT_FIELDS = [], ALL_FIELDS = [];
  let SCHEMA = { groups: [], fields: {} };

  // Lay the panel out from the schema: super-group headers are built here and
  // sections are moved into the backend's order. Nothing about grouping lives
  // in the markup, so the two can't drift apart.
  function layoutPanel() {
    const panel = $('panel');
    const anchor = document.querySelector('#panel .actions');
    if (!panel || !anchor || !SCHEMA.groups || !SCHEMA.groups.length) return;

    // Only the schema-driven headers are rebuilt. The diagnostics header is
    // written into the markup and stays put — it has no settings behind it.
    panel.querySelectorAll('.supergroup:not([data-static])').forEach((h) => h.remove());

    const supers = SCHEMA.supergroups || [];
    const byId = {};
    // [data-group] on purpose: the diagnostics rows are details.sec too, and
    // without the filter they all collapse onto the key `undefined` and one of
    // them gets relocated into the settings list.
    panel.querySelectorAll('details.sec[data-group]').forEach((sec) => {
      byId[sec.dataset.group] = sec;
    });

    const missing = SCHEMA.groups.filter((g) => !byId[g.id]).map((g) => g.id);
    const unknown = Object.keys(byId).filter(
      (id) => !SCHEMA.groups.some((g) => g.id === id));
    if (missing.length || unknown.length) {
      // Loud on purpose: this means the schema and the markup disagree.
      console.warn('settings layout mismatch', { missing, unknown });
    }

    supers.forEach((sup) => {
      const members = SCHEMA.groups.filter((g) => g.super === sup.id && byId[g.id]);
      if (!members.length) return;
      const hdr = document.createElement('div');
      hdr.className = 'supergroup';
      hdr.dataset.super = sup.id;
      hdr.innerHTML = '<span></span><em></em>';
      hdr.querySelector('span').textContent = sup.title;
      hdr.querySelector('em').textContent = sup.blurb || '';
      anchor.parentNode.insertBefore(hdr, anchor);
      members.forEach((g) => anchor.parentNode.insertBefore(byId[g.id], anchor));
    });

    // Anything the schema doesn't place still gets shown, at the end.
    Object.keys(byId).forEach((id) => {
      if (!SCHEMA.groups.some((g) => g.id === id)) {
        anchor.parentNode.insertBefore(byId[id], anchor);
      }
    });
  }

  function adoptSchema(schema) {
    SCHEMA = schema || { groups: [], fields: {} };
    const byKind = (k) => Object.keys(SCHEMA.fields).filter(
      (f) => SCHEMA.fields[f].kind === k && document.getElementById(f));
    TEXT_FIELDS = byKind('text');
    NUM_FIELDS = byKind('number');
    CHECK_FIELDS = byKind('check');
    SELECT_FIELDS = byKind('select');
    ALL_FIELDS = SELECT_FIELDS.concat(TEXT_FIELDS, NUM_FIELDS, CHECK_FIELDS);
    layoutPanel();
    bindFieldEvents();
    decorateFields();
  }

  // Starts empty rather than null: the panel now paints as soon as the
  // schema arrives, before the slow provider lists have loaded, so every
  // read of this has to survive it being empty.
  let options = {}, overrides = {}, resolved = {}, secrets = {};
  // Whether the panel has ever been filled. Its own flag rather than a
  // truthiness test on `options`, which is how 0.9.58 silently emptied the
  // whole panel: that commit changed `options` from null to {} for the
  // paint-early fix, and the gear's "already loaded?" guard was reading it as
  // "not loaded yet". {} is truthy, so the guard fired on the FIRST open and
  // the settings were never fetched at all.
  let loaded = false;

  // Browsers restore form state across reloads; since Save diffs against the
  // stored overrides, a restored value would look like a deliberate edit.
  document.querySelectorAll('#panel select, #panel input')
    .forEach((el) => el.setAttribute('autocomplete', 'off'));

  function fill(sel, values, { blankLabel = 'Default', labels = null } = {}) {
    const el = $(sel);
    const keep = el.value;
    el.innerHTML = '';
    const blank = document.createElement('option');
    blank.value = ''; blank.textContent = blankLabel;
    el.appendChild(blank);
    (values || []).forEach((v) => {
      const o = document.createElement('option');
      o.value = v; o.textContent = labels ? (labels[v] || v) : v;
      el.appendChild(o);
    });
    if (keep && [...el.options].some((o) => o.value === keep)) el.value = keep;
  }

  const PROVIDER_KEY = {
    openai: 'openai_api_key', google: 'google_api_key',
    anthropic: 'anthropic_api_key', openrouter: 'openrouter_api_key',
    ollama: null,
  };

  // Selects with a fixed choice list (no "Default" blank, value always set)
  // are declared once in settings.py STATIC_CHOICES and arrive via the
  // schema. Previously profanity_mode and greeting_style were hand-exempted
  // by name in three separate places here, with a duplicate option list in
  // the markup — a drift trap every time a choice changed.
  const hasChoices = (f) => !!(SCHEMA.fields[f] && SCHEMA.fields[f].choices);

  function fillStatic(f) {
    const el = $(f);
    const choices = SCHEMA.fields[f].choices || [];
    el.innerHTML = '';
    choices.forEach((c) => {
      const o = document.createElement('option');
      o.value = c[0]; o.textContent = c[1] || c[0];
      el.appendChild(o);
    });
    el.value = resolved[f] != null ? String(resolved[f]) : '';
  }

  function syncModels() {
    const llm = $('llm_provider').value || resolved.llm_provider;
    const list = (options.llmModels || {})[llm] || [];
    const liveList = (options.modelsDiscovered || {})[llm];
    const station = options.stationLlm || {};

    const labels = {};
    if (station.model && list.includes(station.model)) {
      labels[station.model] = station.model + '  — same as the station';
    }
    fill('llm_model', list, { labels });
    $('llm_model').value = overrides.llm_model || '';

    const note = $('modelSourceNote');
    if (!liveList && PROVIDER_KEY[llm]) {
      note.textContent = 'Showing a fallback list — add the ' + llm
        + ' key and hit “Reload model lists” to read the real one.';
    } else if (liveList) {
      note.textContent = list.length + ' models read live from ' + llm + '.'
        + (station.model && list.includes(station.model)
            ? ' The station runs ' + station.model + '.' : '');
    } else { note.textContent = ''; }

    const stt = $('stt_provider').value || resolved.stt_provider;
    fill('stt_model', (options.sttModels || {})[stt] || []);
    $('stt_model').value = overrides.stt_model || '';
  }

  function keyJump(container, field) {
    const row = $('sec_' + field);
    if (!row) return;
    const link = document.createElement('button');
    link.textContent = 'Add ' + (secrets[field] ? secrets[field].label : field) + ' key';
    link.className = 'btnaccent';
    link.style.cssText = 'display:block;margin-top:9px;font-size:12.5px;padding:8px 14px';
    link.onclick = () => {
      const sec = row.closest('details');
      if (sec) sec.open = true;
      row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      row.focus();
      row.style.borderColor = 'var(--accent)';
      setTimeout(() => { row.style.borderColor = ''; }, 2500);
    };
    container.appendChild(link);
  }

  function maybeOfferKey(container, provider, errorText) {
    const field = PROVIDER_KEY[provider];
    if (!field) return;
    if (/api[_ ]?key|401|unauthor|credential|permission/i.test(errorText || '')) {
      keyJump(container, field);
    }
  }

  // Section headers summarise their own state, so the panel is readable folded.
  function paintTags() {
    $('tagStation').textContent = (options.personas || []).length + ' personas';
    const setKeys = Object.values(secrets).filter((s) => s.set).length;
    $('tagKeys').textContent = setKeys ? setKeys + ' set' : 'none set';
    $('tagVoice').textContent = (resolved.tts_mode || '') +
      (resolved.tts_voice ? ' · ' + resolved.tts_voice : ' · station voice');
    $('tagBrains').textContent = (resolved.llm_provider || '') + ' · ' + (resolved.llm_model || '');
    // Permission count comes from the schema group, so it can't go stale when
    // a new permission is added.
    const permFields = Object.keys(SCHEMA.fields)
      .filter((f) => SCHEMA.fields[f].group === 'perms');
    const perms = permFields.filter((f) => resolved[f]).length;
    $('tagPerms').textContent = perms + ' of ' + permFields.length + ' enabled';
    $('tagSounds').textContent = resolved.call_sounds
      ? (resolved.sound_pack === 'phone' ? 'handset' : 'exchange') : 'off';
    $('tagStyle').textContent = [resolved.style_answering, resolved.style_signoff]
      .filter(Boolean).length + ' set';
    $('tagHygiene').textContent = (resolved.strip_stage_directions ? 'directions stripped' : 'raw')
      + ' · ' + (resolved.profanity_mode === 'off' ? 'no filter' : resolved.profanity_mode);
    $('tagUsage').textContent = resolved.calls_paused ? 'PAUSED — no calls'
      : (resolved.max_concurrent_calls || '∞') + ' at once · '
        + (resolved.calls_per_hour || '∞') + '/hr · '
        + (resolved.calls_per_day || '∞') + '/day · '
        + (resolved.max_actions_per_call || '∞') + ' actions';
    $('tagCallback').textContent = resolved.callback_enabled
      ? 'on · ' + resolved.callback_max_words + ' words' : 'off';
    $('tagContext').textContent = [resolved.context_recent_tracks + ' played',
      resolved.context_upcoming + ' queued', resolved.context_booth_lines + ' on-air'].join(' · ');
    $('tagCall').textContent = (resolved.persona_override
      ? 'pinned persona' : 'live DJ') + ' · ' + resolved.max_call_seconds + 's';
  }

  // Worked examples of what a caller can actually say, tied to the permission
  // that enables each one — so the list can't drift from the real tool surface.
  // What a permission is set to RIGHT NOW, including an unsaved tick. The
  // reference lists are there to answer "what does this switch do" — reading
  // only the saved value meant they didn't move until after you'd committed
  // the change you were trying to understand.
  function permOn(field) {
    const el = $(field);
    if (el && el.type === 'checkbox') return el.checked;
    return !!resolved[field];
  }

  function paintAsks() {
    const host = $('askList');
    if (!host) return;
    host.innerHTML = '';
    let on = 0;
    ASKS.forEach((a) => {
      const enabled = !a.need || permOn(a.need);
      if (enabled) on++;
      const li = document.createElement('li');
      li.className = enabled ? '' : 'off';
      li.innerHTML = '<span class="mark"></span><span class="say"></span>';
      li.querySelector('.mark').textContent = enabled ? '✓' : '–';
      const say = li.querySelector('.say');
      say.textContent = a.say;
      const why = document.createElement('span');
      why.className = 'why';
      why.textContent = enabled ? a.why : a.why + ' (turn on above to enable)';
      say.appendChild(why);
      host.appendChild(li);
    });

    // Always-off actions, listed once at the end so the boundary is visible
    // rather than something you discover by toggling everything on.
    const never = document.createElement('li');
    never.className = 'nevergroup';
    never.innerHTML = '<span class="mark">×</span><span class="say">'
      + 'Never available to callers, whatever the settings say'
      + '<span class="why"></span></span>';
    never.querySelector('.why').textContent =
      NEVER.map(([what, why]) => what + ' — ' + why).join(' · ');
    host.appendChild(never);

    const tag = $('tagAsk');
    if (tag) tag.textContent = on + ' of ' + ASKS.length + ' available';
  }

  // The station's whole tool surface, straight from the schema so it can't
  // drift from what the worker actually allows.
  function paintTools() {
    const host = $('toolList');
    if (!host) return;
    const tools = SCHEMA.mcpTools || [];
    host.innerHTML = '';
    let reachable = 0;
    tools.forEach((t) => {
      const on = t.gate === 'read' || (t.gate !== 'never' && permOn(t.gate));
      if (on) reachable++;
      const li = document.createElement('li');
      li.className = t.gate === 'never' ? 'blocked' : (on ? '' : 'off');

      const state = document.createElement('span');
      state.className = 'tstate';
      state.textContent = t.gate === 'never' ? 'never'
        : (t.gate === 'read' ? 'always' : (on ? 'on' : 'off'));

      const body = document.createElement('span');
      body.className = 'tbody';
      const name = document.createElement('code');
      name.textContent = t.name;
      const what = document.createElement('span');
      what.className = 'twhat';
      what.textContent = t.what;
      body.append(name, what);
      if (t.note) {
        const note = document.createElement('span');
        note.className = 'tnote';
        note.textContent = t.note;
        body.appendChild(note);
      }
      li.append(state, body);
      host.appendChild(li);
    });
    const tag = $('tagTools');
    if (tag) tag.textContent = reachable + ' of ' + tools.length + ' reachable';
  }

  // Only show configuration that applies to the current selection. A local-model
  // URL box is noise when you're on a hosted provider, and vice versa.
  function applyVisibility() {
    // Every rule comes from the schema: a field declares what it depends on,
    // and advanced fields stay hidden until asked for.
    Object.keys(SCHEMA.fields).forEach((f) => {
      const el = $(f);
      if (!el) return;
      const meta = SCHEMA.fields[f];
      const anchor = el.closest('.row') || el.closest('.check');
      if (!anchor) return;

      let visible = true;
      if (meta.needs) {
        const [dep, want] = meta.needs;
        const depEl = $(dep);
        const current = depEl
          ? (depEl.type === 'checkbox' ? depEl.checked : (depEl.value || resolved[dep]))
          : resolved[dep];
        if (want === true) visible = !!current;
        // `false` means "only while the other field is EMPTY". Used where one
        // setting replaces another: writing an Opening line overrides Greeting
        // style entirely, and showing both with no sign of which wins is the
        // shape 0.9.61 took out of front_access.
        else if (want === false) visible = !current;
        else if (Array.isArray(want)) visible = want.indexOf(current) !== -1;
        else visible = current === want;
      }

      anchor.style.display = visible ? '' : 'none';
      const hint = anchor.nextElementSibling;
      if (hint && hint.classList.contains('hint')) {
        hint.style.display = visible ? '' : 'none';
      }
    });

    // A section with nothing visible in it is just noise — and a super-group
    // header with no visible sections under it is worse.
    document.querySelectorAll('details.sec').forEach((sec) => {
      const rows = [...sec.querySelectorAll('.row, .check')];
      if (!rows.length) return;
      const anyVisible = rows.some((r) => r.style.display !== 'none');
      sec.style.display = anyVisible ? '' : 'none';
    });

    document.querySelectorAll('.supergroup').forEach((hdr) => {
      let n = hdr.nextElementSibling, anyVisible = false;
      while (n && !n.classList.contains('supergroup')) {
        if (n.tagName === 'DETAILS' && n.style.display !== 'none') anyVisible = true;
        n = n.nextElementSibling;
      }
      hdr.style.display = anyVisible ? '' : 'none';
    });
  }

  function setEmbedSnippet() {
    $('embedSnippet').value =
      '<div id="subwave-callin"></div>\n' +
      '<script src="' + location.origin + '/embed.js"><\/script>';
  }

  // First-run guidance: a fresh install opens on a panel with no keys and no
  // "start here". When the chosen LLM provider has no key, say what to do
  // first — once a key exists this disappears on its own.
  function paintFirstRun() {
    let banner = $('firstRun');
    const provider = resolved.llm_provider || 'openai';
    const keyField = PROVIDER_KEY[provider];
    const needsKey = keyField && !(secrets[keyField] && secrets[keyField].set);
    if (!needsKey) { if (banner) banner.remove(); return; }
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'firstRun';
      banner.className = 'banner';
      const sub = document.querySelector('#panel .sub');
      (sub || $('panel').firstElementChild).insertAdjacentElement('afterend', banner);
    }
    banner.innerHTML = '';
    banner.append('Start here: 1) add your ' + provider + ' API key under ');
    const jump = document.createElement('a');
    jump.textContent = 'API keys';
    jump.href = '#';
    jump.onclick = (e) => {
      e.preventDefault();
      const sec = document.querySelector('details.sec[data-group="keys"]');
      if (sec) { sec.open = true; sec.scrollIntoView({ behavior: 'smooth' }); }
    };
    banner.append(jump,
      ' · 2) run the full pipeline check at the bottom · 3) press Call.');
  }

  function paint() {
    fill('tts_mode', options.ttsModes);
    SELECT_FIELDS.filter(hasChoices).forEach(fillStatic);
    fill('tts_adapter', options.ttsAdapters);
    fill('tts_voice', options.voices, { blankLabel: "Station's voice for this DJ" });
    fill('llm_provider', options.llmProviders);
    fill('stt_provider', options.sttProviders);

    // "Random each call" sits alongside the roster because it's the same
    // choice: who answers the phone. Blank stays the honest default.
    const RANDOM = '__random__';
    const roster = options.personas || [];
    const ids = [RANDOM].concat(roster.map((p) => p.id));
    const names = { [RANDOM]: 'Random each call' };
    roster.forEach((p) => { names[p.id] = p.name; });
    fill('persona_override', ids, {
      blankLabel: 'Whoever is live on air', labels: names,
    });

    SELECT_FIELDS.filter((f) => !hasChoices(f))
      .forEach((f) => { $(f).value = overrides[f] || ''; });
    TEXT_FIELDS.forEach((f) => {
      $(f).value = overrides[f] || '';
      // What an EMPTY box does is a real setting with real behaviour, so say
      // it: the resolved value if something lower down supplies one, else the
      // schema's own description of the default.
      const meta = SCHEMA.fields[f] || {};
      if (resolved[f]) $(f).placeholder = resolved[f];
      else if (meta.placeholder) $(f).placeholder = meta.placeholder;
    });
    NUM_FIELDS.forEach((f) => { $(f).value = overrides[f] !== '' ? overrides[f] : resolved[f]; });
    CHECK_FIELDS.forEach((f) => { $(f).checked = !!resolved[f]; });

    syncModels();
    applyVisibility();
    setEmbedSnippet();
    paintAsks();
    paintTools();
    paintTags();
    paintFirstRun();
    paintSecurity();
    markClean();

    // paint() runs once before the provider lists land, so this has to survive
    // `options` being empty. It did not: reading .mirroringStation off an
    // undefined voiceSource threw from inside the FIRST paint, which aborted
    // loadSettings before the sounds, the provider lists and the version line —
    // leaving a half-painted panel and "Could not load settings" in the corner.
    // Nothing to say yet is a reason to say nothing, not to guess.
    const src = options.voiceSource, banner = $('mirrorBanner');
    if (banner && !src) {
      banner.style.display = 'none';
    } else if (banner) {
      banner.style.display = 'block';
      if (src.mirroringStation) {
        banner.className = 'banner ok';
        banner.textContent = 'Mirroring ' + src.count + ' persona voices from the station.';
      } else {
        banner.className = 'banner';
        banner.textContent = src.adminConfigured
          ? 'Station settings readable, but no persona–voice mapping found — using the local fallback.'
          : 'Using the local persona–voice fallback. Add the station admin credentials to mirror the station instead.';
      }
    }
  }

  // Station admin credentials belong with the station they unlock, not in
  // the generic key list.
  const STATION_SECRETS = ['subwave_admin_user', 'subwave_admin_pass'];

  // Security section: set/change the panel password, and nudge loudly while
  // none exists — an open panel is fine on a trusted LAN but should be a
  // choice, not an accident.
  function paintSecurity() {
    $('tagSecurity').textContent =
      (authConfigured ? 'admin set' : 'admin OPEN')
      + ' · ' + (guestConfigured ? 'line private' : 'line open');
    $('curPwRow').style.display = authConfigured ? '' : 'none';
    $('setPwBtn').textContent = authConfigured ? 'Change password' : 'Set password';
    $('logoutBtn').hidden = !authConfigured;
    $('setGuestBtn').textContent = guestConfigured ? 'Change guest code' : 'Set guest code';
    $('clearGuestBtn').hidden = !guestConfigured;

    let nudge = $('pwNudge');
    if (authConfigured) { if (nudge) nudge.remove(); return; }
    if (!nudge) {
      nudge = document.createElement('div');
      nudge.id = 'pwNudge';
      nudge.className = 'banner';
      const sub = document.querySelector('#panel .sub');
      (sub || $('panel').firstElementChild).insertAdjacentElement('afterend', nudge);
    }
    nudge.textContent = 'No panel password set — anyone who can reach this page can '
      + 'change settings and spend your API keys. Set one under Security before '
      + 'exposing this beyond your own machine.';
  }

  // Signing out only forgets the password on THIS browser — the panel
  // itself stays protected everywhere.
  $('logoutBtn').onclick = () => {
    // Both credentials, not just the panel one. They are stored separately
    // because they buy different things, but "sign out" means signed out —
    // clearing only the admin key left the phone still open on a deployment
    // whose whole point was that it wasn't.
    localStorage.removeItem('callinAdminKey');
    localStorage.removeItem(CALL_KEY);
    location.reload();
  };

  async function setGuest(code) {
    const out = $('guestResult');
    const btn = code ? $('setGuestBtn') : $('clearGuestBtn');
    btn.disabled = true;
    try {
      const r = await afetch('/auth/password', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: 'guest', new: code }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { showResult(out, false, d.error || 'failed'); return; }
      guestConfigured = !!d.guestConfigured;
      $('sec_guest_pw').value = '';
      paintSecurity();
      // The operator's own browser shouldn't now be locked out of the phone
      // it just locked — the admin password opens the guest door anyway, but
      // storing the code saves them typing it.
      if (code) localStorage.setItem(CALL_KEY, code);
      else localStorage.removeItem(CALL_KEY);
      await refreshLive();
      showResult(out, true, code
        ? 'Guest code set. Callers are asked for it before the line opens; this '
          + 'browser is already through.'
        : 'Guest code removed — anyone who can load the page can call again.');
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  }

  $('setGuestBtn').onclick = () => {
    const code = $('sec_guest_pw').value.trim();
    if (code.length < 6) {
      showResult($('guestResult'), false, 'Use at least 6 characters.');
      return;
    }
    setGuest(code);
  };
  $('clearGuestBtn').onclick = () => setGuest('');

  $('setPwBtn').onclick = async () => {
    const out = $('pwResult');
    const newPw = $('sec_new_pw').value;
    if (newPw.length < 8) {
      showResult(out, false, 'Use at least 8 characters.');
      return;
    }
    const btn = $('setPwBtn'); btn.disabled = true;
    try {
      const r = await afetch('/auth/password', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current: $('sec_current_pw').value, new: newPw }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { showResult(out, false, d.error || 'failed'); return; }
      localStorage.setItem('callinAdminKey', newPw);
      authConfigured = true;
      $('sec_current_pw').value = ''; $('sec_new_pw').value = '';
      paintSecurity();
      showResult(out, true, 'Password saved. This browser stays signed in '
        + 'until you sign out; other browsers and devices will be asked.');
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  function paintSecrets() {
    const host = $('secretRows');
    const stationHost = $('stationSecretRows');
    host.innerHTML = '';
    if (stationHost) stationHost.innerHTML = '';
    Object.keys(secrets).forEach((field) => {
      if (STATION_SECRETS.includes(field) && stationHost) {
        paintSecretRow(stationHost, field);
        return;
      }
      paintSecretRow(host, field);
    });
  }

  function paintSecretRow(host, field) {
    {
      const s = secrets[field];
      const row = document.createElement('div');
      row.className = 'row';

      const label = document.createElement('label');
      label.setAttribute('for', 'sec_' + field);
      label.textContent = s.label;

      const input = document.createElement('input');
      input.type = s.visible ? 'text' : 'password';
      input.id = 'sec_' + field;
      input.autocomplete = 'off';
      input.value = s.visible && s.set ? s.hint : '';
      input.placeholder = s.set
        ? (s.visible ? '' : s.hint + '  (from ' + s.source + ')')
        : 'not set';

      const clear = document.createElement('button');
      clear.textContent = 'Clear';
      clear.className = 'btnquiet';
      clear.disabled = s.source !== 'settings';
      clear.title = s.source === 'settings'
        ? 'Remove the stored key (falls back to .env)' : 'Nothing stored for this key';
      clear.onclick = async () => { clear.disabled = true; await postSecrets({}, [field]); };

      row.append(label, input, clear);
      host.appendChild(row);
    }
  }

  async function postSecrets(set, clear) {
    const out = $('keysResult');
    out.className = 'result on'; out.textContent = 'Saving…';
    try {
      const r = await afetch('/settings/secrets', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ set, clear }),
      });
      const d = await r.json();
      if (!r.ok) { showResult(out, false, d.error || 'Save failed'); return; }
      secrets = d.secrets;
      paintSecrets(); paintTags(); paintFirstRun();
      const n = Object.keys(set).length, c = (clear || []).length;
      showResult(out, true,
        (n ? n + ' key' + (n > 1 ? 's' : '') + ' saved. ' : '') +
        (c ? c + ' cleared. ' : '') + 'Applies to the next caller and to the tests.');
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
  }

  let authConfigured = false, guestConfigured = false;

  async function loadSettings() {
    // Two requests, wildly different costs: /settings is the schema and the
    // current values (~190ms), /settings/options asks the station, the TTS
    // server and Ollama what they can offer (~5s). Waiting for both left the
    // panel showing its raw ungrouped markup for five seconds and then
    // visibly rearranging itself. Only the fast one decides the layout.
    const optionsSoon = afetch('/settings/options');
    optionsSoon.catch(() => {});          // handled below; don't warn early
    const rs = await afetch('/settings');
    // A 401 means the panel is password-protected and this tab isn't in yet.
    if (rs.status === 401) {
      const body = await rs.json().catch(() => ({}));
      const err = new Error(body.error || 'locked');
      err.auth = true; err.body = body;
      throw err;
    }
    const s = await rs.json();
    overrides = s.overrides; resolved = s.resolved; secrets = s.secrets || {};
    authConfigured = !!s.authConfigured;
    guestConfigured = !!s.guestConfigured;
    adoptSchema(s.schema);
    paint(); paintSecrets(); loadSounds();
    // Loaded as of here: the panel is filled and usable. The provider lists
    // below only add choices to dropdowns, and a failure there must not leave
    // the gear trying to fetch everything again on every open.
    loaded = true;

    // Then the provider lists, which only fill in the dropdowns. fill()
    // keeps whatever is already selected, so this cannot steal a choice made
    // while it was in flight.
    try {
      const ro = await optionsSoon;
      if (ro.status !== 401) {
        options = await ro.json();
        paint();
      }
    } catch (e) {
      // The panel is still usable without them — every field keeps its
      // current value, they just cannot be picked from a list.
      console.info('provider lists unavailable:', e && e.message);
    }
    // Which build is this? Anchors every bug report and change over time.
    fetch('/health').then((r) => r.json()).then((h) => {
      $('versionLine').textContent = 'Wave Talk v' + (h.version || '?')
        + ' · ' + location.host;
    }).catch(() => {});
  }

  function showLoginGate(body) {
    $('panel').classList.add('locked');
    $('loginGate').hidden = false;
    const msg = body && body.error;
    $('loginMsg').textContent = (msg && msg !== 'password required') ? msg : '';
    $('loginPw').focus();
  }

  async function tryUnlock() {
    const pw = $('loginPw').value;
    if (!pw) return;
    localStorage.setItem('callinAdminKey', pw);
    $('loginMsg').textContent = 'Checking…';
    try {
      // Single probe first: loadSettings fires two requests in parallel, and
      // a wrong password would count twice against the five-try lockout.
      const probe = await afetch('/settings');
      if (probe.status === 401) {
        const body = await probe.json().catch(() => ({}));
        const err = new Error(body.error || 'wrong password');
        err.auth = true; err.body = body;
        throw err;
      }
      await loadSettings();
      $('panel').classList.remove('locked');
      $('loginGate').hidden = true;
      $('loginPw').value = '';
      $('loginMsg').textContent = '';
    } catch (e) {
      localStorage.removeItem('callinAdminKey');
      $('loginMsg').textContent = (e && e.body && e.body.error) || 'wrong password';
    }
  }
  $('loginBtn').onclick = tryUnlock;
  $('loginPw').addEventListener('keydown', (e) => { if (e.key === 'Enter') tryUnlock(); });

  // Unsaved-change tracking, so Save says whether there's anything to do.
  function pendingPatch() {
    const patch = {};
    SELECT_FIELDS.concat(TEXT_FIELDS).forEach((f) => {
      const base = hasChoices(f) ? String(resolved[f]) : (overrides[f] || '');
      if ($(f).value !== base) patch[f] = $(f).value;
    });
    NUM_FIELDS.forEach((f) => {
      const base = overrides[f] !== '' ? overrides[f] : resolved[f];
      if (String($(f).value) !== String(base)) patch[f] = $(f).value;
    });
    CHECK_FIELDS.forEach((f) => {
      if ($(f).checked !== !!resolved[f]) patch[f] = $(f).checked;
    });
    return patch;
  }
  function markClean() {
    const n = Object.keys(pendingPatch()).length;
    $('saveBtn').classList.toggle('clean', n === 0);
    $('saveBtn').textContent = n ? 'Save ' + n + ' change' + (n > 1 ? 's' : '') : 'Save';
  }
  let eventsBound = false;
  function bindFieldEvents() {
    if (eventsBound) return;
    eventsBound = true;
    ALL_FIELDS.forEach((f) => {
      const el = $(f);
      if (!el) return;
      const onChange = () => {
        markClean();
        applyVisibility();
        // The two reference lists describe the permissions, so they follow
        // the switches rather than waiting for a save.
        if (SCHEMA.fields[f] && SCHEMA.fields[f].group === 'perms') {
          paintAsks(); paintTools();
        }
      };
      el.addEventListener('input', onChange);
      el.addEventListener('change', onChange);
    });
  }

  // Inject the schema's help text under each field, so the explanation lives
  // beside the definition rather than being duplicated in the markup.
  function decorateFields() {
    Object.keys(SCHEMA.fields).forEach((f) => {
      const el = $(f);
      if (!el) return;
      const meta = SCHEMA.fields[f];
      const anchor = el.closest('.row') || el.closest('.check');
      if (!anchor || !meta.help) return;
      let hint = anchor.nextElementSibling;
      if (!hint || !hint.classList.contains('hint') || !hint.dataset.fromSchema) {
        hint = document.createElement('p');
        hint.className = 'hint wide';
        hint.dataset.fromSchema = '1';
        anchor.insertAdjacentElement('afterend', hint);
      }
      hint.textContent = meta.help;
    });
  }

  async function saveSettings(patch) {
    $('saveMsg').textContent = 'Saving…';
    const r = await afetch('/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      $('saveMsg').textContent = e.error || 'Save failed';
      return;
    }
    const fresh = await afetch('/settings').then((x) => x.json());
    resolved = fresh.resolved; overrides = fresh.overrides;
    paint();
    await refreshLive();   // sound + volume settings feed the card
    $('saveMsg').textContent = 'Saved — applies to the next caller';
    setTimeout(() => { $('saveMsg').textContent = ''; }, 4000);
  }

  function draft() {
    const d = {};
    SELECT_FIELDS.concat(TEXT_FIELDS, NUM_FIELDS).forEach((f) => {
      if ($(f).value !== '') d[f] = $(f).value;
    });
    return d;
  }
  function showResult(el, ok, text) {
    el.className = 'result on ' + (ok ? 'good' : 'bad');
    el.textContent = text;
  }

  // A test can come back with something true about HOW it ran rather than
  // what it found — chiefly that a draft URL was tested without the stored
  // key, because a key only ever travels to the host it is saved for.
  // Without this the operator sees an unexplained 401 from their own server.
  function withNote(text, d) {
    return d && d.note ? text + '\n\n' + d.note : text;
  }

  // ------------------------------------------------------------- autofill
  $('tts_mode').onchange = async () => {
    const mode = $('tts_mode').value;
    if (mode && options.ttsBaseUrls[mode]) $('tts_base_url').value = options.ttsBaseUrls[mode];
    await reloadVoices();
    markClean();
  };
  $('llm_provider').onchange = () => {
    const p = $('llm_provider').value;
    $('llm_base_url').value = (options.providerBaseUrls || {})[p] || '';
    syncModels(); markClean();
  };
  $('stt_provider').onchange = () => { syncModels(); markClean(); };

  async function reloadVoices() {
    const url = $('tts_base_url').value;
    const o = await afetch('/settings/options?fresh=1'
      + (url ? '&tts_base_url=' + encodeURIComponent(url) : '')).then((r) => r.json());
    options.voices = o.voices;
    const keep = $('tts_voice').value;
    fill('tts_voice', o.voices, { blankLabel: "Station's voice for this DJ" });
    if (keep) $('tts_voice').value = keep;
  }
  $('refreshVoicesBtn').onclick = async () => {
    const b = $('refreshVoicesBtn'); b.disabled = true;
    try { await reloadVoices(); } finally { b.disabled = false; }
  };

  // ---------------------------------------------------------------- tests
  function playPcm(b64, sampleRate) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const pcm = new Int16Array(bytes.buffer);
    const c = ctx();
    const buf = c.createBuffer(1, pcm.length, sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
    const src = c.createBufferSource();
    const g = c.createGain(); g.gain.value = Math.min(1, volume / 100);
    src.buffer = buf; src.connect(g); g.connect(c.destination); src.start();
  }

  $('testTtsBtn').onclick = async () => {
    const btn = $('testTtsBtn'), out = $('ttsResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Synthesizing…';
    try {
      const d = await afetch('/test/tts', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft()),
      }).then((r) => r.json());
      if (!d.ok) { showResult(out, false, withNote('Failed: ' + d.error, d)); return; }
      const rtf = d.realtimeFactor;
      const verdict = rtf == null ? ''
        : rtf < 0.7 ? '\n✓ Fast enough for a live call.'
        : rtf < 1.0 ? '\n⚠ Tight — usable but little headroom.'
        : '\n✗ Slower than realtime: playback will starve and gap.';
      // A declared sample rate that disagrees with the audio is silent
      // everywhere else — it plays at the wrong speed and pitch and nothing
      // errors — so it has to be able to fail this test on its own, whatever
      // the realtime factor says.
      const rateWrong = d.measuredSampleRate && d.measuredSampleRate !== d.sampleRate;
      showResult(out, rtf != null && rtf < 1.0 && !rateWrong,
        withNote('voice ' + d.voice + '\nfirst audio ' + d.firstAudioMs + 'ms' +
        '\ngenerated ' + d.audioSec + 's in ' + d.wallMs + 'ms' +
        '\nrealtime factor ' + rtf + verdict +
        (d.sampleRateNote ? '\n' + d.sampleRateNote : ''), d));
      if (d.pcmBase64) playPcm(d.pcmBase64, d.sampleRate);
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  $('testLlmBtn').onclick = async () => {
    const btn = $('testLlmBtn'), out = $('llmResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Asking the model…';
    try {
      const d = await afetch('/test/llm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft()),
      }).then((r) => r.json());
      if (!d.ok) {
        showResult(out, false, withNote('Failed: ' + d.error, d));
        // A withheld key is the likeliest reason a draft endpoint 401s, and
        // it is not a missing key — offering to paste one would be wrong.
        if (!d.note) {
          maybeOfferKey(out, $('llm_provider').value || resolved.llm_provider, d.error);
        }
        return;
      }
      const slow = d.firstTokenMs > 1500;
      showResult(out, d.toolCalling && !slow,
        withNote(d.provider + ' / ' + d.model +
        '\nfirst token ' + d.firstTokenMs + 'ms, total ' + d.totalMs + 'ms' +
        '\ntool calling: ' + (d.toolCalling ? '✓ works' : '✗ model did not call the tool') +
        (d.reply ? '\nreply: ' + d.reply : '') +
        (slow ? '\n⚠ Slow to first token — the call will feel laggy.' : '') +
        (d.toolCalling ? '' : '\n✗ Without tool calling the DJ can never submit a request.'), d));
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  function stationQuery() {
    const q = new URLSearchParams();
    if ($('station_base_url').value) q.set('station_base_url', $('station_base_url').value);
    if ($('station_mcp_url').value) q.set('station_mcp_url', $('station_mcp_url').value);
    return q.toString() ? '?' + q.toString() : '';
  }

  $('saveAdminBtn').onclick = async () => {
    const out = $('stationResult');
    const set = {};
    STATION_SECRETS.forEach((f) => {
      const el = $('sec_' + f);
      if (el && el.value.trim()) set[f] = el.value.trim();
    });
    if (!Object.keys(set).length) {
      out.className = 'result on';
      out.textContent = 'Nothing to save — blank fields keep their current value.';
      return;
    }
    const btn = $('saveAdminBtn'); btn.disabled = true;
    try {
      await postSecrets(set, []);
      STATION_SECRETS.forEach((f) => { const el = $('sec_' + f); if (el) el.value = ''; });
      showResult(out, true, 'Credentials saved — applies to the next caller and the tests.');
    } finally { btn.disabled = false; }
  };

  // Tests the DRAFT values if the fields hold anything, otherwise whatever
  // is stored — so a credential can be checked before committing to it.
  $('testAdminBtn').onclick = async () => {
    const btn = $('testAdminBtn'), out = $('stationResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Checking admin access…';
    const body = {};
    STATION_SECRETS.forEach((f) => {
      const el = $('sec_' + f);
      if (el && el.value.trim()) body[f] = el.value.trim();
    });
    try {
      const d = await afetch('/test/admin', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then((r) => r.json());
      showResult(out, !!d.ok, d.detail || d.error || 'no answer');
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  $('testStationBtn').onclick = async () => {
    const btn = $('testStationBtn'), out = $('stationResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Checking station…';
    try {
      const d = await afetch('/test/station' + stationQuery()).then((r) => r.json());
      if (!d.ok) { showResult(out, false, 'Failed: ' + (d.error || 'unreachable')); return; }
      showResult(out, true,
        d.stationUrl + '\nreachable, live DJ: ' + d.liveDj +
        '\n' + d.toolCount + ' tools exposed to callers:\n  ' + d.tools.join('\n  '));
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  $('reloadStationBtn').onclick = async () => {
    const btn = $('reloadStationBtn'), out = $('stationResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Re-reading station…';
    try {
      const q = new URLSearchParams({ fresh: '1' });
      if ($('station_base_url').value) q.set('station_base_url', $('station_base_url').value);
      if ($('tts_base_url').value) q.set('tts_base_url', $('tts_base_url').value);
      const o = await afetch('/settings/options?' + q.toString()).then((r) => r.json());
      options = o;
      const keepPersona = $('persona_override').value;
      const names = {};
      o.personas.forEach((p) => { names[p.id] = p.name; });
      fill('persona_override', o.personas.map((p) => p.id),
           { blankLabel: 'Whoever is live', labels: names });
      $('persona_override').value = keepPersona;
      fill('tts_voice', o.voices, { blankLabel: "Station's voice for this DJ" });
      paintTags();
      await refreshLive();
      showResult(out, true, o.personas.length + ' personas, ' + o.voices.length + ' voices loaded.');
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  $('refreshModelsBtn').onclick = async () => {
    const btn = $('refreshModelsBtn'), out = $('keysResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Reading model lists…';
    try {
      const o = await afetch('/settings/options?fresh=1').then((r) => r.json());
      options = o; syncModels();
      const liveL = Object.keys(o.modelsDiscovered || {}).filter((p) => o.modelsDiscovered[p]);
      showResult(out, liveL.length > 0, liveL.length
        ? 'Live model lists from: ' + liveL.join(', ')
        : 'No provider answered — add a key and try again.');
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  // Sound previews use the DRAFT values — the pack you've just picked and the
  // file you've just chosen — so you hear what you're about to save, not what
  // is currently live.
  // What each pack bundles as files, so a preview plays what a caller would
  // really hear rather than always demonstrating the synthesized set.
  let packAssets = {};
  async function loadPackAssets() {
    try {
      const d = await fetch('/sound-packs').then((r) => r.json());
      packAssets = Object.fromEntries(
        (d.packs || []).map((p) => [p.id, p.assets || {}]));
    } catch (e) { packAssets = {}; }
  }

  function previewSound(kind) {
    const raw = ($('sound_' + kind).value || '').trim();
    const chosen = $('sound_pack').value || 'classic';
    const configured = raw.startsWith(UPLOAD_PREFIX)
      ? '/sounds/' + encodeURIComponent(raw.slice(UPLOAD_PREFIX.length)) : raw;
    // Same order the server resolves in: configured, then bundled, then the
    // synthesized fallback (which playSound reaches when the url is empty).
    const bundled = (packAssets[chosen] || {})[kind] || '';
    const url = configured || bundled;
    const prev = live && live.sounds;
    live = live || {};
    live.sounds = { enabled: true, pack: chosen };
    live.sounds[kind] = url;
    playSound(kind);
    const setName = ($('sound_pack').selectedOptions[0] || {}).textContent;
    const out = $('soundResult');
    out.className = 'result on';
    out.textContent = configured
      ? 'Playing your file: ' + configured
      : bundled
        ? `Playing the ${kind} sound bundled with the ${setName} set.`
        : `Playing the built-in ${kind} sound from the ${setName} set.`;
    setTimeout(() => { if (prev) live.sounds = prev; }, 1500);
  }

  // ------------------------------------------------------------- uploads
  // Somewhere to put your own ring without hosting a file yourself.
  const UPLOAD_PREFIX = 'upload:';
  let uploaded = [];

  function paintSounds() {
    const host = $('soundList');
    if (!host) return;
    host.innerHTML = '';
    if (!uploaded.length) {
      $('uploadHint').textContent = 'Nothing uploaded yet — the built-in set is in use.';
      return;
    }
    $('uploadHint').textContent = '';
    const slots = ['ring', 'pickup', 'hold', 'hangup', 'failed'];
    const labels = { ring: 'Ring', pickup: 'Pick up', hold: 'On hold',
                     hangup: 'Hang up', failed: "Can't connect" };
    uploaded.forEach((name) => {
      const li = document.createElement('li');
      const who = document.createElement('span');
      who.className = 'sname';
      who.textContent = name;

      const play = document.createElement('button');
      play.className = 'btnquiet'; play.textContent = 'Play';
      play.onclick = () => { new Audio('/sounds/' + encodeURIComponent(name)).play(); };

      // Assigning is the point of uploading — without this you'd have to know
      // to type "upload:name.mp3" into the right box yourself.
      const use = document.createElement('select');
      use.innerHTML = '<option value="">Use for…</option>';
      slots.forEach((s) => {
        const o = document.createElement('option');
        o.value = s; o.textContent = labels[s];
        use.appendChild(o);
      });
      use.onchange = () => {
        if (!use.value) return;
        $('sound_' + use.value).value = UPLOAD_PREFIX + name;
        use.value = '';
        markClean();
        showResult($('soundResult'), true,
          'Assigned — press Save to apply it to the next caller.');
      };

      const del = document.createElement('button');
      del.className = 'btnquiet'; del.textContent = 'Remove';
      del.onclick = async () => {
        del.disabled = true;
        const r = await afetch('/settings/sounds/' + encodeURIComponent(name),
                               { method: 'DELETE' });
        const d = await r.json().catch(() => ({}));
        if (r.ok) { uploaded = d.sounds || []; paintSounds(); }
        else { showResult($('soundResult'), false, d.error || 'Could not remove it.'); }
      };

      li.append(who, use, play, del);
      host.appendChild(li);
    });
  }

  async function loadSounds() {
    // Bundled packs need no auth and are useful even if the upload list
    // fails, so they load independently.
    loadPackAssets();
    try {
      const r = await afetch('/settings/sounds');
      if (!r.ok) return;
      const d = await r.json();
      uploaded = d.sounds || [];
      paintSounds();
    } catch (e) { /* the built-ins still work */ }
  }

  if ($('uploadSoundBtn')) {
    $('uploadSoundBtn').onclick = () => $('soundFile').click();
    $('soundFile').onchange = async () => {
      const file = $('soundFile').files[0];
      if (!file) return;
      const out = $('soundResult');
      out.className = 'result on'; out.textContent = 'Uploading ' + file.name + '…';
      const form = new FormData();
      form.append('file', file, file.name);
      try {
        const r = await afetch('/settings/sounds', { method: 'POST', body: form });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { showResult(out, false, d.error || 'Upload failed'); return; }
        uploaded = d.sounds || [];
        paintSounds();
        showResult(out, true, d.name + ' uploaded. Pick which sound it should be, '
          + 'then Save.');
      } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
      finally { $('soundFile').value = ''; }
    };
  }
  // ------------------------------------------------- full pipeline check
  // Runs every leg a real call depends on, in call order, so the first red
  // line is the thing that would actually break the call.
  // Which of the station's own addresses a caller could actually route to.
  //
  // Read from the live peer connection's remote candidates — the addresses
  // the SERVER offered us — because that, not our own connectivity, is what
  // decides whether a stranger can call. Reaches through the SDK for the
  // RTCPeerConnection, so it is written to give up quietly: an unknown result
  // reports the old pass/fail rather than a wrong diagnosis.
  function classifyAddress(addr) {
    if (!addr) return null;
    if (addr.indexOf(':') >= 0) {
      const a = addr.toLowerCase();
      if (a.startsWith('fe80') || a.startsWith('::1')) return 'v6-local';
      if (a.startsWith('fc') || a.startsWith('fd')) return 'v6-private';
      return 'v6-public';
    }
    const o = addr.split('.').map(Number);
    if (o.length !== 4 || o.some(isNaN)) return null;
    if (o[0] === 10 || o[0] === 127 || (o[0] === 192 && o[1] === 168)
      || (o[0] === 172 && o[1] >= 16 && o[1] <= 31)
      || (o[0] === 169 && o[1] === 254)) return 'v4-private';
    // Carrier-grade NAT: a real address, but not one anyone can reach in.
    if (o[0] === 100 && o[1] >= 64 && o[1] <= 127) return 'v4-cgnat';
    return 'v4-public';
  }

  function peerConnectionsOf(room) {
    const pcs = [];
    const push = (pc) => { if (pc && typeof pc.getStats === 'function') pcs.push(pc); };
    try {
      const eng = room.engine || {};
      const mgr = eng.pcManager || {};
      [mgr.publisher, mgr.subscriber, eng.publisher, eng.subscriber]
        .forEach((t) => push(t && (t.pc || t._pc)));
    } catch (e) { /* SDK internals moved — reported as unknown */ }
    return pcs;
  }

  async function serverReachability(room) {
    const kinds = new Set();
    try {
      const pcs = peerConnectionsOf(room);
      if (!pcs.length) return { unknown: true };
      for (const pc of pcs) {
        const stats = await pc.getStats();
        stats.forEach((s) => {
          if (s.type !== 'remote-candidate') return;
          const kind = classifyAddress(s.address || s.ip);
          if (kind) kinds.add(kind);
        });
      }
    } catch (e) {
      return { unknown: true };
    }
    if (!kinds.size) return { unknown: true };
    return {
      unknown: false,
      publicV4: kinds.has('v4-public'),
      publicV6: kinds.has('v6-public'),
      summary: Array.from(kinds).sort().join(', '),
    };
  }

  const PIPELINE = [
    {
      key: 'station', name: 'Station + tools',
      run: async () => {
        const d = await afetch('/test/station' + stationQuery()).then((r) => r.json());
        if (!d.ok) return { status: 'fail', detail: d.error || 'unreachable' };
        return { status: 'pass', detail: d.liveDj + ' live · ' + d.toolCount + ' tools' };
      },
    },
    {
      key: 'livekit', name: 'LiveKit + workers',
      run: async (env) => {
        if (!env.livekit?.ok) return { status: 'fail', detail: env.livekit?.detail || 'unreachable' };
        if (!env.livekitAuth?.ok) return { status: 'fail', detail: env.livekitAuth.detail };
        return { status: 'pass', detail: env.livekit.url + ' · credentials OK' };
      },
    },
    {
      key: 'admin', name: 'Station admin',
      run: async (env) => env.admin?.ok
        ? { status: 'pass', detail: env.admin.detail }
        : { status: 'warn', detail: env.admin?.detail || 'not set' },
    },
    {
      key: 'webhook', name: 'Station webhooks',
      run: async (env) => env.webhook?.registered
        ? { status: 'pass', detail: 'push events registered → ' + (env.webhook.url || '') }
        : { status: 'warn',
            detail: (env.webhook?.detail || 'not registered')
              + ' — the card falls back to 20s polling' },
    },
    {
      key: 'listeners', name: 'Listeners',
      run: async (env) => env.listeners?.requestsOpen
        ? { status: 'pass', detail: env.listeners.detail }
        : { status: 'warn', detail: env.listeners?.detail || 'unknown' },
    },
    {
      key: 'keys', name: 'API keys',
      run: async (env) => env.keys?.ok
        ? { status: 'pass', detail: 'all keys for the current config are set' }
        : { status: 'fail', detail: 'missing: ' + (env.keys?.missing || []).join(', ') },
    },
    {
      key: 'stt', name: 'Speech-to-text',
      run: async (env) => {
        if (!env.stt?.ok) return { status: 'fail', detail: env.stt?.detail || 'could not build' };
        // A corrected mismatch still works, but the settings are lying to you.
        if (env.stt.note) return { status: 'warn', detail: env.stt.detail };
        return { status: 'pass', detail: env.stt.detail + ' (not exercised without live audio)' };
      },
    },
    {
      key: 'llm', name: 'Model + tools',
      run: async () => {
        const d = await afetch('/test/llm', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(draft()),
        }).then((r) => r.json());
        if (!d.ok) return { status: 'fail', detail: d.error, provider: $('llm_provider').value };
        if (!d.toolCalling) {
          return { status: 'fail',
            detail: d.model + ' answered but never called the tool — it could never submit a request' };
        }
        if (d.firstTokenMs > 1500) {
          return { status: 'warn',
            detail: d.model + ' · tools OK but ' + d.firstTokenMs + 'ms to first token — the call will lag' };
        }
        return { status: 'pass', detail: d.model + ' · tools OK · ' + d.firstTokenMs + 'ms' };
      },
    },
    {
      key: 'tts', name: 'Voice synthesis',
      run: async () => {
        const d = await afetch('/test/tts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(draft()),
        }).then((r) => r.json());
        if (!d.ok) return { status: 'fail', detail: d.error };
        const rtf = d.realtimeFactor;
        if (rtf != null && rtf >= 1.0) {
          return { status: 'warn', detail: d.voice + ' · ' + rtf
            + '× realtime — slower than playback, audio will gap' };
        }
        return { status: 'pass', detail: d.voice + ' · ' + d.firstAudioMs + 'ms · ' + rtf + '× realtime' };
      },
    },
    {
      // The one leg no server-side test can see: THIS browser establishing a
      // real WebRTC connection. Every other stage can pass while this fails —
      // classically when LiveKit runs in docker and advertises its container
      // IP as the media address. Uses a probe room the worker ignores; costs
      // nothing and doesn't count against usage limits.
      key: 'media', name: 'Browser media path',
      run: async () => {
        const res = await afetch('/token', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ probe: true }),
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          return { status: 'fail', detail: d.error || ('token mint failed (HTTP ' + res.status + ')') };
        }
        const { token, url, room: roomName } = await res.json();
        const r = new LivekitClient.Room();
        try {
          await Promise.race([
            r.connect(url, token),
            new Promise((_, rej) => setTimeout(() => rej(new Error('no media connection within 10s')), 10000)),
          ]);
          // Connecting from HERE only proves it works from here. What decides
          // whether strangers can call is which addresses the server offered:
          // if the only publicly routable one is IPv6, everyone on an
          // IPv4-only network gets fifteen seconds of ringing and a dead line,
          // and nothing anywhere says so. That is roughly half of callers.
          const reach = await serverReachability(r);
          const base = 'this browser connected to ' + url;
          if (reach.unknown) {
            return { status: 'pass', detail: base + ' — signalling and media both OK' };
          }
          if (!reach.publicV4 && !reach.publicV6) {
            return { status: 'warn',
              detail: base + ', but the station only offered private addresses ('
                + reach.summary + '). LAN calls work; nobody outside your network '
                + 'can connect. Set use_external_ip: true and remove node_ip if it '
                + 'points at a LAN address.' };
          }
          if (!reach.publicV4) {
            return { status: 'warn',
              detail: base + ', but the only public address offered is IPv6 ('
                + reach.summary + '). IPv6 callers connect with no port forwarding; '
                + 'IPv4-only callers — roughly half, and most office wifi — cannot '
                + 'connect at all. Open UDP 7882 and make sure node_ip is not '
                + 'pinned to a LAN address.' };
          }
          return { status: 'pass',
            detail: base + ' — signalling and media OK, publicly reachable ('
              + reach.summary + ')' };
        } catch (e) {
          // A wss endpoint on a different origin than this page usually
          // means a self-signed certificate the browser has never accepted —
          // and there is no popup for that, only visiting the https origin.
          if (url && url.indexOf('wss://') === 0) {
            const httpsOrigin = 'https://' + url.slice('wss://'.length).replace(/\/.*$/, '');
            if (location.origin !== httpsOrigin) {
              return { status: 'fail',
                detail: 'this page (' + location.origin + ') is not the TLS front '
                  + 'door — open ' + httpsOrigin + ', accept the certificate '
                  + 'screen once, and use THAT page for calls. Signalling at '
                  + url + ' can only be trusted from its own origin. ('
                  + ((e && e.message) || e) + ')' };
            }
          }
          return { status: 'fail',
            detail: 'browser could not establish media with ' + url + ' — '
              + 'signalling worked, audio had nowhere to flow. Check '
              + 'rtc.udp_port (7882) is open to this machine, and that '
              + 'livekit.yaml does NOT set node_ip to a LAN address: that '
              + 'overrides the public address use_external_ip discovers and '
              + 'breaks every caller who is not on your network. ('
              + ((e && e.message) || e) + ')' };
        } finally {
          try { r.disconnect(); } catch (e2) {}
          fetch('/call-ended', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ room: roomName }), keepalive: true,
          }).catch(() => {});
        }
      },
    },
    {
      // Tested HERE rather than on the server, because the failure only
      // exists here: an http stream on an https page is blocked as mixed
      // content, silently, and the call runs with no station behind it. The
      // server can fetch that same URL perfectly well and learn nothing.
      key: 'stream', name: 'Station stream',
      run: async () => {
        const s = (live && live.stream) || {};
        if (!s.tuneIn) return { status: 'skip', detail: 'tune-in is off — callers hear only the DJ' };
        if (!s.url) return { status: 'warn', detail: 'no stream URL resolved' };
        const mixed = location.protocol === 'https:' && s.url.indexOf('http://') === 0;
        const loaded = await new Promise((res) => {
          const el = new Audio(); el.muted = true; el.volume = 0; el.preload = 'auto';
          const done = (v) => { try { el.pause(); el.src = ''; } catch (e) {} res(v); };
          el.addEventListener('loadeddata', () => done(true));
          el.addEventListener('canplay', () => done(true));
          el.addEventListener('error', () => done(false));
          el.src = s.url; el.load();
          setTimeout(() => done(false), 8000);
        });
        if (loaded) {
          return { status: 'pass',
            detail: s.url + ' — playing behind the call at ' + (s.volume || 0) + '%' };
        }
        return { status: 'fail',
          detail: mixed
            ? 'this page is https and the stream is http:// — the browser blocks '
              + 'it as mixed content, silently, so the caller hears no station. '
              + 'Set the station stream URL to an https one. (' + s.url + ')'
            : s.url + ' would not load in this browser — callers hear no station '
              + 'behind the DJ. Check the URL is reachable and serves audio.' };
      },
    },
    {
      // Browsers only allow mic capture on HTTPS or localhost. Everything
      // else can pass while a call on a plain http:// LAN address connects
      // and instantly hangs up when capture fails.
      key: 'mic', name: 'Microphone',
      run: async () => {
        if (!window.isSecureContext) {
          const so = (live && live.secureOrigin) || '';
          return { status: 'fail',
            detail: location.origin + ' is not a secure context — browsers '
              + 'only allow the microphone on HTTPS or localhost. '
              + (so && so !== location.origin
                  ? 'Use the secure page instead: ' + so
                    + ' (one-time certificate approval on first visit).'
                  : 'Put a TLS front door in front (see README), or for LAN '
                    + 'testing use Chrome\'s "insecure origins treated as '
                    + 'secure" flag.') };
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
          return { status: 'fail', detail: 'this browser exposes no media devices API' };
        }
        const mics = (await navigator.mediaDevices.enumerateDevices())
          .filter((d) => d.kind === 'audioinput');
        if (!mics.length) return { status: 'fail', detail: 'no microphone found on this device' };
        return { status: 'pass',
          detail: mics.length + ' microphone(s) · permission is asked when a call starts' };
      },
    },
  ];

  const ICON = { pending: '·', running: '◌', pass: '✓', warn: '!', fail: '✕', skip: '–' };

  function renderStages(rows) {
    const host = $('stages');
    host.classList.add('on');
    host.innerHTML = '';
    rows.forEach((r) => {
      const li = document.createElement('li');
      li.className = r.status;
      li.innerHTML = '<span class="icon"></span><span class="nm"></span><span class="dt"></span>';
      li.querySelector('.icon').textContent = ICON[r.status] || '·';
      li.querySelector('.nm').textContent = r.name;
      li.querySelector('.dt').textContent = r.detail || '';
      host.appendChild(li);
    });
  }

  // The same row layout the pipeline check uses, because these are the same
  // kind of thing and were being rendered as padded monospace text — which
  // fell apart the moment a stage had a long note (the STT stage quotes what
  // it heard), taking the column alignment with it.
  function renderTimings(out, d) {
    out.className = 'result on ' + (d.turnMs < 2000 ? 'good' : 'bad');
    out.innerHTML = '';
    const ul = document.createElement('ul');
    ul.className = 'stages timings on';

    // Same three columns as the pipeline check — icon, name, detail — so the
    // two lists read as one design. The elapsed time leads the detail cell
    // with a fixed width, which keeps the numbers in a column without pushing
    // the names out of line with the list above.
    const row = (cls, icon, ms, name, note) => {
      const li = document.createElement('li');
      li.className = cls;
      li.innerHTML = '<span class="icon"></span><span class="nm"></span>'
        + '<span class="dt"><span class="ms"></span><span class="note"></span></span>';
      li.querySelector('.icon').textContent = icon;
      li.querySelector('.nm').textContent = name;
      li.querySelector('.ms').textContent = ms;
      li.querySelector('.note').textContent = note || '';
      ul.appendChild(li);
    };

    // Which stage to point at. Only worth naming a chokepoint when the turn
    // is actually slow — on a fast call the largest stage is just the largest
    // stage, and colouring it would train you to ignore the colour.
    const counting = d.stages.filter((st) => st.counts);
    const slowTurn = d.turnMs >= 1500;
    const worst = counting.reduce((a, b) => (b.ms > (a ? a.ms : -1) ? b : a), null);

    let oneOffs = 0;
    d.stages.forEach((st) => {
      if (!st.counts) {
        oneOffs++;
        return row('oneoff', '·', st.ms + 'ms', st.name, st.note);
      }
      const choke = slowTurn && worst && st === worst;
      row(choke ? 'choke' : '', choke ? '!' : '✓', st.ms + 'ms', st.name, st.note);
    });

    // The number the caller actually experiences, judged against the same
    // 1.5s the panel's own blurb quotes.
    const verdictClass = d.turnMs < 1500 ? 'good' : d.turnMs < 2500 ? 'warn' : 'bad';
    row('total ' + verdictClass, d.turnMs < 1500 ? '✓' : '!',
        d.turnMs + 'ms', 'Per turn', d.verdict);
    out.appendChild(ul);

    if (oneOffs) {
      const foot = document.createElement('p');
      foot.className = 'hint';
      foot.style.margin = '9px 0 0';
      // One line on purpose — it wrapped to two and unbalanced the panel.
      foot.textContent = 'Dimmed rows run once per call, not per turn.';
      out.appendChild(foot);
    }
  }

  // Stage-by-stage timing, and what they compound to for one turn.
  // The speed test writes to its OWN box so running it never wipes the
  // pipeline results — both stay visible side by side.
  $('speedBtn').onclick = async () => {
    const btn = $('speedBtn'), out = $('speedResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Timing every stage…';
    try {
      const d = await afetch('/test/speed', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft()),
      }).then((r) => r.json());
      if (!d.ok) { showResult(out, false, 'Failed: ' + (d.error || 'unknown')); return; }

      renderTimings(out, d);
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  $('runAllBtn').onclick = async () => {
    const btn = $('runAllBtn'), out = $('allResult');
    btn.disabled = true; btn.classList.add('running');
    out.className = 'result on'; out.textContent = 'Running…';

    const rows = PIPELINE.map((s) => ({ name: s.name, status: 'pending', detail: '' }));
    renderStages(rows);

    // /test/env answers three of the stages in one round trip.
    let env = {};
    try {
      env = await afetch('/test/env', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft()),
      }).then((r) => r.json());
    } catch (e) { env = {}; }

    let failed = null, warned = 0;
    for (let i = 0; i < PIPELINE.length; i++) {
      rows[i].status = 'running'; rows[i].detail = 'checking…';
      renderStages(rows);
      try {
        const res = await PIPELINE[i].run(env);
        rows[i].status = res.status;
        rows[i].detail = res.detail;
        if (res.status === 'fail' && !failed) failed = { stage: PIPELINE[i], res };
        if (res.status === 'warn') warned++;
      } catch (e) {
        rows[i].status = 'fail';
        rows[i].detail = e.message;
        if (!failed) failed = { stage: PIPELINE[i], res: { detail: e.message } };
      }
      renderStages(rows);
    }

    const fails = rows.filter((r) => r.status === 'fail').length;
    if (fails) {
      showResult(out, false, fails + ' of ' + rows.length + ' checks failed. '
        + 'First failure: ' + failed.stage.name + '.\nA call will not work until that is fixed.');
      // Offer the key shortcut when the failure is an auth problem.
      maybeOfferKey(out, $('llm_provider').value || resolved.llm_provider, failed.res.detail);
    } else if (warned) {
      showResult(out, false, 'All checks passed, but ' + warned
        + ' would degrade the call — see the warnings above.');
      out.className = 'result on';
    } else {
      showResult(out, true, 'All ' + rows.length
        + ' checks passed. A call should work end to end.');
    }

    btn.disabled = false; btn.classList.remove('running');
  };

  $('testRingBtn').onclick = () => previewSound('ring');
  $('testPickupBtn').onclick = () => previewSound('pickup');
  $('testHoldBtn').onclick = () => previewSound('hold');
  $('testHangupBtn').onclick = () => previewSound('hangup');
  $('testFailedBtn').onclick = () => previewSound('failed');

  $('saveKeysBtn').onclick = async () => {
    const set = {};
    Object.keys(secrets).forEach((field) => {
      const el = $('sec_' + field);
      const v = el ? el.value.trim() : '';
      if (!v) return;
      if (secrets[field].visible && v === secrets[field].hint) return;
      set[field] = v;
    });
    if (!Object.keys(set).length) {
      const out = $('keysResult');
      out.className = 'result on';
      out.textContent = 'Nothing to save — blank fields keep their current value.';
      return;
    }
    $('saveKeysBtn').disabled = true;
    try {
      await postSecrets(set, []);
      Object.keys(secrets).forEach((field) => {
        const el = $('sec_' + field);
        if (el && !secrets[field].visible) el.value = '';
      });
    } finally { $('saveKeysBtn').disabled = false; }
  };

  $('viewPromptBtn').onclick = async () => {
    const btn = $('viewPromptBtn'), out = $('promptResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Assembling…';
    try {
      const d = await afetch('/prompt').then((r) => r.json());
      if (d.error) { showResult(out, false, d.error); return; }
      // Soft budget: past ~1,800 tokens the per-turn latency starts to show.
      // Keep growth visible so it never creeps silently again.
      const BUDGET = 1800;
      const over = d.approxTokens > BUDGET;
      showResult(out, true,
        '── ' + d.persona + ' · ~' + d.approxTokens + ' tokens ('
        + (over ? (d.approxTokens - BUDGET) + ' OVER' : (BUDGET - d.approxTokens) + ' under')
        + ' the ' + BUDGET + ' budget) · ' + d.tools.length + ' tools ──\n\n' + d.prompt);
      out.className = 'result on';
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  // A run button lives inside its row's <summary>, so a click would toggle the
  // row as well as run the thing. Swallow the toggle, and open the row on the
  // way so the output is visible when it arrives.
  document.querySelectorAll('details.diag .runbtn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const row = btn.closest('details');
      if (row) row.open = true;
    });
  });

  // Renders a call as a call: who said what, in order, with the tools the DJ
  // reached for shown inline where they happened. Reading a raw JSON dump to
  // answer "why did that call go wrong" is most of the work.
  // Call records store an instant with its UTC offset; the container runs in
  // UTC, so rendering the raw string showed an operator in New York every
  // timestamp four hours out. Records written before 0.9.49 have no offset —
  // those parse as local and read exactly as they did before, so nothing
  // moves for them.
  function callTime(iso, withDate) {
    const d = new Date(iso || '');
    if (!iso || isNaN(d.getTime())) return (iso || '').slice(11, 19);
    if (withDate === 'short') {
      // Fits one line in a list row; the year is noise across forty calls.
      return d.toLocaleString([], {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        second: '2-digit',
      });
    }
    return withDate
      ? d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'medium' })
      : d.toLocaleTimeString([], { hour12: false });
  }

  // A call's verdict, in one glyph. "Had a problem entry" alone is too blunt
  // — a station 503 the DJ recovered from is not the same as a call where
  // nobody could hear anything — so the caller having actually spoken is what
  // separates a warning from a failure.
  function callVerdict(c) {
    const problems = (c.problems || []).length;
    const spoke = (c.callerTurns || 0) > 0;
    if (!spoke) {
      return { cls: 'fail', icon: '!',
        note: 'no caller audio' + (problems ? ` · ${problems} problem${problems === 1 ? '' : 's'}` : '') };
    }
    if (problems) {
      return { cls: 'warn', icon: '!',
        note: `${problems} problem${problems === 1 ? '' : 's'}` };
    }
    return { cls: 'pass', icon: '✓', note: '' };
  }

  // The body of an opened call, in the order you actually read it: what the
  // call was, what it ran on, who was calling, what went wrong, and only then
  // the conversation. Dumping all of it as one block meant the warning that
  // explained the call was buried under the transcript that didn't.
  function callBody(c) {
    const box = document.createElement('div');
    box.className = 'callbody';

    const section = (title) => {
      const h = document.createElement('div');
      h.className = 'cbhead';
      h.textContent = title;
      box.appendChild(h);
    };
    const facts = (pairs) => {
      const dl = document.createElement('dl');
      dl.className = 'cbfacts';
      pairs.filter(([, v]) => v !== '' && v != null).forEach(([k, v]) => {
        const dt = document.createElement('dt'); dt.textContent = k;
        const dd = document.createElement('dd'); dd.textContent = v;
        dl.appendChild(dt); dl.appendChild(dd);
      });
      box.appendChild(dl);
    };

    const turns = c.callerTurns || 0;
    section('Call');
    facts([
      ['Started', callTime(c.startedAt, true)],
      ['Length', `${Math.round(c.durationSecs || 0)}s`],
      ['DJ', c.persona?.name || '—'],
      ['Caller turns', turns],
      ['Tools used', (c.tools || []).length],
      ['Ended', c.endedBecause || 'caller hung up or the line timed out'],
      ['Room', c.room || c.id || ''],
    ]);

    section('Running on');
    facts([
      ['AI model', c.config?.llm || '—'],
      ['Speech-to-text', c.config?.stt || '—'],
      ['Voice', c.config?.tts || '—'],
    ]);

    // Known only while the process that minted the token is still up, so it
    // is absent rather than wrong on older calls.
    if (c.caller) {
      section('Caller');
      facts([
        ['Client', c.caller.client || '—'],
        ['Network', c.caller.network || 'unknown'],
        ['Address', c.caller.ip || '—'],
      ]);
    }

    if ((c.problems || []).length) {
      section('What went wrong');
      const ul = document.createElement('ul');
      ul.className = 'cbproblems';
      c.problems.forEach((p) => {
        const li = document.createElement('li');
        li.textContent = p.what;
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }

    section('Conversation');
    const events = []
      .concat((c.turns || []).map((t) => ({ t: t.t, kind: t.who, text: t.text })))
      .concat((c.tools || []).map((t) => ({
        t: t.t, kind: 'tool', name: t.name, result: t.result || '',
      })))
      .sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));

    if (!events.length) {
      const p = document.createElement('p');
      p.className = 'cbempty';
      p.textContent = 'Nothing was said on this call.';
      box.appendChild(p);
      return box;
    }

    const talk = document.createElement('div');
    talk.className = 'cbtalk';
    const who = { caller: 'Caller', dj: 'DJ' };
    events.forEach((e) => {
      const line = document.createElement('div');
      line.className = 'cbline ' + e.kind;
      const failed = e.kind === 'tool'
        && /refus|error|fail|could ?n.t|didn.t/i.test(e.result);
      if (failed) line.className += ' bad';
      line.innerHTML = '<span class="t"></span><span class="w"></span><span class="x"></span>';
      line.querySelector('.t').textContent = callTime(e.t);
      line.querySelector('.w').textContent = e.kind === 'tool' ? 'tool' : (who[e.kind] || e.kind);
      line.querySelector('.x').textContent = e.kind === 'tool'
        ? e.name + (e.result ? ' → ' + e.result : '')
        : e.text;
      talk.appendChild(line);
    });
    box.appendChild(talk);
    return box;
  }

  // One <details> per call, closed. Forty records as forty scrolling walls of
  // transcript was unreadable; the header answers "which call was that, and
  // did it go wrong" without opening anything.
  function renderCallRow(c) {
    const v = callVerdict(c);
    const turns = c.callerTurns || 0;
    const tools = (c.tools || []).length;
    const bits = [
      c.persona?.name || 'DJ',
      `${Math.round(c.durationSecs || 0)}s`,
      `${turns} turn${turns === 1 ? '' : 's'}`,
    ];
    if (tools) bits.push(`${tools} tool${tools === 1 ? '' : 's'}`);
    if (v.note) bits.push(v.note);

    const el = document.createElement('details');
    el.className = 'callrow ' + v.cls;
    el.dataset.verdict = v.cls;
    const sum = document.createElement('summary');
    sum.innerHTML = '<span class="icon"></span><span class="nm"></span><span class="dt"></span>';
    sum.querySelector('.icon').textContent = v.icon;
    // No year: "Aug 5, 2026, 2:29:24 AM" wrapped onto a second line and broke
    // the row. The year is never the thing you are looking for in a list that
    // holds the last forty calls.
    sum.querySelector('.nm').textContent = callTime(c.startedAt, 'short');
    sum.querySelector('.dt').textContent = bits.join(' · ');
    el.appendChild(sum);
    el.appendChild(callBody(c));
    return el;
  }

  $('viewCallsBtn').onclick = async () => {
    const btn = $('viewCallsBtn'), out = $('callsResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Fetching…';
    try {
      const d = await afetch('/calls').then((r) => r.json());
      if (d.error) { showResult(out, false, d.error); return; }
      const calls = d.calls || [];
      out.className = 'result on';
      out.innerHTML = '';
      if (!calls.length) {
        $('callBar').hidden = true;
        out.textContent = 'No calls recorded yet. One file is written as each call ends.';
        return;
      }
      // /calls already returns newest first — the call you want is almost
      // always the last one — so this renders in the order given.
      const list = document.createElement('div');
      list.className = 'calllist';
      calls.forEach((c) => list.appendChild(renderCallRow(c)));
      out.appendChild(list);

      // The toolbar is markup rather than built here, so it shares one shape
      // with the log viewer's. The common case is reading the last call, not
      // hunting failures, so the filter is a checkbox and not a remembered mode.
      const rough = calls.filter((c) => callVerdict(c).cls !== 'pass').length;
      const box = $('callsOnlyBad');
      $('callsOnlyBadLabel').textContent = rough
        ? `Only calls with problems (${rough} of ${calls.length})`
        : `Only calls with problems — none of the last ${calls.length}`;
      box.disabled = !rough;
      box.checked = false;
      box.onchange = () => list.classList.toggle('onlybad', box.checked);
      $('callCount').textContent = `${calls.length} call${calls.length === 1 ? '' : 's'}`;
      $('callBar').hidden = false;
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  // Clearing is destructive and the transcripts are a caller's words, so it
  // asks first and says exactly what it removed rather than just emptying.
  $('callsClearBtn').onclick = async () => {
    if (!confirm('Delete every stored call transcript? This cannot be undone.')) return;
    const btn = $('callsClearBtn'), out = $('callsResult');
    btn.disabled = true;
    try {
      const d = await afetch('/calls', { method: 'DELETE' }).then((r) => r.json());
      if (d.error) { showResult(out, false, d.error); return; }
      $('callBar').hidden = true;
      out.className = 'result on';
      out.textContent = d.removed
        ? `Cleared ${d.removed} call record${d.removed === 1 ? '' : 's'}.`
        : 'There was nothing stored to clear.';
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  // The log viewer. Records rather than pre-formatted lines, so a warning can
  // look different from a station read and the 20-second poll can be hidden to
  // leave the calls visible — neither of which is possible against a string.
  let logRecords = [];

  // Levels in severity order, so the filter reads as a scale rather than as
  // whatever order the server happened to see them in.
  const LEVEL_ORDER = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

  function paintLogs() {
    const out = $('logsResult');
    const chosen = [...$('logLevels').selectedOptions].map((o) => o.value);
    const needle = ($('logSearch').value || '').toLowerCase();
    const rows = logRecords.filter((r) =>
      (!chosen.length || chosen.indexOf(r.level) !== -1)
      && (!needle || (r.msg + ' ' + r.logger).toLowerCase().indexOf(needle) !== -1));

    out.innerHTML = '';
    if (!rows.length) {
      const p = document.createElement('p');
      p.className = 'capempty';
      p.textContent = logRecords.length
        ? 'Nothing matches that filter.' : 'No log lines yet.';
      out.appendChild(p);
    } else {
      rows.forEach((r) => {
        const line = document.createElement('div');
        line.className = 'logline lvl-' + String(r.level || 'INFO').toLowerCase();
        line.innerHTML = '<span class="lt"></span><span class="ll"></span>'
          + '<span class="lg"></span><span class="lm"></span>';
        line.querySelector('.lt').textContent = r.t || '';
        line.querySelector('.ll').textContent = (r.level || '')[0] || '·';
        line.querySelector('.ll').title = r.level || '';
        // The callin. prefix is on every line of ours and earns no width.
        line.querySelector('.lg').textContent =
          String(r.logger || '').replace(/^callin\./, '');
        line.querySelector('.lm').textContent = r.msg || '';
        out.appendChild(line);
      });
    }
    $('logCount').textContent = rows.length === logRecords.length
      ? `${rows.length} lines`
      : `${rows.length} of ${logRecords.length}`;
    out.scrollTop = out.scrollHeight;
  }

  $('viewLogsBtn').onclick = async () => {
    const btn = $('viewLogsBtn'), out = $('logsResult');
    btn.disabled = true;
    out.className = 'result on logs'; out.textContent = 'Fetching…';
    try {
      const d = await afetch('/logs').then((r) => r.json());
      if (d.error) { showResult(out, false, d.error); return; }
      // Fall back to the flat lines if this is an older server, so the viewer
      // degrades to what it used to be rather than to nothing.
      logRecords = d.records || (d.lines || []).map((l) => ({
        t: '', level: 'INFO', logger: '', msg: l,
      }));
      const present = d.levels || [];
      const keep = [...$('logLevels').selectedOptions].map((o) => o.value);
      $('logLevels').innerHTML = '';
      LEVEL_ORDER.filter((l) => present.indexOf(l) !== -1).forEach((l) => {
        const o = document.createElement('option');
        o.value = l; o.textContent = l[0] + l.slice(1).toLowerCase();
        o.selected = keep.indexOf(l) !== -1;
        $('logLevels').appendChild(o);
      });
      $('logFilters').hidden = false;
      out.className = 'result on logs';
      paintLogs();
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  $('logLevels').onchange = paintLogs;
  $('logSearch').oninput = paintLogs;
  $('logClearFilters').onclick = () => {
    [...$('logLevels').options].forEach((o) => { o.selected = false; });
    $('logSearch').value = '';
    paintLogs();
  };

  // No confirm here, unlike the call records: this buffer is in memory and
  // docker still holds its own copy of stdout, so nothing is destroyed.
  $('logsClearBtn').onclick = async () => {
    const btn = $('logsClearBtn'), out = $('logsResult');
    btn.disabled = true;
    try {
      const d = await afetch('/logs', { method: 'DELETE' }).then((r) => r.json());
      if (d.error) { showResult(out, false, d.error); return; }
      logRecords = [];
      $('logLevels').innerHTML = '';
      $('logSearch').value = '';
      paintLogs();
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  $('copyEmbedBtn').onclick = async () => {
    const btn = $('copyEmbedBtn');
    try { await navigator.clipboard.writeText($('embedSnippet').value); btn.textContent = 'Copied'; }
    catch (e) { $('embedSnippet').select(); btn.textContent = 'Press Ctrl+C'; }
    setTimeout(() => { btn.textContent = 'Copy snippet'; }, 2200);
  };
  $('previewEmbedBtn').onclick = () => window.open('/?compact=1', '_blank', 'width=430,height=430');

  let loading = false;
  $('gearBtn').onclick = async () => {
    const panel = $('panel');
    panel.classList.toggle('open');
    if (!panel.classList.contains('open') || loaded || loading) return;
    loading = true;
    $('saveMsg').textContent = 'Loading from station, TTS server and Ollama…';
    $('saveBtn').disabled = true;
    try { await loadSettings(); $('saveMsg').textContent = ''; }
    catch (e) {
      if (e && e.auth) { showLoginGate(e.body); $('saveMsg').textContent = ''; }
      else $('saveMsg').textContent = 'Could not load settings — ' + e.message;
    }
    finally { loading = false; $('saveBtn').disabled = false; }
  };

  $('saveBtn').onclick = () => {
    const patch = pendingPatch();
    if (!Object.keys(patch).length) {
      $('saveMsg').textContent = 'Nothing changed';
      setTimeout(() => { $('saveMsg').textContent = ''; }, 2500);
      return;
    }
    saveSettings(patch);
  };

  $('resetBtn').onclick = async () => {
    // Empty string clears the stored override so the env/default reasserts.
    // Checkboxes included: sending `true` here used to write an explicit
    // override that ENABLED every permission — including SFX and skills,
    // which default off precisely because they put audio on air.
    const cleared = {};
    ALL_FIELDS.forEach((f) => { cleared[f] = ''; });
    await saveSettings(cleared);
    await loadSettings();
  };
})();
