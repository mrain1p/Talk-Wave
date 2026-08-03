/* SUB/WAVE call-in widget logic. Served by token_server as /app.js.
   Loaded at the end of <body>, so the DOM exists when it runs. */
(function () {
  const params  = new URLSearchParams(location.search);
  const compact = params.get('compact') === '1';
  if (compact) document.body.classList.add('compact');

  const $ = (id) => document.getElementById(id);

  // Theme: an explicit choice is remembered and beats the OS setting. Embeds
  // can force one with ?theme=light|dark so the widget matches the host page.
  (function theme() {
    const forced = params.get('theme');
    const saved = forced || localStorage.getItem('callinTheme');
    if (saved === 'light' || saved === 'dark') {
      document.documentElement.setAttribute('data-theme', saved);
    }
    const btn = document.getElementById('themeBtn');
    if (!btn) return;
    btn.onclick = () => {
      const now = document.documentElement.getAttribute('data-theme')
        || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
      const next = now === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('callinTheme', next);
    };
  })();

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
    try {
      streamEl = new Audio(s.url);
      streamEl.crossOrigin = 'anonymous';
      streamEl.volume = Math.min(1, (s.volume || 0) / 100);
      streamEl.muted = !s.volume;
      streamEl.play().catch((e) => console.info('tune-in blocked:', e.message));
    } catch (e) { streamEl = null; }
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

  const BUILTIN = {
    // North-American ringback: 440+480Hz, two-second burst.
    ring:   () => { tone([440, 480], 0, 1.1, 0.13); },
    // Line picking up: a short click, then a soft confirmation blip.
    pickup: () => { tone([220], 0, 0.045, 0.16); tone([660], 0.07, 0.10, 0.09); },
    // Hanging up: descending pair.
    hangup: () => { tone([480], 0, 0.14, 0.11); tone([380], 0.15, 0.22, 0.10); },
  };

  let ringTimer = null;
  function playSound(kind) {
    const s = (live && live.sounds) || {};
    if (!s.enabled) return;
    const url = s[kind];
    if (url) {
      try {
        const a = new Audio(url);
        a.volume = Math.min(1, volume / 100);
        a.play().catch(() => BUILTIN[kind] && BUILTIN[kind]());
        return;
      } catch (e) { /* fall through to built-in */ }
    }
    if (BUILTIN[kind]) BUILTIN[kind]();
  }

  function startRinging() {
    playSound('ring');
    ringTimer = setInterval(() => playSound('ring'), 2600);
  }
  function stopRinging() {
    if (ringTimer) { clearInterval(ringTimer); ringTimer = null; }
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
      live = d;
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

      if (!room) { callBtn.disabled = false; callBtn.textContent = 'Call the DJ'; }

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
      paintOffAir('offline');
      setStatus('Station unreachable', 'error');
    }
  }

  // ------------------------------------------------------------ level meters
  const BAR_COUNT = 14;
  function buildBars(host) {
    host.innerHTML = '';
    for (let i = 0; i < BAR_COUNT; i++) host.appendChild(document.createElement('i'));
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
    const bars = host.children;
    for (let i = 0; i < bars.length; i++) {
      // Rough spectrum shape: middle bars run taller than the edges.
      const shape = 0.55 + 0.45 * Math.sin((i / (bars.length - 1)) * Math.PI);
      const h = active ? Math.max(0.12, Math.min(1, lvl * shape * 1.9)) : 0.12;
      bars[i].style.height = (h * 100) + '%';
      bars[i].classList.toggle('hot', active && h > 0.2);
    }
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
  };
  function setAgentState(state) {
    const chip = $('stateChip');
    chip.dataset.state = state || 'idle';
    $('stateText').textContent = STATE_TEXT[state] || 'Idle';
    // The first time the DJ actually speaks, the call is properly underway:
    // the button flips from Answering to a green On the line for the rest
    // of the call.
    if (state === 'speaking' && room && !callBtn.classList.contains('live')) {
      callBtn.classList.remove('ringing', 'answering');
      callBtn.classList.add('live');
      callBtn.textContent = 'On the line';
    }
  }

  function watchAgentState(r) {
    const read = (p) => {
      const s = p && p.attributes && p.attributes['lk.agent.state'];
      if (s) setAgentState(s);
    };
    r.on(LivekitClient.RoomEvent.ParticipantAttributesChanged, (_changed, p) => read(p));
    r.on(LivekitClient.RoomEvent.ParticipantConnected, read);
    r.remoteParticipants.forEach(read);
  }

  // ---------------------------------------------------------------- captions
  const capNodes = new Map();
  const lastByWho = {};   // { who: {node, text, at} }

  function addCaption(id, who, text, final) {
    if (!text) return;
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
    }
    node.querySelector('.said').textContent = text;
    node.classList.toggle('interim', !final);
    lastByWho[who] = { node, text, at: Date.now() };
    capBox.scrollTop = capBox.scrollHeight;
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
  function applyVolume() {
    $('volPct').textContent = volume + '%';
    if (djEl) djEl.volume = Math.min(1, volume / 100);
  }
  $('volSlider').oninput = (e) => { volume = +e.target.value; applyVolume(); };

  async function startCall() {
    callBtn.disabled = true;
    callBtn.textContent = 'Ringing…';
    callBtn.classList.add('ringing');
    $('rig').classList.add('on');
    $('stateChip').hidden = false;
    setAgentState('initializing');
    startTimer();
    setStatus('Connecting…', 'connecting');
    $('endedBar').hidden = true;
    capNodes.clear();
    capBox.classList.add('on');
    capBox.innerHTML = '<p class="capempty">Captions will appear here as you talk…</p>';

    ctx();          // unlock audio inside the click gesture
    startRinging();
    tuneIn();       // count the caller as a listener so requests are accepted

    try {
      const res = await fetch('/token', { method: 'POST' });
      if (res.status === 429) {
        const d = await res.json().catch(() => ({}));
        stopRinging(); tuneOut();
        setStatus(d.error || 'The lines are busy — try again shortly.', 'error');
        $('rig').classList.remove('on');
        $('stateChip').hidden = true;
        stopTimer();
        capBox.classList.remove('on');
        callBtn.classList.remove('ringing', 'answering');
        callBtn.textContent = 'Call the DJ';
        callBtn.disabled = false;
        room = null;
        return;
      }
      if (!res.ok) throw new Error('token mint failed');
      const { token, url, room: roomName } = await res.json();
      currentRoom = roomName;

      room = new LivekitClient.Room({ adaptiveStream: true, dynacast: true });

      room.on(LivekitClient.RoomEvent.TrackSubscribed, (track) => {
        if (track.kind !== 'audio') return;
        stopRinging();
        playSound('pickup');
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
      // The failure often happens AFTER room.connect() succeeded (a blocked
      // mic, typically). Without this the room stays joined and the agent
      // sits in an empty call.
      tuneOut();
      if (room) { try { await room.disconnect(); } catch (e) {} }
      const denied = err && (err.name === 'NotAllowedError'
        || /permission|not allowed|denied/i.test(err.message || ''));
      setStatus(denied ? 'Microphone blocked — allow mic access' : 'Could not connect', 'error');
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
    paintBars($('barsYou'), 0, false); paintBars($('barsDj'), 0, false);
    $('djAvatar').classList.remove('talking');
    $('rig').classList.remove('on');
    $('stateChip').hidden = true;
    stopTimer();
    setAgentState('idle');
    collapseTranscript();
    callBtn.textContent = 'Call the DJ';
    callBtn.classList.remove('live', 'ringing', 'answering');
    callBtn.disabled = false;
    document.querySelector('.card').classList.remove('oncall');
    muteBtn.textContent = 'Mute';
    muteBtn.classList.remove('on');
    $('meterYou').classList.remove('muted');
    setStatus('Call ended');
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
  }

  $('endedBar').onclick = () => {
    const bar = $('endedBar');
    const open = !capBox.classList.contains('on');
    capBox.classList.toggle('on', open);
    bar.classList.toggle('open', open);
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

  // =================================================== settings (full page)
  if (compact) return;

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

    panel.querySelectorAll('.supergroup').forEach((h) => h.remove());

    const supers = SCHEMA.supergroups || [];
    const byId = {};
    panel.querySelectorAll('details.sec').forEach((sec) => {
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

  let options = null, overrides = {}, resolved = {}, secrets = {};

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
    const list = options.llmModels[llm] || [];
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
    fill('stt_model', options.sttModels[stt] || []);
    $('stt_model').value = overrides.stt_model || '';
  }

  function keyJump(container, field) {
    const row = $('sec_' + field);
    if (!row) return;
    const link = document.createElement('button');
    link.textContent = 'Add ' + (secrets[field] ? secrets[field].label : field) + ' key';
    link.style.cssText = 'display:block;margin-top:9px;background:#e0533d;color:#fff;'
      + 'font-size:12.5px;padding:8px 14px';
    link.onclick = () => {
      const sec = row.closest('details');
      if (sec) sec.open = true;
      row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      row.focus();
      row.style.borderColor = '#e0533d';
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
    $('tagSounds').textContent = resolved.call_sounds ? 'on' : 'off';
    $('tagStyle').textContent = [resolved.style_answering, resolved.style_signoff]
      .filter(Boolean).length + ' set';
    $('tagHygiene').textContent = (resolved.strip_stage_directions ? 'directions stripped' : 'raw')
      + ' · ' + (resolved.profanity_mode === 'off' ? 'no filter' : resolved.profanity_mode);
    $('tagUsage').textContent = (resolved.max_concurrent_calls || '∞') + ' at once · '
      + (resolved.calls_per_hour || '∞') + '/hr · ' + (resolved.caller_cooldown_secs || 0) + 's redial';
    $('tagCallback').textContent = resolved.callback_enabled
      ? 'on · ' + resolved.callback_max_words + ' words' : 'off';
    $('tagContext').textContent = [resolved.context_recent_tracks + ' played',
      resolved.context_upcoming + ' queued', resolved.context_booth_lines + ' on-air'].join(' · ');
    $('tagCall').textContent = (resolved.persona_override
      ? 'pinned persona' : 'live DJ') + ' · ' + resolved.max_call_seconds + 's';
  }

  // Worked examples of what a caller can actually say, tied to the permission
  // that enables each one — so the list can't drift from the real tool surface.
  const ASKS = [
    { need: null, say: '“What’s playing right now?”',
      why: 'Reads live station state — always available.' },
    { need: null, say: '“What have you been playing tonight?”',
      why: 'Recent history and what’s queued next.' },
    { need: 'allow_requests', say: '“Can you play something slower?”',
      why: 'Vague requests work — the station resolves them.' },
    { need: 'allow_library_search', say: '“Have you got any Fleetwood Mac?”',
      why: 'Searches the real library before promising anything.' },
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
    { need: null, say: '“How long have you been doing the night shift?”',
      why: 'Answered in character from the DJ Card — no tool needed.' },
  ];

  function paintAsks() {
    const host = $('askList');
    if (!host) return;
    host.innerHTML = '';
    let on = 0;
    ASKS.forEach((a) => {
      const enabled = !a.need || !!resolved[a.need];
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
    const tag = $('tagAsk');
    if (tag) tag.textContent = on + ' of ' + ASKS.length + ' available';
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

    const ids = options.personas.map((p) => p.id);
    const names = {};
    options.personas.forEach((p) => { names[p.id] = p.name; });
    fill('persona_override', ids, { blankLabel: 'Whoever is live', labels: names });

    SELECT_FIELDS.filter((f) => !hasChoices(f))
      .forEach((f) => { $(f).value = overrides[f] || ''; });
    TEXT_FIELDS.forEach((f) => {
      $(f).value = overrides[f] || '';
      if (resolved[f]) $(f).placeholder = resolved[f];
    });
    NUM_FIELDS.forEach((f) => { $(f).value = overrides[f] !== '' ? overrides[f] : resolved[f]; });
    CHECK_FIELDS.forEach((f) => { $(f).checked = !!resolved[f]; });

    syncModels();
    applyVisibility();
    setEmbedSnippet();
    paintAsks();
    paintTags();
    paintFirstRun();
    markClean();

    const src = options.voiceSource, banner = $('mirrorBanner');
    if (banner) {
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
      clear.style.cssText = 'flex:0 0 auto;background:#2b2b31;color:#9a9aa2;font-size:12px;padding:8px 12px';
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
      const r = await fetch('/settings/secrets', {
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

  async function loadSettings() {
    const [o, s] = await Promise.all([
      fetch('/settings/options').then((r) => r.json()),
      fetch('/settings').then((r) => r.json()),
    ]);
    options = o; overrides = s.overrides; resolved = s.resolved; secrets = s.secrets || {};
    adoptSchema(s.schema);
    paint(); paintSecrets();
  }

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
      const onChange = () => { markClean(); applyVisibility(); };
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
    const r = await fetch('/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      $('saveMsg').textContent = e.error || 'Save failed';
      return;
    }
    const fresh = await fetch('/settings').then((x) => x.json());
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
    const o = await fetch('/settings/options?fresh=1'
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
      const d = await fetch('/test/tts', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft()),
      }).then((r) => r.json());
      if (!d.ok) { showResult(out, false, 'Failed: ' + d.error); return; }
      const rtf = d.realtimeFactor;
      const verdict = rtf == null ? ''
        : rtf < 0.7 ? '\n✓ Fast enough for a live call.'
        : rtf < 1.0 ? '\n⚠ Tight — usable but little headroom.'
        : '\n✗ Slower than realtime: playback will starve and gap.';
      showResult(out, rtf != null && rtf < 1.0,
        'voice ' + d.voice + '\nfirst audio ' + d.firstAudioMs + 'ms' +
        '\ngenerated ' + d.audioSec + 's in ' + d.wallMs + 'ms' +
        '\nrealtime factor ' + rtf + verdict);
      if (d.pcmBase64) playPcm(d.pcmBase64, d.sampleRate);
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  $('testLlmBtn').onclick = async () => {
    const btn = $('testLlmBtn'), out = $('llmResult');
    btn.disabled = true;
    out.className = 'result on'; out.textContent = 'Asking the model…';
    try {
      const d = await fetch('/test/llm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft()),
      }).then((r) => r.json());
      if (!d.ok) {
        showResult(out, false, 'Failed: ' + d.error);
        maybeOfferKey(out, $('llm_provider').value || resolved.llm_provider, d.error);
        return;
      }
      const slow = d.firstTokenMs > 1500;
      showResult(out, d.toolCalling && !slow,
        d.provider + ' / ' + d.model +
        '\nfirst token ' + d.firstTokenMs + 'ms, total ' + d.totalMs + 'ms' +
        '\ntool calling: ' + (d.toolCalling ? '✓ works' : '✗ model did not call the tool') +
        (d.reply ? '\nreply: ' + d.reply : '') +
        (slow ? '\n⚠ Slow to first token — the call will feel laggy.' : '') +
        (d.toolCalling ? '' : '\n✗ Without tool calling the DJ can never submit a request.'));
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
      const d = await fetch('/test/admin', {
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
      const d = await fetch('/test/station' + stationQuery()).then((r) => r.json());
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
      const o = await fetch('/settings/options?' + q.toString()).then((r) => r.json());
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
      const o = await fetch('/settings/options?fresh=1').then((r) => r.json());
      options = o; syncModels();
      const liveL = Object.keys(o.modelsDiscovered || {}).filter((p) => o.modelsDiscovered[p]);
      showResult(out, liveL.length > 0, liveL.length
        ? 'Live model lists from: ' + liveL.join(', ')
        : 'No provider answered — add a key and try again.');
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  // Sound previews use the draft values, so you hear what you're about to save.
  function previewSound(kind) {
    const url = $('sound_' + kind).value.trim();
    const prev = live && live.sounds;
    live = live || {};
    live.sounds = { enabled: true, ring: '', pickup: '', hangup: '' };
    live.sounds[kind] = url;
    playSound(kind);
    const out = $('soundResult');
    out.className = 'result on';
    out.textContent = url ? 'Playing your file: ' + url : 'Playing the built-in ' + kind + ' sound.';
    setTimeout(() => { if (prev) live.sounds = prev; }, 1500);
  }
  // ------------------------------------------------- full pipeline check
  // Runs every leg a real call depends on, in call order, so the first red
  // line is the thing that would actually break the call.
  const PIPELINE = [
    {
      key: 'station', name: 'Station + tools',
      run: async () => {
        const d = await fetch('/test/station' + stationQuery()).then((r) => r.json());
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
        const d = await fetch('/test/llm', {
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
        const d = await fetch('/test/tts', {
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
        const res = await fetch('/token', {
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
          return { status: 'pass', detail: 'this browser connected to ' + url + ' — signalling and media both OK' };
        } catch (e) {
          return { status: 'fail',
            detail: 'browser could not establish media with ' + url + ' — '
              + 'if LiveKit runs in docker, set rtc.node_ip to the host’s LAN IP '
              + 'in livekit.yaml and check UDP 50000–50100 is open. ('
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

  // Stage-by-stage timing, and what they compound to for one turn.
  $('speedBtn').onclick = async () => {
    const btn = $('speedBtn'), out = $('allResult');
    btn.disabled = true;
    $('stages').classList.remove('on');
    out.className = 'result on'; out.textContent = 'Timing every stage…';
    try {
      const d = await fetch('/test/speed', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft()),
      }).then((r) => r.json());
      if (!d.ok) { showResult(out, false, 'Failed: ' + (d.error || 'unknown')); return; }

      const lines = d.stages.map((st) => {
        const ms = String(st.ms).padStart(6) + 'ms';
        const mark = st.counts ? ' ' : '·';
        return mark + ms + '  ' + st.name + (st.note ? '\n           ' + st.note : '');
      });
      const good = d.turnMs < 2000;
      showResult(out, good,
        lines.join('\n') +
        '\n' + '─'.repeat(46) +
        '\n' + String(d.turnMs).padStart(6) + 'ms  PER TURN (what the caller waits)' +
        '\n           ' + d.verdict +
        '\n\n· = one-off per call, not part of each turn');
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
      env = await fetch('/test/env', {
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
  $('testHangupBtn').onclick = () => previewSound('hangup');

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
      const d = await fetch('/prompt').then((r) => r.json());
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
    if (!panel.classList.contains('open') || options || loading) return;
    loading = true;
    $('saveMsg').textContent = 'Loading from station, TTS server and Ollama…';
    $('saveBtn').disabled = true;
    try { await loadSettings(); $('saveMsg').textContent = ''; }
    catch (e) { $('saveMsg').textContent = 'Could not load settings — ' + e.message; }
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
