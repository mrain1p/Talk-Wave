/* The settings panel: the operator's surface, served at /panel.

   Loaded only by panel.html. The call page does not load it and an embed
   cannot reach it — it used to ship to every anonymous caller as dead weight
   inside app.js, and the question "is this an embed?" had to be asked in
   javascript because there was no other way to tell the two apart.

   Shared foundation comes from shared.js via the Callin global. */
(function () {
  const {
    $, ASKS, NEVER, CALL_KEY,
    ctx, playSound, pack, setSounds, getVolume,
  } = window.Callin;

  // The panel's own copy of /live. It used to read the call page's, which is
  // the only reason previewSound had to borrow the call's sound config and put
  // it back afterwards.
  let live = null;
  async function refreshLiveData() {
    try { live = await fetch('/live').then((r) => r.json()); }
    catch (e) { live = live || {}; }
    setSounds(live && live.sounds);
    return live;
  }


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
      hdr.id = 'sup-' + sup.id;
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

    buildNav(supers);
  }

  // Jump links across the top. The panel is six super-groups long and the only
  // way to reach the bottom of it was scrolling past everything above — which
  // is also how a setting gets changed on the way past. Built from the schema
  // rather than written in the markup, so a new super-group appears here on
  // its own; Diagnostics is appended because it is the one header the schema
  // does not own.
  function buildNav(supers) {
    const nav = $('panelNav');
    if (!nav) return;
    nav.innerHTML = '';
    const link = (id, title) => {
      const a = document.createElement('a');
      a.href = '#' + id;
      a.textContent = title;
      a.onclick = (e) => {
        e.preventDefault();
        const target = document.getElementById(id);
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      };
      nav.appendChild(a);
    };
    supers.forEach((sup) => {
      if (!SCHEMA.groups.some((g) => g.super === sup.id)) return;
      link('sup-' + sup.id, sup.title);
    });
    link('supDiag', 'Diagnostics');
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
  // Every write goes through tag(): a summary is decoration, and a missing
  // element must not be able to abort paint() half way and leave the panel
  // looking like a failed load. That has happened, from one renamed id.
  function setTag(id, text) {
    const el = $(id);
    if (el) el.textContent = text == null ? '' : String(text);
  }

  function paintTags() {
    setTag('tagStation', (options.personas || []).length + ' personas');
    const setKeys = Object.values(secrets).filter((s) => s.set).length;
    setTag('tagKeys', setKeys ? setKeys + ' set' : 'none set');
    setTag('tagVoice', (resolved.tts_mode || '') +
      (resolved.tts_voice ? ' · ' + resolved.tts_voice : ' · station voice'));
    setTag('tagBrains', (resolved.llm_provider || '') + ' · ' + (resolved.llm_model || ''));
    setTag('tagEars', (resolved.stt_provider || '') + ' · ' + (resolved.stt_model || ''));
    // Permission count comes from the schema group, so it can't go stale when
    // a new permission is added.
    const permFields = Object.keys(SCHEMA.fields)
      .filter((f) => SCHEMA.fields[f].group === 'perms');
    const perms = permFields.filter((f) => resolved[f]).length;
    setTag('tagPerms', perms + ' of ' + permFields.length + ' enabled');
    setTag('tagSounds', resolved.call_sounds
      ? (resolved.sound_pack === 'phone' ? 'handset' : 'exchange') : 'off');
    setTag('tagStyle', [resolved.style_conversation, resolved.style_answering,
      resolved.style_signoff].filter(Boolean).length + ' set');
    setTag('tagHygiene', (resolved.strip_stage_directions ? 'directions stripped' : 'raw')
      + ' · ' + (resolved.profanity_mode === 'off' ? 'no filter' : resolved.profanity_mode));
    setTag('tagUsage', (resolved.max_concurrent_calls || '∞') + ' at once · '
      + (resolved.calls_per_hour || '∞') + '/hr · '
      + (resolved.calls_per_day || '∞') + '/day · '
      + (resolved.max_actions_per_call || '∞') + ' actions');
    setTag('tagCallback', resolved.callback_enabled
      ? 'on · ' + resolved.callback_max_words + ' words' : 'off');
    setTag('tagContext', [resolved.context_recent_tracks + ' played',
      resolved.context_upcoming + ' queued',
      resolved.context_booth_lines + ' on-air'].join(' · '));
    setTag('tagCall', resolved.persona_override ? 'pinned persona' : 'live DJ');
    setTag('tagTurns', resolved.allow_interruptions ? 'interruptible' : 'finishes its sentence');
    setTag('tagLimits', 'ends by ' + resolved.max_call_seconds + 's'
      + (resolved.idle_prompt_secs ? ' · checks in at ' + resolved.idle_prompt_secs + 's' : ''));
    setTag('tagOnair', resolved.avoid_on_air_overlap
      ? 'waits ' + resolved.on_air_quiet_secs + 's for quiet air' : 'talks over the broadcast');
    setTag('tagTunein', resolved.tune_in_on_call
      ? 'on · ' + resolved.tune_in_volume + '%' : 'off — requests may be refused');
    setTag('tagRecord', resolved.record_calls ? 'keeping ' + resolved.record_keep : 'not kept');
    setTag('tagPlayer', (resolved.call_button_uses_name ? "DJ's name" : 'generic label')
      + ' · ' + (resolved.widget_theme || 'auto'));
    paintLineState();
  }

  // The kill switch lives above every section, so its own state has to read
  // from up there too — a paused line with the word "paused" three sections
  // down is how an operator spends ten minutes wondering why nobody can call.
  function paintLineState() {
    const note = $('pausedNote');
    if (!note) return;
    const paused = $('calls_paused') ? $('calls_paused').checked : !!resolved.calls_paused;
    note.textContent = paused
      ? 'The line is closed — the card still shows who is on air.'
      : 'The line is open.';
    note.classList.toggle('paused', paused);
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
      // Three cells, not a paragraph. The "why" used to be nested inside the
      // example, so nineteen rows were nineteen two-line blocks and there was
      // no way to read down either column. Side by side, the panel is wide
      // enough for both and the list is half as tall.
      const li = document.createElement('li');
      li.className = enabled ? '' : 'off';
      li.innerHTML = '<span class="mark"></span><span class="say"></span>'
        + '<span class="why"></span>';
      li.querySelector('.mark').textContent = enabled ? '✓' : '–';
      li.querySelector('.say').textContent = a.say;
      li.querySelector('.why').textContent =
        enabled ? a.why : a.why + ' — switch it on above';
      host.appendChild(li);
    });

    // Always-off actions, listed once at the end so the boundary is visible
    // rather than something you discover by toggling everything on.
    const never = document.createElement('li');
    never.className = 'nevergroup';
    never.innerHTML = '<span class="mark">×</span><span class="say"></span>'
      + '<span class="why"></span>';
    never.querySelector('.say').textContent =
      'Never available to a caller, whatever you set';
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

      // Three columns rather than a status beside a stack of three lines. The
      // name is what you scan for and it was the first line of a paragraph, so
      // nineteen tools came to nineteen ragged blocks. Now the names are a
      // column and the prose is a column.
      const name = document.createElement('code');
      name.textContent = t.name.replace(/^subwave_/, '');
      name.title = t.name;

      const body = document.createElement('span');
      body.className = 'tbody';
      const what = document.createElement('span');
      what.className = 'twhat';
      what.textContent = t.what;
      body.append(what);
      if (t.note) {
        const note = document.createElement('span');
        note.className = 'tnote';
        note.textContent = t.note;
        body.appendChild(note);
      }
      li.append(state, name, body);
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
    jump.textContent = 'Connections';
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
    paintAdminNeeded();
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
    const MODE = { auto: 'automatic', open: 'open to anyone',
                   guest: 'guest code', admin: 'admin only' };
    setTag('tagSecurity', (MODE[resolved.front_access] || resolved.front_access || '')
      + ' · ' + (authConfigured ? 'admin set' : 'ADMIN OPEN'));
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
      + 'change settings and spend your API keys. Set one under Access before '
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
      await refreshLiveData();
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
        if (f === 'calls_paused') paintLineState();
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

  // Some of these switches do nothing at all without the station's own admin
  // username and password — the station refuses the call and our client
  // returns an empty list or a soft failure, which from the panel looks
  // exactly like a feature that is on and simply never happens. So each one
  // says up front that it needs them.
  //
  // The tag is always present, and only its COLOUR reads as a warning: coral
  // while the credentials are missing, grey once they are stored. Both facts
  // are worth having, and a tag that appears and disappears would leave an
  // operator wondering whether they had imagined it.
  function paintAdminNeeded() {
    const have = !!(secrets.subwave_admin_user && secrets.subwave_admin_user.set
                    && secrets.subwave_admin_pass && secrets.subwave_admin_pass.set);
    Object.keys(SCHEMA.fields).forEach((f) => {
      const el = $(f);
      if (!el || !SCHEMA.fields[f].admin) return;
      const anchor = el.closest('.row') || el.closest('.check');
      if (!anchor) return;
      let tag = anchor.querySelector('.needsadmin');
      if (!tag) {
        tag = document.createElement('span');
        tag.className = 'needsadmin';
        tag.textContent = 'Station admin';
        anchor.appendChild(tag);
      }
      tag.classList.toggle('missing', !have);
      tag.title = have
        ? 'Uses the station admin credentials stored under Station.'
        : 'Needs the station admin username and password under Station. '
          + 'Without them this stays switched on and quietly never happens.';
    });
  }

  // Inject the schema's help text under each field, so the explanation lives
  // beside the definition rather than being duplicated in the markup.
  function decorateFields() {
    Object.keys(SCHEMA.fields).forEach((f) => {
      const el = $(f);
      if (!el) return;
      const meta = SCHEMA.fields[f];
      // A .prow is one row of the Player settings matrix and holds TWO
      // fields, so it names the one whose help it wants — otherwise both
      // would insert a hint and the second would sit under the first,
      // describing a checkbox two lines above it.
      const prow = el.closest('.prow');
      const anchor = prow || el.closest('.row') || el.closest('.check');
      if (prow && prow.dataset.help !== f) return;
      if (!anchor || !meta.help) return;
      // The kill switch is rendered in the header bar rather than in its
      // section, and a paragraph of schema help dropped into that bar would
      // push every section below it down the page. It has its own line there.
      if (anchor.closest('.linestate')) return;
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
    await refreshLiveData();   // sound + volume settings feed the card
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

  // The viewers live in panel-viewers.js and need exactly these two: the
  // authenticated fetch, and the shared way of reporting a result into a
  // panel row. Published rather than duplicated, so there is one definition
  // of what carries the admin key.
  window.Panel = { afetch, showResult };

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
    const g = c.createGain(); g.gain.value = Math.min(1, getVolume() / 100);
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
      await refreshLiveData();
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
    // Feed the draft straight to the sound engine and put the saved config
    // back afterwards. This used to reach into the call page's own /live copy
    // and mutate it, which meant a preview briefly changed what a caller on
    // the line would have heard.
    const preview = { enabled: true, pack: chosen };
    preview[kind] = url;
    setSounds(preview);
    playSound(kind);
    const setName = ($('sound_pack').selectedOptions[0] || {}).textContent;
    const out = $('soundResult');
    out.className = 'result on';
    out.textContent = configured
      ? 'Playing your file: ' + configured
      : bundled
        ? `Playing the ${kind} sound bundled with the ${setName} set.`
        : `Playing the built-in ${kind} sound from the ${setName} set.`;
    setTimeout(() => setSounds(live && live.sounds), 1500);
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
      // "Registered" only ever meant the station accepted a row. Whether a
      // push can get back to this box is a separate question, and with a
      // receiver on a LAN address behind a NAS it is the one that actually
      // fails — so ask the station to fire one and wait for it to land.
      run: async (env) => {
        const polling = ' · the card falls back to 20s polling';
        if (!env.webhook?.registered) {
          return { status: 'warn', detail: (env.webhook?.detail || 'not registered') + polling };
        }
        const d = await afetch('/hooks/test', { method: 'POST' })
          .then((r) => r.json()).catch(() => null);
        if (!d) return { status: 'warn', detail: 'registered, delivery untested' };
        return d.ok
          ? { status: 'pass', detail: d.detail }
          : { status: 'warn', detail: d.detail + polling };
      },
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


  $('copyEmbedBtn').onclick = async () => {
    const btn = $('copyEmbedBtn');
    try { await navigator.clipboard.writeText($('embedSnippet').value); btn.textContent = 'Copied'; }
    catch (e) { $('embedSnippet').select(); btn.textContent = 'Press Ctrl+C'; }
    setTimeout(() => { btn.textContent = 'Copy snippet'; }, 2200);
  };
  $('previewEmbedBtn').onclick = () => window.open('/?compact=1', '_blank', 'width=430,height=430');

  // This used to hang off the gear button, because the panel was a section of
  // the call page that slid open over it. On its own page there is nothing to
  // open — arriving here IS the request to load, so it runs once at startup.
  let loading = false;
  async function open_() {
    if (loaded || loading) return;
    loading = true;
    $('saveMsg').textContent = 'Loading from station, TTS server and Ollama…';
    $('saveBtn').disabled = true;
    // The pipeline check reads live.stream and live.secureOrigin, so this has
    // to land before any of it can run.
    try { await refreshLiveData(); } catch (e) { /* the pipeline check will say */ }
    try { await loadSettings(); $('saveMsg').textContent = ''; }
    catch (e) {
      if (e && e.auth) { showLoginGate(e.body); $('saveMsg').textContent = ''; }
      else $('saveMsg').textContent = 'Could not load settings — ' + e.message;
    }
    finally { loading = false; $('saveBtn').disabled = false; }
  }

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

  open_();
})();
