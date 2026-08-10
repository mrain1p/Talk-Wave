/* The ACTIVITY strip: four charts between the dashboard and the settings.

   Spec §7 (wavetalk-panel-spec.md). Two honest sources and nothing else: the
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
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(LS) || '{}'); } catch (e) { /* fresh */ }
  const state = Object.assign(
    { range: 'week', show: 7, dim: 'type', filter: '' }, stored);
  const save = () => localStorage.setItem(LS, JSON.stringify(state));
  const clampShow = (n) => Math.max(1, Math.min(30, n | 0 || 7));
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
    // Seconds from pickup to the DJ's first word, from the record's own
    // timestamps. No DJ turn = no bar; a call that never spoke is the DOORS
    // chart's failure, not a zero-second answer here.
    const dj = (c.turns || []).find((t) => t.who === 'dj');
    if (!dj || !c.startedAt) return null;
    const secs = (new Date(dj.t).getTime() - when(c)) / 1000;
    return isFinite(secs) && secs >= 0 && secs < 600 ? secs : null;
  };
  const matches = (c) => (state.dim === 'type'
    ? (!state.filter || kindOf(c) === state.filter)
    : (!state.filter || c.rating === state.filter));

  // ------------------------------------------------------------- buckets
  const DAY_MS = 24 * 3600 * 1000;
  function buckets() {
    const out = [];
    if (state.range === 'day') {
      // 24 hourly buckets ending at the current hour.
      const end = new Date(); end.setMinutes(0, 0, 0);
      const endMs = end.getTime() + 3600 * 1000;
      for (let i = 23; i >= 0; i--) {
        const start = endMs - (i + 1) * 3600 * 1000;
        const h = new Date(start).getHours();
        out.push({ start, end: start + 3600 * 1000,
                   label: (h < 10 ? '0' : '') + h + ':00' });
      }
      // The spec's axis: 00:00 / 12:00 / NOW — three ticks, not 24.
      out.forEach((b, i) => {
        const h = new Date(b.start).getHours();
        b.tick = i === out.length - 1 ? 'NOW'
          : h === 0 ? '00:00' : h === 12 ? '12:00' : '';
      });
    } else {
      const n = state.range === 'month' ? clampShow(state.show) : Math.min(7, clampShow(state.show));
      const today = new Date(); today.setHours(0, 0, 0, 0);
      const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
      // One tick per bucket, thinned to every Nth when more than 10 —
      // weekday prefixes on the endpoints only.
      const every = n > 20 ? 4 : n > 10 ? 2 : 1;
      for (let i = n - 1; i >= 0; i--) {
        const start = today.getTime() - i * DAY_MS;
        const d = new Date(start);
        const md = (d.getMonth() + 1) + '/' + d.getDate();
        const endpoint = i === n - 1 || i === 0;
        out.push({ start, end: start + DAY_MS, label: md,
                   tick: (endpoint || (n - 1 - i) % every === 0)
                     ? (endpoint ? DAYS[d.getDay()] + ' ' + md : md) : '' });
      }
    }
    return out;
  }
  const periodWord = () => (state.range === 'day' ? 'today'
    : state.range === 'week' ? 'this week'
    : clampShow(state.show) === 30 ? 'this month'
    : 'last ' + clampShow(state.show) + ' days');
  const filterWord = () => {
    if (!state.filter) return null;
    if (state.dim === 'type') {
      return { call: 'calls', chat: 'texts', voicemail: 'voicemails' }[state.filter];
    }
    return state.filter === 'up' ? 'rated ▲' : 'rated ▼';
  };

  // ------------------------------------------------------------- renderers
  const empty = (bodyId, capId) => {
    $(bodyId).innerHTML = '<p class="actempty">—</p>';
    $(capId).textContent = '—';
  };

  function renderDoors(bs) {
    if (!calls) return empty('doorsChart', 'doorsCap');
    const rows = calls.filter(matches);
    const per = bs.map((b) => {
      const inB = rows.filter((c) => when(c) >= b.start && when(c) < b.end);
      return { n: inB.length, bad: inB.some(failedC), tick: b.tick };
    });
    const max = Math.max(1, ...per.map((p) => p.n));
    $('doorsChart').innerHTML =
      '<div class="actbars">' + per.map((p) =>
        '<span class="actbar' + (p.bad ? ' bad' : '') + '" style="height:'
        + (p.n ? Math.max(6, Math.round((p.n / max) * 100)) : 0) + '%"></span>'
      ).join('') + '</div>'
      + '<div class="actaxis">' + per.map((p) =>
        '<span>' + p.tick + '</span>').join('') + '</div>';
    const total = rows.filter((c) => when(c) >= bs[0].start).length;
    const bad = rows.filter((c) => when(c) >= bs[0].start && failedC(c)).length;
    $('doorsCap').textContent = total + ' ' + (filterWord() || 'doors') + ' '
      + periodWord() + (bad ? ' · ' + bad + ' failed' : '');
  }

  function renderMix(bs) {
    if (!calls) return empty('mixChart', 'mixCap');
    const inP = calls.filter((c) => when(c) >= bs[0].start).filter(matches);
    const segs = state.dim === 'type'
      ? [['call', 'calls', 'CALLS'], ['chat', 'texts', 'TEXTS'],
         ['voicemail', 'vm', 'VOICEMAIL']]
      : [['up', 'calls', '▲ UP'], ['down', 'vm', '▼ DOWN']];
    const of = (v) => inP.filter((c) => (state.dim === 'type'
      ? kindOf(c) === v : c.rating === v)).length;
    const counts = segs.map(([v, cls, label]) => ({ v, cls, label, n: of(v) }));
    const total = counts.reduce((a, s) => a + s.n, 0);
    if (!total) return empty('mixChart', 'mixCap');
    $('mixChart').innerHTML =
      '<div class="mixband">' + counts.filter((s) => s.n).map((s) =>
        '<span class="mixseg ' + s.cls + '" style="flex:' + s.n + '"></span>'
      ).join('') + '</div>'
      + '<div class="mixlegend">' + counts.map((s) =>
        '<button type="button" class="mixkey" data-v="' + s.v + '">'
        + '<span class="sw ' + s.cls + '"></span>' + s.label + ' ' + s.n
        + ' · ' + Math.round((s.n / total) * 100) + '%</button>'
      ).join('') + '</div>'
      + '<p class="mixfoot">' + total + ' doors total · ' + periodWord() + '</p>';
    // A legend swatch is the filter's shortcut: click isolates, click again
    // returns to all (spec §7).
    $('mixChart').querySelectorAll('.mixkey').forEach((k) => {
      k.onclick = () => {
        state.filter = state.filter === k.dataset.v ? '' : k.dataset.v;
        $('actFilter').value = state.filter;
        save(); render();
      };
    });
    $('mixCap').textContent = 'by ' + (state.dim === 'type' ? 'type' : 'rating');
  }

  function renderListeners(bs) {
    if (!samples || !samples.length) return empty('lisChart', 'lisCap');
    const per = bs.map((b) => {
      const inB = samples.filter((s) =>
        s.t * 1000 >= b.start && s.t * 1000 < b.end);
      return inB.length ? Math.max(...inB.map((s) => s.n)) : null;
    });
    if (per.every((p) => p === null)) return empty('lisChart', 'lisCap');
    const peak = Math.max(1, ...per.filter((p) => p !== null));
    const W = 100, H = 40;
    const x = (i) => (per.length === 1 ? W / 2 : (i / (per.length - 1)) * W);
    const y = (n) => H - 2 - (n / peak) * (H - 6);
    // Gaps stay gaps: an hour the sampler couldn't reach the station breaks
    // the line rather than being drawn as zero listeners.
    const runs = [];
    let run = [];
    per.forEach((p, i) => {
      if (p === null) { if (run.length) runs.push(run); run = []; }
      else run.push([x(i), y(p)]);
    });
    if (run.length) runs.push(run);
    const lines = runs.map((r) =>
      '<polyline points="' + r.map((p) => p[0].toFixed(1) + ','
        + p[1].toFixed(1)).join(' ') + '" />').join('');
    const areas = runs.filter((r) => r.length > 1).map((r) =>
      '<polygon points="' + r.map((p) => p[0].toFixed(1) + ','
        + p[1].toFixed(1)).join(' ')
      + ' ' + r[r.length - 1][0].toFixed(1) + ',' + H
      + ' ' + r[0][0].toFixed(1) + ',' + H + '" />').join('');
    $('lisChart').innerHTML =
      '<svg class="lissvg" viewBox="0 0 ' + W + ' ' + H
      + '" preserveAspectRatio="none">' + areas + lines + '</svg>'
      + '<div class="actaxis">' + bs.map((b) =>
        '<span>' + (b.tick || '') + '</span>').join('') + '</div>';
    $('lisCap').textContent = 'peak ' + peak;
  }

  function renderTtfw(bs) {
    if (!calls) return empty('ttfwChart', 'ttfwCap');
    // Calls only, by definition — and only when the filter doesn't exclude
    // calls (a voicemail/text filter leaves this frame honestly empty).
    if (state.dim === 'type' && state.filter && state.filter !== 'call') {
      return empty('ttfwChart', 'ttfwCap');
    }
    const rows = calls
      .filter((c) => kindOf(c) === 'call' && when(c) >= bs[0].start)
      .filter((c) => state.dim === 'rating' ? matches(c) : true)
      .map((c) => ({ t: when(c), secs: ttfwOf(c) }))
      .filter((r) => r.secs !== null)
      .sort((a, b) => a.t - b.t);
    if (!rows.length) return empty('ttfwChart', 'ttfwCap');
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
      + '<div class="actaxis"><span>oldest</span><span>latest</span></div>';
    $('ttfwCap').textContent = 'median ' + median.toFixed(1) + 's';
  }

  function render() {
    const bs = buckets();
    renderDoors(bs); renderMix(bs); renderListeners(bs); renderTtfw(bs);
  }

  // ------------------------------------------------------------- controls
  function paintControls() {
    [['actDay', 'day'], ['actWeek', 'week'], ['actMonth', 'month']]
      .forEach(([id, r]) => $(id).classList.toggle('on', state.range === r));
    $('actShow').value = state.range === 'day' ? '' : clampShow(state.show);
    $('actShow').disabled = state.range === 'day';
    const dim = $('actDim');
    dim.innerHTML = '<option value="type">By type</option>'
      + '<option value="rating">By rating</option>';
    dim.value = state.dim;
    const fil = $('actFilter');
    fil.innerHTML = state.dim === 'type'
      ? '<option value="">All doors</option><option value="call">Calls</option>'
        + '<option value="chat">Texts</option>'
        + '<option value="voicemail">Voicemail</option>'
      : '<option value="">All</option><option value="up">▲ Up</option>'
        + '<option value="down">▼ Down</option>';
    fil.value = state.filter;
  }
  [['actDay', 'day', 7], ['actWeek', 'week', 7], ['actMonth', 'month', 30]]
    .forEach(([id, r, n]) => {
      $(id).onclick = () => {
        state.range = r; state.show = n;
        save(); paintControls(); render();
      };
    });
  $('actShow').onchange = () => {
    // Snap back on nonsense rather than guessing (spec: clamp 1–30).
    state.show = clampShow(parseInt($('actShow').value, 10));
    $('actShow').value = state.show;
    save(); render();
  };
  $('actDim').onchange = () => {
    state.dim = $('actDim').value;
    state.filter = '';               // the old filter's vocabulary is gone
    save(); paintControls(); render();
  };
  $('actFilter').onchange = () => {
    state.filter = $('actFilter').value;
    save(); render();
  };

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
