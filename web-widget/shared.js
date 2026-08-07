/* Everything the call page and the settings panel both need.

   Loaded first by both surfaces. Deliberately small: this is a shared
   foundation, not a place to put things that only one page uses. Anything
   here has to be genuinely wanted by both, or it belongs in call.js or
   panel.js instead.

   No bundler here by choice, so this publishes one global rather than
   exporting: `Callin`. */
window.Callin = (function () {
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

  // ------------------------------------------------------------ sound state
  // The sound engine used to read `live.sounds` directly off the call page's
  // own copy of /live. Both surfaces now fetch /live for themselves and feed
  // the result in here, so neither has to reach into the other's state.
  let audioCtx = null;
  let soundConfig = {};
  let volume = 100;

  function setSounds(s) { soundConfig = s || {}; }
  function setVolume(v) { volume = v; }
  function getVolume() { return volume; }

  // The caller's guest code. Both surfaces touch it: the call page reads it to
  // decide whether the door is already open, and the panel writes it when the
  // operator sets a code, so the browser that just locked the phone is not the
  // one left outside it.
  const CALL_KEY = 'callinCallKey';
  const callKey = () => localStorage.getItem(CALL_KEY) || '';

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
    return PACKS[soundConfig.pack] || PACKS.classic;
  }

  let ringTimer = null;
  function playSound(kind) {
    const s = soundConfig;
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
    // The three station-wide ones. Each says who it lands on, because that is
    // the whole difference between this group and everything above it.
    { need: 'allow_skip_track', say: '“Can you skip this one?”',
      why: 'Ends the record for EVERYONE listening, not just the caller.' },
    { need: 'allow_dj_segment', say: '“Do the station ident.” / “Read the time.”',
      why: 'Fires a programme beat on air — a station ID, the hour, a link.' },
    { need: 'allow_takeover', say: '“Any chance of putting the late show on?”',
      why: 'Puts a different DJ on air for everyone, for an hour, from the end of this record.' },
    { need: null, say: '“Who is this? What’s the story behind this record?”',
      why: 'Answered in character — the DJ knows what’s playing and talks about it.' },
    { need: null, say: '“How long have you been doing the night shift?”',
      why: 'Answered in character from the DJ Card — no tool needed.' },
  ];

  // The other half of the truth: what a caller CANNOT do, and why. Without
  // this the permissions list reads as if anything might be one toggle away.
  //
  // Everything here must be true at EVERY setting, which is the whole value of
  // the list — and it has now been wrong twice in the same way. "Start or end
  // a show, or hand over to another DJ" lived here until show takeover shipped
  // as an opt-in permission. "Skip or stop the current track" outlasted
  // `allow_skip_track` by longer: the panel told an operator, in the section
  // whose job is to say what a caller can never do, that a caller could never
  // skip a track — three sections below a checkbox that lets them.
  //
  // So: nothing goes in this list that has a settings field. The entries below
  // are exactly the tools gated NEVER in call/tools/registry.py, plus the
  // shows CRUD the station never exposes to us at all.
  const NEVER = [
    ['Fire sound effects or stingers', 'nothing to add to a call, plenty to disrupt on air'],
    ['Rebuild the playlist', 'one caller should not reshape the night for everyone'],
    ['Create, edit or delete a show, or change the weekly schedule', 'the programming itself is the operator’s'],
  ];

  return {
    $, params, compact, captionsMode, framed, themeForcedByHost,
    ASKS, NEVER, CALL_KEY, callKey,
    ctx, tone, noise, pack, playSound, startRinging, stopRinging,
    setSounds, setVolume, getVolume,
  };
})();

