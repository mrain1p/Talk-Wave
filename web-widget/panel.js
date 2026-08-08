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

    // The name and the one-line subtitle come from the schema, not from the
    // markup that also carries them. settings.py already owns the order and
    // the grouping; letting it own the wording too means a section cannot be
    // renamed in one file and not the other, which is how "AI brains" outlived
    // being called that in the group table.
    SCHEMA.groups.forEach((g) => {
      const sec = byId[g.id];
      if (!sec) return;
      const name = sec.querySelector(':scope > summary > .secname');
      const blurb = sec.querySelector(':scope > summary > .secblurb');
      if (name && g.title) name.textContent = g.title;
      if (blurb) blurb.textContent = g.blurb || '';
    });

    supers.forEach((sup) => {
      const members = SCHEMA.groups.filter((g) => g.super === sup.id && byId[g.id]);
      if (!members.length) return;
      const hdr = document.createElement('div');
      hdr.className = 'supergroup';
      hdr.dataset.super = sup.id;
      hdr.id = 'sup-' + sup.id;
      hdr.innerHTML = '<span></span><em></em>';
      hdr.querySelector('span').textContent = sup.title;
      // No subtext on the super-group band — the operator called it noise,
      // and the sections right under it each explain themselves.
      hdr.querySelector('em').textContent = '';
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
    decoratePermissions();
    bindFieldEvents();
    decorateFields();
    decorateSoundRows();
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
    deepseek: 'deepseek_api_key', requesty: 'requesty_api_key',
    gateway: 'gateway_api_key',
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
    // `auto` is the stored default and is deliberately not one of the three
    // choices — it is a rule for picking between two of them, not a third
    // kind of access. Show whichever it currently resolves to, which is what
    // it is actually doing.
    if (f === 'front_access' && resolved[f] === 'auto') {
      el.value = guestConfigured ? 'guest' : 'open';
    }
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
    missingProviderNote(note, 'llm');

    const stt = $('stt_provider').value || resolved.stt_provider;
    fill('stt_model', (options.sttModels || {})[stt] || []);
    $('stt_model').value = overrides.stt_model || '';
    const sttNote = $('sttSourceNote');
    if (sttNote) { sttNote.textContent = ''; missingProviderNote(sttNote, 'stt'); }
  }

  // The dropdowns above list only what a key exists for, so the ones that are
  // absent have to be accounted for — otherwise a list that is shorter than
  // the docs describe reads as a bug rather than as a missing key.
  function missingProviderNote(note, which) {
    const missing = ((options.providersNeedingKeys || {})[which]) || [];
    if (!missing.length) return;
    const line = 'Not listed, no key yet: ' + missing.join(', ')
      + '. Add its key below and it appears here.';
    note.textContent = note.textContent ? note.textContent + ' ' + line : line;
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
    if (!el) return;
    el.textContent = text == null ? '' : String(text);
    // The first word is the state, and colour carries it: green for a thing
    // that is ON, dimmed for one that is off — "on · 10%" in the same grey
    // as "off" made the header row a list of words instead of a glance.
    const head = String(text || '').split(/[ ·]/)[0].toLowerCase();
    el.dataset.state = ['on', 'open', 'always', 'live', 'ok'].includes(head)
      ? 'on'
      : ['off', 'never', 'closed', 'none'].includes(head) ? 'off' : '';
  }

  // How many keys a section holds, and how many of them are set. Each section
  // summarises its own keys now that Connections is gone; one page-wide "3 of
  // 8 set" said nothing about whether the section you were looking at could
  // work.
  function keysTag(group) {
    const fields = Object.keys(secrets).filter((f) => (secrets[f].group || '') === group);
    if (!fields.length) return '';
    const set = fields.filter((f) => secrets[f].set).length;
    return set ? set + ' of ' + fields.length + ' keys' : 'no key yet';
  }

  function paintTags() {
    setTag('tagStation', (options.personas || []).length + ' personas'
      + (keysTag('station') ? ' · ' + keysTag('station') : ''));
    setTag('tagVoice', (resolved.tts_mode || '') +
      (resolved.tts_voice ? ' · ' + resolved.tts_voice : ' · station voice'));
    setTag('tagBrains', (resolved.llm_provider || '') + ' · ' + (resolved.llm_model || '')
      + ' · ' + keysTag('brains'));
    setTag('tagEars', (resolved.stt_provider || '') + ' · ' + (resolved.stt_model || ''));
    // Permission count comes from the schema group, so it can't go stale when
    // a new permission is added. Tiered ones are counted through permOn:
    // reading `resolved[f]` directly would count "off" as enabled, since it is
    // a non-empty string.
    const permFields = Object.keys(SCHEMA.fields)
      .filter((f) => SCHEMA.fields[f].group === 'perms');
    const perms = permFields.filter(permOn).length;
    // The lowest tier anything is granted at is the interesting half: "6 of 11"
    // says nothing about whether strangers can reach any of it.
    const tiers = permFields.filter(isTiered).map(permTier)
      .filter((t) => t !== 'off');
    const lowest = TIER_IDS.find((t) => tiers.indexOf(t) !== -1);
    setTag('tagPerms', perms + ' of ' + permFields.length + ' on'
      + (lowest ? ' · from ' + { open: 'anyone', guest: 'guest code',
                                 admin: 'admin' }[lowest] : ''));
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
    paintDash();
  }

  // ------------------------------------------------------------- dashboard
  // The kill switch lives above every section, so its own state has to read
  // from up there too — a paused line with the word "paused" three sections
  // down is how an operator spends ten minutes wondering why nobody can call.
  //
  // The tiles beside it answer the four questions that decide whether any of
  // the settings below are worth changing yet, and each is a real answer read
  // back from something: /live for the station and who is on air, the auth
  // state for who can call, the resolved config for the three legs of a call.
  function paintDash() {
    const paused = $('calls_paused') ? $('calls_paused').checked : !!resolved.calls_paused;
    const btn = $('pauseBtn'), note = $('pausedNote'), sub = $('pausedSub');
    if (btn) {
      btn.textContent = paused ? 'Take calls again' : 'Pause all calls';
      btn.classList.toggle('resume', paused);
    }
    if (note) {
      note.textContent = paused ? 'The line is closed' : 'The line is open';
      note.classList.toggle('paused', paused);
    }
    if (sub) {
      sub.textContent = paused
        ? 'Callers are turned away; the card still shows who is on air.'
        : 'Takes effect the moment you press it — no Save, no restart.';
    }

    const l = live || {};
    const face = $('tileOnAirImg');
    if (face) {
      face.hidden = !l.avatar;
      if (l.avatar && face.getAttribute('src') !== l.avatar) {
        face.src = l.avatar;
      }
    }
    tile('tileOnAir', l.name || '—',
      l.onAir === false
        ? 'the station says nothing is on air'
        : [l.show, l.track && l.track.title].filter(Boolean).join(' · '));
    tile('tileStation',
      l.reachable === false ? 'not answering' : (l.degraded ? 'degraded' : 'connected'),
      l.reachable === false
        ? 'check the Station API address below'
        : (l.degraded ? 'answering slowly or partially'
                      : (options.personas || []).length + ' personas'),
      l.reachable === false ? 'bad' : (l.degraded ? 'warn' : 'ok'));

    const ACCESS = { open: 'Anyone', guest: 'Guest code', admin: 'Admin only' };
    const access = ($('front_access') && $('front_access').value) || resolved.front_access;
    $('dashLogoutBtn').hidden = !authConfigured;
    tile('tileAccess', ACCESS[access] || access || '—',
      authConfigured ? 'panel password set' : 'this panel has no password',
      authConfigured ? (access === 'open' ? 'warn' : 'ok') : 'bad');

    // Named rather than counted: "3 of 3 configured" is true of a call that
    // cannot happen, because a provider with no key is still a provider.
    // Unsaved picks included, like every other live read on this page.
    const pick = (id, fallback) => ($(id) && $(id).value) || fallback || '?';
    const llm = pick('llm_provider', resolved.llm_provider);
    const chain = [llm, pick('tts_mode', resolved.tts_mode),
                   pick('stt_provider', resolved.stt_provider)].join(' · ');
    const keyField = PROVIDER_KEY[llm];
    const needKey = !!(keyField && secrets[keyField] && !secrets[keyField].set);
    tile('tileChain', chain,
      needKey ? 'no key for ' + llm : (resolved.llm_model || ''),
      needKey ? 'bad' : 'ok');
  }

  async function paintNightTile() {
    if (!$('tileCalls')) return;
    const jumpToRecords = () => {
      const sec = document.querySelector('details.diag[data-diag="calls"]');
      if (sec) {
        sec.open = true;
        sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
        $('viewCallsBtn').click();
      }
    };
    const jumpToVoicemail = () => {
      const sec = document.querySelector('details.sec[data-group="voicemail"]');
      if (sec) {
        sec.open = true;
        sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    };
    try {
      const d = await afetch('/calls').then((r) => r.json());
      const calls = d.calls || [];
      const vms = calls.filter((c) => c.kind === 'voicemail');
      const lives = calls.filter((c) => c.kind !== 'voicemail');
      const rough = lives.filter((c) =>
        (c.problems || []).length || !(c.callerTurns || 0)).length;
      const up = calls.filter((c) => c.rating === 'up').length;
      const down = calls.filter((c) => c.rating === 'down').length;
      tile('tileCalls',
        lives.length ? lives.length + ' recent' : 'none yet',
        [rough && rough + ' with problems',
         up && '\ud83d\udc4d' + up, down && '\ud83d\udc4e' + down]
          .filter(Boolean).join(' \u00b7 ')
          || (lives.length ? 'all clean' : 'records appear here'),
        rough ? 'warn' : lives.length ? 'ok' : undefined);
      tile('tileVm', vms.length ? vms.length + ' taken' : 'none yet',
        vms.length ? 'open the section for the messages' : '');
      $('tileCalls').onclick = jumpToRecords;
      $('tileVm').onclick = jumpToVoicemail;
    } catch (e) {
      tile('tileCalls', '\u2014', 'sign in to read the records');
      tile('tileVm', '\u2014', '');
    }
  }

  function tile(id, value, note, tone) {
    const el = $(id);
    if (!el) return;
    el.querySelector('.tv').textContent = value;
    el.querySelector('.tn').textContent = note || '';
    el.classList.remove('ok', 'warn', 'bad');
    if (tone) el.classList.add(tone);
  }

  // The one control on this page that does not wait for Save. Everything else
  // here is a draft of how the next call should go; closing the line is a
  // thing being done to the calls happening now, and a kill switch that needs
  // a second press somewhere else on the page is not one.
  //
  // Only calls_paused is posted, and nothing else is repainted from the
  // response — the ordinary save path calls paint(), which refills every field
  // from the server and would silently throw away whatever edits were in
  // progress further down the page.
  async function savePaused(next) {
    const box = $('calls_paused'), btn = $('pauseBtn');
    if (!box) return;
    const before = box.checked;
    box.checked = next;
    btn.disabled = true;
    paintDash();
    try {
      const r = await afetch('/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ calls_paused: next }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'refused');
      resolved.calls_paused = next;
      overrides.calls_paused = next;
      $('saveMsg').textContent = next ? 'Line closed.' : 'Line open.';
      setTimeout(() => { $('saveMsg').textContent = ''; }, 3000);
      await refreshLiveData();
    } catch (e) {
      box.checked = before;
      $('saveMsg').textContent = 'Could not change the line — ' + e.message;
    } finally {
      btn.disabled = false;
      paintDash();
    }
  }

  $('pauseBtn').onclick = () => savePaused(!$('calls_paused').checked);

  // A tile is a jump link with an answer written on it. data-jump names a
  // section's group id, and it OPENS the section as well as scrolling to it:
  // everything here is folded by default, so a scroll alone lands on a
  // one-line heading with the answer still hidden behind it.
  document.querySelectorAll('.dashtiles .tile').forEach((el) => {
    el.onclick = () => {
      const sec = document.querySelector(
        'details.sec[data-group="' + el.dataset.jump + '"]');
      if (!sec) return;
      sec.open = true;
      sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
  });

  // The dashboard's sign-out is the Access section's, surfaced — one
  // implementation, one meaning (this browser forgets both credentials).
  $('dashLogoutBtn').onclick = () => $('logoutBtn').click();

  paintNightTileOnce();
  function paintNightTileOnce() {
    // Deferred: afetch needs the stored key, and the tile is furniture, not
    // a gate — a failed read paints "sign in" and the dash carries on.
    setTimeout(paintNightTile, 800);
  }

  $('dashCheckBtn').onclick = () => {
    // Everything Diagnostics offers, in reading order: the pipeline, the
    // speed test, the recent calls, the server logs. One button, because
    // "run the full check" that ran a third of the page was a name writing
    // a cheque the button didn't cash.
    const sec = document.querySelector('details.sec[data-diag="pipeline"]');
    if (sec) {
      sec.open = true;
      sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    $('runAllBtn').click();
    ['speedBtn', 'viewCallsBtn', 'viewLogsBtn'].forEach((id) => {
      const btn = $(id);
      if (btn && !btn.disabled) {
        const owner = btn.closest('details');
        if (owner) owner.open = true;
        btn.click();
      }
    });
  };

  // Worked examples of what a caller can actually say, tied to the permission
  // that enables each one — so the list can't drift from the real tool surface.
  // What a permission is set to RIGHT NOW, including an unsaved tick. The
  // reference lists are there to answer "what does this switch do" — reading
  // only the saved value meant they didn't move until after you'd committed
  // the change you were trying to understand.
  function permOn(field) {
    const el = $(field);
    // A tiered permission is a <select> holding off/open/guest/admin, and
    // "off" is a truthy string — so the old `!!resolved[field]` would have
    // read every switched-off permission as on, in the two lists whose entire
    // job is saying what is switched on.
    if (isTiered(field)) {
      return permTier(field) !== 'off';
    }
    if (el && el.type === 'checkbox') return el.checked;
    return !!resolved[field];
  }

  // The tier a permission is set to RIGHT NOW, unsaved edits included.
  function permTier(field) {
    const el = $(field);
    const value = (el && el.value) || resolved[field] || 'off';
    return TIER_IDS.indexOf(value) === -1 ? 'off' : value;
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
      // Which callers get it. "Available" and "available to strangers" are
      // different answers and the list used to give only the first.
      if (enabled && a.need && isTiered(a.need)) {
        const chip = document.createElement('span');
        chip.className = 'whotier t-' + permTier(a.need);
        chip.textContent = { open: 'anyone', guest: 'guest code',
                             admin: 'admin' }[permTier(a.need)];
        li.querySelector('.why').appendChild(chip);
      }
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

      // The state column says WHO reaches it, not just whether anyone does —
      // which is the question this section is now able to answer.
      const state = document.createElement('span');
      state.className = 'tstate';
      state.textContent = t.gate === 'never' ? 'never'
        : t.gate === 'read' ? 'always'
        : !on ? 'off'
        : isTiered(t.gate)
          ? { open: 'anyone', guest: 'guest', admin: 'admin' }[permTier(t.gate)]
          : 'on';

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
    // A voicemail-only line has no live Call button, so the options that
    // shape one are moot. Dashed rather than hidden — the operator can
    // still see what comes back when live calls do.
    const vmSel = $('voicemail_when');
    const vmOn = $('voicemail_enabled')
      ? $('voicemail_enabled').checked : !!resolved.voicemail_enabled;
    const vmAlways = vmOn
      && (vmSel ? (vmSel.value || resolved.voicemail_when)
                : resolved.voicemail_when) === 'always';
    const liveChk = $('live_calls_enabled');
    const liveOn = liveChk ? liveChk.checked
                           : resolved.live_calls_enabled !== false;
    const liveOff = vmAlways || !liveOn;
    const MOOT_WITHOUT_LIVE = ['call_button_mode', 'call_button_label',
                               'show_push_to_talk', 'embed_push_to_talk'];

    // Every rule comes from the schema: a field declares what it depends on,
    // and advanced fields stay hidden until asked for.
    Object.keys(SCHEMA.fields).forEach((f) => {
      const el = $(f);
      if (!el) return;
      const meta = SCHEMA.fields[f];
      const anchor = el.closest('.row') || el.closest('.check');
      if (!anchor) return;

      if (MOOT_WITHOUT_LIVE.indexOf(f) !== -1) {
        anchor.classList.toggle('moot', liveOff);
        anchor.title = liveOff
          ? 'The line is voicemail-only — there is no live Call button for '
            + 'this to apply to.' : '';
      }

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
      // Only a hint that is a SIBLING needs hiding alongside its field — a
      // .row carries its help inside itself now, and hiding the row takes it
      // with it. Without the class check this would reach past the row and
      // hide whatever happened to follow it.
      const hint = anchor.nextElementSibling;
      if (hint && hint.classList.contains('hint') && hint.dataset.fromSchema) {
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
    paintEmbedSnippet();
  }

  function paintEmbedSnippet() {
    if (!$('embedSnippet')) return;
    const attrs = [];
    const theme = $('embedTheme') && $('embedTheme').value;
    const caps = $('embedCaptions') && $('embedCaptions').value;
    if (theme) attrs.push(' data-theme="' + theme + '"');
    if (caps) attrs.push(' data-captions="' + caps + '"');
    $('embedSnippet').value =
      '<div id="subwave-callin"></div>\n' +
      '<script src="' + location.origin + '/embed.js"'
      + attrs.join('') + '><\/script>';
  }
  if ($('embedTheme')) {
    $('embedTheme').onchange = paintEmbedSnippet;
    $('embedCaptions').onchange = paintEmbedSnippet;
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
    jump.textContent = 'Brains';
    jump.href = '#';
    jump.onclick = (e) => {
      e.preventDefault();
      const sec = document.querySelector('details.sec[data-group="brains"]');
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
    // Labelled, because the ids alone do not say what they are — "gateway" in
    // a list next to "google" is a coin toss, and two of these are
    // aggregators rather than vendors.
    fill('llm_provider', options.llmProviders,
      { labels: options.llmProviderLabels || null });
    fill('stt_provider', options.sttProviders, {
      labels: { local: 'Built-in Whisper — local, no key (default)' },
    });

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
    paintPermissions();
    syncSoundPickers();
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

  // Station admin credentials belong with the station they unlock. They are
  // still named here — not to place them, which the server's own grouping now
  // does, but because the station's test buttons submit and clear this
  // specific pair.
  const STATION_SECRETS = ['subwave_admin_user', 'subwave_admin_pass'];

  // Security section: set/change the panel password, and nudge loudly while
  // none exists — an open panel is fine on a trusted LAN but should be a
  // choice, not an accident.
  function paintSecurity() {
    const MODE = { open: 'open to anyone', guest: 'guest code',
                   admin: 'admin only' };
    // From the picker, not from `resolved`: the stored value may still be
    // `auto`, which is not one of the three and would have read as
    // "automatic · " — a summary naming a mode the section below does not
    // offer.
    const access = ($('front_access') && $('front_access').value)
      || resolved.front_access;
    setTag('tagSecurity', (MODE[access] || access || '')
      + ' · ' + (authConfigured ? 'admin set' : 'ADMIN OPEN'));
    $('sec_current_pw').style.display = authConfigured ? '' : 'none';
    // Set / not set, said in the heading — the operator could not tell
    // which credentials existed without trying them.
    const chip = (id, isSet) => {
      const el = $(id);
      if (!el) return;
      el.textContent = isSet ? 'set' : 'not set';
      el.dataset.state = isSet ? 'on' : 'off';
    };
    chip('adminSetChip', authConfigured);
    chip('guestSetChip', !!guestConfigured);
    $('setPwBtn').textContent = authConfigured ? 'Change password' : 'Set password';
    $('logoutBtn').hidden = !authConfigured;
    $('setGuestBtn').textContent = guestConfigured ? 'Change guest code' : 'Set guest code';
    $('clearGuestBtn').hidden = !guestConfigured;
    // Setting or clearing the guest code changes which permission columns can
    // be ticked at all — that has to follow immediately, not on a reload.
    paintPermissions();

    // First run: the setup card in the markup, shown until a password exists.
    $('pwNudge').hidden = authConfigured;
    if (!authConfigured) $('firstPw').focus();
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
      // Setting a code and not being asked for it is the trap `auto` used to
      // paper over. With the three explicit levels, an open line stays open
      // until somebody says otherwise — so say it here, as a pending change
      // the operator can see and undo, rather than silently either way.
      const access = $('front_access');
      if (code && access && access.value === 'open') {
        access.value = 'guest';
        access.dispatchEvent(new Event('change', { bubbles: true }));
      }
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

  // The first-run card and the Access section set the same password, so they
  // run the same handler — the card fills the section's fields and presses its
  // button rather than posting for itself. Two implementations of "set the
  // admin password" is two places for the rules to drift.
  function setFirstPassword() {
    $('sec_new_pw').value = $('firstPw').value;
    $('sec_current_pw').value = '';
    $('firstPwMsg').textContent = 'Saving…';
    $('setPwBtn').click();
  }
  $('firstPwBtn').onclick = setFirstPassword;
  $('firstPw').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') setFirstPassword();
  });

  $('setPwBtn').onclick = async () => {
    const out = $('pwResult');
    // Two steps, one button: the new-password box only exists once asked
    // for — the operator called the always-there box wasted space, rightly.
    // The first-run banner fills the box BEFORE clicking, so its value being
    // set is what lets that path sail through in one step.
    if ($('sec_new_pw').hidden && !$('sec_new_pw').value) {
      $('sec_new_pw').hidden = false;
      $('sec_new_pw').focus();
      $('setPwBtn').textContent = 'Save password';
      return;
    }
    const newPw = $('sec_new_pw').value;
    if (newPw.length < 8) {
      showResult(out, false, 'Use at least 8 characters.');
      if ($('firstPwMsg')) $('firstPwMsg').textContent = 'Use at least 8 characters.';
      return;
    }
    const btn = $('setPwBtn'); btn.disabled = true;
    try {
      const r = await afetch('/auth/password', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current: $('sec_current_pw').value, new: newPw }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        showResult(out, false, d.error || 'failed');
        // The first-run card is a second way into this handler, and it is at
        // the top of the page where `out` is not. Without this, a refused
        // password there just sat there doing nothing visible.
        if ($('firstPwMsg')) $('firstPwMsg').textContent = d.error || 'failed';
        return;
      }
      localStorage.setItem('callinAdminKey', newPw);
      authConfigured = true;
      $('sec_current_pw').value = ''; $('sec_new_pw').value = '';
      $('sec_new_pw').hidden = true;
      $('setPwBtn').textContent = 'Change password…';
      paintSecurity();
      showResult(out, true, 'Password saved. This browser stays signed in '
        + 'until you sign out; other browsers and devices will be asked.');
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  // Where each block's result pane currently is. paintSecrets rebuilds the
  // blocks, so a pane captured before a save is a detached node by the time
  // the answer arrives — every write goes through a fresh lookup here.
  const keyResults = {};

  // Every key block on the page is built from the group each secret declares
  // (secrets_store.SECRET_GROUPS), into whichever `.keyblock` div carries that
  // group's id. There is no list of fields in this file and no per-section
  // Save handler, which is the point: the old single Connections section had
  // one hand-written host div, one hand-written Save button and one hardcoded
  // exception for the station pair, and splitting the keys across four
  // sections that way would have been four of each.
  function paintSecrets() {
    const byGroup = {};
    Object.keys(secrets).forEach((f) => {
      const g = secrets[f].group || 'brains';
      (byGroup[g] = byGroup[g] || []).push(f);
    });

    document.querySelectorAll('.keyblock').forEach((host) => {
      const group = host.id.replace(/^keys_/, '');
      const fields = byGroup[group] || [];
      host.innerHTML = '';
      // A block with nothing in it is a heading and a paragraph describing an
      // empty space. Hide the whole thing rather than leave the promise.
      host.hidden = !fields.length;
      if (!fields.length) return;

      fields.forEach((f) => paintSecretRow(host, f, group));

      const bar = document.createElement('div');
      bar.className = 'testrow';
      const save = document.createElement('button');
      save.textContent = 'Save keys';
      bar.appendChild(save);
      if (group === 'station') {
        // The operator wants the station's four buttons on one row. The
        // block repaints wholesale, so the static three are RELOCATED here
        // on every paint; their original row is left empty and hidden.
        ['testAdminBtn', 'testStationBtn', 'reloadStationBtn'].forEach((id) => {
          const btn = $(id);
          if (btn) {
            if (btn.parentElement && btn.parentElement !== bar) {
              btn.parentElement.hidden = true;
            }
            bar.appendChild(btn);
            btn.hidden = false;
          }
        });
      }

      const out = document.createElement('div');
      out.className = 'result';
      host.append(bar, out);
      keyResults[group] = out;

      save.onclick = async () => {
        const set = {};
        fields.forEach((f) => {
          const el = $('sec_' + f);
          const v = el ? el.value.trim() : '';
          if (!v) return;
          // An untouched masked box arrives empty and must not wipe a working
          // key — see secrets_store's module docstring.
          if (secrets[f].visible && v === secrets[f].hint) return;
          set[f] = v;
        });
        if (!Object.keys(set).length) {
          showResult(out, true,
            'Nothing to save — a blank box keeps the current value.');
          return;
        }
        save.disabled = true;
        try { await postSecrets(set, [], group); }
        finally { save.disabled = false; }
      };
    });
  }

  function paintSecretRow(host, field, group) {
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
    clear.onclick = async () => {
      clear.disabled = true;
      await postSecrets({}, [field], group);
    };

    row.append(label, input, clear);
    // What the key buys, in the row's third column — the same shape every
    // other setting on this page uses. A column of vendor names asks the
    // operator to already know which of eight this deployment needs.
    if (s.help) {
      const hint = document.createElement('p');
      hint.className = 'hint inrow';
      hint.textContent = s.help;
      row.appendChild(hint);
    }
    host.appendChild(row);
  }

  async function postSecrets(set, clear, group) {
    const say = (ok, text) => {
      // After the repaint, not before: paintSecrets replaces the pane.
      const el = keyResults[group] || keyResults.brains;
      if (el) showResult(el, ok, text);
    };
    const pending = keyResults[group];
    if (pending) { pending.className = 'result on'; pending.textContent = 'Saving…'; }
    try {
      const r = await afetch('/settings/secrets', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ set, clear }),
      });
      const d = await r.json();
      if (!r.ok) {
        paintSecrets();
        say(false, d.error || 'Save failed');
        return;
      }
      secrets = d.secrets;
      paintSecrets(); paintTags(); paintFirstRun();
      const n = Object.keys(set).length, c = (clear || []).length;
      say(true,
        (n ? n + ' key' + (n > 1 ? 's' : '') + ' saved. ' : '') +
        (c ? c + ' cleared. ' : '') + 'Applies to the next caller and to the tests.');
    } catch (e) {
      paintSecrets();
      say(false, 'Failed: ' + e.message);
    }
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
  let userTouched = false;
  ['input', 'change'].forEach((kind) => {
    document.addEventListener(kind, (e) => {
      if (e.isTrusted) userTouched = true;
    }, true);
  });

  function markClean() {
    const n = Object.keys(pendingPatch()).length;
    $('saveBtn').classList.toggle('clean', n === 0);
    $('saveBtn').textContent = n ? 'Save ' + n + ' change' + (n > 1 ? 's' : '') : 'Save';
    // The floating verdict: visible exactly while anything is unsaved AND a
    // human has actually edited something this visit — repaints during load
    // briefly disagree with themselves and must not flash the bar.
    const bar = $('saveOverlay');
    if (bar) {
      bar.hidden = n === 0 || !userTouched;
      $('saveOverlayMsg').textContent =
        n + ' unsaved change' + (n === 1 ? '' : 's');
    }
  }
  // ------------------------------------------------------- the live preview
  // A real call card in a frame, repainted from the form as it is edited.
  //
  // It resolves through /live/preview rather than working the rules out here.
  // Whether the gear appears, which lines of the who's-on-air block each
  // surface paints, what the Call button says — those rules already exist in
  // api/live.py and are already answered per surface. A second copy in
  // JavaScript would agree with the first one right up until somebody changed
  // one of them, and a preview that quietly disagrees with the card is worse
  // than no preview: it is confidently wrong about the one thing you opened
  // it to check.
  //
  // Which fields matter is read from the SCHEMA rather than listed, so a look
  // setting added later is previewed without anyone remembering to come here.
  const LOOK_GROUPS = new Set(['player']);
  function isLookField(f) {
    return !!(SCHEMA.fields[f] && LOOK_GROUPS.has(SCHEMA.fields[f].group));
  }

  let previewSurface = 'page';
  let previewTimer = null;

  function previewFrame() {
    const f = $('previewFrame');
    return f && f.contentWindow ? f : null;
  }

  async function pushPreview() {
    const f = previewFrame();
    if (!f) return;
    try {
      const r = await afetch('/live/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pendingPatch()),
      });
      if (!r.ok) return;
      const look = await r.json();
      // The frame renders as whichever surface is selected, so it is handed
      // that surface's answers under BOTH names. The widget picks by whether
      // it is in a frame, and a preview is always in a frame — which would
      // otherwise make the Page tab quietly show the embed's answers.
      const controls = previewSurface === 'embed' ? look.embedControls : look.controls;
      const card = previewSurface === 'embed' ? look.embedCard : look.card;
      f.contentWindow.postMessage({
        type: 'swtv:preview',
        live: Object.assign({}, look, {
          controls: controls, embedControls: controls,
          card: card, embedCard: card,
        }),
      }, location.origin);
    } catch (e) { /* the preview is a nicety; never let it break the form */ }
  }

  // Coalesced: typing in the Call button's label field fires per keystroke,
  // and each one is a request.
  function queuePreview() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(pushPreview, 180);
  }

  function setPreviewSurface(which) {
    previewSurface = which;
    $('previewPage').classList.toggle('on', which === 'page');
    $('previewEmbed').classList.toggle('on', which === 'embed');
    const f = $('previewFrame');
    if (!f) return;
    // compact=1 is what makes the widget render as an embed, and it is read
    // at load, so this is a reload rather than a message.
    f.src = which === 'embed' ? '/?preview=1&compact=1' : '/?preview=1';
    // The frame pushes nothing on its own; it paints from its own /live and
    // then waits. The load event is the earliest it can be told about the
    // unsaved changes.
    f.onload = pushPreview;
  }

  if ($('previewPage')) {
    $('previewPage').onclick = () => setPreviewSurface('page');
    $('previewEmbed').onclick = () => setPreviewSurface('embed');
    $('previewFrame').onload = pushPreview;
    // The frame is the real widget, and the real widget reports its own
    // height — the same subwave-callin:height contract embed.js consumes.
    // Sizing the box from it shows the whole card with no scrollbar, on
    // either surface tab; the fixed 300px in the stylesheet is only the
    // pre-report placeholder. Clamped because a report is still input.
    addEventListener('message', (e) => {
      const f = $('previewFrame');
      if (!f || !e.data || e.data.type !== 'subwave-callin:height') return;
      if (e.source !== f.contentWindow) return;
      f.style.height = Math.max(220, Math.min(760, e.data.px | 0)) + 'px';
    });
  }

  // ------------------------------------------------------- theme cycle
  // The same four stops the call card offers — light, dark, the station's
  // show colours, match the page — with the same glyphs and the same stored
  // choice (localStorage.callinTheme is shared across this origin), because
  // the operator met a two-state toggle here and a four-state one on the
  // card and reasonably called it inconsistent. The station stop only
  // exists when /live carries a palette.
  let panelStationTheme = null;

  function panelThemeOptions() {
    const opts = ['light', 'dark'];
    if (panelStationTheme && panelStationTheme.tokens) opts.push('station');
    opts.push('');
    return opts;
  }

  function panelApplyTheme(choice) {
    const root = document.documentElement;
    [...root.style].filter((prop) => prop.startsWith('--'))
      .forEach((prop) => root.style.removeProperty(prop));
    if (choice === 'station' && panelStationTheme) {
      root.setAttribute('data-theme',
        panelStationTheme.mode === 'light' ? 'light' : 'dark');
      Object.entries(panelStationTheme.tokens || {}).forEach(([k, v]) => {
        root.style.setProperty(k, v);
      });
    } else if (choice === 'light' || choice === 'dark') {
      root.setAttribute('data-theme', choice);
    } else {
      root.removeAttribute('data-theme');
    }
    if (choice) localStorage.setItem('callinTheme', choice);
    else localStorage.removeItem('callinTheme');
    panelPaintGlyph();
  }

  function panelPaintGlyph() {
    const btn = $('themeBtn');
    if (!btn) return;
    const opts = panelThemeOptions();
    const stored = localStorage.getItem('callinTheme') || '';
    const idx = opts.indexOf(opts.includes(stored) ? stored : '');
    const next = opts[(idx + 1) % opts.length];
    btn.textContent = { light: '\u2600', dark: '\u263e',
                        station: '\u2733', '': '\u25a6' }[next];
    btn.title = { light: 'Switch to light', dark: 'Switch to dark',
                  station: "The station's show colours",
                  '': 'Match the device' }[next];
  }

  (function bindPanelThemeCycle() {
    const btn = $('themeBtn');
    if (!btn) return;
    btn.onclick = () => {
      const opts = panelThemeOptions();
      const stored = localStorage.getItem('callinTheme') || '';
      const idx = opts.indexOf(opts.includes(stored) ? stored : '');
      panelApplyTheme(opts[(idx + 1) % opts.length]);
    };
    // The palette arrives with /live — one public read, cached server-side.
    fetch('/live').then((r) => r.json()).then((d) => {
      panelStationTheme = (d && d.stationTheme) || null;
      // A stored 'station' choice could not paint at boot (shared.js only
      // knows light/dark); honour it now the palette exists.
      if (localStorage.getItem('callinTheme') === 'station') {
        panelApplyTheme('station');
      }
      panelPaintGlyph();
    }).catch(() => panelPaintGlyph());
    panelPaintGlyph();
  })();

  // ----------------------------------------------------- settings search
  // Type, and only the rows whose label or help mention it remain; the
  // sections holding them open, everything else steps aside. Empty restores
  // the panel exactly as it stood, including which sections were open.
  (function bindSettingsSearch() {
    const box = $('settingsSearch');
    if (!box) return;
    let timer = null;
    const apply = () => {
      const needle = (box.value || '').trim().toLowerCase();
      document.querySelectorAll('details.sec').forEach((sec) => {
        const rows = sec.querySelectorAll('.row, label.check, .prow, .permrow');
        if (!needle) {
          rows.forEach((r) => { r.style.removeProperty('display'); });
          sec.style.removeProperty('display');
          if (sec.dataset.searchOpened) {
            sec.open = false;
            delete sec.dataset.searchOpened;
          }
          return;
        }
        let any = false;
        rows.forEach((r) => {
          const hit = r.textContent.toLowerCase().includes(needle);
          r.style.display = hit ? '' : 'none';
          any = any || hit;
        });
        sec.style.display = any ? '' : 'none';
        if (any && !sec.open) {
          sec.open = true;
          sec.dataset.searchOpened = '1';
        }
      });
    };
    box.oninput = () => { clearTimeout(timer); timer = setTimeout(apply, 120); };
  })();

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
        if (f === 'calls_paused') paintDash();
        // The dashboard reads the call chain and the access mode live, so the
        // tiles follow the pickers the same way the reference lists do.
        if (f === 'front_access' || f === 'llm_provider'
            || f === 'tts_mode' || f === 'stt_provider') paintDash();
        // Which tiers exist at all depends on the door. Closing the line to
        // callers without a code means there are no `open` callers, and a
        // column that can no longer be reached has to grey out as you pick it
        // — not on the next reload.
        if (f === 'front_access') { paintPermissions(); paintTags(); }
        // The two reference lists describe the permissions, so they follow
        // the switches rather than waiting for a save.
        if (SCHEMA.fields[f] && SCHEMA.fields[f].group === 'perms') {
          paintAsks(); paintTools(); paintTags();
        }
        // The card in the frame follows the form, not the save button. That
        // is the entire point: you find out what "DJ photo off" looks like
        // before you commit it to everyone who rings.
        if (isLookField(f)) queuePreview();
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
      // A .permrow is display:contents so its cells share the grid's columns,
      // which means anything appended to it lands in the next free CELL — the
      // tag would sit where a checkbox goes. It belongs beside the name.
      const host = anchor.querySelector('.plabel') || anchor;
      let tag = host.querySelector('.needsadmin');
      if (!tag) {
        tag = document.createElement('span');
        tag.className = 'needsadmin';
        tag.textContent = 'Station admin';
        host.appendChild(tag);
      }
      tag.classList.toggle('missing', !have);
      tag.title = have
        ? 'Uses the station admin credentials stored under Station.'
        : 'Needs the station admin username and password under Station. '
          + 'Without them this stays switched on and quietly never happens.';
    });
  }

  // ------------------------------------------------- the permission matrix
  // A tiered permission is ONE field holding one of off / open / guest /
  // admin. The three columns are a way of setting it, not three settings: the
  // <select> in the markup is what Save diffs and what the server stores, so
  // everything else in this file — pendingPatch, paint, applyVisibility — goes
  // on working without knowing the matrix exists.
  const TIER_IDS = ['open', 'guest', 'admin'];
  const isTiered = (f) => !!(SCHEMA.fields[f] && SCHEMA.fields[f].tiered);

  // Which columns can be ticked at all, given the doors that exist. Ticking
  // "Guest" with no guest code set would grant a permission to a tier nobody
  // can ever be — the setting would save, look right, and never once apply.
  function tierReachable(tier) {
    const access = ($('front_access') && $('front_access').value)
      || resolved.front_access || 'auto';
    if (tier === 'admin') return true;      // always a door
    if (tier === 'guest') return guestConfigured;
    // `open` callers only exist while the line lets somebody in without a
    // code. On auto that is "until a guest code is set".
    if (access === 'open') return true;
    if (access === 'auto') return !guestConfigured;
    return false;
  }

  function tierWhyNot(tier) {
    if (tier === 'guest') return 'No guest code set — nobody can be this caller yet.';
    return 'The line is closed to callers without a code, so there are no '
      + 'callers at this level. Change Call-in access under Access.';
  }

  // Build the label and the three cells once, then keep them in step with the
  // select. Called from adoptSchema, so it runs before the first paint.
  function decoratePermissions() {
    document.querySelectorAll('.permrow').forEach((row) => {
      const field = row.dataset.perm;
      const meta = SCHEMA.fields[field];
      if (!meta || row.dataset.built) return;
      row.dataset.built = '1';

      const label = document.createElement('span');
      label.className = 'plabel';
      label.textContent = meta.label || field;
      row.insertBefore(label, row.firstChild);

      TIER_IDS.forEach((tier) => {
        const cell = document.createElement('span');
        cell.className = 'tcell';
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.dataset.tier = tier;
        box.setAttribute('aria-label', (meta.label || field) + ' — ' + tier);
        box.onchange = () => {
          const sel = $(field);
          // Ticking a column means "this tier, and everyone above it".
          // Unticking means "take it away from this level" — so the floor
          // rises to the next tier up, and unticking the top one is the only
          // thing that reaches off. One rule for all three columns: the first
          // version turned the permission off outright when you unticked the
          // column that happened to BE the setting, which made unticking
          // Anyone and unticking Guest do completely different things.
          if (box.checked) sel.value = tier;
          else sel.value = TIER_IDS[TIER_IDS.indexOf(tier) + 1] || 'off';
          sel.dispatchEvent(new Event('change', { bubbles: true }));
          paintPermissions();
        };
        cell.appendChild(box);
        row.appendChild(cell);
      });
    });
  }

  // Reflect each select into its row of checkboxes, and grey the columns that
  // no caller could ever be.
  function paintPermissions() {
    document.querySelectorAll('.permrow').forEach((row) => {
      const field = row.dataset.perm;
      const sel = $(field);
      if (!sel) return;
      const at = TIER_IDS.indexOf(sel.value);
      row.querySelectorAll('input[type=checkbox]').forEach((box) => {
        const idx = TIER_IDS.indexOf(box.dataset.tier);
        // Cascade: granted to a lower tier means granted here too.
        box.checked = at !== -1 && idx >= at;
        const reachable = tierReachable(box.dataset.tier);
        // A column with no door behind it is disabled, not hidden — the
        // operator needs to see that the level exists and why it is shut.
        box.disabled = !reachable && !box.checked;
        box.title = reachable ? '' : tierWhyNot(box.dataset.tier);
        box.closest('.tcell').classList.toggle('unreachable', !reachable);
      });
      row.classList.toggle('off', at === -1);
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
      if (anchor.closest('.dash')) return;
      // A .row is label + field, and the field is a dropdown or a box holding
      // a number — so the right two thirds of every one of those rows was
      // empty, with the explanation on a line of its own underneath. Put the
      // help IN the row and it reads across in one line, using the width that
      // was already being paid for. .check and .prow anchors keep the help
      // below them: .check is a <label>, and a paragraph inside one is both
      // invalid and clickable-to-toggle.
      // NOT inline for a permission row, even though it carries .row too. A
      // .permrow is display:contents inside the four-column matrix, so a hint
      // appended inside it becomes a fifth grid item that wraps into the
      // label column of the next line — which is what shredded the whole
      // Caller permissions section. As a sibling it is a .permgrid > .hint,
      // which the grid already knows to span full width.
      const inline = anchor.classList.contains('row')
        && !anchor.classList.contains('permrow');
      // A matrix row's help flows INSIDE the label cell rather than taking a
      // band of its own underneath — the operator asked for one line across,
      // wrapping when it runs long, and they were right: forty rows times an
      // extra band was most of the page's height.
      const plabel = prow && prow.querySelector('.plabel');
      // A checkbox's help joins the label's own line too — a <span> inside
      // a <label> is valid where the old <p> was not, and a band of help
      // under every toggle was the same height tax the matrix paid. The
      // help becomes clickable-to-toggle, which is how most settings UIs
      // already behave. Operator-asked, twice.
      const check = !plabel && anchor.classList.contains('check');
      let hint = plabel ? plabel.querySelector(':scope > .hint')
        : (inline || check) ? anchor.querySelector(':scope > .hint')
        : anchor.nextElementSibling;
      if (!hint || !hint.classList.contains('hint') || !hint.dataset.fromSchema) {
        hint = document.createElement((plabel || check) ? 'span' : 'p');
        hint.className = (plabel || check) ? 'hint inlabel'
          : inline ? 'hint inrow' : 'hint wide';
        hint.dataset.fromSchema = '1';
        if (plabel) plabel.appendChild(hint);
        else if (inline || check) anchor.appendChild(hint);
        else anchor.insertAdjacentElement('afterend', hint);
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
  // An adapter and an endpoint have to match or the audio arrives at the wrong
  // sample rate, which sounds broken and logs nothing — see tts_base_url's
  // help. An adapter that knows its own vendor's address fills the box in
  // rather than leaving the operator to find it in a doc.
  $('tts_adapter').onchange = async () => {
    const hint = (options.ttsAdapterBaseUrls || {})[$('tts_adapter').value];
    if (hint) $('tts_base_url').value = hint;
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
    const btn = $('refreshModelsBtn'), out = $('llmResult');
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

  // Hear the selected voice THROUGH the selected effect. The DSP constants
  // mirror call.js's FX table — the caller's browser is the canonical copy,
  // and this is a preview of it, kept in step by eye and by ear.
  const FX_PREVIEW = {
    telephone:  { hp: 300, lp: 3400, grit: 0 },
    cb:         { hp: 400, lp: 2500, grit: 26 },
    walkie:     { hp: 500, lp: 2800, grit: 55 },
    am:         { hp: 200, lp: 4800, grit: 12 },
    megaphone:  { hp: 500, lp: 4000, grit: 70 },
    underwater: { hp: 40,  lp: 500,  grit: 0 },
  };

  function fxCurve(amount) {
    const n = 512, curve = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const x = (i * 2) / n - 1;
      curve[i] = ((3 + amount) * x * 20 * (Math.PI / 180))
        / (Math.PI + amount * Math.abs(x));
    }
    return curve;
  }

  function playPcmWithEffect(b64, sampleRate, kind) {
    // Same interpolation the caller's browser applies — see fxSpec() in
    // call.js. A preview at a different intensity than the call is a lie.
    let spec = FX_PREVIEW[kind];
    if (spec) {
      const el = $('voice_effect_level');
      const lvl = el && el.value !== '' ? +el.value
        : (resolved.voice_effect_level == null ? 100
           : +resolved.voice_effect_level);
      const t = Math.max(0, Math.min(100, lvl)) / 100;
      spec = t <= 0 ? null : {
        hp: spec.hp * t,
        lp: spec.lp + (16000 - spec.lp) * (1 - t),
        grit: Math.round(spec.grit * t),
      };
    }
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const pcm = new Int16Array(bytes.buffer);
    const c = ctx();
    const buf = c.createBuffer(1, pcm.length, sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
    const src = c.createBufferSource();
    src.buffer = buf;
    let node = src;
    if (spec) {
      const hp = c.createBiquadFilter();
      hp.type = 'highpass'; hp.frequency.value = spec.hp;
      const lp = c.createBiquadFilter();
      lp.type = 'lowpass'; lp.frequency.value = spec.lp;
      const chain = [hp, lp];
      if (spec.grit) {
        const sh = c.createWaveShaper();
        sh.curve = fxCurve(spec.grit); sh.oversample = '2x';
        chain.push(sh);
      }
      chain.forEach((n) => { node.connect(n); node = n; });
    }
    node.connect(c.destination);
    src.start();
  }

  if ($('fxTestBtn')) {
    // The test can borrow ANY DJ's voice: the per-persona voices arrive
    // with the voicemail status, fetched when the section opens.
    const fxSec = document.querySelector('details.sec[data-group="effects"]');
    if (fxSec) fxSec.addEventListener('toggle', async () => {
      if (!fxSec.open || !$('fxVoice') || $('fxVoice').options.length > 1) return;
      await loadVmStatus();
      const pick = $('fxVoice');
      vmPersonas.filter((per) => per.voice && per.id !== '_station')
        .forEach((per) => {
          const o = document.createElement('option');
          o.value = per.voice;
          o.textContent = per.name + ' \u2014 ' + per.voice;
          pick.appendChild(o);
        });
    });

    const runFxTest = async (kind, btn) => {
      const out = $('ttsResult');
      btn.disabled = true;
      out.className = 'result on';
      out.textContent = 'Rendering one line, then playing it through the '
        + (kind === 'none' ? 'clean path' : kind + ' effect') + '\u2026';
      try {
        const body = draft();
        const voice = $('fxVoice') && $('fxVoice').value;
        if (voice) body.tts_voice = voice;
        const r = await afetch('/test/tts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || !d.pcmBase64) {
          showResult(out, false, d.error || 'The voice test failed.');
          return;
        }
        playPcmWithEffect(d.pcmBase64, d.sampleRate || 24000, kind);
        showResult(out, true, kind === 'none'
          ? 'Playing clean \u2014 the same line the effect button colours.'
          : 'Playing through the ' + kind + ' effect. The broadcast never '
            + 'hears this; only callers do.');
      } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
      finally { btn.disabled = false; }
    };
    if ($('fxCleanBtn')) {
      $('fxCleanBtn').onclick = () => runFxTest('none', $('fxCleanBtn'));
    }
    $('fxTestBtn').onclick = async () => {
      const btn = $('fxTestBtn'), out = $('ttsResult');
      const kind = $('voice_effect').value || resolved.voice_effect || 'none';
      return runFxTest(kind, btn);
    };
  }

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

  // vm_beep is server-played (the worker beeps into the room), so its
  // dropdown offers uploads only — a URL would have no browser to play it.
  const SOUND_SLOTS = ['ring', 'pickup', 'hold', 'hangup', 'failed', 'vm_beep'];

  function paintSounds() {
    // The shelf itself is the sound BOARD now (paintSoundBoard) — bundled
    // clips and uploads in one table. This keeps only the hint and the
    // dropdowns in step.
    if ($('uploadHint')) {
      $('uploadHint').textContent = uploaded.length
        ? '' : 'Nothing uploaded yet — the built-in shelf is below.';
    }
    syncSoundPickers();
  }

  // ------------------------------------------- the per-sound dropdowns
  // Each sound slot used to be a free-text box whose useful values ("",
  // "upload:name.mp3", a URL) all had to be known in advance. The text field
  // is still the real setting — Save diffs it and the server stores it — but
  // it is driven by a dropdown now, the same trick the permission matrix
  // uses: sound-set default, any uploaded file, or "a URL you host", which is
  // the one case that reveals the box itself.
  function decorateSoundRows() {
    SOUND_SLOTS.forEach((slot) => {
      const field = $('sound_' + slot);
      const row = field && field.closest('.row');
      if (!row || row.dataset.soundBuilt) return;
      row.dataset.soundBuilt = '1';

      const pick = document.createElement('select');
      pick.id = 'soundpick_' + slot;
      pick.onchange = () => {
        if (pick.value === '__url__') {
          if (field.value.startsWith(UPLOAD_PREFIX)) field.value = '';
          field.hidden = false;
          field.focus();
        } else {
          field.value = pick.value;
          field.hidden = true;
        }
        markClean();
      };
      row.insertBefore(pick, field);

      const up = document.createElement('button');
      up.className = 'btnquiet'; up.textContent = 'Upload…';
      up.title = 'Upload a file and use it for this sound';
      up.onclick = () => { pendingAssignSlot = slot; $('soundFile').click(); };
      // Beside the dropdown it serves, BEFORE the row's help — appended at
      // the end it landed after the injected hint and read as furniture for
      // the wrong row. Operator-reported as "not intuitive", correctly.
      row.insertBefore(up, field.nextSibling);
    });
    // The default labels name the selected set, so they go stale the moment
    // the operator picks the other one.
    if ($('sound_pack') && !$('sound_pack').dataset.syncBound) {
      $('sound_pack').dataset.syncBound = '1';
      $('sound_pack').addEventListener('change', syncSoundPickers);
    }
    syncSoundPickers();
  }

  function syncSoundPickers() {
    // "Sound set default" answered the wrong question — the operator asked
    // WHICH sound that is (Exchange? Handset?). Name the set that is
    // actually selected, and for the beep — which no set carries; the
    // default is synthesized by the server — say exactly that.
    const packSel = $('sound_pack');
    const packName = packSel && packSel.selectedIndex >= 0
      ? packSel.options[packSel.selectedIndex].textContent.split('—')[0].trim()
      : 'sound set';
    SOUND_SLOTS.forEach((slot) => {
      const field = $('sound_' + slot), pick = $('soundpick_' + slot);
      if (!field || !pick) return;
      const value = (field.value || '').trim();
      pick.innerHTML = '';
      const add = (v, label) => {
        const o = document.createElement('option');
        o.value = v; o.textContent = label;
        pick.appendChild(o);
      };
      add('', slot === 'vm_beep'
        ? 'Classic tone — synthesized (default)'
        : 'Default — the ' + packName + ' set’s ' + slot.replace('_', ' '));
      // The beep is server-played and the server reads WAV only — offering
      // an m4a here is offering a file that will silently become the tone.
      // (m4p is Apple-DRM'd audio: nothing outside iTunes can play it.)
      soundLibrary.forEach((e) => {
        add(e.url, 'Built-in — ' + (e.label || e.name)
          + (e.secs ? ' (' + e.secs + 's)' : ''));
      });
      const eligible = slot === 'vm_beep'
        ? uploaded.filter((n) => /\.wav$/i.test(n)) : uploaded;
      eligible.forEach((n) => add(UPLOAD_PREFIX + n, 'Uploaded — ' + n));
      // A slot pointing at a file that was deleted must say so, not silently
      // show the default while the caller hears the fallback.
      if (value.startsWith(UPLOAD_PREFIX)
          && !uploaded.includes(value.slice(UPLOAD_PREFIX.length))) {
        add(value, 'Missing upload — ' + value.slice(UPLOAD_PREFIX.length));
      }
      add('__url__', 'A URL you host…');
      const isUrl = value && !value.startsWith(UPLOAD_PREFIX);
      pick.value = isUrl ? '__url__' : value;
      field.hidden = !isUrl;
    });
  }

  let soundLibrary = [];
  let uploadMeta = [];

  async function loadSounds() {
    // Bundled packs need no auth and are useful even if the upload list
    // fails, so they load independently.
    loadPackAssets();
    try {
      const r = await afetch('/settings/sounds');
      if (!r.ok) return;
      const d = await r.json();
      uploaded = d.sounds || [];
      soundLibrary = d.library || [];
      uploadMeta = d.uploads || [];
      paintSounds();
      paintSoundBoard();
    } catch (e) { /* the built-ins still work */ }
  }

  // The whole shelf as one table — bundled clips and uploads together,
  // playable, timed, and filed under an editable category: the operator's
  // own taxonomy, which is what makes it a soft sound pack.
  function paintSoundBoard() {
    const board = $('soundBoard'), body = $('soundBoardBody');
    if (!board || !body) return;
    const rows = soundLibrary.map((e) => ({ ...e, builtin: true }))
      .concat(uploadMeta.map((e) => ({ ...e, builtin: false })));
    board.hidden = !rows.length;
    body.innerHTML = '';
    const mmss = (secs) => secs == null ? '—'
      : Math.floor(secs / 60) + ':' + String(Math.round(secs % 60)).padStart(2, '0');
    rows.forEach((e) => {
      const tr = document.createElement('tr');
      const name = document.createElement('td');
      name.textContent = (e.label || e.name)
        + (e.builtin ? '' : ' (upload)');
      const len = document.createElement('td');
      len.textContent = mmss(e.secs);
      const cat = document.createElement('td');
      const catIn = document.createElement('input');
      catIn.type = 'text';
      catIn.value = e.category || '';
      catIn.className = 'catbox';
      catIn.onchange = async () => {
        await afetch('/settings/sounds/meta', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: e.name, category: catIn.value }),
        });
      };
      cat.appendChild(catIn);
      const act = document.createElement('td');
      const play = document.createElement('button');
      play.className = 'btnquiet'; play.textContent = 'Play';
      play.onclick = () => { new Audio(e.url).play().catch(() => {}); };
      act.appendChild(play);
      if (!e.builtin) {
        const del = document.createElement('button');
        del.className = 'btnquiet'; del.textContent = 'Remove';
        del.onclick = async () => {
          await afetch('/settings/sounds/' + encodeURIComponent(e.name),
                       { method: 'DELETE' });
          loadSounds();
        };
        act.appendChild(del);
      }
      tr.append(name, len, cat, act);
      body.appendChild(tr);
    });
  }

  // Set by a slot's own Upload… button, so the file lands assigned to the
  // sound it was uploaded for instead of arriving on a shelf to be wired up
  // by hand. The plain "Upload a sound…" button leaves it null.
  let pendingAssignSlot = null;

  if ($('uploadSoundBtn')) {
    $('uploadSoundBtn').onclick = () => { pendingAssignSlot = null; $('soundFile').click(); };
    $('soundFile').onchange = async () => {
      const file = $('soundFile').files[0];
      const slot = pendingAssignSlot;
      pendingAssignSlot = null;
      if (!file) return;
      const out = $('soundResult');
      // The beep is server-played and the server reads WAV only — but the
      // BROWSER ships mp3/m4a decoders, so instead of refusing (0.9.138 did,
      // and the operator reasonably asked for a converter) the panel decodes
      // and re-wraps as WAV before anything travels. Zero server footprint.
      // A real WAV still skips all of this: nothing decoded, nothing lost.
      let toSend = file;
      if (slot === 'vm_beep' && !/\.wav$/i.test(file.name)) {
        out.className = 'result on';
        out.textContent = 'Converting ' + file.name + ' to WAV\u2026';
        try {
          toSend = await convertToWav(file);
        } catch (e) {
          showResult(out, false, file.name + ' could not be decoded ('
            + e.message + '). Re-export it as a plain WAV — m4p in '
            + 'particular is DRM\u2019d and nothing can convert it.');
          $('soundFile').value = '';
          return;
        }
      }
      out.className = 'result on';
      out.textContent = 'Uploading ' + toSend.name
        + (toSend === file ? '' : ' (converted from ' + file.name
           + ' — a WAV upload skips this)') + '…';
      const form = new FormData();
      form.append('file', toSend, toSend.name);
      try {
        const r = await afetch('/settings/sounds', { method: 'POST', body: form });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { showResult(out, false, d.error || 'Upload failed'); return; }
        uploaded = d.sounds || [];
        if (slot) {
          $('sound_' + slot).value = d.value || (UPLOAD_PREFIX + d.name);
          markClean();
        }
        paintSounds();
        showResult(out, true, slot
          ? d.name + ' uploaded and set as the ' + slot + ' sound — press Save '
            + 'to apply it to the next caller.'
          : d.name + " uploaded. Pick it from a sound's dropdown, then Save.");
      } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
      finally { $('soundFile').value = ''; }
    };
  }
  // ------------------------------------------------------------ voicemail
  // Staging renders one greeting per persona through the real TTS and
  // reports each result by name — a persona whose voice this backend does
  // not have has to be pointed at, not averaged away.
  let vmPersonas = [];

  function paintVmStatus(personas) {
    vmPersonas = personas || [];
    const host = $('vmStatusList');
    if (!host) return;
    host.innerHTML = '';
    vmPersonas.forEach((p) => {
      const li = document.createElement('li');
      li.className = 'vmrow';
      li.dataset.pid = p.id;

      const who = document.createElement('span');
      who.className = 'sname';
      who.textContent = p.name + ' — ' + (p.voice || 'station voice');
      who.title = 'Rendered with this voice';

      const state = document.createElement('span');
      state.className = 'vmstate' + (p.current ? ' ok' : '');
      state.textContent = p.current ? 'staged'
        : p.staged ? 'stale' : 'not staged';
      state.title = p.renderedAt ? 'Rendered ' + p.renderedAt : '';

      // The exact words this persona's clip speaks, editable in place. An
      // edit is saved per persona and invalidates only that clip.
      const line = document.createElement('input');
      line.type = 'text';
      line.className = 'vmline';
      line.value = p.text || '';
      line.title = p.overridden ? 'This persona has its own line'
                                : 'Shared greeting — edit to give ' + p.name
                                  + ' their own';
      line.onchange = async () => {
        await afetch('/voicemail/greeting/' + encodeURIComponent(p.id), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: line.value }),
        });
        loadVmStatus();
      };

      const stage = document.createElement('button');
      stage.className = 'btnquiet'; stage.textContent = 'Stage';
      stage.onclick = () => stagePersonas([p.id], stage);

      const play = document.createElement('button');
      play.className = 'btnquiet'; play.textContent = 'Play';
      play.disabled = !p.staged;
      play.onclick = () => {
        // Admin-gated audio, so it travels with the header via fetch.
        afetch('/voicemail/greeting/' + encodeURIComponent(p.id))
          .then((r) => r.blob())
          .then((blob) => new Audio(URL.createObjectURL(blob)).play())
          .catch(() => {});
      };

      const del = document.createElement('button');
      del.className = 'btnquiet'; del.textContent = 'Delete';
      del.disabled = !p.staged;
      del.onclick = async () => {
        del.disabled = true;
        await afetch('/voicemail/greeting/' + encodeURIComponent(p.id),
                     { method: 'DELETE' });
        loadVmStatus();
      };

      li.append(who, state, line, stage, play, del);
      host.appendChild(li);
    });
  }

  // One persona at a time, so the operator watches it happen instead of
  // wondering whether the button took — which is exactly what they reported.
  async function stagePersonas(ids, btn) {
    const out = $('vmStageResult');
    const label = btn.textContent;
    btn.disabled = true;
    out.className = 'result on';
    const lines = [];
    try {
      for (let i = 0; i < ids.length; i++) {
        btn.textContent = 'Rendering ' + (i + 1) + '/' + ids.length + '…';
        out.textContent = lines.concat('… rendering '
          + (vmPersonas.find((p) => p.id === ids[i]) || {}).name).join('\n');
        const r = await afetch('/voicemail/stage?persona='
          + encodeURIComponent(ids[i]), { method: 'POST' });
        const d = await r.json().catch(() => ({}));
        const res = ((d.results || [])[0]) || { ok: false, error: 'no answer' };
        lines.push((res.ok ? '✓ ' : '✗ ') + (res.name || ids[i])
          + (res.skipped ? ' — unchanged, skipped'
             : res.ok ? ' — rendered' : ' — ' + (res.error || 'failed')));
        out.textContent = lines.join('\n');
      }
    } finally {
      btn.disabled = false;
      btn.textContent = label;
      out.classList.add('on');
      loadVmStatus();
    }
  }

  async function loadVmStatus() {
    try {
      const r = await afetch('/voicemail/status');
      if (!r.ok) return;
      const d = await r.json();
      paintVmStatus(d.personas);
      // The custom beep's verdict, tried server-side — the worker plays it
      // at pickup and fails to the tone SILENTLY, so without this line the
      // setting just looks ignored when the file can't play.
      const beep = d.beep || {};
      const note = $('vmBeepNote');
      if (note) {
        note.hidden = !beep.set;
        note.style.color = beep.set && !beep.ok ? 'var(--coral)' : '';
        if (beep.set) {
          note.textContent = beep.ok
            ? 'Custom beep ' + beep.name + ' converts and will play at '
              + 'pickup. If callers still hear the classic tone, the WORKER '
              + 'container is running an older version — pull and restart '
              + 'both.'
            : 'Custom beep ' + beep.name + ' cannot play (' 
              + (beep.error || 'unreadable')
              + ') — callers get the classic tone. Re-export it as a plain '
              + 'PCM WAV and upload again.';
        }
      }
      const staged = (d.personas || []).filter((p) => p.current).length;
      setTag('tagVoicemail',
        (!resolved.voicemail_enabled ? 'off' : resolved.voicemail_when)
        + ' · ' + staged + '/' + (d.personas || []).length + ' staged'
        + (d.messages ? ' · ' + d.messages + ' msg' : ''));
    } catch (e) { /* the section still works without the station */ }
  }

  if ($('vmStageBtn')) {
    const vmSec = document.querySelector('details.sec[data-group="voicemail"]');
    if (vmSec) vmSec.addEventListener('toggle', () => {
      if (vmSec.open) loadVmStatus();
    });
    // The beep verdict is painted by loadVmStatus but DISPLAYED in Call
    // sounds (the operator found it baffling inside Voicemail, beside
    // staging errors it had nothing to do with) — so opening the sounds
    // section fetches it too.
    const sndSec = document.querySelector('details.sec[data-group="sounds"]');
    if (sndSec) sndSec.addEventListener('toggle', () => {
      if (sndSec.open) loadVmStatus();
    });

    $('vmStageBtn').onclick = async () => {
      if (!vmPersonas.length) await loadVmStatus();
      if (!vmPersonas.length) {
        showResult($('vmStageResult'), false,
          'No personas found — is the station reachable?');
        return;
      }
      stagePersonas(vmPersonas.map((p) => p.id), $('vmStageBtn'));
    };

    $('vmRefreshBtn').onclick = async () => {
      const out = $('vmMessages');
      out.className = 'result on'; out.textContent = 'Loading…';
      try {
        const r = await afetch('/voicemail/messages');
        const d = await r.json().catch(() => ({}));
        const msgs = d.messages || [];
        out.textContent = msgs.length
          ? msgs.slice().reverse().map((m) =>
              m.at + '  [' + (m.delivered || 'hold') + ']  '
              + (m.dj ? m.dj + ' · ' : '') + m.text
              + (m.note ? '\n         ' + m.note : '')).join('\n')
          : 'No messages.';
      } catch (e) { out.textContent = 'Failed: ' + e.message; }
    };

    $('vmClearBtn').onclick = async () => {
      await afetch('/voicemail/messages', { method: 'DELETE' });
      $('vmMessages').textContent = 'Cleared.';
      loadVmStatus();
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

  // The beep is not in the browser sound engine — it is the server's own
  // sound — so its preview mirrors what the server will do: the uploaded
  // WAV when one is set and playable in a browser, else the shaped tone.
  // Decode whatever the browser can play and wrap it as 16-bit PCM WAV.
  // Mono at the source's own rate: the worker downmixes and resamples for
  // itself, so this stays a container change, not a quality decision.
  async function convertToWav(file) {
    const buf = await file.arrayBuffer();
    const audio = await ctx().decodeAudioData(buf);
    const ch = audio.numberOfChannels > 1
      ? (() => {
          const a = audio.getChannelData(0), b = audio.getChannelData(1);
          const mix = new Float32Array(a.length);
          for (let i = 0; i < a.length; i++) mix[i] = (a[i] + b[i]) / 2;
          return mix;
        })()
      : audio.getChannelData(0);
    const out = new DataView(new ArrayBuffer(44 + ch.length * 2));
    const str = (o, t) => { for (let i = 0; i < t.length; i++) out.setUint8(o + i, t.charCodeAt(i)); };
    str(0, 'RIFF'); out.setUint32(4, 36 + ch.length * 2, true); str(8, 'WAVE');
    str(12, 'fmt '); out.setUint32(16, 16, true); out.setUint16(20, 1, true);
    out.setUint16(22, 1, true); out.setUint32(24, audio.sampleRate, true);
    out.setUint32(28, audio.sampleRate * 2, true); out.setUint16(32, 2, true);
    out.setUint16(34, 16, true);
    str(36, 'data'); out.setUint32(40, ch.length * 2, true);
    for (let i = 0; i < ch.length; i++) {
      const v = Math.max(-1, Math.min(1, ch[i]));
      out.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
    }
    const name = file.name.replace(/\.[a-z0-9]+$/i, '') + '.wav';
    return new File([out.buffer], name, { type: 'audio/wav' });
  }

  function previewBeep() {
    const out = $('soundResult');
    out.className = 'result on';
    const raw = ($('sound_vm_beep').value || '').trim();
    if (raw.startsWith(UPLOAD_PREFIX)) {
      const name = raw.slice(UPLOAD_PREFIX.length);
      new Audio('/sounds/' + encodeURIComponent(name)).play().catch(() => {});
      out.textContent = 'Playing your beep: ' + name + '. The worker plays '
        + 'this at pickup — the Voicemail section reports whether it '
        + 'converts.';
      return;
    }
    const c = ctx();
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.frequency.value = 1000;
    gain.gain.setValueAtTime(0.0001, c.currentTime);
    gain.gain.linearRampToValueAtTime(0.25, c.currentTime + 0.015);
    gain.gain.setValueAtTime(0.25, c.currentTime + 0.38);
    gain.gain.linearRampToValueAtTime(0.0001, c.currentTime + 0.4);
    osc.connect(gain).connect(c.destination);
    osc.start();
    osc.stop(c.currentTime + 0.42);
    out.textContent = 'Playing the classic tone — the synthesized default.';
  }
  $('testBeepBtn').onclick = previewBeep;

  $('testRingBtn').onclick = () => previewSound('ring');
  $('testPickupBtn').onclick = () => previewSound('pickup');
  $('testHoldBtn').onclick = () => previewSound('hold');
  $('testHangupBtn').onclick = () => previewSound('hangup');
  $('testFailedBtn').onclick = () => previewSound('failed');

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

  $('saveOverlaySave').onclick = () => $('saveBtn').click();
  $('saveOverlayDiscard').onclick = () => {
    // Back to what is stored: paint() refills every control from
    // overrides/resolved, which IS the discard.
    paint();
    $('saveMsg').textContent = 'Changes discarded';
    setTimeout(() => { $('saveMsg').textContent = ''; }, 2500);
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
