/* The log and call viewers: reading back what already happened.

   Split out of panel.js because it is the one part of the operator surface
   with a real boundary — it needs two names from the rest of the panel, where
   the test probes need fifteen and the pipeline check ten. Those stay in
   panel.js: threading that many names across a seam would be the same module
   in more files rather than a smaller one.

   Loaded after panel.js, which publishes what this needs on window.Panel. */
(function () {
  const { $ } = window.Callin;
  const { afetch, showResult } = window.Panel;

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

  // The same icons the caller sees when an action lands on the card
  // (call/actions.py LABELS announces them over talkwave.action) — one
  // vocabulary on both surfaces. The short word is what the row chips show:
  // full tool names clipped mid-word ("voicemail_del…") in the summary
  // column, and a chip that can't say its word is worse than a shorter word.
  const TOOL_BADGES = [
    [/voicemail/i, '✉', 'voicemail'],
    [/request|queue/i, '🎵', 'request'],
    [/announce/i, '📢', 'announce'],
    [/skill/i, '🎙', 'skill'],
    [/skip/i, '⏭', 'skip'],
    [/segment/i, '📻', 'segment'],
    [/takeover|show/i, '🔀', 'takeover'],
    [/search|library|music|track/i, '🔎', 'search'],
  ];
  function toolBadge(name) {
    const hit = TOOL_BADGES.find(([re]) => re.test(String(name || '')));
    return hit ? { icon: hit[1], word: hit[2] }
               : { icon: '✅', word: String(name || '').replace(/^subwave_/, '') };
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

    // The ducking timeline. Reading this used to mean watching the worker's
    // air file at 200ms for five minutes and correlating by hand — three
    // separate diagnoses did exactly that. "Audible in" is the number the
    // whole thing turns on: a voice.* timestamp is stamped where the station
    // mixes, and the caller is the stream buffer behind it, so a hold that
    // opens while that is still counting down is a duck that started early.
    if ((c.air || []).length) {
      section('On air, and the hold');
      const ul = document.createElement('ul');
      ul.className = 'cbproblems';
      c.air.forEach((a) => {
        const li = document.createElement('li');
        const bits = [a.why, a.forSecs && 'for ' + a.forSecs + 's',
          a.heldSecs && 'held ' + a.heldSecs + 's',
          a.durSecs && a.durSecs + 's of speech',
          typeof a.audibleIn === 'number' && (a.audibleIn > 0
            ? 'audible to the caller in ' + a.audibleIn + 's'
            : 'audible ' + Math.abs(a.audibleIn) + 's ago'),
          a.bufSecs && 'caller ' + a.bufSecs + 's behind',
          a.ignored && 'IGNORED: ' + a.ignored].filter(Boolean);
        li.textContent = callTime(a.t) + '  ' + a.what
          + (bits.length ? ' — ' + bits.join(', ') : '');
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
      // 'Tool', capitalised like its neighbours — 'tool' between 'DJ' and
      // 'Caller' read as a rendering slip, not a row kind.
      line.querySelector('.w').textContent =
        e.kind === 'tool' ? 'Tool' : (who[e.kind] || e.kind);
      line.querySelector('.x').textContent = e.kind === 'tool'
        ? toolBadge(e.name).icon + ' ' + e.name
          + (e.result ? ' → ' + e.result : '')
        : e.text;
      talk.appendChild(line);
    });
    box.appendChild(talk);
    return box;
  }

  // One <details> per call, closed. Forty records as forty scrolling walls of
  // transcript was unreadable; the header answers "which call was that, and
  // did it go wrong" without opening anything.
  // One column per fact rather than one joined sentence. Every row used to be
  // "Wade · 30s · 0 turns · no caller audio" in a single cell, which meant the
  // turn count sat at a different x on every line and the only way to find the
  // short calls was to read all forty. Separate cells put each fact in a
  // column you can run your eye down — which is the entire job of this list.
  function renderCallRow(c) {
    const v = callVerdict(c);
    const turns = c.callerTurns || 0;
    const tools = (c.tools || []).length;

    const el = document.createElement('details');
    el.className = 'callrow ' + v.cls;
    el.dataset.verdict = v.cls;
    if (c.rating === 'up' || c.rating === 'down') {
      el.dataset.rating = c.rating;
    }
    // What the toolbar's dropdowns filter on. Tools are stored as the badge
    // WORDS, not the raw names — the dropdown offers what the chips say, and
    // 8 stable words filter better than every raw tool spelling.
    el.dataset.kind = c.kind === 'voicemail' ? 'voicemail'
      : c.kind === 'chat' ? 'chat' : 'call';
    el.dataset.tier = String((c.config && c.config.callerTier) || '').toLowerCase();
    el.dataset.tools = [...new Set((c.tools || []).map((t) =>
      toolBadge(t.name).word))].join(' ');
    const sum = document.createElement('summary');
    sum.innerHTML = '<span class="icon"></span><span class="when"></span>'
      + '<span class="dj"></span><span class="len"></span>'
      + '<span class="did"></span><span class="dt"></span>';
    sum.querySelector('.icon').textContent = v.icon;
    // No year: "Aug 5, 2026, 2:29:24 AM" wrapped onto a second line and broke
    // the row. The year is never the thing you are looking for in a list that
    // holds the last forty calls.
    sum.querySelector('.when').textContent = callTime(c.startedAt, 'short');
    sum.querySelector('.dj').textContent =
      (c.kind === 'voicemail' ? '✉ Voicemail · '
        : c.kind === 'chat' ? '💬 Text chat · ' : '')
      + (c.persona?.name || 'DJ');
    sum.querySelector('.len').textContent = `${Math.round(c.durationSecs || 0)}s`;
    sum.querySelector('.did').textContent =
      `${turns} turn${turns === 1 ? '' : 's'}`;
    // Who placed it and what it touched, at a glance: the caller's tier as
    // a chip, and the distinct tools by name (the count alone answered a
    // question nobody was asking).
    const tier = c.config && c.config.callerTier;
    if (tier) {
      const chip = document.createElement('span');
      chip.className = 'ctier';
      chip.textContent = tier;
      sum.querySelector('.dj').appendChild(chip);
    }
    if (tools) {
      const names = [...new Set((c.tools || []).map((t) =>
        String(t.name || '').replace(/^subwave_/, '')))].filter(Boolean);
      const wrap = document.createElement('span');
      wrap.className = 'ctools';
      names.slice(0, 3).forEach((n) => {
        const t = document.createElement('span');
        t.className = 'ctool';
        const badge = toolBadge(n);
        t.textContent = badge.icon + ' ' + badge.word;
        t.title = n;                 // the full name survives as the tooltip
        wrap.appendChild(t);
      });
      if (names.length > 3) {
        const more = document.createElement('span');
        more.className = 'ctool';
        more.textContent = '+' + (names.length - 3);
        wrap.appendChild(more);
      }
      sum.querySelector('.did').appendChild(wrap);
    }
    sum.querySelector('.dt').textContent =
      // Neutral marks in the row's own ink \u2014 the emoji thumbs were the one
      // thing on the page the theme could not colour. \u25b2\u25bc match the sort
      // arrows' vocabulary: direction, no cartoon.
      (c.rating === 'down' ? '\u25bc ' : c.rating === 'up' ? '\u25b2 ' : '')
      + v.note;
    // Delete THIS one. Clear-all was the only way to remove a transcript,
    // which after a run of test calls meant throwing away the evidence you
    // were about to read. Lives on the summary so it is reachable without
    // opening the record, and stops the click from toggling the <details>.
    if (c.id) {
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'cdel';
      del.textContent = '×';
      del.title = 'Delete this record';
      del.setAttribute('aria-label', 'Delete this call record');
      del.onclick = async (ev) => {
        ev.preventDefault(); ev.stopPropagation();
        if (del.dataset.armed !== '1') {
          // Two presses, no modal: a transcript is a caller's words and one
          // stray click should not take them, but a confirm() over a list
          // you are working through is its own annoyance.
          del.dataset.armed = '1';
          del.textContent = 'Delete?';
          del.classList.add('armed');
          setTimeout(() => {
            if (!del.isConnected || del.dataset.armed !== '1') return;
            del.dataset.armed = ''; del.textContent = '×';
            del.classList.remove('armed');
          }, 4000);
          return;
        }
        del.disabled = true;
        try {
          const r = await afetch('/calls/' + encodeURIComponent(c.id),
                                 { method: 'DELETE' });
          if (!r.ok) throw new Error('refused');
          el.remove();
        } catch (e) {
          del.disabled = false; del.textContent = 'failed';
        }
      };
      sum.appendChild(del);
    }
    el.appendChild(sum);
    el.appendChild(callBody(c));
    return el;
  }

  $('viewCallsBtn').onclick = async () => {
    const btn = $('viewCallsBtn'), out = $('callsResult');
    btn.disabled = true;
    // 'scrolly' must survive every one of these assignments. The markup
    // carries it, but className is a full replacement, and a rewrite here
    // once dropped it — the box kept its max-height and lost its overflow,
    // so the content poured straight through the border and over the page
    // footer (operator screenshot, 0.10.46). Same trap in the log viewer.
    out.className = 'result scrolly on'; out.textContent = 'Fetching…';
    try {
      const d = await afetch('/calls').then((r) => r.json());
      if (d.error) { showResult(out, false, d.error); return; }
      const calls = d.calls || [];
      out.className = 'result scrolly on';
      out.innerHTML = '';
      if (!calls.length) {
        $('callBar').hidden = true;
        out.textContent = 'Nothing recorded yet. One file is written as each '
          + 'call, text chat or voicemail ends.';
        return;
      }
      // /calls already returns newest first — the call you want is almost
      // always the last one — so this renders in the order given.
      const list = document.createElement('div');
      list.className = 'calllist';
      calls.forEach((c) => list.appendChild(renderCallRow(c)));
      out.appendChild(list);

      // The toolbar is markup rather than built here, so it shares one shape
      // with the log viewer's. Every control narrows the SAME list through
      // one filter pass — the old one-CSS-class-per-filter scheme could not
      // say "thumbs down AND text chats only", and each dropdown it gained
      // would have needed a class per possible value.
      const filters = { bad: false, rating: '', kind: '', tier: '', tool: '' };
      const rows = [...list.children];
      const apply = () => {
        let shown = 0;
        rows.forEach((row) => {
          const ok = (!filters.bad || row.dataset.verdict !== 'pass')
            && (!filters.rating || row.dataset.rating === filters.rating)
            && (!filters.kind || row.dataset.kind === filters.kind)
            && (!filters.tier || row.dataset.tier === filters.tier)
            && (!filters.tool || (' ' + (row.dataset.tools || '') + ' ')
                  .indexOf(' ' + filters.tool + ' ') !== -1);
          row.hidden = !ok;
          if (ok) shown += 1;
        });
        $('callCount').textContent = shown === calls.length
          ? `${calls.length} call${calls.length === 1 ? '' : 's'}`
          : `${shown} of ${calls.length}`;
      };

      const rough = calls.filter((c) => callVerdict(c).cls !== 'pass').length;
      const box = $('callsOnlyBad');
      $('callsOnlyBadLabel').textContent = rough || '';
      box.disabled = !rough;
      box.classList.remove('on');
      box.onclick = () => {
        filters.bad = !filters.bad;
        box.classList.toggle('on', filters.bad);
        apply();
      };
      // The caller's own verdicts, as filters. One at a time — a call can't
      // be rated both ways — and they stack with everything else on the bar.
      const down = calls.filter((c) => c.rating === 'down').length;
      const up = calls.filter((c) => c.rating === 'up').length;
      [['callsOnlyDown', 'down', down], ['callsOnlyUp', 'up', up]]
        .forEach(([id, val, n]) => {
          const btn = $(id);
          if (!btn) return;
          btn.querySelector('span').textContent = n || '';
          btn.disabled = !n;
          btn.classList.remove('on');
          btn.onclick = () => {
            filters.rating = filters.rating === val ? '' : val;
            $('callsOnlyDown').classList.toggle('on', filters.rating === 'down');
            $('callsOnlyUp').classList.toggle('on', filters.rating === 'up');
            apply();
          };
        });

      // The three dropdowns: how they came in, who they were, what the DJ
      // reached for. Each hides entirely when the loaded calls give it only
      // one answer — a dropdown that can't change the list is noise on a bar
      // this small. Options are drawn from the records, so a door that was
      // never used isn't offered as a filter that finds nothing.
      const KIND_WORDS = { call: 'Calls', chat: 'Text chats', voicemail: 'Voicemails' };
      const fillSelect = (id, key, allLabel, values, wordFor) => {
        const sel = $(id);
        if (!sel) return;
        sel.innerHTML = '';
        const all = document.createElement('option');
        all.value = ''; all.textContent = allLabel;
        sel.appendChild(all);
        values.forEach((v) => {
          const o = document.createElement('option');
          o.value = v; o.textContent = wordFor ? wordFor(v) : v;
          sel.appendChild(o);
        });
        sel.hidden = values.length < (key === 'tool' ? 1 : 2);
        sel.onchange = () => { filters[key] = sel.value; apply(); };
      };
      const distinct = (fn) => [...new Set(calls.map(fn).filter(Boolean))].sort();
      fillSelect('callKind', 'kind', 'All types',
        ['call', 'chat', 'voicemail'].filter((k) =>
          calls.some((c) => (c.kind === 'voicemail' ? 'voicemail'
            : c.kind === 'chat' ? 'chat' : 'call') === k)),
        (v) => KIND_WORDS[v]);
      fillSelect('callTier', 'tier', 'All tiers',
        distinct((c) => String((c.config && c.config.callerTier) || '').toLowerCase()));
      fillSelect('callTool', 'tool', 'All tools',
        [...new Set(calls.flatMap((c) => (c.tools || []).map((t) =>
          toolBadge(t.name).word)))].sort());

      apply();
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
      out.className = 'result scrolly on';
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
  // The log buffer runs to hundreds of lines; dropping all of them into the
  // settings page at once made it a wall to scroll past (operator-reported).
  // Show the most recent LOG_PAGE and reveal older ones a page at a time.
  const LOG_PAGE = 20;
  let logShown = LOG_PAGE;

  // Levels in severity order, so the filter reads as a scale rather than as
  // whatever order the server happened to see them in.
  const LEVEL_ORDER = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

  function paintLogs() {
    const out = $('logsResult');
    // One choice, read as a floor: "Warnings and up" keeps errors visible,
    // which is what anyone picking a level actually wants. '' is All — the
    // default, because the old multi-select opened on an ambiguous
    // nothing-selected state the operator rightly called ugly.
    const floor = $('logLevels').value;
    const floorIdx = LEVEL_ORDER.indexOf(floor);
    const needle = ($('logSearch').value || '').toLowerCase();
    const rows = logRecords.filter((r) =>
      (floorIdx < 0 || LEVEL_ORDER.indexOf(r.level) >= floorIdx)
      && (!needle || (r.msg + ' ' + r.logger).toLowerCase().indexOf(needle) !== -1));

    out.innerHTML = '';
    if (!rows.length) {
      const p = document.createElement('p');
      p.className = 'capempty';
      p.textContent = logRecords.length
        ? 'Nothing matches that filter.' : 'No log lines yet.';
      out.appendChild(p);
    } else {
      // The newest are what an operator looks at first, so show the TAIL and
      // let them page back through older lines rather than scroll a wall.
      const shown = rows.slice(-logShown);
      if (rows.length > shown.length) {
        const more = document.createElement('button');
        more.type = 'button';
        more.className = 'logmore';
        more.textContent = 'Show older — ' + (rows.length - shown.length) + ' more';
        more.onclick = () => { logShown += LOG_PAGE; paintLogs(); };
        out.appendChild(more);
      }
      shown.forEach((r) => {
        const line = document.createElement('div');
        line.className = 'logline lvl-' + String(r.level || 'INFO').toLowerCase();
        line.innerHTML = '<span class="lt"></span><span class="ll"></span>'
          + '<span class="lg"></span><span class="lm"></span>';
        line.querySelector('.lt').textContent = r.t || '';
        line.querySelector('.ll').textContent = (r.level || '')[0] || '·';
        line.querySelector('.ll').title = r.level || '';
        // The callin. prefix is on every line of ours and earns no width.
        const logger = String(r.logger || '').replace(/^callin\./, '');
        line.querySelector('.lg').textContent = logger;
        // The logger column is truncated, and hidden outright on a narrow
        // panel, so the full name has to survive somewhere readable.
        line.title = [r.level, logger].filter(Boolean).join(' · ');
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
    // 'scrolly' kept for the same reason as the call viewer's assignments:
    // without it the lines overflow the border instead of scrolling.
    out.className = 'result scrolly on logs'; out.textContent = 'Fetching…';
    try {
      const d = await afetch('/logs').then((r) => r.json());
      if (d.error) { showResult(out, false, d.error); return; }
      // Fall back to the flat lines if this is an older server, so the viewer
      // degrades to what it used to be rather than to nothing.
      logRecords = d.records || (d.lines || []).map((l) => ({
        t: '', level: 'INFO', logger: '', msg: l,
      }));
      const present = d.levels || [];
      const keep = $('logLevels').value;
      $('logLevels').innerHTML = '';
      const all = document.createElement('option');
      all.value = ''; all.textContent = 'All levels';
      $('logLevels').appendChild(all);
      LEVEL_ORDER.filter((l) => present.indexOf(l) !== -1).forEach((l) => {
        const o = document.createElement('option');
        o.value = l;
        o.textContent = l === 'ERROR' ? 'Errors only'
          : l[0] + l.slice(1).toLowerCase() + ' and up';
        $('logLevels').appendChild(o);
      });
      $('logLevels').value =
        [...$('logLevels').options].some((o) => o.value === keep) ? keep : '';
      $('logFilters').hidden = false;
      out.className = 'result scrolly on logs';
      logShown = LOG_PAGE;
      paintLogs();
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  // A filter change is a new result set, so page back to the most recent.
  const repaintFromTop = () => { logShown = LOG_PAGE; paintLogs(); };
  $('logLevels').onchange = repaintFromTop;
  $('logSearch').oninput = repaintFromTop;
  $('logClearFilters').onclick = () => {
    $('logLevels').value = '';
    $('logSearch').value = '';
    repaintFromTop();
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
})();

