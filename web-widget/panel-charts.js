/* The ACTIVITY strip: four charts between the dashboard and the settings.

   Spec §7 (talkwave-panel-spec.md). Two honest sources and nothing else: the
   call records the panel already keeps (/calls) and the sampled listener
   series (/stats/listeners). A series that isn't available — signed out, no
   records yet, a station that never answered the sampler — renders as an
   em-dash caption over an empty frame, never as invented bars.

   Its own file rather than more panel-viewers.js: the viewers are READERS of
   individual records; this is a renderer with its own persisted state
   (range, count, dimension, filter) and it needs exactly one name from the
   rest of the panel (afetch). No chart library, per the spec — flex bars and
   one inline SVG. */
(function () {
  const { $ } = window.Callin;
  const { afetch } = window.Panel;
  if (!$('activityStrip')) return;

  // ------------------------------------------------------------- state
  const LS = 'callinActivity';
  const DOORS = ['call', 'chat', 'voicemail'];
  const RATES = ['up', 'down', 'none'];
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(LS) || '{}'); } catch (e) { /* fresh */ }
  // Two independent multi-selects, both defaulting to everything — which
  // doors count, and which ratings count — applied to every chart at once.
  // (Replaced the single dimension+filter pair on the operator's ask.)
  // A sensible span per unit, not one number for all three: seven days is a
  // week, but seven months is over half a year and seven weeks is an odd
  // shape nobody asks for. The operator's 2026-08-12 ask — that switching the
  // unit must not silently change what the box means — is still honoured,
  // because the count is REMEMBERED per unit rather than reset on every
  // switch: change weeks to 6 and it stays 6.
  const SHOW_DEFAULTS = { day: 7, week: 4, month: 12 };
  const state = Object.assign(
    { range: 'week', shows: Object.assign({}, SHOW_DEFAULTS),
      doors: DOORS.slice(), rates: RATES.slice() },
    stored);
  // A store written before per-unit counts carries a single `show`; seed
  // every unit from it so an upgrade does not silently re-scale their charts.
  if (typeof state.show === 'number' && !stored.shows) {
    state.shows = { day: state.show, week: state.show, month: state.show };
  }
  state.shows = Object.assign({}, SHOW_DEFAULTS, state.shows || {});
  Object.defineProperty(state, 'show', {
    get() { return this.shows[this.range]; },
    set(v) { this.shows[this.range] = v; },
  });
  state.doors = (Array.isArray(state.doors) ? state.doors : DOORS)
    .filter((d) => DOORS.indexOf(d) !== -1);
  state.rates = (Array.isArray(state.rates) ? state.rates : RATES)
    .filter((r) => RATES.indexOf(r) !== -1);
  delete state.dim; delete state.filter;      // pre-0.10.50 vocabulary
  const save = () => localStorage.setItem(LS, JSON.stringify(state));
  // Show is HOW MANY of whatever unit is selected — 7 days, 7 weeks, 7
  // months — so switching the unit keeps the count and changes the span
  // (operator's ask, 2026-08-12). It used to mean days no matter what, with
  // week silently capping at 7 and month at 30.
  const clampShow = (n) => Math.max(1, Math.min(45, n | 0 || 7));
  state.show = clampShow(state.show);

  let calls = null;       // null = unavailable; [] = genuinely no records
  let samples = null;

  // ------------------------------------------------------------- facts
  const kindOf = (c) => (c.kind === 'voicemail' ? 'voicemail'
    : c.kind === 'chat' ? 'chat' : 'call');
  // Same verdict the calls viewer draws: nobody heard = failed, whatever
  // else went wrong is a warning — both count as "failed" for the bars.
  const failedC = (c) => !!((c.problems || []).length || !(c.callerTurns || 0));
  const when = (c) => new Date(c.startedAt || 0).getTime();
  const ttfwOf = (c) => {
    // Seconds from pickup until the caller stops hearing silence.
    // firstWordAt (0.10.55) is stamped when the DJ's audio STARTS; older
    // records fall back to the first dj turn, which commits only after the
    // utterance finishes and so overstates by the greeting's length. No DJ
    // audio at all = no bar; that call is the DOORS chart's failure.
    if (!c.startedAt) return null;
    let at = c.firstWordAt ? new Date(c.firstWordAt).getTime() : NaN;
    if (!isFinite(at)) {
      const dj = (c.turns || []).find((t) => t.who === 'dj');
      if (!dj) return null;
      at = new Date(dj.t).getTime();
    }
    const secs = (at - when(c)) / 1000;
    return isFinite(secs) && secs >= 0 && secs < 600 ? secs : null;
  };
  const rateOf = (c) => (c.rating === 'up' ? 'up'
    : c.rating === 'down' ? 'down' : 'none');
  const matches = (c) => state.doors.indexOf(kindOf(c)) !== -1
    && state.rates.indexOf(rateOf(c)) !== -1;

  // ------------------------------------------------------------- buckets
  const DAY_MS = 24 * 3600 * 1000;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  const md = (d) => (d.getMonth() + 1) + '/' + d.getDate();

  // Every bucket carries a label; how many of them get PRINTED is the only
  // thing that thins. Aim for about ten ticks whatever the count, so 45
  // buckets read as an axis rather than a smear (the operator asked for
  // labels at the increments "within reason" — this is the reason).
  function tickEvery(n) { return n <= 12 ? 1 : Math.ceil(n / 10); }

  // Labels and tooltips are built as HTML strings here, so anything derived
  // from a record has to be escaped on the way in.
  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // A short when for a single call: the date, plus the time when the window
  // is a single day and the date alone would say nothing.
  function stamp(ms) {
    const d = new Date(ms);
    return state.range === 'day' && clampShow(state.show) === 1
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : md(d);
  }

  function buckets() {
    const out = [];
    const n = clampShow(state.show);
    if (state.range === 'day' && n === 1) {
      // One day, hour by hour — the old intraday view, now reached by
      // asking for a single day rather than being what "day" always meant.
      const end = new Date(); end.setMinutes(0, 0, 0);
      const endMs = end.getTime() + 3600 * 1000;
      for (let i = 23; i >= 0; i--) {
        const start = endMs - (i + 1) * 3600 * 1000;
        const h = new Date(start).getHours();
        out.push({ start, end: start + 3600 * 1000,
                   label: (h < 10 ? '0' : '') + h + ':00' });
      }
      out.forEach((b, i) => {
        const h = new Date(b.start).getHours();
        b.tick = i === out.length - 1 ? 'NOW'
          : h === 0 ? '00:00' : h === 12 ? '12:00' : '';
      });
      return out;
    }
    const every = tickEvery(n);
    const marked = (i) => i === n - 1 || i === 0 || (n - 1 - i) % every === 0;
    if (state.range === 'month') {
      // Calendar months, so "3 months" means three months as anyone reads
      // it, not ninety days.
      const now = new Date();
      for (let i = n - 1; i >= 0; i--) {
        const s = new Date(now.getFullYear(), now.getMonth() - i, 1);
        const e = new Date(now.getFullYear(), now.getMonth() - i + 1, 1);
        const label = MONTHS[s.getMonth()]
          + (s.getFullYear() === now.getFullYear()
             ? '' : " '" + String(s.getFullYear()).slice(2));
        out.push({ start: s.getTime(), end: e.getTime(), label,
                   tick: marked(i) ? label : '' });
      }
      return out;
    }
    const today = new Date(); today.setHours(0, 0, 0, 0);
    if (state.range === 'week') {
      // Weeks back from the one containing today, each starting Sunday, so
      // a bucket is the week people mean rather than a rolling seven days.
      const sun = new Date(today);
      sun.setDate(sun.getDate() - sun.getDay());
      for (let i = n - 1; i >= 0; i--) {
        const s = new Date(sun); s.setDate(s.getDate() - i * 7);
        const label = md(s);
        out.push({ start: s.getTime(), end: s.getTime() + 7 * DAY_MS,
                   label, tick: marked(i) ? label : '' });
      }
      return out;
    }
    for (let i = n - 1; i >= 0; i--) {
      const start = today.getTime() - i * DAY_MS;
      const d = new Date(start);
      const endpoint = i === n - 1 || i === 0;
      out.push({ start, end: start + DAY_MS, label: md(d),
                 tick: marked(i)
                   ? (endpoint ? DAYS[d.getDay()] + ' ' + md(d) : md(d)) : '' });
    }
    return out;
  }
  const periodWord = () => {
    const n = clampShow(state.show);
    const unit = state.range === 'month' ? 'month'
      : state.range === 'week' ? 'week' : 'day';
    if (n === 1) return unit === 'day' ? 'today' : 'this ' + unit;
    return 'last ' + n + ' ' + unit + 's';
  };
  const DOOR_WORDS = { call: 'calls', chat: 'texts', voicemail: 'voicemails' };
  const filterWord = () => {
    // "doors" until the door set narrows; a narrowed rating set is an
    // appended clause either way.
    const doors = state.doors.length === DOORS.length ? 'doors'
      : state.doors.length ? state.doors.map((d) => DOOR_WORDS[d]).join(' + ')
      : 'nothing';
    const rated = state.rates.length === RATES.length ? ''
      : !state.rates.length ? ' (no ratings ticked)'
      : ' rated ' + state.rates.map((r) =>
          r === 'up' ? '▲' : r === 'down' ? '▼' : 'none').join('/');
    return doors + rated;
  };

  // ------------------------------------------------------------- renderers
  // An empty frame says WHY it is empty (operator's ask, 0.10.78): the bare
  // em-dash read the same for "no data source" and "no traffic yet", and
  // neither told a fresh install that the blank was normal.
  const empty = (bodyId, capId, msg) => {
    $(bodyId).innerHTML = '<p class="actempty">' + (msg || '—') + '</p>';
    $(capId).textContent = '—';
  };
  const NO_RECORDS = 'no records to read — they appear once the first caller '
    + 'comes through';

  function renderDoors(bs) {
    if (!calls) return empty('doorsChart', 'doorsCap', NO_RECORDS);
    const rows = calls.filter(matches);
    const per = bs.map((b) => {
      const inB = rows.filter((c) => when(c) >= b.start && when(c) < b.end);
      return { n: inB.length, bad: inB.some(failedC), tick: b.tick,
               label: b.label };
    });
    // Zero traffic in the window is an answer, not a fault: a sentence in
    // the frame instead of a row of invisible zero-height bars.
    if (!per.some((p) => p.n)) {
      $('doorsChart').innerHTML = '<p class="actempty">nothing '
        + periodWord() + ' — the first call draws the first bar</p>';
      $('doorsCap').textContent = '0 ' + filterWord() + ' ' + periodWord();
      return;
    }
    const max = Math.max(1, ...per.map((p) => p.n));
    // Every bar says its own number. Reading the total off the caption and
    // the shape off the bars left the actual values nowhere.
    const dense = per.length > 14;
    $('doorsChart').innerHTML =
      '<div class="actbars' + (dense ? ' dense' : '') + '">' + per.map((p) =>
        '<span class="actbar' + (p.bad ? ' bad' : '') + '" style="height:'
        + (p.n ? Math.max(6, Math.round((p.n / max) * 100)) : 0) + '%"'
        + (p.n ? ' data-v="' + p.n + '"' : '')
        + ' title="' + esc(p.label) + ': ' + p.n + ' '
        + (p.n === 1 ? 'record' : 'records')
        + (p.bad ? ' — one or more failed' : '') + '"></span>'
      ).join('') + '</div>'
      + '<div class="actaxis">' + per.map((p) =>
        '<span>' + p.tick + '</span>').join('') + '</div>';
    const total = rows.filter((c) => when(c) >= bs[0].start).length;
    const bad = rows.filter((c) => when(c) >= bs[0].start && failedC(c)).length;
    $('doorsCap').textContent = total + ' ' + filterWord() + ' '
      + periodWord() + (bad ? ' · ' + bad + ' failed' : '');
  }

  function renderMix(bs) {
    if (!calls) return empty('mixChart', 'mixCap', NO_RECORDS);
    const inP = calls.filter((c) => when(c) >= bs[0].start).filter(matches);
    const segs = [['call', 'calls', 'CALLS'], ['chat', 'texts', 'TEXTS'],
                  ['voicemail', 'vm', 'VOICEMAIL']];
    const counts = segs.map(([v, cls, label]) => ({
      v, cls, label,
      n: inP.filter((c) => kindOf(c) === v).length,
    }));
    const total = counts.reduce((a, s) => a + s.n, 0);
    if (!total) {
      // Zero traffic is an ANSWER, not a missing series: say it the way
      // DOORS does, instead of the em-dash that means "no data source".
      $('mixChart').innerHTML = '<p class="actempty">no doors '
        + periodWord() + ' — the mix appears with the first caller</p>';
      $('mixCap').textContent = '0 doors ' + periodWord();
      return;
    }
    $('mixChart').innerHTML =
      '<div class="mixband">' + counts.filter((s) => s.n).map((s) =>
        '<span class="mixseg ' + s.cls + '" style="flex:' + s.n + '"></span>'
      ).join('') + '</div>'
      + '<div class="mixlegend">' + counts.map((s) =>
        // Name on the left rail, numbers on the right — see .mixlegend.
        '<button type="button" class="mixkey" data-v="' + s.v + '">'
        + '<span class="kname"><span class="sw ' + s.cls + '"></span>'
        + s.label + '</span>'
        + '<span class="knum">' + s.n + ' · '
        + Math.round((s.n / total) * 100) + '%</span></button>'
      ).join('') + '</div>'
      + '<p class="mixfoot">' + total + ' doors total · ' + periodWord() + '</p>';
    // A legend swatch is the doors multi-select's shortcut: click solos that
    // door, click it again to bring every door back (spec §7's isolate).
    $('mixChart').querySelectorAll('.mixkey').forEach((k) => {
      k.onclick = () => {
        const solo = state.doors.length === 1 && state.doors[0] === k.dataset.v;
        state.doors = solo ? DOORS.slice() : [k.dataset.v];
        save(); paintControls(); render();
      };
    });
    $('mixCap').textContent = 'by type';
  }

  // What the LINE produced, by tool, over the window.
  //
  // This card was Concurrent listeners until 0.10.117 — a STATION metric on a
  // Talk Wave dashboard, and one the station's own admin already shows you.
  // It told you how many people were listening, which is true and interesting
  // and says nothing whatever about whether the phone line is doing its job.
  // "The line put fourteen songs on air this week" is the sentence that
  // justifies this software existing, and every number in it was already in
  // the call records, unread.
  //
  // The listener series is still sampled and still served — nothing was
  // ripped out — it simply isn't the headline any more.
  function renderTools(bs) {
    if (!calls) return empty('lisChart', 'lisCap', NO_RECORDS);
    const rows = calls.filter(matches).filter((c) => when(c) >= bs[0].start);
    const counts = new Map();
    let failed = 0;
    rows.forEach((c) => (c.tools || []).forEach((t) => {
      const badge = toolWord(t.name);
      if (!badge) return;
      counts.set(badge, (counts.get(badge) || 0) + 1);
      if (t.failed) failed++;
    }));
    const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    if (!ranked.length) {
      $('lisChart').innerHTML = '<p class="actempty">the DJ did nothing '
        + periodWord() + ' — no requests, no shoutouts, nothing on air. '
        + 'That is either a quiet line or a broken one</p>';
      $('lisCap').textContent = '0 actions ' + periodWord();
      return;
    }
    const top = ranked.slice(0, 6);
    const max = top[0][1];
    $('lisChart').innerHTML = '<div class="toolrows">' + top.map(([word, n]) =>
      '<div class="toolrow">'
      + '<span class="tw">' + esc(word) + '</span>'
      + '<span class="tbar"><i style="width:'
      + Math.max(3, Math.round((n / max) * 100)) + '%"></i></span>'
      + '<span class="tn">' + n + '</span>'
      + '</div>').join('') + '</div>';
    const total = ranked.reduce((a, r) => a + r[1], 0);
    $('lisCap').textContent = total + ' action' + (total === 1 ? '' : 's')
      + ' across ' + rows.length + ' conversation'
      + (rows.length === 1 ? '' : 's')
      + (failed ? ' · ' + failed + ' refused' : '');
  }

  // The same eight words the call viewer's chips use, so the chart and the
  // transcripts below it name the same things. Reads are dropped: "checked
  // what was playing" is not something the line PRODUCED, and leaving them in
  // buried the four actions under sixty lookups.
  const TOOL_WORDS = [
    [/request_song|queue_track/, 'queued'],
    [/cancel_queued/, 'cancelled'],
    [/search_by_sound|more_like_this|browse_library|search_library|recent_tracks/, null],
    [/like_track/, 'liked'],
    [/unlike_track/, 'un-liked'],
    [/dj_announce/, 'on air'],
    [/run_skill|dj_segment/, 'segments'],
    [/skip_track/, 'skipped'],
    [/takeover/, 'show change'],
  ];

  function toolWord(name) {
    const n = String(name || '');
    const hit = TOOL_WORDS.find(([re]) => re.test(n));
    if (hit) return hit[1];
    // An unknown WRITE is worth showing; the reads are all matched above.
    return /health|now_playing|station_state|schedule|session|lyrics|status/
      .test(n) ? null : n.replace(/^subwave_/, '');
  }

  function renderTtfw(bs) {
    if (!calls) return empty('ttfwChart', 'ttfwCap', NO_RECORDS);
    // Calls only, by definition — a door set with calls unticked leaves this
    // frame honestly empty rather than charting doors that never speak first.
    if (state.doors.indexOf('call') === -1) {
      return empty('ttfwChart', 'ttfwCap',
        'calls are unticked in the doors filter — only calls have a first word');
    }
    const rows = calls
      .filter((c) => kindOf(c) === 'call' && when(c) >= bs[0].start)
      .filter((c) => state.rates.indexOf(rateOf(c)) !== -1)
      .map((c) => ({ t: when(c), secs: ttfwOf(c) }))
      .filter((r) => r.secs !== null)
      .sort((a, b) => a.t - b.t);
    if (!rows.length) {
      return empty('ttfwChart', 'ttfwCap', 'no answered calls '
        + periodWord() + ' — each call charts how long its caller waited '
        + 'to hear a voice');
    }
    const sorted = rows.map((r) => r.secs).sort((a, b) => a - b);
    const mid = sorted.length >> 1;
    const median = sorted.length % 2
      ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    const max = Math.max(...sorted);
    $('ttfwChart').innerHTML =
      '<div class="actbars">' + rows.map((r) =>
        // Twice the median is where "one slow call" becomes worth a colour.
        '<span class="actbar' + (r.secs > median * 2 ? ' bad' : '')
        + '" style="height:' + Math.max(6, Math.round((r.secs / max) * 100))
        + '%" title="' + r.secs.toFixed(1) + 's"></span>').join('')
      + '</div>'
      // "oldest → latest" named the direction and nothing else: no when, no
      // how long, on a chart whose whole subject is seconds. The ends now
      // carry the real dates and the caption carries the range.
      + '<div class="actaxis"><span>' + esc(stamp(rows[0].t)) + '</span>'
      + '<span>' + esc(stamp(rows[rows.length - 1].t)) + '</span></div>';
    $('ttfwCap').textContent = 'median ' + median.toFixed(1) + 's · fastest '
      + sorted[0].toFixed(1) + 's · slowest ' + max.toFixed(1) + 's · '
      + rows.length + ' call' + (rows.length === 1 ? '' : 's');
  }

  function render() {
    const bs = buckets();
    renderDoors(bs); renderMix(bs); renderTools(bs); renderTtfw(bs);
  }

  // ------------------------------------------------------------- controls
  function paintControls() {
    [['actDay', 'day'], ['actWeek', 'week'], ['actMonth', 'month']]
      .forEach(([id, r]) => $(id).classList.toggle('on', state.range === r));
    // Always live now: the count means the same thing in every unit, so
    // there is nothing to disable it for.
    $('actShow').value = clampShow(state.show);
    $('actShow').disabled = false;
    // The pickers' summaries say what is ticked without opening them.
    $('doorPick').querySelectorAll('input').forEach((box) => {
      box.checked = state.doors.indexOf(box.dataset.v) !== -1;
    });
    $('ratePick').querySelectorAll('input').forEach((box) => {
      box.checked = state.rates.indexOf(box.dataset.v) !== -1;
    });
    $('doorPickSum').textContent =
      state.doors.length === DOORS.length ? 'All doors'
        : !state.doors.length ? 'No doors'
        : state.doors.map((d) => DOOR_WORDS[d]).join(' · ');
    $('ratePickSum').textContent =
      state.rates.length === RATES.length ? 'All ratings'
        : !state.rates.length ? 'No ratings'
        : state.rates.map((r) =>
            r === 'up' ? '▲' : r === 'down' ? '▼' : 'unrated').join(' · ');
  }
  // Switching the unit KEEPS the count — 7 days becomes 7 weeks becomes 7
  // months. It used to reset to a per-unit default, which is what made the
  // box look like it meant days whatever was selected.
  [['actDay', 'day'], ['actWeek', 'week'], ['actMonth', 'month']]
    .forEach(([id, r]) => {
      $(id).onclick = () => {
        state.range = r;
        save(); paintControls(); render();
      };
    });
  $('actShow').onchange = () => {
    // Snap back on nonsense rather than guessing (1–45). No per-unit cap
    // any more: the field said "14" while the chart quietly drew 7, which
    // is exactly the lie this rework removes.
    state.show = clampShow(parseInt($('actShow').value, 10));
    // Write the CLAMPED number back, or the field keeps saying 99 while the
    // chart draws 45 — the same lie in the other direction.
    $('actShow').value = state.show;
    save(); render();
  };
  [['doorPick', 'doors'], ['ratePick', 'rates']].forEach(([id, key]) => {
    $(id).querySelectorAll('input').forEach((box) => {
      box.onchange = () => {
        state[key] = [...$(id).querySelectorAll('input')]
          .filter((b) => b.checked).map((b) => b.dataset.v);
        save(); paintControls(); render();
      };
    });
  });
  // An open picker folds when the pointer commits anywhere else — the
  // checkboxes inside keep it open across several ticks.
  document.addEventListener('click', (e) => {
    document.querySelectorAll('.actpick[open]').forEach((p) => {
      if (!p.contains(e.target)) p.open = false;
    });
  });

  // ------------------------------------------------------------- data
  paintControls();
  render();                          // em-dash frames until the data lands
  Promise.allSettled([
    afetch('/calls').then((r) => r.json()),
    afetch('/stats/listeners').then((r) => r.json()),
  ]).then(([c, l]) => {
    if (c.status === 'fulfilled' && !c.value.error && c.value.calls) {
      calls = c.value.calls;
    }
    if (l.status === 'fulfilled' && !l.value.error && l.value.samples) {
      samples = l.value.samples;
    }
    render();
  });
})();
