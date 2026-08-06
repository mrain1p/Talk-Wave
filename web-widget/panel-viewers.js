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
})();

