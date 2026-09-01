/* The sound board: the six slot cards, the shelf of uploads and bundled
   clips, the previews, and the WAV wrapper the beep upload needs.

   Split out of panel.js at 0.10.85 — the seam recorded by the file-length
   ratchet (TestNoFileGrowsWithoutSomebodyDeciding) since 0.10.79: this
   cluster was nearly closed, owing the rest of the panel three names and
   needing five back. Same shape as panel-viewers.js and panel-charts.js:
   no build step, one IIFE, reading window.Panel (published by panel.js —
   the script order in panel.html is load-bearing) and publishing its own
   three names back as Panel.sounds for panel.js's call sites. */
(function () {
  const { $, ctx, playSound, setSounds } = window.Callin;
  const { afetch, showResult, markClean, getLive, schemaFields } = window.Panel;

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
    setTimeout(() => setSounds(getLive() && getLive().sounds), 1500);
  }

  // ------------------------------------------------------------- uploads
  // Somewhere to put your own ring without hosting a file yourself.
  const UPLOAD_PREFIX = 'upload:';
  let uploaded = [];

  // vm_beep is server-played (the worker beeps into the room), so its
  // dropdown offers uploads only — a URL would have no browser to play it.
  const SOUND_SLOTS = ['ring', 'pickup', 'hold', 'hangup', 'failed', 'vm_beep'];

  function paintSounds() {
    paintSlotCards();
  }

  // ------------------------------------------------- the six slot cards
  // Each call moment is a CARD: what plays now, ▶ to hear it, press to
  // change it. The hidden sound_X inputs stay the real settings — Save
  // diffs them, the server stores them — the same contract the dashboard's
  // cards keep with their checkboxes. This replaced six dropdown rows over
  // a shelf that could not assign anything: eighteen controls, and the one
  // gesture people reach for (see a sound, give it a job) missing.
  const SLOT_NAMES = { ring: 'Ring', pickup: 'Pick up', hold: 'On hold',
                       hangup: 'Hang up', failed: "Can't connect",
                       vm_beep: 'Voicemail beep' };

  function packName() {
    const sel = $('sound_pack');
    return sel && sel.selectedIndex >= 0
      ? sel.options[sel.selectedIndex].textContent.split('—')[0].trim()
      : 'sound set';
  }

  // What a slot's stored value amounts to — the card and the shelf's
  // used-for chips both read this, so they cannot disagree.
  function describePick(slot) {
    const field = $('sound_' + slot);
    const value = ((field && field.value) || '').trim();
    if (!value) {
      return { text: slot === 'vm_beep' ? 'Classic tone'
                                        : packName() + ' default',
               kind: 'default' };
    }
    if (value.startsWith(UPLOAD_PREFIX)) {
      const name = value.slice(UPLOAD_PREFIX.length);
      if (!uploaded.includes(name)) return { text: 'Missing — ' + name, kind: 'missing' };
      return { text: name, kind: 'upload' };
    }
    const lib = soundLibrary.find((e) => e.url === value);
    if (lib) return { text: lib.label || lib.name, kind: 'library' };
    return { text: value.replace(/^https?:\/\//, ''), kind: 'url' };
  }

  function buildSlotCards() {
    const grid = $('slotGrid');
    if (!grid || grid.dataset.built) return;
    grid.dataset.built = '1';
    SOUND_SLOTS.forEach((slot) => {
      const wrap = document.createElement('div');
      wrap.className = 'slotwrap';
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'slotcard';
      card.id = 'slot_' + slot;
      card.dataset.slot = slot;
      card.innerHTML =
        '<span class="ck"></span><span class="cv"></span><span class="cn"></span>';
      card.querySelector('.ck').textContent = SLOT_NAMES[slot];
      card.onclick = () => openSlotMenu(slot, card);
      // ▶ is a SIBLING, not a child — a button cannot nest a button, and
      // hearing the current pick must not open the picker.
      const play = document.createElement('button');
      play.type = 'button';
      play.className = 'slotplay';
      play.title = 'Play the current pick';
      play.textContent = '▶';
      play.onclick = (ev) => {
        ev.stopPropagation();
        if (slot === 'vm_beep') previewBeep(); else previewSound(slot);
      };
      wrap.append(card, play);
      grid.appendChild(wrap);
    });
    paintSlotCards();
  }

  function paintSlotCards() {
    if (!$('slotGrid') || !$('slotGrid').dataset.built) return;
    SOUND_SLOTS.forEach((slot) => {
      const card = $('slot_' + slot), field = $('sound_' + slot);
      if (!card || !field) return;
      const pick = describePick(slot);
      card.querySelector('.cv').textContent = pick.text;
      card.classList.toggle('missing', pick.kind === 'missing');
      card.querySelector('.cn').textContent =
        pick.kind === 'default' ? 'press to change'
        : pick.kind === 'upload' ? 'uploaded file'
        : pick.kind === 'library' ? 'built-in clip'
        : pick.kind === 'url' ? 'a URL you host'
        : 'press to fix — callers hear the default';
      const meta = schemaFields()['sound_' + slot];
      if (meta && meta.help) card.title = meta.help;
      // The slot's URL row surfaces only while the slot points at a URL —
      // and never hides under a focused cursor mid-edit.
      const row = field.closest('.sloturl');
      if (row && document.activeElement !== field) {
        row.hidden = pick.kind !== 'url';
      }
    });
    paintSoundBoard();          // the used-for chips follow the cards
  }

  // One floating picker, rebuilt under whichever card was pressed.
  function closeSlotMenu() {
    const m = $('slotMenu');
    if (!m) return;
    m.hidden = true;
    m.innerHTML = '';
    m.dataset.slot = '';
  }

  function openSlotMenu(slot, anchor) {
    const m = $('slotMenu');
    if (!m) return;
    if (!m.hidden && m.dataset.slot === slot) { closeSlotMenu(); return; }
    m.innerHTML = '';
    m.dataset.slot = slot;
    const field = $('sound_' + slot);
    const value = (field.value || '').trim();
    const choose = (v) => {
      field.value = v;
      markClean();
      closeSlotMenu();
      paintSlotCards();
    };
    const add = (label, fn, current) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      if (current) b.className = 'current';
      b.onclick = fn;
      m.appendChild(b);
    };
    add(slot === 'vm_beep'
      ? 'Classic tone — synthesized (default)'
      : 'Default — the ' + packName() + ' set’s ' + slot.replace('_', ' '),
      () => choose(''), !value);
    soundLibrary.forEach((e) => {
      add('Built-in — ' + (e.label || e.name)
        + (e.secs ? ' (' + e.secs + 's)' : ''),
        () => choose(e.url), value === e.url);
    });
    // The beep is server-played and the server reads WAV only — offering
    // an m4a here is offering a file that silently becomes the tone.
    const eligible = slot === 'vm_beep'
      ? uploaded.filter((n) => /\.wav$/i.test(n)) : uploaded;
    eligible.forEach((n) => {
      add('Uploaded — ' + n, () => choose(UPLOAD_PREFIX + n),
          value === UPLOAD_PREFIX + n);
    });
    // A slot pointing at a deleted file must say so, not silently show the
    // default while the caller hears the fallback.
    if (value.startsWith(UPLOAD_PREFIX)
        && !uploaded.includes(value.slice(UPLOAD_PREFIX.length))) {
      add('Missing upload — ' + value.slice(UPLOAD_PREFIX.length), () => {});
    }
    // No URL for the beep: the worker plays it, and a URL it cannot fetch
    // would silently become the tone — the trap the old dropdown offered.
    if (slot !== 'vm_beep') {
      add('A URL you host…', () => {
        if (field.value.startsWith(UPLOAD_PREFIX)) field.value = '';
        closeSlotMenu();
        const row = field.closest('.sloturl');
        if (row) row.hidden = false;
        field.focus();
      }, !!value && !value.startsWith(UPLOAD_PREFIX)
         && !soundLibrary.some((e) => e.url === value));
    }
    add('Upload a file for this…', () => {
      pendingAssignSlot = slot;
      closeSlotMenu();
      $('soundFile').click();
    });
    // Under the pressed card, inside the slot area's own coordinates.
    const area = anchor.closest('.slotarea');
    const ar = anchor.getBoundingClientRect(), gr = area.getBoundingClientRect();
    m.hidden = false;
    m.style.left = Math.max(0, ar.left - gr.left) + 'px';
    m.style.top = (ar.bottom - gr.top + 4) + 'px';
  }

  document.addEventListener('click', (ev) => {
    const m = $('slotMenu');
    if (m && !m.hidden && !m.contains(ev.target)
        && !ev.target.closest('.slotcard')) closeSlotMenu();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') closeSlotMenu();
  });

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
  // playable, timed, sortable, and each row able to say where it is USED
  // and to take a job (Use for…). Assignment flows from the sound as well
  // as from the slot: seeing a clip and giving it a job was the gesture
  // the old two-halves layout could not make at all.
  let shelfSort = { key: '', dir: 1 };

  function slotUses(entry) {
    const target = entry.builtin ? entry.url : UPLOAD_PREFIX + entry.name;
    return SOUND_SLOTS.filter((slot) => {
      const f = $('sound_' + slot);
      return f && f.value.trim() === target;
    });
  }

  // What the currently pickable SET DEFAULTS are, as shelf rows — the
  // operator looked for them in the list and they were nowhere: five
  // synthesized sounds per set plus the machine's classic tone, playable
  // right here. Not assignable from the shelf (a default IS the empty
  // slot), so they carry Play and nothing else.
  function defaultRows() {
    const sel = $('sound_pack');
    if (!sel) return [];
    const packs = [...sel.options].map((o) => ({
      id: o.value || 'classic',
      label: (o.textContent || o.value).split('—')[0].trim(),
    }));
    const rows = [];
    packs.forEach((p) => {
      ['ring', 'pickup', 'hold', 'hangup', 'failed'].forEach((kind) => {
        rows.push({
          name: 'default:' + p.id + ':' + kind,
          label: p.label + ' — ' + SLOT_NAMES[kind].toLowerCase(),
          category: 'set default', pack: p.label, secs: null,
          isDefault: true, packId: p.id, kind, suggests: kind,
          url: (packAssets[p.id] || {})[kind] || '',
        });
      });
    });
    rows.push({
      name: 'default:vm_beep',
      label: 'Classic tone — voicemail beep',
      category: 'set default', pack: '', secs: null,
      isDefault: true, kind: 'vm_beep', suggests: 'vm_beep', url: '',
    });
    return rows;
  }

  let shelfPage = 0;
  const SHELF_PAGE_SIZE = 12;

  function paintSoundBoard() {
    const board = $('soundBoard'), body = $('soundBoardBody');
    if (!board || !body) return;
    // Real clips lead, defaults trail: the rows you can DO something with
    // should not sit behind a page of read-only set defaults.
    let rows = uploadMeta.map((e) => ({ ...e, builtin: false }))
      .concat(soundLibrary.map((e) => ({ ...e, builtin: true })))
      .concat(defaultRows());
    rows.forEach((e) => {
      if (e.isDefault) {
        // A default is "in use" for its own slot while that slot is empty
        // and its set is the chosen one (the beep's default needs no set).
        const chosen = ($('sound_pack') && $('sound_pack').value) || 'classic';
        const field = $('sound_' + e.kind);
        e.used = (field && !field.value.trim()
          && (e.kind === 'vm_beep' || e.packId === chosen)) ? [e.kind] : [];
      } else {
        e.used = slotUses(e);
      }
    });
    // The category pick offers whatever the shelf actually holds — built
    // from the UNFILTERED rows, or picking a category would empty its own
    // list of alternatives.
    const catSel = $('shelfCat');
    if (catSel) {
      const cats = [...new Set(rows.map((e) => e.category).filter(Boolean))].sort();
      const keep = catSel.value;
      // "(no category)" is a grouping too (operator's ask): a sound nobody
      // has filed yet must still be findable as a group, or the uncategorised
      // half of the shelf can only be found by knowing names.
      catSel.innerHTML = '<option value="">All categories</option>'
        + '<option value="~none">No category yet</option>';
      cats.forEach((c) => {
        const o = document.createElement('option');
        o.value = c; o.textContent = c;
        catSel.appendChild(o);
      });
      catSel.value = (cats.includes(keep) || keep === '~none') ? keep : '';
    }
    const needle = (($('shelfSearch') && $('shelfSearch').value) || '')
      .trim().toLowerCase();
    const wantCat = (catSel && catSel.value) || '';
    if (needle) {
      rows = rows.filter((e) =>
        [e.label, e.name, e.category, e.pack].join(' ')
          .toLowerCase().includes(needle));
    }
    if (wantCat) {
      rows = rows.filter((e) => (wantCat === '~none'
        ? !e.category : e.category === wantCat));
    }
    const wantType = ($('shelfType') && $('shelfType').value) || '';
    if (wantType) {
      // "~none" groups the sounds serving no slot at all — no assignment, no
      // declared type, not a set's own default.
      rows = rows.filter((e) => (wantType === '~none'
        ? !(e.used.length || e.suggests || (e.isDefault && e.kind))
        : (e.used.indexOf(wantType) !== -1
           || e.suggests === wantType
           || (e.isDefault && e.kind === wantType))));
    }
    if (shelfSort.key) {
      // The type column sorts by the TYPE — assignment, else declared type,
      // else a default's own kind — so rings group with rings. Sorting by
      // the count of assignments put one used clip on top and shuffled the
      // rest, which read as the column not sorting at all.
      const typeOf = (e) => SLOT_NAMES[e.used[0] || e.suggests
        || (e.isDefault ? e.kind : '')] || '~';
      const keyOf = (e) =>
        shelfSort.key === 'secs' ? (e.secs || 0)
        : shelfSort.key === 'used' ? typeOf(e)
        : shelfSort.key === 'category' ? String(e.category || '').toLowerCase()
        : String(e.label || e.name).toLowerCase();
      rows.sort((a, b) => {
        const av = keyOf(a), bv = keyOf(b);
        return (av < bv ? -1 : av > bv ? 1 : 0) * shelfSort.dir;
      });
    }
    board.querySelectorAll('th.sortable').forEach((th) => {
      th.classList.toggle('asc', shelfSort.key === th.dataset.sort && shelfSort.dir === 1);
      th.classList.toggle('desc', shelfSort.key === th.dataset.sort && shelfSort.dir === -1);
    });
    board.hidden = !rows.length;
    // Twelve to a page: with the defaults listed and four packs shipped the
    // shelf passed thirty rows, and a table that long stops being a glance.
    const pages = Math.max(1, Math.ceil(rows.length / SHELF_PAGE_SIZE));
    shelfPage = Math.min(shelfPage, pages - 1);
    const pager = $('shelfPager');
    if (pager) {
      pager.hidden = pages < 2;
      const from = shelfPage * SHELF_PAGE_SIZE + 1;
      const to = Math.min(rows.length, from + SHELF_PAGE_SIZE - 1);
      pager.querySelector('.pcount').textContent =
        from + '–' + to + ' of ' + rows.length;
      pager.querySelector('.pprev').disabled = shelfPage === 0;
      pager.querySelector('.pnext').disabled = shelfPage >= pages - 1;
    }
    rows = rows.slice(shelfPage * SHELF_PAGE_SIZE,
                      (shelfPage + 1) * SHELF_PAGE_SIZE);
    body.innerHTML = '';
    const mmss = (secs) => secs == null ? '—'
      : Math.floor(secs / 60) + ':' + String(Math.round(secs % 60)).padStart(2, '0');
    rows.forEach((e) => {
      const tr = document.createElement('tr');
      const name = document.createElement('td');
      name.textContent = e.label || e.name;
      // One chip, the most specific true thing: the pack a clip ships in,
      // else which side of the shelf it came from. A pack chip AND a
      // built-in chip on every row was the duplication it read as.
      const kind = document.createElement('span');
      kind.className = 'kindchip';
      kind.textContent = e.isDefault ? 'default'
        : (e.pack || (e.builtin ? 'built-in' : 'upload'));
      if (e.pack && !e.isDefault) kind.title = 'Ships with the ' + e.pack + ' set';
      name.appendChild(kind);
      const len = document.createElement('td');
      len.textContent = mmss(e.secs);
      const cat = document.createElement('td');
      if (e.isDefault) {
        // A default's category is a fact, not a filing decision.
        cat.textContent = 'set default';
        cat.className = 'unused';
        const len0 = document.createElement('td');
        len0.textContent = mmss(e.secs);
        const used0 = document.createElement('td');
        if (e.used.length) {
          const chip = document.createElement('span');
          chip.className = 'usedchip';
          chip.textContent = SLOT_NAMES[e.kind];
          used0.appendChild(chip);
        } else {
          const sug = document.createElement('span');
          sug.className = 'suggestchip';
          sug.textContent = 'for ' + SLOT_NAMES[e.kind];
          used0.appendChild(sug);
        }
        const act0 = document.createElement('td');
        act0.className = 'shelfacts';
        const play0 = document.createElement('button');
        play0.className = 'btnquiet'; play0.textContent = 'Play';
        play0.onclick = () => {
          if (e.kind === 'vm_beep') return previewBeep();
          if (e.url) { new Audio(e.url).play().catch(() => {}); return; }
          // Synthesized: feed the engine that set and let it speak.
          setSounds({ enabled: true, pack: e.packId });
          playSound(e.kind);
        };
        act0.appendChild(play0);
        tr.append(name, len0, cat, used0, act0);
        body.appendChild(tr);
        return;
      }
      const catIn = document.createElement('input');
      catIn.type = 'text';
      catIn.value = e.category || '';
      catIn.className = 'catbox';
      catIn.onchange = async () => {
        // The server saved this all along; the CLIENT kept repainting the
        // shelf from its stale local copy, so the very next repaint undid
        // the edit on screen and the box read as refusing to save.
        // Operator-reported. Update the source array the repaints read.
        try {
          const r = await afetch('/settings/sounds/meta', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: e.name, category: catIn.value }),
          });
          if (!r.ok) throw new Error('refused');
          if (!catIn.value.trim()) {
            // Blank CLEARS, per the settings invariant — the mistyped
            // "test" on a shipped clip needed a way home. The server
            // dropped the override; refetch for the true category.
            showResult($('soundResult'), true, (e.label || e.name)
              + ' goes back to its shipped category.');
            loadSounds();
            return;
          }
          const src = (e.builtin ? soundLibrary : uploadMeta)
            .find((s) => s.name === e.name);
          if (src) src.category = catIn.value.trim();
          showResult($('soundResult'), true, (e.label || e.name)
            + ' filed under “' + catIn.value.trim() + '”.');
          paintSoundBoard();     // the category filter's options follow
        } catch (err) {
          showResult($('soundResult'), false,
            'Could not save the category — ' + err.message);
        }
      };
      cat.appendChild(catIn);
      // Where this sound is on duty, drafts included — reading the same
      // fields the cards read, so the two can never disagree.
      const used = document.createElement('td');
      if (e.used.length) {
        e.used.forEach((slot) => {
          const chip = document.createElement('span');
          chip.className = 'usedchip';
          chip.textContent = SLOT_NAMES[slot];
          used.appendChild(chip);
        });
      } else if (!e.builtin) {
        // An upload's type is the OPERATOR's to declare — a shipped clip
        // knows what it is, a mystery.wav does not. Saved to the meta
        // store the moment it is picked, like the category beside it.
        const typeSel = document.createElement('select');
        typeSel.className = 'usefor';
        const blank = document.createElement('option');
        blank.value = ''; blank.textContent = 'type…';
        typeSel.appendChild(blank);
        Object.keys(SLOT_NAMES).forEach((slot) => {
          const o = document.createElement('option');
          o.value = slot; o.textContent = SLOT_NAMES[slot];
          typeSel.appendChild(o);
        });
        typeSel.value = e.suggests || '';
        typeSel.onchange = async () => {
          try {
            const r = await afetch('/settings/sounds/meta', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name: e.name, suggests: typeSel.value }),
            });
            if (!r.ok) throw new Error('refused');
            const src = uploadMeta.find((s) => s.name === e.name);
            if (src) src.suggests = typeSel.value;
            paintSoundBoard();   // the type filter and sort follow
          } catch (err) {
            showResult($('soundResult'), false,
              'Could not save the type — ' + err.message);
            typeSel.value = e.suggests || '';
          }
        };
        used.appendChild(typeSel);
      } else if (e.suggests && SLOT_NAMES[e.suggests]) {
        // Not on duty, but MADE for a slot — a busy signal is a
        // can't-connect whatever the operator does with it. Dim, so a
        // suggestion never reads as an assignment.
        const sug = document.createElement('span');
        sug.className = 'suggestchip';
        sug.textContent = 'for ' + SLOT_NAMES[e.suggests];
        used.appendChild(sug);
      } else {
        used.textContent = '—';
        used.className = 'unused';
      }
      const act = document.createElement('td');
      act.className = 'shelfacts';
      const play = document.createElement('button');
      play.className = 'btnquiet'; play.textContent = 'Play';
      play.onclick = () => { new Audio(e.url).play().catch(() => {}); };
      act.appendChild(play);
      // The assignment gesture, on the sound itself. The beep only takes
      // what the server can play: built-ins (WAV by policy) and WAV uploads.
      const use = document.createElement('select');
      use.className = 'usefor';
      const first = document.createElement('option');
      first.value = ''; first.textContent = 'Use for…';
      use.appendChild(first);
      SOUND_SLOTS.forEach((slot) => {
        if (slot === 'vm_beep' && !e.builtin && !/\.wav$/i.test(e.name)) return;
        const o = document.createElement('option');
        o.value = slot; o.textContent = SLOT_NAMES[slot];
        use.appendChild(o);
      });
      use.onchange = () => {
        const slot = use.value;
        if (!slot) return;
        use.value = '';
        $('sound_' + slot).value = e.builtin ? e.url : UPLOAD_PREFIX + e.name;
        markClean();
        paintSlotCards();
        showResult($('soundResult'), true, (e.label || e.name) + ' set as the '
          + SLOT_NAMES[slot] + ' sound — press Save to apply it to the next '
          + 'caller.');
      };
      act.appendChild(use);
      if (!e.builtin) {
        const dl = document.createElement('a');
        dl.className = 'btnquiet'; dl.textContent = 'Download';
        dl.href = e.url; dl.setAttribute('download', e.name);
        act.appendChild(dl);
        const del = document.createElement('button');
        del.className = 'btnquiet'; del.textContent = 'Remove';
        del.onclick = async () => {
          await afetch('/settings/sounds/' + encodeURIComponent(e.name),
                       { method: 'DELETE' });
          loadSounds();
        };
        act.appendChild(del);
      }
      tr.append(name, len, cat, used, act);
      body.appendChild(tr);
    });
  }

  // Column headers sort; a second press flips the direction.
  if ($('soundBoard')) {
    $('soundBoard').querySelectorAll('th.sortable').forEach((th) => {
      th.onclick = () => {
        shelfSort = { key: th.dataset.sort,
                      dir: shelfSort.key === th.dataset.sort ? -shelfSort.dir : 1 };
        paintSoundBoard();
      };
    });
  }
  // The find box and the category pick repaint the shelf as they change —
  // and put it back on page one, because a filter that lands you on an
  // empty page 3 reads as "no results".
  if ($('shelfSearch')) {
    let shelfTimer = null;
    $('shelfSearch').oninput = () => {
      clearTimeout(shelfTimer);
      shelfTimer = setTimeout(() => { shelfPage = 0; paintSoundBoard(); }, 120);
    };
    $('shelfCat').onchange = () => { shelfPage = 0; paintSoundBoard(); };
    $('shelfType').onchange = () => { shelfPage = 0; paintSoundBoard(); };
  }
  if ($('shelfPager')) {
    $('shelfPager').querySelector('.pprev').onclick = () => {
      shelfPage = Math.max(0, shelfPage - 1); paintSoundBoard();
    };
    $('shelfPager').querySelector('.pnext').onclick = () => {
      shelfPage += 1; paintSoundBoard();
    };
  }

  // Set by a slot's own Upload… button, so the file lands assigned to the
  // sound it was uploaded for instead of arriving on a shelf to be wired up
  // by hand. The plain "Upload a sound…" button leaves it null.
  let pendingAssignSlot = null;

  if ($('uploadSoundBtn')) {
    $('uploadSoundBtn').onclick = () => { pendingAssignSlot = null; $('soundFile').click(); };
    $('soundFile').onchange = () => {
      const file = $('soundFile').files[0];
      const slot = pendingAssignSlot;
      pendingAssignSlot = null;
      if (file) uploadSoundFile(file, slot);
    };
    // Dropping a file on the shelf uploads it — the same path the button
    // takes, so the WAV conversion and the ceilings apply identically.
    const dropHost = $('soundDrop').closest('details');
    ['dragover', 'dragleave', 'drop'].forEach((kind) => {
      dropHost.addEventListener(kind, (ev) => {
        if (kind !== 'dragleave'
            && !(ev.dataTransfer && [...(ev.dataTransfer.types || [])].includes('Files'))) return;
        ev.preventDefault();
        dropHost.classList.toggle('dropping', kind === 'dragover');
        if (kind === 'drop') {
          const file = ev.dataTransfer.files && ev.dataTransfer.files[0];
          if (file) uploadSoundFile(file, null);
        }
      });
    });
  }

  async function uploadSoundFile(file, slot) {
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
          ? d.name + ' uploaded and set as the ' + SLOT_NAMES[slot]
            + ' sound — press Save to apply it to the next caller.'
          : d.name + ' uploaded — it is on the shelf. Give it a job with '
            + 'Use for…, then Save.');
      } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
      finally { $('soundFile').value = ''; }
  }

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

  // What panel.js calls back into: the schema-driven rebuild, the repaint,
  // and the initial load. Everything else on this file is reached from its
  // own listeners.
  // Changing the Sound set plays the new set's ring straight away. The
  // audition path always existed — press any card's ▶ after switching — but
  // nothing said so, and a dropdown that changes five sounds silently is a
  // choice made deaf. previewSound already reads the dropdown's CURRENT
  // value, so this is only the cue.
  const packSelect = $('sound_pack');
  if (packSelect) packSelect.addEventListener('change', () => previewSound('ring'));

  window.Panel.sounds = { loadSounds, paintSlotCards, buildSlotCards };
})();
