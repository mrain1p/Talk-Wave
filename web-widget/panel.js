/* The settings panel: the operator's surface, served at /panel.

   Loaded only by panel.html. The call page does not load it and an embed
   cannot reach it — it used to ship to every anonymous caller as dead weight
   inside app.js, and the question "is this an embed?" had to be asked in
   javascript because there was no other way to tell the two apart.

   Shared foundation comes from shared.js via the Callin global. */
(function () {
  const {
    $, ASKS, ASK_GROUPS, NEVER, CALL_KEY,
    ctx, playSound, pack, setSounds, getVolume, THEME_ICONS, LINK_ICONS,
  } = window.Callin;

  // The panel's own copy of /live. It used to read the call page's, which is
  // the only reason previewSound had to borrow the call's sound config and put
  // it back afterwards.
  let live = null;
  async function refreshLiveData() {
    try { live = await fetch('/live').then((r) => r.json()); }
    catch (e) { live = live || {}; }
    setSounds(live && live.sounds);
    paintOnairWiring();
    paintQuietWiring();
    return live;
  }

  // The wiring warning beside the on-air rows: go-live saved ON while the
  // mixer is unreachable means nothing can actually air and every phone-in
  // quietly falls back private — the door-truth failure, seen the day the
  // wiring doc first said "join both networks". Painted from SAVED state on
  // every /live refresh (the save path refreshes, so flipping the row on
  // without the wiring shows this immediately). The quick kill is excluded
  // on purpose: a door the operator closed is a choice, not a fault.
  function paintOnairWiring() {
    const box = $('onairWiring');
    if (!box) return;
    const oa = (live && live.onAirCalls) || null;
    const unwired = !!(oa && oa.tier && oa.tier !== 'off'
                       && oa.enabled !== false && !oa.calls);
    box.style.display = unwired ? 'block' : 'none';
  }

  // Same door-truth rule for quiet-the-station: /live's stationQuiet verdict
  // is null while the setting is off, and carries ok:false with a why when
  // the saved-on feature cannot actually reach the station's Voice switch —
  // credentials missing, or the last flip didn't stick.
  function paintQuietWiring() {
    const box = $('quietWiring');
    if (!box) return;
    const q = (live && live.stationQuiet) || null;
    const broken = !!(q && q.ok === false);
    if (broken) $('quietWiringWhy').textContent = q.why || 'the last flip failed';
    box.style.display = broken ? 'block' : 'none';
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
      // The Players page is the one page with furniture of its own — the
      // tab strip and the pinned preview live in a static wrapper
      // (#cardWrap) — so its sections are moved INTO the wrapper's settings
      // column rather than into the page flow. The schema still owns their
      // order; the wrapper is parked where the band's sections would sit.
      if (sup.id === 'card' && $('cardWrap')) {
        anchor.parentNode.insertBefore($('cardWrap'), anchor);
        members.forEach((g) => {
          const sec = byId[g.id];
          sec.classList.add('cardblock');
          // Closed to start, foldable like every other section (operator's
          // ask, 2026-08-18 — 1a forced them all open and the page arrived
          // as one long wall). Clicking an element on the preview card
          // still reaches inside: the spot handler opens the block it owns.
          $('cardCol').appendChild(sec);
        });
        paintCardBands();
        return;
      }
      members.forEach((g) => anchor.parentNode.insertBefore(byId[g.id], anchor));
      soloPage(members.map((g) => byId[g.id]));
    });

    // Anything the schema doesn't place still gets shown, at the end.
    Object.keys(byId).forEach((id) => {
      if (!SCHEMA.groups.some((g) => g.id === id)) {
        anchor.parentNode.insertBefore(byId[id], anchor);
      }
    });

    // Settings sections start CLOSED (operator's ask, 2026-08-12 — reversing
    // their own 0.10.64 ask that shipped them open): folded drawers read
    // cleaner and are easier to navigate, and the summaries carry name, blurb
    // and state chip, so a shut page still reads. Nothing to do here — the
    // markup carries no `open`, search and the nav call showSection() to open
    // what they land on, and each section's lazy painter fires on its own
    // toggle when the operator opens it. The diagnostics rows were always
    // folded for their own reason: viewers with run buttons in their headers,
    // where four empty result panes stacked open was reported.

    buildNav(supers);
    paintPage();
    // An address naming a section has to be honoured on ARRIVAL too, not
    // only on a later hashchange — a link somebody was handed opens the
    // panel with the hash already set, and nothing fires.
    revealSection(sectionFromHash());
  }

  // A page holding exactly ONE section is a page you click twice to reach one
  // thing: turn to the page, then open the fold — and the fold is the only
  // thing on it, so there is nothing for it to be folded away FROM. Voicemail
  // and Texts were both like this. The page simply IS the section now: it
  // arrives open, with no chevron to press. Same rule the sections already
  // follow ("a section whose only field moved elsewhere retires"), one level
  // up.
  //
  // The summary stays: it carries the blurb and the state chip, which are
  // worth reading. It just stops being a control.
  function soloPage(secs) {
    if (secs.length !== 1) return;
    const only = secs[0];
    only.classList.add('solo');
    only.open = true;
    if (only.dataset.soloBound) return;
    only.dataset.soloBound = '1';
    // Belt and braces for anything that toggles it programmatically — the
    // search restores folds by writing `open`, and a solo section closing
    // would leave its page blank.
    only.addEventListener('toggle', () => { if (!only.open) only.open = true; });
  }

  // The page picker. One chip per page — Dashboard, one per super-group, and
  // Diagnostics, the one header the schema does not own. Built from the
  // schema rather than written in the markup, so a new super-group becomes a
  // page on its own. A chip is a plain hash link: navigation is the
  // browser's own (back works, a page survives a refresh, /settings#calls
  // can be handed to someone), and the hashchange listener below does the
  // turning.
  //
  // ONE FLAT ROW. It was banded into five labelled rows during the 0.98.22
  // work and the operator turned it down before any of it shipped: grouping
  // eleven pages by kind read as more furniture than map, and the labels
  // took more room than they earned. The measurement that prompted the bands
  // still stands — a 375px phone shows two of the twelve chips, because the
  // strip wants 1107px in 343px of room — so if that is worth fixing later,
  // fix it without regrouping the pages.
  //
  // The pages the schema does not own come from NAV_EXTRA_PAGES rather than
  // being written in here, so the picker's whole order stays readable in one
  // file — the same rule the section order already follows.
  function buildNav(supers) {
    const nav = $('panelNav');
    if (!nav) return;
    nav.innerHTML = '';
    PAGE_IDS = [];
    PAGE_TITLES = {};

    const extras = SCHEMA.navExtraPages
      || [{ id: 'dash', title: 'Dashboard', where: 'lead' },
          { id: 'diag', title: 'Diagnostics', where: 'tail' }];
    const link = (id, title) => {
      if (PAGE_IDS.indexOf(id) === -1) PAGE_IDS.push(id);
      PAGE_TITLES[id] = title;
      const a = document.createElement('a');
      a.href = '#' + id;
      a.dataset.page = id;
      a.textContent = title;
      nav.appendChild(a);
    };

    extras.filter((p) => p.where !== 'tail')
      .forEach((p) => link(p.id, p.title));
    supers.forEach((sup) => {
      if (!SCHEMA.groups.some((g) => g.super === sup.id)) return;
      link(sup.id, sup.title);
    });
    extras.filter((p) => p.where === 'tail')
      .forEach((p) => link(p.id, p.title));

    // Collapse all retired at 0.10.80 (operator's call, the same review that
    // added it at 0.10.64): pages made every page short enough that folding
    // is the section chevrons' job, and an action chip among page chips read
    // as a page.
    sizeStickyBar();
  }

  // The band's real height feeds the sections' scroll-margin, so an in-page
  // jump (a tile, a key-row link) lands below it whatever the viewport made
  // of the header row. Zero is "not laid out yet" (buildNav can run behind
  // the login gate), so it never overwrites the CSS fallback — paintPage
  // re-measures once visible.
  function sizeStickyBar() {
    const bar = $('settingsBar');
    if (!bar) return;
    if (bar.offsetHeight > 0) {
      document.documentElement.style.setProperty(
        '--stickybar', bar.offsetHeight + 'px');
    }
    if (!sizeStickyBar.armed) {
      addEventListener('resize', sizeStickyBar);
      sizeStickyBar.armed = true;
    }
  }

  // --- pages ---------------------------------------------------------------
  // The panel turned into pages at 0.10.62 (the operator's ask): one
  // super-group at a time behind /settings#<id>, the dashboard as the
  // landing page. Everything stays in ONE document shown a slice at a time —
  // the dashboard's live repaints read fields on other pages, and a
  // half-edited field keeps its state while the operator reads another page,
  // neither of which survives real navigation.
  let PAGE_IDS = ['dash', 'diag'];
  let PAGE_TITLES = { dash: 'Dashboard', diag: 'Diagnostics' };

  function currentPage() {
    const id = (location.hash || '').replace(/^#/, '');
    if (PAGE_IDS.indexOf(id) !== -1) return id;
    // A SECTION id resolves to the page holding it (0.98.22). Before this,
    // #turns — the section's own id, and the obvious guess — fell through to
    // the dashboard with nothing open and nothing said. So nothing below page
    // level had an address: no bookmark, no link to hand somebody, and no way
    // for help text to point at anything finer than a whole page.
    const g = (SCHEMA.groups || []).find((x) => x.id === id);
    if (g && PAGE_IDS.indexOf(g.super) !== -1) return g.super;
    return 'dash';
  }

  // The section a hash names, or null when it names a page (or nothing).
  // One reader, so the hash listener, the boot path and the link expander
  // cannot disagree about what an address means.
  function sectionFromHash() {
    const id = (location.hash || '').replace(/^#/, '');
    if (!id || PAGE_IDS.indexOf(id) !== -1) return null;
    return document.querySelector('details.sec[data-group="' + id + '"]');
  }

  function pageOfSection(sec) {
    if (sec.classList.contains('diag')) return 'diag';
    const g = SCHEMA.groups.find((x) => x.id === sec.dataset.group);
    return g ? g.super : null;
  }

  // One writer for page membership, and it is a CLASS, not style.display —
  // applyVisibility and the search both own inline display on these same
  // elements, and a third hand on one property is how things end up visible
  // that should not be. The class wins via !important; removing it hands the
  // element straight back to whatever the other two decided.
  function paintPage() {
    const page = currentPage();
    const searching =
      !!((($('settingsSearch') || {}).value || '').trim());
    const on = (el, yes) => { if (el) el.classList.toggle('offpage', !yes); };
    document.querySelectorAll('.dashband, .dash, .activity').forEach((el) =>
      on(el, page === 'dash' && !searching));
    // Search results carry their section's own name, so the bands stay out
    // of a results view rather than headlining pages with no matches.
    document.querySelectorAll('.supergroup').forEach((hdr) => {
      const id = hdr.dataset.super || (hdr.id === 'supDiag' ? 'diag' : '');
      on(hdr, id === page && !searching);
    });
    document.querySelectorAll('details.sec').forEach((sec) => {
      const id = pageOfSection(sec);
      // A section the schema doesn't place stays visible on every page —
      // loud, to match layoutPanel's console warning about the mismatch.
      on(sec, searching || !id || id === page);
    });
    // The Players page: the wrapper shows with its page (or whenever a
    // search needs the sections inside it), and the current tab decides
    // which of its sections are on screen. Searching lifts the tab filter
    // and turns the wrapper into a plain results column — no tab strip, no
    // preview — the same way it parks the dashboard.
    const wrap = $('cardWrap');
    if (wrap) {
      on(wrap, searching || page === 'card');
      // Searching turns the wrapper into a plain results column — no
      // preview, no band captions, the same way it parks the dashboard.
      wrap.classList.toggle('searching', searching);
    }
    // Save/Reset belong under the pages that hold form fields.
    on(document.querySelector('#panel .actions'),
       searching || (page !== 'dash' && page !== 'diag'));
    const nav = $('panelNav');
    if (nav) {
      nav.querySelectorAll('a.here').forEach((a) => a.classList.remove('here'));
      const chip = nav.querySelector('a[data-page="' + page + '"]');
      if (chip) chip.classList.add('here');
    }
    if ($('mastSub')) {
      // The PAGE, and nothing else. The host name led this line and told the
      // operator something they typed into the address bar a second ago —
      // and on a real hostname it was long enough to read as the subtitle,
      // with the page name trailing off it (operator's ask).
      $('mastSub').textContent = (PAGE_TITLES[page] || page).toUpperCase();
    }
    sizeStickyBar();
  }

  // Reaching a section can mean turning to its page first. The hash is
  // written with pushState, which does not fire hashchange — so the smooth
  // scroll below is the only movement, not a fight with the listener's
  // jump-to-top.
  function showSection(sec) {
    if (!sec) return;
    // The address left behind is the SECTION, not its page (0.98.22), so
    // every jump inside the panel — a tile, a notification, a search result,
    // a cross-reference — ends somewhere that can be bookmarked and handed
    // to somebody else. The page still turns: currentPage() resolves a
    // section id to the page holding it.
    const addr = sec.dataset.group;
    if (addr && ('#' + addr) !== location.hash) {
      history.pushState(null, '', '#' + addr);
      paintPage();
    }
    revealSection(sec);
  }

  // Turn the tab, open the fold, scroll to it. Split out of showSection
  // because arriving BY ADDRESS has to do the same three things without
  // rewriting the hash it just arrived on.
  function revealSection(sec) {
    if (!sec) return;
    sec.open = true;
    sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  addEventListener('hashchange', () => {
    paintPage();
    const sec = sectionFromHash();
    // An address naming a section opens it where it stands. Scrolling to the
    // top first would be a visible jump away from the thing just asked for.
    if (sec) { revealSection(sec); return; }
    // Turning to a page starts at its top — the scroll position of the page
    // you left means nothing on the one you arrived at.
    window.scrollTo({ top: 0 });
  });

  // ---------------------------------------------- the Players page bands
  // Three groups over one page. They were TABS (the operator's design
  // handoff, direction 1a) until 0.98.22: THE CARD is the element blocks in
  // card order, BEHAVIOUR is what a call does with nothing visual about it,
  // EMBED is the frame and the snippet.
  //
  // The grouping was right and stays. The TAB was not. Three tabs holding
  // six, two and one section hid two thirds of the page behind a control
  // nothing else in the panel uses, and bought a fourth level of depth on
  // the one page that could least afford it — "Start calls on loudspeaker"
  // sat at Players → Behaviour → On the caller's phone → row, while every
  // other setting in the panel is three levels down. As ruled captions in
  // one column the grouping still reads, the whole page is scrollable, and
  // the depth matches everywhere else.
  //
  // The schema owns section ORDER; this map only says which band a section
  // belongs to — a card group missing here lands on THE CARD, which is where
  // a new element block would belong anyway.
  const CARD_TABS = {
    topcorner: 'card', whosonair: 'card', linebox: 'card', talkbar: 'card',
    buttons: 'card', surface: 'card',
    phone: 'behaviour', feedback: 'behaviour',
    embed: 'embed',
  };
  const CARD_BAND_TITLES = {
    card: 'The card',
    behaviour: 'Behaviour',
    embed: 'Embed',
  };

  function cardTabOf(sec) {
    return CARD_TABS[sec.dataset.group] || 'card';
  }

  // A caption above the first section of each band, inside the settings
  // column. Written from the sections themselves, so a band with nothing in
  // it never grows a heading.
  function paintCardBands() {
    const col = $('cardCol');
    if (!col) return;
    col.querySelectorAll(':scope > .cardband').forEach((b) => b.remove());
    let seen = '';
    [...col.querySelectorAll(':scope > details.sec')].forEach((sec) => {
      const band = cardTabOf(sec);
      if (band === seen) return;
      seen = band;
      const cap = document.createElement('p');
      cap.className = 'cardband';
      cap.dataset.band = band;
      cap.textContent = CARD_BAND_TITLES[band] || band;
      col.insertBefore(cap, sec);
    });
  }

  // "N on page · M in embed" — the tab strip's live tally of the card's
  // toggleable elements, following the CHECKBOXES rather than what was
  // saved, so it always agrees with the preview beside it. The link-out
  // counts only while it exists at all. The same eleven-element list the
  // old section tag counted; the embed simply has no gear.
  const COUNT_ELS = ['caller_help', 'theme_toggle', 'settings_gear',
    'push_to_talk', 'dj_avatar', 'dj_show', 'dj_tagline', 'now_playing',
    'voicemail_button', 'chat_button', 'signin'];
  function paintCardCounts() {
    const el = $('cardCounts');
    if (!el) return;
    const val = (f) => {
      const box = $(f);
      return box ? box.checked : !!resolved[f];
    };
    let onPage = COUNT_ELS.filter((k) => val('show_' + k)).length;
    let inEmb = COUNT_ELS.filter((k) => $('embed_' + k) && val('embed_' + k)).length;
    if (val('corner_link_enabled')) {
      if (val('show_corner_link')) onPage += 1;
      if (val('embed_corner_link')) inEmb += 1;
    }
    el.textContent = onPage + ' on page · ' + inEmb + ' in embed';
  }

  // ------------------------------------------- hover → spotlight the card
  // Hovering a settings row outlines the element it controls on the preview
  // card (the handoff's discovery gesture — the row says which element it
  // owns with data-spot, written in the markup beside the fields). The card
  // side is call.js's swtv:spotlight handler, preview frames only. Focusing
  // a line-box wording field goes further: the card's line box shows that
  // state's text — the typed value, or the built-in default the placeholder
  // carries — until blur, so the operator reads the wording ON the card
  // rather than imagining it there.
  function sendToPreview(msg) {
    const f = previewFrame();
    if (!f) return;
    try { f.contentWindow.postMessage(msg, location.origin); }
    catch (e) { /* the preview is a nicety; never let it break the form */ }
  }

  (function bindCardSpotlight() {
    const col = $('cardCol');
    if (!col) return;
    let current = null;
    col.addEventListener('mouseover', (e) => {
      const row = e.target.closest ? e.target.closest('[data-spot]') : null;
      const name = row && col.contains(row) ? row.dataset.spot : null;
      if (name !== current) {
        current = name;
        sendToPreview({ type: 'swtv:spotlight', el: name || null });
      }
    });
    col.addEventListener('mouseleave', () => {
      if (current) {
        current = null;
        sendToPreview({ type: 'swtv:spotlight', el: null });
      }
    });

    const lineField = (el) =>
      el && el.id && el.id.indexOf('word_') === 0
        && el.closest('[data-group="linebox"]') ? el : null;
    const sendLinePreview = (el) => {
      sendToPreview({
        type: 'swtv:linepreview',
        text: el
          ? (el.value || String(el.placeholder || '').replace(/^default:\s*/, ''))
          : null,
      });
    };
    col.addEventListener('focusin', (e) => {
      const el = lineField(e.target);
      if (el) sendLinePreview(el);
    });
    col.addEventListener('focusout', (e) => {
      if (lineField(e.target)) sendLinePreview(null);
    });
    col.addEventListener('input', (e) => {
      const el = lineField(e.target);
      if (el && document.activeElement === el) sendLinePreview(el);
    });
  })();

  // The other direction: clicking an element ON the card flashes the block
  // that owns it — the same data-spot names, reported by call.js from
  // inside the frame.
  addEventListener('message', (e) => {
    const f = $('previewFrame');
    if (!f || e.source !== f.contentWindow) return;
    if (!e.data || e.data.type !== 'subwave-callin:spot') return;
    const spot = String(e.data.el || '').replace(/[^a-z]/g, '');
    const row = spot
      && document.querySelector('#cardCol [data-spot="' + spot + '"]');
    const sec = row && row.closest('details.sec');
    if (!sec) return;
    // Blocks start closed now — a flash inside a shut drawer is invisible,
    // so the click that named the element also opens its block.
    sec.open = true;
    sec.classList.remove('flash');
    void sec.offsetWidth;   // restart the animation when re-clicked
    sec.classList.add('flash');
    sec.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });

  // The mobile dock: on a phone the preview aside pins to the bottom of the
  // screen and this chevron folds the card away so the settings get the
  // height back. Open by default — the live card is the page's point.
  if ($('dockChev')) {
    $('dockChev').onclick = () => {
      const folded = $('cardAside').classList.toggle('folded');
      $('dockChev').textContent = folded ? '▴' : '▾';
    };
  }

  function adoptSchema(schema) {
    SCHEMA = schema || { groups: [], fields: {} };
    const byKind = (k) => Object.keys(SCHEMA.fields).filter(
      (f) => SCHEMA.fields[f].kind === k && document.getElementById(f));
    // `emoji` and `order` are text fields with a control attached — they
    // save, load and diff exactly like one, and the picker below only writes
    // into them. `order`'s input is type=hidden, which is still a text field
    // as far as every read and write here is concerned.
    TEXT_FIELDS = byKind('text').concat(byKind('emoji'), byKind('order'),
                                        byKind('picks'));
    NUM_FIELDS = byKind('number');
    CHECK_FIELDS = byKind('check');
    SELECT_FIELDS = byKind('select');
    ALL_FIELDS = SELECT_FIELDS.concat(TEXT_FIELDS, NUM_FIELDS, CHECK_FIELDS);
    layoutPanel();
    decoratePermissions();
    decorateAccess();
    bindFieldEvents();
    decorateFields();
    window.Panel.sounds.buildSlotCards();
    buildEmojiGrid();
    buildDoorOrder();
  }

  // THE ICON PICKER. A popup over DRAWN icons, and both halves of that were
  // corrections. It was two dozen emoji wrapped under the row — full-colour
  // glyphs for a button that sits in a line of stroked controls in the card's
  // own ink, in a grid that made the row three lines tall whether or not
  // anyone was choosing. The icons come from shared.js so the panel and the
  // card draw from ONE list; the field still takes a typed emoji, which is
  // what keeps every deployment that stored one working.

  // ------------------------------------------------------ the door order
  // Three rows you drag. Native HTML5 drag-and-drop rather than a pointer-move
  // implementation: it is ~30 lines instead of ~150, it gives keyboard users
  // nothing, so the buttons beside each row do — an order you can only set by
  // dragging is an order some people cannot set at all.
  const DOORS = [
    ['call', 'Call'],
    ['chat', 'Text'],
    ['vm', 'Message'],
  ];

  function doorOrderValue() {
    // The field, or what the layers below it resolved to. Reading `resolved`
    // here rather than writing it into the field keeps invariant 2 intact:
    // blank means "fall through", so an operator who never touches this saves
    // nothing and stays on the default rather than pinning a copy of it.
    const stored = (($('door_order') || {}).value || '')
      || String(resolved.door_order || '');
    const seen = [], known = DOORS.map((d) => d[0]);
    stored.split(',').map((s) => s.trim().toLowerCase()).forEach((n) => {
      if (known.includes(n) && !seen.includes(n)) seen.push(n);
    });
    // Anything the stored value did not mention goes on the end, so a door
    // added in a later version appears rather than disappearing.
    return seen.concat(known.filter((n) => !seen.includes(n)));
  }

  function writeDoorOrder(order) {
    const field = $('door_order');
    if (!field) return;
    field.value = order.join(',');
    field.dispatchEvent(new Event('input', { bubbles: true }));
    paintDoorOrder();
  }

  function paintDoorOrder() {
    const list = $('doorOrderList');
    if (!list) return;
    const order = doorOrderValue();
    list.innerHTML = '';
    order.forEach((name, i) => {
      const label = (DOORS.find((d) => d[0] === name) || [name, name])[1];
      const li = document.createElement('li');
      li.className = 'doorrow';
      li.draggable = true;
      li.dataset.door = name;
      li.innerHTML = '<span class="doorgrip" aria-hidden="true">⣿</span>'
        + '<span class="doorname"></span>';
      li.querySelector('.doorname').textContent = label;

      const move = (delta) => {
        const now = doorOrderValue();
        const at = now.indexOf(name);
        const to = at + delta;
        if (to < 0 || to >= now.length) return;
        now.splice(to, 0, now.splice(at, 1)[0]);
        writeDoorOrder(now);
        const again = $('doorOrderList').querySelector(
          '[data-door="' + name + '"] .doorup');
        if (again) again.focus();
      };
      const btn = (cls, glyph, text, delta, disabled) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'doormove ' + cls;
        b.textContent = glyph;
        b.title = text;
        b.setAttribute('aria-label', label + ': ' + text);
        b.disabled = disabled;
        b.onclick = () => move(delta);
        return b;
      };
      li.appendChild(btn('doorup', '↑', 'move earlier', -1, i === 0));
      li.appendChild(btn('doordown', '↓', 'move later', 1, i === order.length - 1));

      li.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', name);
        e.dataTransfer.effectAllowed = 'move';
        li.classList.add('dragging');
      });
      li.addEventListener('dragend', () => li.classList.remove('dragging'));
      li.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        li.classList.add('over');
      });
      li.addEventListener('dragleave', () => li.classList.remove('over'));
      li.addEventListener('drop', (e) => {
        e.preventDefault();
        li.classList.remove('over');
        const dragged = e.dataTransfer.getData('text/plain');
        if (!dragged || dragged === name) return;
        const now = doorOrderValue();
        now.splice(now.indexOf(name), 0,
                   now.splice(now.indexOf(dragged), 1)[0]);
        writeDoorOrder(now);
      });
      list.appendChild(li);
    });
  }

  function buildDoorOrder() {
    const field = $('door_order');
    if (!field || field.dataset.built) return;
    field.dataset.built = '1';
    // Repainted when the value arrives from /settings too, not only on edit —
    // the field is populated after the schema paints.
    field.addEventListener('input', paintDoorOrder);
    field.addEventListener('change', paintDoorOrder);
    paintDoorOrder();
  }

  function buildEmojiGrid() {
    const grid = $('cornerIconGrid');
    const field = $('corner_link_icon');
    const trigger = $('cornerIconBtn');
    if (!grid || !field || grid.dataset.built) return;
    grid.dataset.built = '1';
    Object.keys(LINK_ICONS).forEach((name) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'iconbtn';
      b.dataset.icon = name;
      b.innerHTML = LINK_ICONS[name];
      b.title = name;
      b.setAttribute('aria-label', 'Use the ' + name + ' icon');
      b.onclick = () => {
        field.value = name;
        // The same event a typed edit fires, so Save sees it as one edit and
        // the picker cannot drift from the field it writes into.
        field.dispatchEvent(new Event('input', { bubbles: true }));
        closeIconPop();
        if (trigger) trigger.focus();
      };
      grid.appendChild(b);
    });
    field.addEventListener('input', paintEmojiGrid);

    if (trigger) {
      trigger.onclick = () => {
        const pop = $('cornerIconPop');
        if (pop && pop.hidden) openIconPop(); else closeIconPop();
      };
    }
    const close = $('cornerIconClose');
    if (close) close.onclick = () => { closeIconPop(); if (trigger) trigger.focus(); };
    // Click-away and Escape, the same two exits every other popup here has.
    document.addEventListener('click', (e) => {
      const pop = $('cornerIconPop');
      if (!pop || pop.hidden) return;
      if (pop.contains(e.target) || (trigger && trigger.contains(e.target))) return;
      closeIconPop();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeIconPop();
    });
    paintEmojiGrid();
  }

  function openIconPop() {
    const pop = $('cornerIconPop'), trigger = $('cornerIconBtn');
    if (!pop) return;
    pop.hidden = false;
    if (trigger) trigger.setAttribute('aria-expanded', 'true');
    const on = pop.querySelector('.iconbtn.on') || pop.querySelector('.iconbtn');
    if (on) on.focus();
  }

  function closeIconPop() {
    const pop = $('cornerIconPop'), trigger = $('cornerIconBtn');
    if (!pop) return;
    pop.hidden = true;
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
  }

  function paintEmojiGrid() {
    const grid = $('cornerIconGrid');
    const field = $('corner_link_icon');
    const trigger = $('cornerIconBtn');
    if (!grid || !field) return;
    // Same rule as the order list: show what the card is actually using,
    // without writing it into the field.
    const now = ((field.value || '').trim()
                 || String(resolved.corner_link_icon || '')).trim();
    [...grid.children].forEach((b) => {
      b.classList.toggle('on', b.dataset.icon === now);
    });
    // The trigger IS the answer to "what will the button look like", so it
    // shows the drawn icon when we have one and the typed character when we
    // do not — which is exactly what the card itself does with the value.
    if (trigger) {
      if (LINK_ICONS[now]) trigger.innerHTML = LINK_ICONS[now];
      else trigger.textContent = now || '—';
    }
  }

  // Starts empty rather than null: the panel now paints as soon as the
  // schema arrives, before the slow provider lists have loaded, so every
  // read of this has to survive it being empty.
  let options = {}, overrides = {}, resolved = {}, secrets = {};
  let openLineLive = false;
  // What a field falls back to when cleared — see settings.beneath().
  let beneath = {};
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
    fill('llm_model', list, { labels,
      blankLabel: blankFor('llm_model', 'a model') });
    $('llm_model').value = overrides.llm_model || '';

    const note = $('modelSourceNote');
    if (!liveList && PROVIDER_KEY[llm]) {
      note.textContent = 'Showing a fallback list — add the ' + llm
        + ' key and hit “Test keys + reload models” to read the real one.';
    } else if (liveList) {
      // Read from the operator's own endpoint, or from the provider's
      // catalogue — the difference matters: only the endpoint's names are
      // guaranteed to route on a server like llama-swap.
      note.textContent = list.length + ((options.modelsFromEndpoint || '') === llm
          ? ' models read from your endpoint — these are the names it routes.'
          : ' models read live from ' + llm + '.')
        + (station.model && list.includes(station.model)
            ? ' The station runs ' + station.model + '.' : '');
    } else { note.textContent = ''; }
    missingProviderNote(note, 'llm');

    const stt = $('stt_provider').value || resolved.stt_provider;
    // The Whisper ladder wears its trade-offs in the dropdown itself: the
    // bare ids read as if "base" were the best one, and the operator chose
    // it believing exactly that. Cloud model ids fall through unlabelled.
    fill('stt_model', (options.sttModels || {})[stt] || [], {
      blankLabel: blankFor('stt_model', 'a model'),
      labels: {
        'tiny.en': 'tiny.en — fastest, hears the least; half the CPU of base',
        'base.en': 'base.en — light (default)',
        'small.en': 'small.en — hears phone audio clearly better; ~3x base per turn',
        'medium.en': 'medium.en — hears the most; ~8x base — test before trusting it',
      } });
    $('stt_model').value = overrides.stt_model || '';
    // The ladder describes the BUILT-IN models; behind a cloud pick it
    // would explain four options that are not in the dropdown.
    if ($('whisperLadder')) $('whisperLadder').hidden = stt !== 'local';
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
      // The offer can sit in a diagnostics result while the key's row lives
      // on the Configuration page — showSection turns the page first.
      showSection(row.closest('details'));
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

  // A cross-reference in help text, as a real link. The panel carried
  // fourteen of these written as prose — "under Caller permissions" four
  // times, "on the On air page" twice — and every one of them was a
  // hand-written apology for a jump the operator then had to make on foot,
  // because nothing below page level had an address to point at. Now that
  // currentPage() resolves a section id, `[Caller permissions](#perms)` in
  // a help string is a link that opens the section.
  function sectionLink(label, id) {
    const a = document.createElement('a');
    a.className = 'seclink';
    a.href = '#' + id;
    a.textContent = label;
    a.onclick = (e) => {
      const sec = document.querySelector('details.sec[data-group="' + id + '"]');
      // No section by that id: leave the plain hash navigation to it
      // rather than swallowing the click and going nowhere.
      if (!sec) return;
      // preventDefault does double duty — it stops the hash write AND the
      // activation of whatever this link sits inside, which is a <summary>
      // for a state chip and a <label> for a checkbox's help.
      e.preventDefault();
      e.stopPropagation();
      showSection(sec);
    };
    return a;
  }

  // Write text that may carry cross-references into an element. Builds
  // nodes rather than assigning innerHTML: the strings come from the
  // schema, but a renderer that only ever creates text nodes and anchors
  // cannot be talked into anything else later.
  function writeLinked(el, text) {
    const src = String(text == null ? '' : text);
    if (src.indexOf('](#') === -1) { el.textContent = src; return; }
    el.textContent = '';
    const re = /\[([^\]]+)\]\(#([a-z0-9_]+)\)/g;
    let last = 0, m;
    while ((m = re.exec(src)) !== null) {
      if (m.index > last) {
        el.appendChild(document.createTextNode(src.slice(last, m.index)));
      }
      el.appendChild(sectionLink(m[1], m[2]));
      last = m.index + m[0].length;
    }
    if (last < src.length) {
      el.appendChild(document.createTextNode(src.slice(last)));
    }
  }

  // Section headers summarise their own state, so the panel is readable folded.
  // Every write goes through tag(): a summary is decoration, and a missing
  // element must not be able to abort paint() half way and leave the panel
  // looking like a failed load. That has happened, from one renamed id.
  function setTag(id, text) {
    const el = $(id);
    if (!el) return;
    // A tag may name the section that explains it — "off — Go live is off
    // under [Caller permissions](#perms)" — and that reference is the one
    // an operator reads at the exact moment they want to go there.
    writeLinked(el, text);
    // The first word is the state, and colour carries it: green for a thing
    // that is ON, dimmed for one that is off — "on · 10%" in the same grey
    // as "off" made the header row a list of words instead of a glance.
    const head = String(text || '').split(/[ ·]/)[0].toLowerCase();
    el.dataset.state = ['on', 'open', 'always', 'live', 'ok'].includes(head)
      ? 'on'
      : ['off', 'never', 'closed', 'none'].includes(head) ? 'off' : '';
  }

  // Whether the door this page is about is even open. The three door
  // switches live on the dashboard as control cards — the right place for
  // them, and not being moved — but their PAGES said nothing about it, and
  // the fields, though declared with labels ("Enable voicemail", "Take live
  // calls", "Take text chats"), rendered in no section, matched no search
  // and appeared in no list built from the markup. So a setting you could
  // read about in the schema was reachable only by recognising a card on
  // another page.
  //
  // A line, not a control. Two switches for one thing is how a panel starts
  // disagreeing with itself; this reads the same checkbox the card writes.
  // [element, field, subject, verb, what being on means, what off means]
  const DOOR_STATES = [
    ['doorStateCalls', 'live_calls_enabled', 'Live calls', 'are',
     'the booth picks up', 'nothing answers live'],
    ['doorStateVm', 'voicemail_enabled', 'Voicemail', 'is',
     'the machine takes messages', 'the machine never answers'],
    ['doorStateChat', 'chat_enabled', 'The text line', 'is',
     'callers can type to the booth', 'nobody can type to the booth'],
  ];

  function paintDoorStates() {
    const paused = $('calls_paused')
      ? $('calls_paused').checked : !!resolved.calls_paused;
    DOOR_STATES.forEach(([id, field, subject, verb, onWhy, offWhy]) => {
      const el = $(id);
      const box = $(field);
      if (!el) return;
      const on = box ? box.checked : !!resolved[field];
      // The switch's own LABEL, named out loud. This is the only place in the
      // panel it appears: the control is a dashboard card that says "Live
      // calls", so "Enable voicemail" and "Take text chats" — real settings,
      // with real labels — were words the finder could never match and the
      // operator could never look up.
      const label = (SCHEMA.fields[field] || {}).label || field;
      const where = ' The switch is \u201c' + label
        + '\u201d, on the [dashboard](#dash).';
      el.hidden = false;
      el.dataset.state = paused ? 'held' : on ? 'on' : 'off';
      // The kill switch outranks every door, so a page whose door is on
      // while the line is paused must not claim anything is answering.
      writeLinked(el, paused
        ? subject + ' ' + verb + ' held — the line is paused, so nothing '
          + 'answers whatever this page says. Reopen the line on the '
          + '[dashboard](#dash).'
        : on
        ? subject + ' ' + verb + ' on — ' + onWhy + '. Everything below '
          + 'shapes what happens when it does.' + where
        : subject + ' ' + verb + ' off — ' + offWhy + ', so nothing below is '
          + 'reaching a caller yet.' + where);
    });
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
    // "not set" reads as the state it is; a fresh install's blank provider
    // used to render this tag as a floating " · " (0.10.80).
    setTag('tagBrains', resolved.llm_provider
      ? resolved.llm_provider + ' · ' + (resolved.llm_model || 'no model')
        + ' · ' + keysTag('brains')
      : 'not set — pick a provider');
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
    // The tag says what the operator SET, not an internal fallback: the hold
    // is sized to the words the station actually spoke, so quoting a number
    // of seconds here described something that almost never happens.
    setTag('tagOnair', resolved.avoid_on_air_overlap
      ? 'waits for quiet air' : 'talks over the broadcast');
    // Open Lines' tag answers "is a subject up right now", which is not the
    // same question as "is the feature on" — an operator glancing at a folded
    // section wants to know whether the station is currently asking listeners
    // something. The live half is filled in by paintOpenLines() from the
    // server's own verdict; this is the switched-off case, which needs no read.
    if (!openLineLive) {
      setTag('tagOpenlines', resolved.open_lines_enabled
        ? 'on — nothing up right now' : 'off');
    }
    // Live reads, like the dashboard cluster these doors share — and the
    // tier row outranks both doors: with Go live off, two open doors lead
    // to a route nobody may take, and "on" would be the tag lying.
    const doorOn = (id) => ($(id) ? $(id).checked : !!resolved[id]);
    setTag('tagAirdoors', permTier('allow_on_air') === 'off'
      ? 'off — Go live is off under [Caller permissions](#perms)'
      : doorOn('on_air_calls_enabled')
        ? (doorOn('on_air_voicemail_enabled')
            ? 'on · calls + voicemails' : 'on · calls only')
        : doorOn('on_air_voicemail_enabled')
          ? 'on · voicemails only' : 'off — both doors shut');
    setTag('tagTunein', resolved.tune_in_on_call
      ? 'on · ' + resolved.tune_in_volume + '%' : 'off — requests may be refused');
    setTag('tagRecord', resolved.record_calls ? 'keeping ' + resolved.record_keep : 'not kept');
    // The Players page's per-block tags. Counted or named, never listed —
    // a tag is a glance, and the first word is the state (setTag colours
    // it). The whole-page tally lives in the tab strip (paintCardCounts).
    const onPage = (k) => !!resolved['show_' + k];
    const inEmb = (k) => !!resolved['embed_' + k];
    const corner = ['caller_help', 'theme_toggle', 'settings_gear', 'signin'];
    const linkOn = !!resolved.corner_link_enabled;
    setTag('tagTopcorner',
      (corner.filter(onPage).length + (linkOn && resolved.show_corner_link ? 1 : 0))
      + ' on page · '
      + (corner.filter(inEmb).length + (linkOn && resolved.embed_corner_link ? 1 : 0))
      + ' in embed');
    const who = ['dj_avatar', 'dj_show', 'dj_tagline', 'now_playing'];
    setTag('tagWhosonair', who.filter(onPage).length + ' of 4 · '
      + (resolved.avatar_style || 'round') + ' photo');
    const lineWords = ['word_ringing', 'word_answering', 'word_connecting',
      'word_waiting', 'word_online', 'word_recording', 'word_closed',
      'word_message_only', 'word_ended'];
    const reworded = lineWords.filter((f) => String(resolved[f] || '').trim()).length;
    setTag('tagLinebox', reworded ? reworded + ' reworded' : 'built-in wording');
    setTag('tagTalkbar', resolved.show_push_to_talk ? 'on' : 'off — open mic');
    setTag('tagButtons', doorOrderValue().map((n) =>
      (DOORS.find((d) => d[0] === n) || [n, n])[1].toLowerCase()).join(' · '));
    setTag('tagSurface', (resolved.widget_theme || 'auto')
      + (resolved.widget_skin && resolved.widget_skin !== 'default'
          ? ' · ' + resolved.widget_skin : ''));
    setTag('tagPhone', (resolved.default_to_speaker ? 'loudspeaker' : 'earpiece')
      + (resolved.swipe_player ? ' · player' : ''));
    const fb = [resolved.ask_call_feedback && 'calls',
                resolved.ask_chat_feedback && 'texts',
                resolved.ask_vm_feedback && 'voicemail'].filter(Boolean);
    setTag('tagFeedback', fb.length ? 'asks · ' + fb.join(' · ') : 'never asks');
    paintCardCounts();
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
    // The door pages read the same switches this cluster writes, so they
    // repaint together and cannot disagree about whether a door is open.
    paintDoorStates();
    const btn = $('pauseBtn'), note = $('pausedNote'), sub = $('pausedSub');
    if (btn) {
      btn.classList.toggle('paused', paused);
      // ALSO `on`, the same class the three line buttons beneath it use.
      // Without it the line card was the only switch on the dashboard that
      // did not dim when it was off — it said "Paused" in words and stayed
      // at full strength, so the one control that stops every caller looked
      // identical whichever way it was set (operator-reported).
      btn.classList.toggle('on', !paused);
      btn.setAttribute('aria-pressed', paused ? 'false' : 'true');
      btn.title = paused ? 'Press to take calls again'
                         : 'Press to pause all calls immediately';
    }
    if (note) note.textContent = paused ? 'Paused' : 'Open';
    // …unless it is currently showing why the last press did not save. That
    // message has six seconds and paintDash runs several times inside them.
    if (sub && !sub.classList.contains('failed')) {
      sub.textContent = paused
        ? 'callers are turned away — press to reopen'
        : 'press to pause every call at once';
    }

    const liveOn = $('live_calls_enabled')
      ? $('live_calls_enabled').checked : !!resolved.live_calls_enabled;
    const vmOn2 = $('voicemail_enabled')
      ? $('voicemail_enabled').checked : !!resolved.voicemail_enabled;
    const chatOn = $('chat_enabled')
      ? $('chat_enabled').checked : !!resolved.chat_enabled;
    const mb = (id, on) => {
      const el = $(id);
      if (el) {
        el.classList.toggle('on', on);
        el.setAttribute('aria-pressed', on ? 'true' : 'false');
        const v = el.querySelector('.cv');
        if (v) v.textContent = on ? 'On' : 'Off';
      }
    };
    mb('modeLiveBtn', liveOn);
    mb('modeVmBtn', vmOn2);
    mb('modeChatBtn', chatOn);
    // The Live-on-air cluster's two quick kills, painted like the Lines
    // rows above them — same helper, same vocabulary.
    const oacOn = $('on_air_calls_enabled')
      ? $('on_air_calls_enabled').checked : !!resolved.on_air_calls_enabled;
    const oavOn = $('on_air_voicemail_enabled')
      ? $('on_air_voicemail_enabled').checked
      : !!resolved.on_air_voicemail_enabled;
    mb('modeOnAirCallsBtn', oacOn);
    mb('modeOnAirVmBtn', oavOn);
    // The two doors hang off the line itself: while it is paused nothing
    // answers whichever way they point — the server refuses the mint — so
    // they grey out and stop taking presses until the line reopens.
    ['modeLiveBtn', 'modeVmBtn', 'modeChatBtn',
     'modeOnAirCallsBtn', 'modeOnAirVmBtn'].forEach((id) => {
      if ($(id)) $(id).disabled = paused;
    });

    // What each door amounts to, written on it — unsaved picks included,
    // like every other live read on this page. Live calls: how many of the
    // caller permissions each tier can actually use (the same current-value
    // read the reference lists run on). Voicemail: who may talk to the
    // machine and where a message goes afterwards. Switched off, each card
    // goes back to saying what the switch does.
    const cnOf = (id) => { const el = $(id); return el && el.querySelector('.cn'); };
    // Counted once, read twice: the Live calls door and the Who-can-call
    // tile answer with the same numbers, because they ARE the same numbers.
    const RANK = { open: 0, guest: 1, admin: 2 };
    const usable = { open: 0, guest: 0, admin: 0 };
    Object.keys(SCHEMA.fields).forEach((f) => {
      if (!SCHEMA.fields[f].tiered) return;
      const v = permTier(f);
      if (v === 'off') return;
      Object.keys(RANK).forEach((t) => {
        if (RANK[t] >= RANK[v]) usable[t] += 1;
      });
    });
    const canDo = 'anyone ' + usable.open + ' · guest ' + usable.guest
      + ' · admin ' + usable.admin;
    // Chips, not a muted sentence (spec §6): each line row wears its
    // permissions as square chips, with the remaining detail as plain text
    // after them. Every word here is this file's own vocabulary — nothing
    // caller-supplied reaches the innerHTML.
    const chip = (t) => '<span class="permchip">' + t + '</span>';
    const said = (t) => '<span>' + t + '</span>';
    // The dump card exists exactly while the on-air door is open — unsaved
    // picks included, like every read on this page. The panel does not
    // poll, so the card is standing furniture for an armed feature; the
    // press itself asks whether a phone-in is actually live.
    if ($('onAirLine')) {
      $('onAirLine').hidden = permTier('allow_on_air') === 'off';
    }
    const liveNote = cnOf('modeLiveBtn');
    if (liveNote) {
      if (liveOn) {
        const vmFallback = vmOn2 && ((($('voicemail_when')
          && $('voicemail_when').value) || resolved.voicemail_when) !== 'always');
        const fallback = vmFallback && chatOn ? 'voicemail + text'
          : vmFallback ? 'voicemail' : chatOn ? 'text line' : 'none';
        liveNote.innerHTML = chip('anyone ' + usable.open)
          + chip('guest ' + usable.guest) + chip('admin ' + usable.admin)
          + said('fallback: ' + fallback);
        $('modeLiveBtn').title = 'How many caller permissions each tier can '
          + 'use, counted from the switches under Permissions & safety. '
          + 'Fallback is what answers when a live call cannot start.';
      } else {
        liveNote.textContent = 'the booth picks up';
        $('modeLiveBtn').title = '';
      }
    }
    const chatNote = cnOf('modeChatBtn');
    if (chatNote) {
      if (chatOn) {
        const WHO2 = { open: 'open to anyone', guest: 'guest code needed',
                       admin: 'admin only', off: 'no caller may use it' };
        chatNote.innerHTML = chip(WHO2[permTier('allow_chat')] || 'open to anyone')
          + said('closes after '
            + (($('chat_idle_minutes') && $('chat_idle_minutes').value)
               || resolved.chat_idle_minutes || 30) + 'm quiet');
      } else {
        chatNote.textContent = 'typed chat with the booth';
      }
    }
    const vmNote = cnOf('modeVmBtn');
    if (vmNote) {
      if (vmOn2) {
        // WHEN leads: "fallback" and "always on" are different machines,
        // and the card never said which one was running. Operator-reported.
        const when = (($('voicemail_when') && $('voicemail_when').value)
          || resolved.voicemail_when) === 'always'
          ? 'always on — voicemail-only'
          : 'fallback when the booth can’t pick up';
        const WHO = { open: 'open to anyone',
                      guest: 'guest code needed',
                      admin: 'admin only',
                      off: 'no caller may use it' };
        const DEST = { hold: 'held for you',
                       request: 'sent as song requests',
                       air: 'handed to the DJ',
                       triage: 'triaged by the model' };
        const dest = ($('voicemail_destination')
          && $('voicemail_destination').value) || resolved.voicemail_destination;
        vmNote.innerHTML = chip(WHO[permTier('allow_voicemail')] || WHO.open)
          + said(when + ' · ' + (DEST[dest] || DEST.hold));
      } else {
        vmNote.textContent = 'the machine answers';
      }
    }

    if ($('modeSay')) {
      $('modeSay').textContent = paused
        ? 'The line is paused — nothing answers, whatever these say, '
          + 'until it reopens.'
        : 'Together: ' + (liveOn && vmOn2
        ? 'a phone with an answering machine'
        : liveOn ? 'a plain phone — no machine'
        : vmOn2 ? 'a voicemail-only line'
        : 'both off — the line is closed, and the card tells callers so')
          + '.';
    }

    const l = live || {};
    const face = $('tileOnAirImg');
    const sil = $('tileOnAirSil');
    if (face) {
      // A degraded station can hand back an avatar URL that 404s, and a
      // broken-image glyph on the dashboard read as a fault (operator's
      // screenshot, 0.10.77). The drawn silhouette is the default; the
      // photo earns its place only by actually LOADING — 0.10.77 unhid it
      // on the URL merely existing, so the first paint showed the broken
      // glyph while the fetch was still in flight, and a poll repaint after
      // a failure re-showed it without re-judging (operator-reported,
      // 0.10.81). The silhouette holds the tile until onload says otherwise.
      const show = (photo) => {
        face.hidden = !photo;
        if (sil) sil.hidden = !!photo;
      };
      face.onload = () => show(true);
      face.onerror = () => { face.removeAttribute('src'); show(false); };
      if (!l.avatar) {
        face.removeAttribute('src');
        show(false);
      } else if (face.getAttribute('src') !== l.avatar) {
        show(false);              // silhouette until the new photo lands
        face.src = l.avatar;      // fires onload/onerror above
      } else if (face.complete && face.naturalWidth) {
        show(true);               // same photo, already proven — keep it up
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
    // The note answers "and what does each tier GET" — named as what it
    // counts, because bare numbers read as a riddle (operator-reported).
    // The missing-password warning still outranks it.
    // No password = no line: the server refuses every mint until one exists
    // (0.10.78), so the honest value here is the lockdown, not the mode the
    // door will have once it opens.
    tile('tileAccess', authConfigured ? (ACCESS[access] || access || '—') : 'Locked',
      authConfigured ? 'permissions — ' + canDo
                     : 'no calls until the admin password is set',
      authConfigured ? (access === 'open' ? 'warn' : 'ok') : 'bad');
    $('tileAccess').title = 'How many caller permissions each door tier can '
      + 'use — the switches under Caller permissions decide.';

    // Named rather than counted: "3 of 3 configured" is true of a call that
    // cannot happen, because a provider with no key is still a provider.
    // Unsaved picks included, like every other live read on this page.
    const pick = (id, fallback) => ($(id) && $(id).value) || fallback || '';
    // Blank provider is a state, not a glyph (0.10.80): a fresh install has
    // no brain until one is picked, and this tile says so instead of "?".
    const llm = pick('llm_provider', resolved.llm_provider);
    const ttsMode = pick('tts_mode', resolved.tts_mode);
    const chain = [llm || 'no brain', ttsMode || 'no voice',
                   pick('stt_provider', resolved.stt_provider) || '?'].join(' · ');
    const keyField = PROVIDER_KEY[llm];
    const needKey = !!(llm && keyField && secrets[keyField] && !secrets[keyField].set);
    tile('tileChain', chain,
      !llm ? 'pick the AI provider'
        : !ttsMode ? 'pick the voice backend'
        : needKey ? 'no key for ' + llm : (resolved.llm_model || ''),
      (!llm || !ttsMode || needKey) ? 'bad' : 'ok');
    paintNeeds();
    // Open Lines' dashboard row. Its own read, because what is UP right now
    // is server state rather than a setting — `resolved` cannot answer it.
    readOpenLines();
    readOpenLinesShelf();
  }

  // ------------------------------------------------- needs attention
  // The dashboard's other half (operator's ask, 0.10.76): what stands
  // between this deployment and a working call, derived from the SAME
  // signals the tiles read so the two can never disagree. Each row jumps to
  // its fix, and any page holding an item pins its chip in the picker.
  function computeNeeds() {
    const items = [];
    if (!authConfigured) {
      items.push({ page: 'safety', group: 'security',
                   label: 'Set the admin password',
                   note: 'the panel is open to whoever walks up, and every '
                     + 'line stays locked until it is set' });
    }
    const access = ($('front_access') && $('front_access').value)
      || resolved.front_access;
    if (access === 'guest' && !guestConfigured) {
      items.push({ page: 'safety', group: 'security',
                   label: 'Set the guest code',
                   note: 'the door demands a code nobody has — every call is refused' });
    }
    // A caller listening to the station loses it for the whole call. The
    // player cannot survive a live microphone (the stream feeds straight
    // back in and gets transcribed as the caller's own words), so it is
    // silenced at pickup — and tune-in is what is supposed to take over.
    // With tune-in off, or audible-off, nothing does. Only worth saying
    // while the card actually OFFERS the player: without it nobody was
    // listening through the card in the first place.
    const swipeOn = $('swipe_player')
      ? $('swipe_player').checked : !!resolved.swipe_player;
    const tuneOn = $('tune_in_on_call')
      ? $('tune_in_on_call').checked : !!resolved.tune_in_on_call;
    const tuneHeard = $('tune_in_audible')
      ? $('tune_in_audible').checked : resolved.tune_in_audible !== false;
    if (swipeOn && !(tuneOn && tuneHeard)) {
      items.push({ page: 'calls', group: 'tunein',
                   label: 'Calls go silent for a listener',
                   note: 'the card offers the station player, but a call '
                     + 'stops the music and nothing replaces it — turn on '
                     + 'Tune the caller in, and pipe the broadcast into the '
                     + 'call, or they hear nothing until they hang up' });
    }

    const llm = ($('llm_provider') && $('llm_provider').value)
      || resolved.llm_provider;
    // Blank since 0.10.80: a fresh install ships no provider pre-picked, so
    // the first gap is the choice itself — the key row below only makes
    // sense once there is a provider to hold a key for.
    if (!llm) {
      items.push({ page: 'config', group: 'brains',
                   label: 'Pick the AI provider',
                   note: 'no provider is chosen — the DJ has no model to think with' });
    }
    // The voice went blank-by-default at 0.10.85, same reasoning as the
    // brain: 'cloud' pre-picked failed mid-greeting on a keyless install.
    const ttsPick = ($('tts_mode') && $('tts_mode').value) || resolved.tts_mode;
    if (!ttsPick) {
      items.push({ page: 'config', group: 'voice',
                   label: 'Pick the voice backend',
                   note: 'no backend is chosen — the DJ has no voice to speak with' });
    }
    const keyField = PROVIDER_KEY[llm];
    if (llm && keyField && secrets[keyField] && !secrets[keyField].set) {
      items.push({ page: 'config', group: 'brains',
                   label: 'Add the ' + llm + ' key',
                   note: 'the DJ has no model to think with' });
    }
    if (live && live.reachable === false) {
      items.push({ page: 'config', group: 'station',
                   label: 'Reach the station',
                   note: 'nothing answers at the SUB/WAVE station API address' });
    }
    return items.concat(runningNeeds());
  }

  // ---- the other half: a WORKING install that has drifted -----------------
  // Everything above answers "can this deployment place a call at all", which
  // is a first-run question — so once you had set it up the box was blank for
  // good, and the operator said so: "kind of blank if you've done what you're
  // supposed to". These only appear on a deployment that already works, and
  // every one is something that has actually gone wrong here and gone
  // unnoticed because nothing on the page said it.
  function runningNeeds() {
    const items = [];
    const num = (k) => {
      const el = $(k);
      const v = el && el.value !== '' ? el.value : resolved[k];
      const n = parseFloat(v);
      return isNaN(n) ? null : n;
    };
    const on = (k) => {
      const el = $(k);
      return el ? (el.type === 'checkbox' ? el.checked : !!el.value)
                : !!resolved[k];
    };

    // The duck with a close and no open. A stored 0 beats the default and
    // nothing anywhere said so — the operator spent an evening on ducking
    // that "felt off in general" with the lead silently disabled.
    // The page must follow the `onair` section wherever GROUPS files it —
    // 'calls' once pinned the wrong chip in the picker, and since 0.97.81
    // the section lives on the On air page. A test walks every item here
    // against the schema now, which is how this move was caught.
    if (on('avoid_on_air_overlap') && num('on_air_handover_secs') === 0) {
      items.push({ page: 'air', group: 'onair',
                   label: 'Set the hand-over lead',
                   note: 'overlap protection is on but the lead is 0 — the DJ '
                     + 'is cut off the instant the station speaks, with no '
                     + 'warning to the caller. 5 is the default' });
    }

    // Switched on, credentialed for, and quietly impossible.
    const needsStation = ['allow_exact_queue', 'allow_cancel_queue',
                          'allow_sound_search', 'allow_announcements',
                          'allow_skills', 'allow_skip_track',
                          'allow_dj_segment', 'allow_takeover',
                          'allow_genre_lock', 'allow_never_play',
                          'allow_unfavorite'];
    const stationCreds = !!(secrets.subwave_admin_pass
                            && secrets.subwave_admin_pass.set);
    const armed = needsStation.filter((k) => {
      const v = ($(k) && $(k).value) || resolved[k];
      return v && v !== 'off';
    });
    if (armed.length && !stationCreds) {
      items.push({ page: 'safety', group: 'security',
                   label: 'Add the station admin credentials',
                   note: armed.length + ' caller permission'
                     + (armed.length === 1 ? ' is' : 's are')
                     + ' switched on that cannot work without them — the DJ '
                     + 'reaches for the tool and nothing happens' });
    }

    // The line is shut. It is on the switch, but the switch is a different
    // part of the page from the one you read when you wonder why it is quiet.
    if (on('calls_paused')) {
      items.push({ page: 'calls', group: 'usage',
                   label: 'The line is paused',
                   note: 'every caller is being turned away — unpause it on '
                     + 'the dashboard when you are ready' });
    }

    // Nothing to diagnose the next bad call with.
    if (!on('record_calls')) {
      items.push({ page: 'dj', group: 'record',
                   label: 'Transcripts are off',
                   note: 'nothing is written down, so a call that goes wrong '
                     + 'leaves no record to read back' });
    }

    return items.concat(hookNeeds(), callHealthNeeds());
  }

  // What the last few calls actually did. The records already carried all of
  // this; nothing was reading it back as a health signal.
  let recentCalls = null;

  async function loadRecentCalls() {
    // One read on load, for the health checks only — the call VIEWER still
    // fetches its own copy on demand. A dashboard that cannot see the last
    // ten calls cannot tell you the last ten calls went badly, which is the
    // most useful thing it could say.
    try {
      const d = await afetch('/calls').then((r) => r.json());
      recentCalls = Array.isArray(d.calls) ? d.calls
                    : Array.isArray(d) ? d : [];
    } catch (e) {
      recentCalls = [];    // never let this break the dashboard
    }
    paintNeeds();
  }

  // What the station's pushes are doing. One cheap admin read on load, for
  // the one fault nothing on this page could see: the webhook row can look
  // perfect at the station and still be keyed to a secret this box does not
  // hold, and then every push is turned away. Found on the operator's own
  // deployment 2026-08-16 — 59 rejections, none accepted, the panel showing
  // "registered", and the only reason anyone noticed was reading logs for
  // something else.
  let hookState = null;

  async function loadHookHealth() {
    try {
      const d = await afetch('/hooks/recent').then((r) => r.json());
      hookState = (d && d.registered) || null;
    } catch (e) {
      hookState = null;              // never let this break the dashboard
    }
    paintNeeds();
  }

  function hookNeeds() {
    if (!hookState) return [];
    const turned = Number(hookState.rejected || 0);
    const got = Number(hookState.received || 0);
    if (!turned || got) return [];
    return [{
      page: 'diag', diag: 'pipeline',
      key: 'hook-rejected',
      label: 'The station’s pushes are being turned away',
      note: turned + ' rejected, none accepted — the webhook row at the '
        + 'station is keyed to a secret this box does not hold, so the DJ is '
        + 'working from the slow poll instead of what the station says. It '
        + 're-keys itself within a minute; if this stays, run the pipeline '
        + 'check',
    }];
  }

  function callHealthNeeds() {
    const items = [];
    if (newerRelease) {
      // NO JUMP. Both version notices used to point at Configuration →
      // Station, which has nothing to do with either of them — the fix is a
      // pull and a restart at a terminal, and sending the operator to an
      // unrelated settings section to find that out is worse than sending
      // them nowhere (their ask, 2026-08-16).
      // Its own key, carrying the version: dismissing "0.10.155 is out" must
      // not also silence 0.10.160, which the digit-blind default key would.
      items.push({ key: 'newer:' + newerRelease,
                   label: 'Version ' + newerRelease + ' is out',
                   note: 'this box is on ' + (panelVersion || '?')
                     + ' — pull the image and restart both containers' });
    }
    if (!recentCalls || !recentCalls.length) return items;
    const recent = recentCalls.slice(0, 10);

    // The two processes ship as one image and run as two containers, so a
    // redeploy that recreates one and not the other leaves them skewed.
    //
    // ONLY ON A CALL THIS SERVER LIVED THROUGH. A record carries the version
    // of the worker that answered it, which is evidence about the PAST: pull a
    // new image and the newest transcript still names the old worker, so the
    // box reported a disagreement between what is running now and a call from
    // before the upgrade — "why am I getting the first notification" (operator,
    // 2026-08-16, panel 0.97.6 against a 0.97.4 record). A call answered after
    // this process booted is the only one that proves anything, because both
    // containers were up for it.
    const started = Number(serverSince || 0);
    const live = recent.filter((c) => {
      const t = Date.parse(c.startedAt || '');
      return c.appVersion && !isNaN(t) && (!started || t / 1000 >= started);
    });
    const workerV = (live[0] || {}).appVersion;
    if (workerV && panelVersion && workerV !== panelVersion) {
      items.push({ key: 'skew:' + panelVersion + ':' + workerV,
                   label: 'The two containers disagree',
                   note: 'this panel is ' + panelVersion + ' and a call taken '
                     + 'since it started was answered by a worker on ' + workerV
                     + ' — recreate both, they ship as one image' });
    }

    const hasProblem = (c, needle) => (c.problems || []).some(
      (p) => String(p.what || '').toLowerCase().indexOf(needle) !== -1);

    const silent = recent.filter((c) => hasProblem(c, 'no audio was ever'));
    if (silent.length >= 2) {
      // TO THE RECORDS, not to a settings section. This said "Read the
      // transcripts" and then jumped to ON-AIR DUCKING — it was filed under
      // the `onair` group and the pin follows the group, so the operator was
      // shown a ducking section that had nothing to do with it and asked why
      // ducking was being flagged. It never was. Nothing under settings fixes
      // a caller whose audio never arrived; the only move is to read the
      // calls, so that is where this goes now (operator's ask).
      items.push({ page: 'diag', diag: 'calls',
                   label: silent.length + ' of the last ' + recent.length
                     + ' calls heard nothing',
                   note: 'the caller\u2019s audio never arrived — off-LAN media, '
                     + 'a blocked microphone, or a silent caller. Opens the '
                     + 'transcripts' });
    }

    // The promise guard writes this line when the DJ narrates an action and
    // calls no tool. Once is the guard doing its job; a run of them is the
    // model routing badly, and the line says so itself.
    const narrated = recent.filter((c) => hasProblem(c, 'ran no tool'));
    if (narrated.length >= 3) {
      items.push({ page: 'config', group: 'brains',
                   label: 'The DJ keeps promising without acting',
                   note: narrated.length + ' of the last ' + recent.length
                     + ' calls needed a nudge to make a tool call — try a '
                     + 'model with better tool routing' });
    }

    // The provider refused us outright. ONE is worth saying — it costs a whole
    // reply, and the caller only ever sees "line dropped a beat", so without
    // this line the operator's evidence is a container log they do not keep.
    // Learned from the Gemini thought_signature 400 (0.10.119), which broke
    // every multi-tool chat turn for days while the panel looked healthy.
    const brainErr = recent.filter((c) => hasProblem(c, 'brain returned an error'));
    if (brainErr.length) {
      const why = ((brainErr[0].problems || []).find((p) =>
        String(p.what || '').indexOf('brain returned an error') !== -1) || {});
      items.push({ page: 'config', group: 'brains',
                   label: brainErr.length + ' of the last ' + recent.length
                     + ' conversations lost a reply',
                   note: 'the model provider rejected the request — '
                     + String(why.what || '').replace(
                       /^the DJ.s brain returned an error: /, '').slice(0, 180) });
    }

    const since = sinceLastVisit();
    if (since) items.push(since);

    const typed = recent.filter((c) => hasProblem(c, 'typed a tool call'));
    if (typed.length) {
      items.push({ page: 'config', group: 'brains',
                   label: 'The model is typing tool calls',
                   note: 'it wrote the call out as text instead of making it, '
                     + 'so nothing ran — a model-side failure' });
    }
    return items;
  }

  // ---- what happened while you were away ----------------------------------
  // Not a fault, and the only row in this box that isn't one: how many people
  // got through since the operator last marked the box read (their ask). The
  // watermark moves when they DISMISS it, never when the page loads — "since
  // you were last here, until cleared" means a visit cannot be what clears it,
  // or the answer would be zero every time by construction.
  const VISIT_KEY = 'callinPanelSeenCallsAt';
  const stamp = (c) => {
    const t = Date.parse(c && c.startedAt);
    return isNaN(t) ? 0 : t;
  };
  function visitMark() {
    const v = parseInt(localStorage.getItem(VISIT_KEY) || '', 10);
    return isNaN(v) ? null : v;
  }
  function markVisited() {
    try { localStorage.setItem(VISIT_KEY, String(Date.now())); } catch (e) {}
  }

  function sinceLastVisit() {
    if (!Array.isArray(recentCalls) || !recentCalls.length) return null;
    const mark = visitMark();
    // FIRST EVER LOAD: mark now and say nothing. Otherwise the first sight of
    // this box on any browser is "20 calls since you were last here", counting
    // a history the operator has already read.
    if (mark === null) { markVisited(); return null; }
    const fresh = recentCalls.filter((c) => stamp(c) > mark);
    if (!fresh.length) return null;
    const n = { call: 0, chat: 0, voicemail: 0 };
    fresh.forEach((c) => {
      n[c.kind === 'voicemail' ? 'voicemail'
        : c.kind === 'chat' ? 'chat' : 'call'] += 1;
    });
    const say = (count, one, many) =>
      count ? count + ' ' + (count === 1 ? one : many) : '';
    const bits = [say(n.call, 'call', 'calls'),
                  say(n.chat, 'text', 'texts'),
                  say(n.voicemail, 'voicemail', 'voicemails')].filter(Boolean);
    const rough = fresh.filter((c) => (c.problems || []).length
                                      || !(c.callerTurns || 0)).length;
    return {
      info: true, page: 'diag', diag: 'calls',
      // Its own key, carrying the newest record: dismissed, it stays gone
      // until somebody else calls, and then it is a new notification rather
      // than a silenced one.
      key: 'since:' + Math.max.apply(null, fresh.map(stamp)),
      label: bits.join(', ').replace(/, ([^,]*)$/, ' and $1')
             + ' since you were last here',
      note: (rough ? rough + ' of them had a problem — o' : 'O')
            + 'pens the transcripts. Dismissing marks the box read.',
      onDismiss: markVisited,
    };
  }

  // ---- dismissing one -----------------------------------------------------
  // Every item in this box is COMPUTED from live state, so nothing here can
  // delete one: dismissing is the operator saying "I have read that", and it
  // stays read. The list lives in this browser rather than on the box, because
  // it is a fact about a reader, not about the deployment — and it costs no
  // endpoint, no setting and no file the container has to be able to write.
  //
  // What comes back is what is genuinely NEW, and that is carried in the key
  // rather than in a sweep: `newer:0.98.0` is a different key from
  // `newer:0.97.7`, a skew names both versions, and the activity row names the
  // newest record it counted. A fault whose key does not change is the same
  // fault, and the operator has already read it.
  const SEEN_KEY = 'callinNotesSeen';
  // Digit-blind by default, so "7 of the last 8 calls heard nothing" does not
  // become a new notification when the count moves to 8 of 10. Items that
  // must re-notify on a number carry their own `key` (the version items do).
  const noteKey = (it) => it.key
    || (it.page + '|' + it.group + '|' + it.label.replace(/\d+/g, '#'));
  function seenKeys() {
    try {
      const v = JSON.parse(localStorage.getItem(SEEN_KEY) || '[]');
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }
  function setSeen(keys) {
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(keys)); } catch (e) {}
  }

  function paintNeeds() {
    const list = $('needsList');
    if (!list) return;
    const all = computeNeeds();
    // DISMISSED MEANS DISMISSED. These were pruned to what was live, on the
    // reasoning that a condition which clears and returns is news again — and
    // in practice the conditions here never clear: "5 of the last 10 calls
    // heard nothing" is true until five better calls push the bad ones out of
    // the window, so the row came back over and over after being read
    // ("after you dismiss ones like the ones indicating the failed calls they
    // should not return" — operator, 2026-08-16).
    //
    // The items that genuinely must re-notify carry it in their KEY instead:
    // a new release is `newer:0.98.0`, a new skew names both versions, so
    // those are new keys rather than resurrected ones. Bounded because it is
    // now append-only.
    const seen = seenKeys().slice(-200);
    setSeen(seen);
    const items = all.filter((it) => seen.indexOf(noteKey(it)) === -1);
    const clearBtn = $('needsClearBtn');
    if (clearBtn) {
      clearBtn.hidden = !items.length;
      clearBtn.onclick = () => {
        // Clear-all is a dismissal of each, so anything with a watermark
        // moves it — otherwise the activity row is the one thing Clear
        // cannot clear, and it would come back on the next repaint.
        all.forEach((it) => { if (it.onDismiss) it.onDismiss(); });
        setSeen(all.map(noteKey));
        paintNeeds();
      };
    }
    list.innerHTML = '';
    // Empty is a state, not an absence (operator, 0.10.92): the box keeps its
    // column and says so in the middle of it, instead of shrinking to a bare
    // header beside the tall transmission cluster.
    list.classList.toggle('empty', !items.length);
    if (!items.length) {
      const d = document.createElement('div');
      d.className = 'needempty';
      d.textContent = all.length
        ? 'Nothing new — everything here has been read.'
        : 'Nothing needs attention — the line is ready.';
      list.appendChild(d);
    }
    items.forEach((it) => {
      // A row is a wrapper with TWO buttons in it, not one button — the
      // dismiss × cannot be nested inside the jump, and a × that also jumped
      // to the page it was dismissing would be its own bug report.
      const row = document.createElement('div');
      // `info` is not a fault and must not wear the fault's colour — coral
      // means something is wrong everywhere else on this page.
      row.className = 'needrow' + (it.info ? ' info' : '');
      // A row with nowhere to go is not a button. The version notices are
      // fixed at a terminal, not in a settings section, and dressing them as
      // links sent the operator to Configuration → Station to read about a
      // docker pull.
      const goes = !!(it.diag || it.group);
      const b = document.createElement(goes ? 'button' : 'div');
      if (goes) b.type = 'button';
      b.className = 'needjump' + (goes ? '' : ' flat');
      const k = document.createElement('span');
      k.className = 'nk';
      k.textContent = it.label;
      const n = document.createElement('span');
      n.className = 'nn';
      n.textContent = it.note;
      b.append(k, n);
      // An item either names a settings section to fix, or a DIAGNOSTICS
      // viewer to read. The second is not a lesser case: nothing under
      // settings fixes a call that already went wrong, and sending an
      // operator to a settings page for one is how this box ended up
      // flagging ducking for calls that heard nothing.
      b.onclick = () => {
        if (it.diag) {
          const sec = document.querySelector(
            'details.diag[data-diag="' + it.diag + '"]');
          if (!sec) return;
          showSection(sec);
          // Opened AND loaded — the viewer is a button, and arriving at a
          // closed one having been promised transcripts is a dead end.
          const load = $('viewCallsBtn');
          if (it.diag === 'calls' && load) load.click();
          return;
        }
        showSection(document.querySelector(
          'details.sec[data-group="' + it.group + '"]'));
      };
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'ndismiss';
      x.textContent = '×';
      x.title = 'Dismiss — it comes back if this happens again';
      x.setAttribute('aria-label', 'Dismiss this notification');
      x.onclick = () => {
        if (it.onDismiss) it.onDismiss();
        setSeen(seenKeys().concat([noteKey(it)]));
        paintNeeds();
      };
      row.append(b, x);
      list.appendChild(row);
    });
    if ($('needsSay')) {
      // The empty message lives in the body now — saying it in the caption
      // too would read the same sentence twice in one box.
      // Only the FAULTS are counted here. "3 things before the line is ready"
      // with two of them being "4 calls since you were last here" would be the
      // box telling the operator their working deployment is broken.
      const faults = items.filter((it) => !it.info).length;
      $('needsSay').textContent = faults
        ? faults + ' thing' + (faults === 1 ? '' : 's')
          + ' before the line is ready'
        : '';
    }
    const pages = new Set(items.map((it) => it.page));
    document.querySelectorAll('#panelNav a[data-page]').forEach((a) => {
      a.classList.toggle('attn', pages.has(a.dataset.page));
    });
    // …and on the SECTION the item is actually in. The picker's pin got you to
    // the right page and then stopped, leaving you to guess which of eight
    // folded sections it meant (operator's ask). Every item already names its
    // group, which is the section's own id — so the same mark, one level down,
    // and it clears itself the moment the item does.
    const groups = new Set(items.map((it) => it.group).filter(Boolean));
    document.querySelectorAll('details.sec[data-group]').forEach((sec) => {
      sec.classList.toggle('attn', groups.has(sec.dataset.group));
    });
  }

  async function paintNightTile() {
    if (!$('tileCalls')) return;
    const jumpToRecords = () => {
      const sec = document.querySelector('details.diag[data-diag="calls"]');
      if (sec) {
        showSection(sec);
        $('viewCallsBtn').click();
      }
    };
    const jumpToVoicemail = () => {
      showSection(document.querySelector('details.sec[data-group="voicemail"]'));
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
        [rough && rough + ' failed',
         up && '\u25b2' + up, down && '\u25bc' + down]
          .filter(Boolean).join(' \u00b7 ')
          || (lives.length ? 'all clean' : 'records appear here'),
        rough ? 'warn' : lives.length ? 'ok' : undefined);
      // Where the messages went, not just that they exist \u2014 delivery is the
      // half of voicemail the operator cannot see from the card, and "held"
      // means there is something waiting for them in the section.
      const held = vms.filter((c) => /held/i.test(
        (((c.tools || []).find((t) => t.name === 'voicemail_delivery') || {})
          .result) || '')).length;
      tile('tileVm', vms.length ? vms.length + ' taken' : 'none yet',
        vms.length
          ? [(vms.length - held) && (vms.length - held) + ' passed on',
             held && held + ' held for you']
              .filter(Boolean).join(' \u00b7 ')
          : '',
        held ? 'warn' : vms.length ? 'ok' : undefined);
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
  // The two mode switches post the moment they're pressed — closing a door
  // is a thing you are doing, not a setting you are drafting. Same contract
  // as the kill switch above them.
  async function saveMode(field, next, btn) {
    const box = $(field);
    if (!box) return;
    const before = box.checked;
    box.checked = next;
    if (btn) btn.disabled = true;
    paintDash();
    try {
      const r = await afetch('/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: next }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'refused');
      resolved[field] = next;
      overrides[field] = next;
    } catch (e) {
      // Say it, like savePaused does. This branch used to mutate a throwaway
      // object literal and pass empty text, so a failed door toggle reverted
      // in total silence — the operator thought a door had closed when a
      // transient 401 meant it hadn't (0.10.58 review).
      box.checked = before;
      if ($('saveMsg')) {
        $('saveMsg').textContent = 'Could not change that — ' + e.message;
        setTimeout(() => { $('saveMsg').textContent = ''; }, 4000);
      }
    } finally {
      if (btn) btn.disabled = false;
      // paintTags rather than paintDash: the Doors-to-air section tag reads
      // the same two switches these buttons flip, and paintTags ends by
      // calling paintDash anyway.
      paintTags();
      applyVisibility();
      markClean();
    }
  }

  function bindModeButtons() {
    const wire = (btnId, field) => {
      const btn = $(btnId);
      if (!btn) return;
      btn.onclick = () => saveMode(field, !$(field).checked, btn);
    };
    wire('modeLiveBtn', 'live_calls_enabled');
    wire('modeVmBtn', 'voicemail_enabled');
    wire('modeChatBtn', 'chat_enabled');
    wire('modeOnAirCallsBtn', 'on_air_calls_enabled');
    wire('modeOnAirVmBtn', 'on_air_voicemail_enabled');
    // The broadcast-delay dump: posts immediately like every dash control,
    // and the card's own note line carries the server's answer — dumped, or
    // no phone-in live. Never through Save; a dump is not a form draft.
    const dump = $('dumpBtn');
    if (dump) dump.onclick = async () => {
      const note = $('pullNote');
      const resting = 'pulls whoever is live — the turn in hand never airs';
      dump.disabled = true;
      try {
        const r = await afetch('/on-air/dump', { method: 'POST' });
        const d = await r.json().catch(() => ({}));
        note.textContent = !r.ok
          ? (d.error || 'refused')
          : d.ok ? 'pulled — the held turn will not air'
                 : (d.note || 'no phone-in is on the air right now');
      } catch (e) {
        note.textContent = 'unreachable — try again';
      } finally {
        dump.disabled = false;
        // The answer has its moment, then the card goes back to saying what
        // it does — a stale "unreachable" sat on the operator's dashboard
        // for a whole session (their screenshot, 2026-08-17).
        setTimeout(() => { note.textContent = resting; }, 6000);
      }
    };
  }
  bindModeButtons();

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
      // …and ON THE CARD, which is where the operator is looking. saveMsg
      // lives at the bottom of the page beside Save; the dashboard is at the
      // top. So a refused pause reverted the switch and explained itself
      // three screens away, which reads as "I clicked it and nothing
      // happened" — reported as "you are not able to untick the line box".
      const sub = $('pausedSub');
      if (sub) {
        sub.textContent = 'that did not save — ' + (e.message || 'refused');
        sub.classList.add('failed');
        setTimeout(() => { sub.classList.remove('failed'); paintDash(); }, 6000);
      }
    } finally {
      btn.disabled = false;
      paintDash();
      // The card-section note reads the line state too — pausing is exactly
      // when it appears, so it cannot wait for the next field edit.
      applyVisibility();
    }
  }

  $('pauseBtn').onclick = () => savePaused(!$('calls_paused').checked);

  // A tile is a jump link with an answer written on it. data-jump names a
  // section's group id, and showSection turns to the page that owns it and
  // OPENS it as well as scrolling: everything is folded by default, so a
  // scroll alone lands on a one-line heading with the answer still hidden.
  document.querySelectorAll('.dash .tile').forEach((el) => {
    el.onclick = () => {
      showSection(document.querySelector(
        'details.sec[data-group="' + el.dataset.jump + '"]'));
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

  // Which caller the reference is being read AS. 'all' is the operator's
  // overview; a tier shows exactly the list that tier's caller would be
  // offered — the same filter the card's own "?" popup applies for real.
  let askView = 'all';
  const TIER_RANK = { open: 0, guest: 1, admin: 2 };

  function paintAsks() {
    const host = $('askList');
    if (!host) return;
    host.innerHTML = '';
    let on = 0;

    const renderAsk = (a) => {
      const enabled = !a.need || permOn(a.need);
      // A tier view hides what that caller would never be offered — the
      // point is the caller's own menu, not the operator's inventory.
      if (askView !== 'all') {
        const reachable = enabled && (!a.need || !isTiered(a.need)
          || TIER_RANK[askView] >= TIER_RANK[permTier(a.need)]);
        if (!reachable) return;
      }
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
      } else if (enabled) {
        // No switch behind it — reads of live state and in-character talk.
        // Unlabelled, these rows sat as the odd ones out in a column of
        // answers to "who gets this". Operator-reported.
        const chip = document.createElement('span');
        chip.className = 'whotier t-always';
        chip.textContent = 'always';
        chip.title = 'No switch — available to every caller, whatever is set.';
        li.querySelector('.why').appendChild(chip);
      }
      host.appendChild(li);
    };

    // Count enabled over EVERY ask (the tag reads "N of M available"),
    // separate from what the tier view chooses to draw.
    ASKS.forEach((a) => { if (!a.need || permOn(a.need)) on++; });

    // Grouped, with a heading per group that renders any row in the current
    // view — the reads, the requests and the on-air actions are three
    // different kinds of thing, and the flat list hid that.
    ASK_GROUPS.forEach(([key, label, blurb]) => {
      const before = host.children.length;
      const head = document.createElement('li');
      head.className = 'askhead';
      head.innerHTML = '<span class="askheadname"></span><span class="askheadwhy"></span>';
      head.querySelector('.askheadname').textContent = label;
      head.querySelector('.askheadwhy').textContent = blurb || '';
      host.appendChild(head);
      ASKS.filter((a) => a.group === key).forEach(renderAsk);
      // Nothing rendered under it in this view — drop the empty heading.
      if (host.children.length === before + 1) host.removeChild(head);
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

  // The reference's point of view, one press each. Everything is the
  // operator's inventory; a tier is that caller's own menu.
  const ASK_VIEWS = { askViewAll: 'all', askViewOpen: 'open',
                      askViewGuest: 'guest', askViewAdmin: 'admin' };
  Object.keys(ASK_VIEWS).forEach((id) => {
    const btn = $(id);
    if (!btn) return;
    btn.onclick = () => {
      askView = ASK_VIEWS[id];
      Object.keys(ASK_VIEWS).forEach((b) => {
        if ($(b)) $(b).classList.toggle('on', b === id);
      });
      paintAsks();
    };
  });

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
  // Is a field's prerequisite satisfied right now? Extracted from
  // applyVisibility at 0.98.22 so the finder can ask the same question — it
  // marks a result whose switch is off rather than offering a setting that
  // cannot do anything, which is how an operator sets a value, saves, and
  // watches nothing happen.
  function needsMet(need) {
    if (!need) return true;
    const [dep, want] = need;
    const depEl = $(dep);
    const current = depEl
      ? (depEl.type === 'checkbox' ? depEl.checked : (depEl.value || resolved[dep]))
      : resolved[dep];
    if (want === true) return !!current;
    // `false` means "only while the other field is EMPTY". Used where one
    // setting replaces another: writing an Opening line overrides Greeting
    // style entirely, and showing both with no sign of which wins is the
    // shape 0.9.61 took out of front_access.
    if (want === false) return !current;
    if (Array.isArray(want)) return want.indexOf(current) !== -1;
    return current === want;
  }

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
    // The mirror rule: with the machine off, the card never offers it, so
    // the options that put its button up are equally moot.
    const MOOT_WITHOUT_VM = ['show_voicemail_button', 'embed_voicemail_button'];
    // With both doors to air shut, the Go-live tier row and its two dials
    // still stand under Caller permissions — a permission for a route that
    // cannot happen (operator's ask, 0.97.81). Greyed, not hidden, the same
    // shape as the two lists above: the operator can still read what comes
    // back when a door reopens.
    const oacOn = $('on_air_calls_enabled')
      ? $('on_air_calls_enabled').checked : !!resolved.on_air_calls_enabled;
    const oavOn = $('on_air_voicemail_enabled')
      ? $('on_air_voicemail_enabled').checked
      : !!resolved.on_air_voicemail_enabled;
    const airShut = !oacOn && !oavOn;
    const MOOT_WITHOUT_AIR = ['allow_on_air', 'on_air_max_seconds',
                              'on_air_delay_secs'];

    // The line status outranks everything in the card section: paused (or
    // both modes off) the card shows its closed face whatever is set here,
    // and the preview — a real card — shows that too. Say why, in one line,
    // instead of leaving the operator to think their edits stopped landing.
    const paused = $('calls_paused')
      ? $('calls_paused').checked : !!resolved.calls_paused;
    const note = $('previewLineNote');
    if (note) {
      const msg = paused
        ? 'The line is paused — callers see the closed card whatever is set '
          + 'here, and the preview shows what callers see. It all applies '
          + 'again when the line reopens.'
        : (!liveOn && !vmOn)
        ? 'Live calls and voicemail are both off (the dashboard’s Lines) '
          + '— the line is closed, and the card says so instead of '
          + 'offering these.'
        : liveOff
        ? 'The line is voicemail-only — the card offers the machine, so the '
          + 'live-call options are parked until live calls come back.'
        : '';
      note.textContent = msg;
      note.hidden = !msg;
    }

    // Every rule comes from the schema: a field declares what it depends on,
    // and advanced fields stay hidden until asked for.
    Object.keys(SCHEMA.fields).forEach((f) => {
      const el = $(f);
      if (!el) return;
      const meta = SCHEMA.fields[f];
      // .prow included: the matrix rows are anchors too, or a field that
      // lives there can never be mooted — push to talk was listed in
      // MOOT_WITHOUT_LIVE from the start and silently never dimmed.
      const anchor = el.closest('.row') || el.closest('.check')
        || el.closest('.prow');
      if (!anchor) return;

      if (MOOT_WITHOUT_LIVE.indexOf(f) !== -1) {
        anchor.classList.toggle('moot', liveOff);
        anchor.title = liveOff
          ? 'The line is voicemail-only — there is no live Call button for '
            + 'this to apply to.' : '';
      }
      if (MOOT_WITHOUT_VM.indexOf(f) !== -1) {
        anchor.classList.toggle('moot', !vmOn);
        anchor.title = !vmOn
          ? 'Voicemail is off (its own page, or the dashboard’s Lines) '
            + '— the card never offers the machine, whichever way this '
            + 'points.' : '';
      }
      if (MOOT_WITHOUT_AIR.indexOf(f) !== -1) {
        anchor.classList.toggle('moot', airShut);
        anchor.title = airShut
          ? 'Both doors to air are shut (the On air page, or the dashboard’s '
            + 'Live-on-air cluster) — nobody reaches the broadcast whatever '
            + 'this grants.' : '';
      }

      const visible = needsMet(meta.needs);

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
        // The Players page's sections live inside the wrapper, not as
        // siblings — look through it, or its band always reads as empty.
        if (n.id === 'cardWrap'
            && [...n.querySelectorAll('details.sec')].some(
                 (d) => d.style.display !== 'none')) anyVisible = true;
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
    // The attributes belong on the DIV — embed.js reads its mount element,
    // never the script tag. The builder had them on the script, so a copied
    // snippet's theme and captions choices silently did nothing.
    const attrs = [];
    const mode = $('embedMode') && $('embedMode').value;
    const theme = $('embedTheme') && $('embedTheme').value;
    const caps = $('embedCaptions') && $('embedCaptions').value;
    if (mode) attrs.push(' data-mode="' + mode + '"');
    if (theme) attrs.push(' data-theme="' + theme + '"');
    if (caps) attrs.push(' data-captions="' + caps + '"');
    $('embedSnippet').value =
      '<div id="subwave-callin"' + attrs.join('') + '></div>\n' +
      '<script src="' + location.origin + '/embed.js"><\/script>';
  }
  // The preview stage wears whichever shape is selected: inline shows the card
  // as-is; launcher/dock/button dress the frame as that shape with a mock
  // trigger, so the operator sees what they picked before copying it. Only in
  // the Embed view — the Page tab is the standalone phone, which has no shape.
  function paintPreviewShape() {
    const stage = $('previewStage');
    if (!stage) return;
    const mode = ($('embedMode') && $('embedMode').value) || '';
    const shaped = previewSurface === 'embed'
      && (mode === 'launcher' || mode === 'dock' || mode === 'button');
    stage.dataset.shape = shaped ? mode : 'inline';
    stage.classList.toggle('shaped', shaped);
    // A shape starts CLOSED (the trigger showing), so the operator sees the
    // resting state first; the frame appears when they press it. Inline is
    // always "open" — there is nothing to press.
    stage.classList.toggle('open', !shaped);
    const trig = $('previewTrigger');
    if (trig) trig.hidden = !shaped;
    // Tell the operator the preview trigger is a real control they can press —
    // otherwise the mock pill/bar/button reads as a static picture and the
    // "open the card" half of the shape never gets discovered.
    const note = $('previewNote');
    if (note) {
      note.textContent = shaped
        ? (stage.classList.contains('open')
            ? 'Press it again to close'
            : 'Press the button to see the card open')
        : 'live · unsaved';
    }
  }
  if ($('previewTrigger')) {
    $('previewTrigger').onclick = () => {
      const open = $('previewStage').classList.toggle('open');
      const note = $('previewNote');
      if (note) note.textContent = open
        ? 'Press it again to close'
        : 'Press the button to see the card open';
    };
  }
  if ($('embedTheme')) {
    $('embedMode').onchange = () => {
      paintEmbedSnippet();
      // Picking a shape is a request to SEE it — jump the preview to Embed.
      const mode = $('embedMode').value;
      if (mode && previewSurface !== 'embed') setPreviewSurface('embed');
      else paintPreviewShape();
    };
    $('embedTheme').onchange = paintEmbedSnippet;
    $('embedCaptions').onchange = paintEmbedSnippet;
  }

  // The "Start here" banner and the first-run password card both retired at
  // 0.10.77 (operator's call): the dashboard's NOTIFICATIONS column says
  // the same things, in one place, with a jump on every row.

  // The provider dropdowns, refillable on their own: they list only what a
  // key exists for, so they go stale the moment a key is saved or the lists
  // are reloaded — the operator saved a google key, watched google's MODELS
  // arrive, and found no google in the provider list until a full page
  // reload (operator-reported, 0.10.85). fill() keeps the current selection.
  // THE BLANK OPTION DESCRIBES THE LAYER BELOW, not the current value. It read
  // 'Default — ' + resolved, and `resolved` includes the operator's own choice
  // — so having picked Google, the top of the list said "Default — google" and
  // claimed a default that does not exist. On a fresh install the same option
  // read "Not set — pick a provider" correctly, which is to say it was honest
  // exactly when nobody was looking at it (operator-reported, 2026-08-16).
  // `beneath` is what clearing would actually leave: env over defaults.
  function blankFor(field, pickWord) {
    const under = beneath[field];
    return under ? 'Default — ' + under + ' (from the environment)'
                 : 'Not set — pick ' + pickWord;
  }

  function paintProviderChoices() {
    fill('llm_provider', options.llmProviders,
      { labels: options.llmProviderLabels || null,
        blankLabel: blankFor('llm_provider', 'a provider') });
    // EVERY OPTION SAYS WHAT IT COSTS YOU. The list read as four peers, so
    // "OpenAI and Google reuse the keys above" left the obvious question
    // unanswered — then what is Deepgram? (operator's ask). It is the one
    // with its own account, and the label is where that belongs.
    fill('stt_provider', options.sttProviders, {
      labels: {
        // No "(default)" here: the blank option above it carries that, and
        // the two lines read as one choice offered twice.
        local: 'Built-in Whisper — no key, runs here',
        openai: 'OpenAI — reuses your OpenAI key',
        google: 'Google — reuses your Google key',
        deepgram: 'Deepgram — needs its own account and key, below',
      },
      // NOT a second "built-in Whisper" line. The blank and the local option
      // said the same thing one above the other, which reads as two ways to
      // pick one provider (operator's screenshot). The blank is the fall-back,
      // and it names it as such.
      blankLabel: beneath.stt_provider === 'local'
        ? 'Default — built-in Whisper'
        : blankFor('stt_provider', 'an ear'),
    });
  }

  function paint() {
    fill('tts_mode', options.ttsModes, {
      blankLabel: blankFor('tts_mode', 'a backend'),
      labels: { local: 'Local — your own OpenAI-compatible speech server',
                cloud: 'Cloud — a hosted speech API' },
    });
    SELECT_FIELDS.filter(hasChoices).forEach(fillStatic);
    fill('tts_adapter', options.ttsAdapters);
    fill('tts_voice', options.voices, { blankLabel: "Station's voice for this DJ" });
    // Labelled, because the ids alone do not say what they are — "gateway" in
    // a list next to "google" is a coin toss, and two of these are
    // aggregators rather than vendors.
    paintProviderChoices();

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
      // The order field is the one text field whose STORED value can be blank
      // while the thing it controls has a real order — blank means "fall
      // through", and the resolved value is what the card is actually using.
      // Showing the operator an empty list to drag would be a lie.
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
    // Assigning .value fires no input event, so the control that renders this
    // field has to be told the value moved underneath it.
    paintDoorOrder();
    window.Panel.sounds.paintSlotCards();
    applyVisibility();
    setEmbedSnippet();
    paintAdminNeeded();
    paintAsks();
    paintTools();
    paintTags();
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
    // The slot cards read the sound fields AND the pack picker's selected
    // label, both of which this repaint just refilled — without this they
    // sat on "sound set default" until the first user edit.
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
    paintAccess();
    $('setPwBtn').textContent = authConfigured ? 'Change password' : 'Set password';
    $('logoutBtn').hidden = !authConfigured;
    $('setGuestBtn').textContent = guestConfigured ? 'Change guest code' : 'Set guest code';
    $('clearGuestBtn').hidden = !guestConfigured;
    // Setting or clearing the guest code changes which permission columns can
    // be ticked at all — that has to follow immediately, not on a reload.
    paintPermissions();

    paintNeeds();
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


  // --- Open Lines, direction 1a -------------------------------------------
  // The settings themselves are still ordinary schema fields — the segmented
  // pairs and the chip grid are costumes over real inputs, so saving, the
  // finder, the save diff and every `needs` rule keep working untouched.
  let olRoster = [];
  let olShelf = [];
  let olStatus = {};

  function olWho() {
    const el = $('open_lines_personas');
    return String((el && el.value) || '')
      .split(',').map((s) => s.trim()).filter(Boolean);
  }

  function olSetField(el, value) {
    // A trusted-looking change, so the panel's diff and the save overlay treat
    // this exactly like typing in the field it stands for.
    if (el.type === 'checkbox') el.checked = !!value;
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // Two buttons over one field. data-zero marks the number case: "manual only"
  // is 0 and "on a cadence" is any non-zero, which is the same decision the
  // setting has always stored.
  function paintSegs() {
    for (const seg of document.querySelectorAll('.olseg')) {
      const el = $(seg.dataset.for);
      if (!el) continue;
      const zero = seg.dataset.zero === '1';
      const on = zero ? Number(el.value || 0) > 0 : el.checked;
      for (const b of seg.querySelectorAll('button')) {
        b.setAttribute('aria-pressed', String(!!b.dataset.on === on));
      }
      if (zero) {
        // The interval only means anything on a cadence, and it reads dim
        // while manual rather than vanishing — the number is still the value.
        const num = seg.parentElement.querySelector('.olnum');
        if (num) num.disabled = !on;
      }
    }
  }

  for (const seg of document.querySelectorAll('.olseg')) {
    for (const b of seg.querySelectorAll('button')) {
      b.onclick = () => {
        const el = $(seg.dataset.for);
        if (!el) return;
        const want = !!b.dataset.on;
        if (seg.dataset.zero === '1') {
          // Turning a cadence on needs a number to be a cadence. 20 is the
          // interval the rest of this section already defaults to.
          olSetField(el, want ? (Number(el.value || 0) || 20) : 0);
        } else {
          olSetField(el, want);
        }
        paintSegs();
        paintOpenLinesWho();
      };
    }
  }
  for (const id of ['open_lines_enabled', 'open_lines_every_minutes']) {
    if ($(id)) $(id).addEventListener('change', paintSegs);
  }

  function paintOpenLinesWho() {
    const grid = $('olDjGrid');
    const count = $('olDjCount');
    if (!grid) return;
    const chosen = new Set(olWho());
    grid.innerHTML = '';
    for (const p of olRoster) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'olbtn';
      const on = chosen.has(p.id);
      b.setAttribute('aria-pressed', String(on));
      b.textContent = (on ? '✓ ' : '') + (p.name || p.id);
      b.onclick = () => {
        const next = new Set(olWho());
        if (next.has(p.id)) next.delete(p.id); else next.add(p.id);
        olSetWho([...next]);
      };
      grid.appendChild(b);
    }
    if (count) {
      // Zero ticked is a valid state and says so in words, because "0 of 22"
      // reads as broken rather than as "anyone".
      count.textContent = chosen.size
        ? chosen.size + ' of ' + olRoster.length + ' ticked'
        : 'whoever is on';
    }
  }

  function olSetWho(ids) {
    olSetField($('open_lines_personas'), ids.join(','));
    paintOpenLinesWho();
  }

  if ($('olDjAll')) {
    $('olDjAll').onclick = () => olSetWho(olRoster.map((p) => p.id));
    $('olDjNone').onclick = () => olSetWho([]);
  }

  function olWhoName(id) {
    const p = olRoster.find((x) => x.id === id);
    return p ? (p.name || p.id) : id;
  }

  function olWhoSelect(item) {
    const sel = document.createElement('select');
    sel.className = 'olwho';
    const any = document.createElement('option');
    any.value = '';
    any.textContent = 'Any DJ';
    sel.appendChild(any);
    for (const p of olRoster) {
      const o = document.createElement('option');
      o.value = p.id;
      o.textContent = p.name || p.id;
      sel.appendChild(o);
    }
    const who = (item.personas || [])[0] || '';
    sel.value = who;
    sel.dataset.set = who ? '1' : '';
    sel.onchange = () => olSave('/open-lines/premises/' + encodeURIComponent(item.id),
                               { personas: sel.value ? [sel.value] : [] });
    return sel;
  }

  function olWhen(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()];
    let h = d.getHours();
    const ampm = h >= 12 ? 'pm' : 'am';
    h = h % 12 || 12;
    return day + ' ' + h + ':' + String(d.getMinutes()).padStart(2, '0') + ampm;
  }

  // Least recently used goes up next — the same rule the server picks by, so
  // the row the panel marks is the row that will actually air.
  function olNextUpId() {
    if (!olShelf.length) return '';
    const sorted = [...olShelf].sort((a, b) =>
      String(a.last_used || '').localeCompare(String(b.last_used || ''))
      || (a.used || 0) - (b.used || 0));
    return sorted[0].id;
  }

  function paintOpenLinesShelf() {
    const wrap = $('olShelfRows');
    if (!wrap) return;
    wrap.innerHTML = '';
    const nextUp = olNextUpId();

    for (const item of olShelf) {
      const row = document.createElement('div');
      row.className = 'olrowgrid' + (item.id === nextUp ? ' next' : '');

      const grip = document.createElement('span');
      grip.className = 'olgrip';
      grip.textContent = '⠿';
      grip.setAttribute('aria-hidden', 'true');
      row.appendChild(grip);

      const subj = document.createElement('div');
      subj.className = 'olsubj';
      const line = document.createElement('div');
      line.style.display = 'flex';
      line.style.alignItems = 'baseline';
      line.style.gap = '8px';
      const text = document.createElement('span');
      text.className = 'olsubjtext';
      text.textContent = item.text;          // full text, never truncated
      line.appendChild(text);
      if (item.starter) {
        const chip = document.createElement('span');
        chip.className = 'olbuiltin';
        chip.textContent = 'built in';
        line.appendChild(chip);
      }
      subj.appendChild(line);
      if (item.id === nextUp) {
        const up = document.createElement('span');
        up.className = 'olnextup';
        up.textContent = 'next up when a line opens';
        subj.appendChild(up);
      }
      row.appendChild(subj);

      row.appendChild(olWhoSelect(item));

      const used = document.createElement('span');
      used.className = 'olused' + (item.used ? '' : ' never');
      used.textContent = item.used ? String(item.used) : '—';
      row.appendChild(used);

      const last = document.createElement('span');
      const when = olWhen(item.last_used);
      last.className = 'ollast' + (when ? '' : ' never');
      last.textContent = when || 'never';
      row.appendChild(last);

      const acts = document.createElement('div');
      acts.className = 'olacts';
      const air = document.createElement('button');
      air.type = 'button';
      air.className = 'olbtn air';
      air.textContent = 'Air it';
      air.onclick = () => olOpen({ premise_id: item.id }, air);
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'olbtn x';
      del.textContent = '✕';
      del.setAttribute('aria-label', 'Remove this subject');
      del.onclick = () => {
        del.disabled = true;
        olSave('/open-lines/premises/' + encodeURIComponent(item.id), null, 'DELETE');
      };
      acts.appendChild(air);
      acts.appendChild(del);
      row.appendChild(acts);

      // Narrow only: what the two folded columns were saying.
      const meta = document.createElement('span');
      meta.className = 'olmeta';
      meta.textContent = (item.used ? 'used ' + item.used : 'never used')
        + (when ? ' · ' + when : '');
      row.appendChild(meta);

      wrap.appendChild(row);
    }

    const mine = olShelf.filter((i) => !i.starter).length;
    const built = olShelf.length - mine;
    const bits = [olShelf.length];
    if (olShelf.length) bits.push(mine + ' yours, ' + built + ' built in');
    bits.push('least recently used goes up next');
    if ($('olShelfCount')) $('olShelfCount').textContent = bits.join(' · ');
    if ($('olStatShelf')) {
      $('olStatShelf').textContent = olShelf.length
        + (olShelf.length === 1 ? ' subject' : ' subjects');
    }

    const who = $('olAddWho');
    if (who && who.options.length <= 1) {
      for (const p of olRoster) {
        const o = document.createElement('option');
        o.value = p.id;
        o.textContent = p.name || p.id;
        who.appendChild(o);
      }
    }
  }

  async function olSave(path, body, method) {
    try {
      const opts = { method: method || 'POST' };
      if (body) {
        opts.headers = { 'Content-Type': 'application/json' };
        opts.body = JSON.stringify(body);
      }
      const r = await afetch(path, opts);
      const d = await r.json().catch(() => ({}));
      if (d.items) { olShelf = d.items; paintOpenLinesShelf(); }
      return d;
    } catch (e) {
      return {};
    }
  }

  async function readOpenLinesShelf() {
    try {
      const r = await afetch('/open-lines/premises');
      if (!r.ok) return;
      const d = await r.json().catch(() => ({}));
      olRoster = d.personas || [];
      olShelf = d.items || [];
      paintOpenLinesShelf();
      paintOpenLinesWho();
      olFillPicker();
    } catch (e) { /* the lists stay as they were */ }
  }

  if ($('olAddBtn')) {
    $('olAddBtn').onclick = async () => {
      const box = $('olAddText');
      const text = String(box.value || '').trim();
      if (!text) { box.focus(); return; }
      const who = String(($('olAddWho') || {}).value || '');
      $('olAddBtn').disabled = true;
      const d = await olSave('/open-lines/premises',
                             { text, personas: who ? [who] : [] });
      if (d.ok) box.value = '';
      $('olAddBtn').disabled = false;
      box.focus();                       // keep focus for a second entry
    };
    $('olAddText').onkeydown = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); $('olAddBtn').click(); }
    };
    $('olAddText').oninput = () => {
      $('olAddBtn').disabled = !String($('olAddText').value || '').trim();
    };
    $('olAddFocus').onclick = () => $('olAddText').focus();
  }

  // The header bar, and every press that changes what is on air.
  async function olOpen(body, btn) {
    if (btn) btn.disabled = true;
    try {
      const r = await afetch('/open-lines/open', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
      const d = await r.json().catch(() => ({}));
      if (d.status) { paintOpenLines(d.status); paintOpenLinesDash(d.status); }
      // A refusal names a setting the operator can change, so it lands on
      // the bar's own state line rather than in a red failure box.
      if (!d.ok && d.why && $('olStateHdr')) $('olStateHdr').textContent = d.why;
      readOpenLinesShelf();
    } catch (e) { /* the bar repaints on the next read */ }
    if (btn) btn.disabled = false;
  }

  if ($('olOpenBtn')) {
    $('olOpenBtn').onclick = () => olOpen({}, $('olOpenBtn'));
    $('olCloseBtn').onclick = async () => {
      const btn = $('olCloseBtn');
      btn.disabled = true;
      try {
        const r = await afetch('/open-lines/close', { method: 'POST' });
        const d = await r.json().catch(() => ({}));
        if (d.status) { paintOpenLines(d.status); paintOpenLinesDash(d.status); }
      } catch (e) { /* repaints on the next read */ }
      btn.disabled = false;
    };
  }

  // The header bar and the stats strip. The old `openLineNow` result box is
  // gone with the redesign — the bar carries the state and the shelf's own
  // "next up" row carries what would go out — so this paints the bar instead.
  function paintOpenLines(d) {
    if (!d) return;
    olStatus = d;
    openLineLive = !!d.live;
    const dot = $('olDot');
    const state = $('olStateHdr');
    const closeBtn = $('olCloseBtn');
    const openBtn = $('olOpenBtn');

    let words = 'off';
    if (d.enabled && d.live) {
      const mins = Math.max(0, Math.round((d.secondsLeft || 0) / 60));
      const sent = d.remindersSent || 0;
      // "asked twice" reads as radio; "1 of 2 reminders" reads as a form.
      const asked = sent === 0 ? '' : sent === 1 ? ' · asked once'
        : sent === 2 ? ' · asked twice' : ' · asked ' + sent + ' times';
      words = 'open · ' + mins + ' min left' + asked;
    } else if (d.enabled) {
      words = 'on · nothing up right now';
    }
    setTag('tagOpenlines', d.enabled
      ? (d.live ? words.replace('open · ', 'open — ') : 'on — nothing up right now')
      : 'off');

    if (dot) dot.dataset.on = d.enabled ? '1' : '';
    if (state) {
      state.textContent = words;
      state.dataset.on = d.enabled && !d.live ? '1' : '';
      state.dataset.live = d.live ? '1' : '';
    }
    if (closeBtn) closeBtn.hidden = !d.live;
    if (openBtn) openBtn.textContent = d.live ? 'Open another' : 'Open a line now';

    // Stats. Read-only, and each says "—" rather than a zero it cannot back up.
    const last = $('olStatLast');
    if (last) {
      const when = olWhen(d.openedAt);
      last.textContent = when
        ? when + (d.persona ? ' · ' + d.persona : '')
        : '—';
    }
    const answered = $('olStatAnswered');
    if (answered) {
      // Only what the record actually knows: how many follow-ups this topic
      // reported. Anything wider would be a number nothing counts.
      const n = d.followupsSent || 0;
      answered.textContent = d.live || d.premise
        ? n + (n === 1 ? ' answer reported' : ' answers reported')
        : '—';
    }
    paintSegs();
  }

  // The dashboard box. One frame, one action bar, and the bar's POSITION never
  // moves — only its label and what it does. The first build swapped two "put
  // it up" rows for a "close it" row in the same place, and the operator shut a
  // line 19 seconds after opening it by clicking twice.
  let olLiveNow = false;

  function paintOpenLinesDash(d) {
    const wrap = $('openLinesLine');
    if (!wrap || !d) return;
    wrap.hidden = !d.enabled;
    if (!d.enabled) return;

    olLiveNow = !!d.live;
    const idle = $('olIdle');
    const live = $('olLive');
    const go = $('olGo');
    const tag = $('olState');
    const foot = $('olFoot');
    if (idle) idle.hidden = olLiveNow;
    if (live) live.hidden = !olLiveNow;

    if (olLiveNow) {
      const mins = Math.max(0, Math.round((d.secondsLeft || 0) / 60));
      if (tag) { tag.textContent = 'up now'; tag.dataset.live = '1'; }
      const prem = $('olPremise');
      const meta = $('olMeta');
      if (prem) prem.textContent = d.premise || d.spoken || '';
      if (meta) {
        const bits = [(d.persona || 'the DJ'), mins + ' min left'];
        if (d.reminderMax) {
          bits.push((d.remindersSent || 0) + ' of ' + d.reminderMax + ' reminders');
        }
        if (d.cutByShow) bits.push('ends with the show');
        meta.textContent = bits.join(' · ');
      }
      if (go) {
        go.textContent = 'CLOSE IT →';
        go.dataset.mode = 'stop';
        go.disabled = false;
      }
      if (foot) foot.textContent = 'the DJ signs off on air, in character';
    } else {
      if (tag) { tag.textContent = 'nothing up'; tag.dataset.live = '0'; }
      olFillPicker();
      if (go) {
        go.textContent = 'PUT IT UP →';
        go.dataset.mode = 'go';
        go.disabled = false;
      }
      if (foot) {
        foot.textContent = 'it airs now · the DJ says it in its own voice';
      }
    }
  }

  // The shelf, as choices. Read on demand so the dashboard does not need the
  // settings page to have been opened first.
  function olFillPicker() {
    const pick = $('olPick');
    if (!pick) return;
    const keep = pick.value;
    pick.innerHTML = '';
    const dj = document.createElement('option');
    dj.value = 'dj';
    dj.textContent = 'Let the DJ make one up';
    pick.appendChild(dj);
    for (const item of olShelf) {
      const opt = document.createElement('option');
      opt.value = 'shelf:' + item.id;
      // Long premises are sentences; the box is not that wide.
      opt.textContent = item.text.length > 70
        ? item.text.slice(0, 68) + '…' : item.text;
      opt.title = item.text;
      pick.appendChild(opt);
    }
    if (keep) pick.value = keep;
    if (!pick.value) pick.value = 'dj';
  }

  if ($('olGo')) {
    $('olGo').onclick = async () => {
      const go = $('olGo');
      const foot = $('olFoot');
      const said = foot ? foot.textContent : '';
      go.disabled = true;
      try {
        let r;
        if (olLiveNow) {
          r = await afetch('/open-lines/close', { method: 'POST' });
        } else {
          const own = String(($('olOwn') || {}).value || '').trim();
          const pick = String(($('olPick') || {}).value || 'dj');
          // Typed text wins over the dropdown: somebody who typed a subject
          // meant that one, whatever the picker happens to be showing.
          const body = own
            ? { premise: own }
            : (pick.startsWith('shelf:')
                ? { source: 'shelf', premise_id: pick.slice(6) }
                : { source: 'dj' });
          r = await afetch('/open-lines/open', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        }
        const data = await r.json().catch(() => ({}));
        if (data.status) {
          paintOpenLines(data.status);
          paintOpenLinesDash(data.status);
        }
        // The button stays put but the box below it changes height, so the
        // NEW action lands under the cursor that just clicked. Opening a line
        // and closing it are opposite acts and a double-click must not do
        // both: the operator did exactly that on 2026-08-21 and shut a line
        // 19 seconds after opening it. A beat's cooling-off is what makes the
        // second click harmless.
        if (data.ok) {
          go.disabled = true;
          setTimeout(() => { go.disabled = false; }, 1500);
        } else {
          go.disabled = false;
        }
        if (!data.ok && data.why && foot) foot.textContent = data.why;
        else if (foot && data.ok) {
          if ($('olOwn')) $('olOwn').value = '';
          if (!olLiveNow) foot.textContent = said;
        }
      } catch (e) {
        if (foot) foot.textContent = 'could not reach the server';
        go.disabled = false;
      }
    };
    $('olOwn').onkeydown = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); $('olGo').click(); }
    };
  }

  async function readOpenLines() {
    try {
      const r = await afetch('/open-lines');
      if (!r.ok) return;
        const d = await r.json().catch(() => null);
      paintOpenLines(d);
      paintOpenLinesDash(d);
    } catch (e) { /* the card simply stays as it was */ }
  }

  // Painted when the section opens rather than on every panel load: the read
  // is a disk read on the server and nothing on a folded section is visible.
  // The tag is the exception — it must be right while folded, which is why the
  // switched-off case is painted from `resolved` in the tag painter instead.
  const olSec = document.querySelector('details.sec[data-group="openlines"]');
  if (olSec) olSec.addEventListener('toggle', () => {
    if (olSec.open) { readOpenLines(); readOpenLinesShelf(); }
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
      paintSecrets(); paintTags();
      const n = Object.keys(set).length, c = (clear || []).length;
      say(true,
        (n ? n + ' key' + (n > 1 ? 's' : '') + ' saved. ' : '') +
        (c ? c + ' cleared. ' : '') + 'Applies to the next caller and to the tests.');
      // A saved or cleared key changes WHICH PROVIDERS exist: refresh the
      // choice lists so the provider appears (or leaves) without a page
      // reload. The cached options read is cheap — no fresh=1, no provider
      // round-trips — and a failure here costs nothing but staleness.
      try {
        options = await afetch('/settings/options').then((r) => r.json());
        paintProviderChoices(); syncModels();
      } catch (e) { /* the dropdowns stay as they were */ }
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
    beneath = s.beneath || {};
    authConfigured = !!s.authConfigured;
    guestConfigured = !!s.guestConfigured;
    adoptSchema(s.schema);
    paint(); paintSecrets(); window.Panel.sounds.loadSounds();
    // A real payload landed, so this browser is in: drop the starts-locked
    // curtain and hide the gate. (The panel is .locked from load so a
    // password-protected page never flashes its dashboard first.)
    $('panel').classList.remove('locked');
    $('loginGate').hidden = true;
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
    // The masthead nameplate sub-line and the footer both carry the host; the
    // footer also carries the build, which anchors every bug report over time.
    paintPage();
    if ($('footHost')) $('footHost').textContent = location.host;
    fetch('/health').then((r) => r.json()).then((h) => {
      // `since` is when THIS server process started, which is what makes the
      // container-skew notice honest — see callHealthNeeds.
      serverSince = Number(h.since || 0);
      paintVersion(h.version);
    }).catch(() => {});
    // The health half of Needs attention. Fired here rather than awaited:
    // the dashboard must paint at its normal speed and gain these rows a
    // moment later, not wait on a disk read of forty transcripts.
    loadRecentCalls();
    loadHookHealth();
  }

  // The build number is the thing every bug report is anchored to, so it may
  // as well take you to what actually changed — and tell you when the box is
  // behind. GitHub's releases/latest redirects to the newest tag, so one
  // unauthenticated call answers "is there a newer one" without a token and
  // without the API's 60/hour anonymous rate limit mattering: this fires once
  // per panel load. A failure is silent — an operator who is offline, or
  // behind a proxy that blocks github.com, still gets their version, which is
  // the part that was there before.
  const REPO_URL = 'https://github.com/mrain1p/Talk-Wave';
  // Kept for the container-skew check, which is the only place the two
  // halves' versions are ever compared. `serverSince` is when this server
  // process started, and it is what stops that check reading a transcript
  // from before an upgrade as a live disagreement.
  let panelVersion = '', newerRelease = '', serverSince = 0;

  function paintVersion(version) {
    const el = $('versionLine');
    if (!el) return;
    const v = version || '?';
    panelVersion = version || '';
    paintNeeds();          // skew is only knowable once this is in
    el.textContent = '';
    const link = document.createElement('a');
    // THE RELEASES PAGE until we know better. This pointed straight at
    // /releases/tag/v<this build>, and most builds have no release of their
    // own — only some versions are cut as one — so the link an operator
    // clicked to read what changed was usually GitHub's 404 (operator: "the
    // link often fails to go to a valid page"). The list is always a real
    // page, opens on the newest notes, and needs no network to be right.
    link.href = REPO_URL + '/releases';
    link.textContent = 'Talk Wave v' + v;
    link.target = '_blank';
    link.rel = 'noopener';
    link.title = 'Release notes';
    el.appendChild(link);
    if (v === '?') return;
    // One read of the release list does both jobs: point this build's link at
    // the notes that actually cover it, and say whether a newer one is out.
    // 30 is several months of releases at this repo's rate.
    fetch('https://api.github.com/repos/mrain1p/Talk-Wave/releases?per_page=30',
          { headers: { Accept: 'application/vnd.github+json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((rels) => {
        if (!Array.isArray(rels) || !rels.length) return;
        const tagOf = (r) => String((r && r.tag_name) || '').replace(/^v/, '');
        // The notes for THIS build: its own release if it has one, otherwise
        // the newest release at or below it — which is the one whose notes
        // describe the code this box is running.
        const at = rels.filter((r) => tagOf(r) === v)[0];
        const before = rels.filter((r) => tagOf(r) && !newer(tagOf(r), v))
          .sort((a, b) => (newer(tagOf(a), tagOf(b)) ? -1 : 1))[0];
        const cover = at || before;
        if (cover && cover.html_url) {
          link.href = cover.html_url;
          link.title = at ? 'Release notes for this build'
            : 'The newest release notes at or before this build (v'
              + tagOf(cover) + ')';
        }
        // Newest by VERSION, not by position: a back-dated patch release
        // sorts first in the API's own order and would claim to be the latest.
        const top = rels.map(tagOf).filter(Boolean)
          .reduce((a, b) => (newer(b, a) ? b : a), '');
        if (!top || top === v || !newer(top, v)) return;
        const rel = rels.filter((r) => tagOf(r) === top)[0] || {};
        const flag = document.createElement('a');
        flag.className = 'vnew';
        flag.href = rel.html_url || (REPO_URL + '/releases/latest');
        flag.target = '_blank';
        flag.rel = 'noopener';
        flag.textContent = 'v' + top + ' available';
        flag.title = 'A newer release is out — pull the image and restart';
        el.appendChild(document.createTextNode(' '));
        el.appendChild(flag);
        newerRelease = top;
        paintNeeds();
      })
      .catch(() => {});
  }

  // Numeric, part by part: a string compare calls 0.10.9 newer than 0.10.10,
  // which is the one comparison this has to get right.
  function newer(a, b) {
    const pa = String(a).split('.').map((n) => parseInt(n, 10) || 0);
    const pb = String(b).split('.').map((n) => parseInt(n, 10) || 0);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
      if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0);
    }
    return false;
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
  // Every element block of the Players page's CARD tab, plus the embed frame
  // options — a change in any is a change the preview frame must repaint
  // for. The two BEHAVIOUR groups are excluded on the tab's own rule:
  // nothing visual lives there.
  const LOOK_GROUPS = new Set(['topcorner', 'whosonair', 'linebox', 'talkbar',
                               'buttons', 'surface', 'embed']);
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
    // Switching surface may change whether a shape applies (shapes are an
    // Embed-only idea), so re-dress the stage.
    if (typeof paintPreviewShape === 'function') paintPreviewShape();
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
      // A shaped stage sizes the frame itself (the pop-up panel is a fixed box
      // on a mock page), so the frame's own height report must not override it.
      const stage = $('previewStage');
      if (stage && stage.classList.contains('shaped')) return;
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
    // The newspaper redesign paints its own light/dark palette on body.panelpage.
    // When the operator picks the STATION colours, drop that class so the
    // station's inline :root tokens inherit through instead (see the CSS).
    document.body.classList.toggle('theme-station', choice === 'station');
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
    // Drawn, not typed \u2014 the same table the card's cycle uses (shared.js
    // THEME_ICONS), so the two surfaces' controls cannot drift. The label
    // rides along because the destination icon alone doesn't say "theme" \u2014
    // the operator hunted for this button while it wore the monitor (0.10.78).
    btn.innerHTML = ({ light: THEME_ICONS.light, dark: THEME_ICONS.dark,
                       station: THEME_ICONS.station,
                       '': THEME_ICONS.device }[next])
      + '<span class="glabel">Theme</span>';
    btn.title = 'Theme \u2014 ' + ({ light: 'switch to light',
                  dark: 'switch to dark',
                  station: "the station's show colours",
                  '': 'match the device' }[next]);
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
  //
  // The finder IS the panel's index. 188 settings live in 34 folded sections
  // across nine pages, and at rest the panel shows none of them — so this box
  // is not a filter over what is already on screen, it is the only complete
  // map of the place that exists. 0.98.22 made it behave like one, after a
  // review measured what it actually did:
  //
  //   * "password" HID the Access section. The filter read four row classes
  //     and nothing else, and Change password is a button in a testrow — so
  //     no row matched, the summary did not carry the word either, and the
  //     one section owning the answer was display:none. Three sections made
  //     entirely of prose and buttons could never appear at all.
  //   * A result never said which PAGE it was on. paintPage hides every
  //     super-group band while searching, on purpose, so "voicemail" returned
  //     nine sections spread over six pages labelled "The machine", "Doors to
  //     air", "The line box" — and clearing the box taught you nothing.
  //   * "color" found nothing and "colour" found two; "avatar" found nothing
  //     though the field is called avatar_style; "mute", "logo", "spam" and
  //     "language" all found nothing. Meanwhile "rate" lit up eight sections
  //     through moderate, separate and accurate.
  //   * Rows hidden by an unmet `needs` came back with no marker, and the
  //     switch governing them was filtered OUT — so you could find "Length
  //     (words)", set it, save, and watch nothing happen, because the
  //     Back-to-air commentary switch it hangs off was elsewhere and off.

  // A needle matches at a WORD BOUNDARY. Substring-anywhere is why "rate"
  // returned eight sections; prefix-only would break typing, since "volu"
  // has to keep finding volume. This is the middle: any word may START with
  // the needle, no word may merely contain it.
  function hitsWord(hay, needle) {
    if (!hay || !needle) return false;
    let i = hay.indexOf(needle);
    while (i !== -1) {
      if (i === 0 || !/[a-z0-9]/.test(hay[i - 1])) return true;
      i = hay.indexOf(needle, i + 1);
    }
    return false;
  }

  // Which schema fields live in a row. A .prow and a .permrow hold two, so
  // this is a list, and the map is dropped whenever the schema repaints.
  let ROW_FIELDS = null;
  function rowFields(row) {
    if (!ROW_FIELDS) {
      ROW_FIELDS = new WeakMap();
      Object.keys(SCHEMA.fields || {}).forEach((f) => {
        const el = $(f);
        if (!el) return;
        const r = el.closest('.row') || el.closest('.check') || el.closest('.prow');
        if (!r) return;
        ROW_FIELDS.set(r, (ROW_FIELDS.get(r) || []).concat([f]));
      });
    }
    return ROW_FIELDS.get(row) || [];
  }

  // Everything worth matching a row against: what it says on screen, plus
  // what the schema knows that the screen does not — the field's own id, its
  // id with the underscores opened out, and its `alias=` synonyms. This is
  // what makes "avatar" find "DJ photo shape" and "rate limit" find the caps.
  let HAY = new WeakMap();
  let ROW_HAY = new WeakMap();
  function rowHay(row) {
    let s = ROW_HAY.get(row);
    if (s != null) return s;
    s = row.textContent + ' ';
    // A permission row keeps its help as a SIBLING — the grid cannot hold a
    // hint inside the row — so the row's own text is only half of it.
    const sib = row.nextElementSibling;
    if (sib && sib.classList.contains('hint') && sib.dataset.fromSchema) {
      s += sib.textContent + ' ';
    }
    rowFields(row).forEach((f) => {
      const m = SCHEMA.fields[f] || {};
      s += f + ' ' + f.replace(/_/g, ' ') + ' ' + (m.alias || '') + ' '
        + (m.help || '') + ' ' + (m.placeholder || '') + ' ';
    });
    s = s.toLowerCase();
    ROW_HAY.set(row, s);
    return s;
  }

  // A section's own text, rows excluded: the summary, the prose, the testrow
  // buttons, the slot cards. Rows are matched one at a time, so anything
  // reached through here is a hit on the section rather than on a setting.
  function ownText(el) {
    let s = '';
    el.childNodes.forEach((n) => {
      if (n.nodeType === 3) { s += n.nodeValue + ' '; return; }
      if (n.nodeType !== 1) return;
      if (n.matches && n.matches('.row, label.check, .prow, .permrow')) return;
      s += ownText(n);
    });
    return s;
  }

  // What the section is CALLED — its name, its blurb, its state chip, and
  // the synonyms the schema gives it. A hit here means the operator named
  // the section, so the whole thing opens: typing "sounds" has to find Call
  // sounds, and no single row inside it says the word.
  //
  // Read part by part rather than off the whole summary, because the search
  // writes a PAGE NAME into that summary while it runs. Taken wholesale,
  // one repaint mid-search would leave every crumbed section matching its
  // own page's name.
  function secHay(sec) {
    let rec = HAY.get(sec);
    if (rec) return rec;
    const g = (SCHEMA.groups || []).find((x) => x.id === sec.dataset.group) || {};
    const part = (sel) => {
      const el = sec.querySelector(':scope > summary > ' + sel);
      return el ? el.textContent + ' ' : '';
    };
    rec = {
      name: (part('.secname') + part('.secblurb') + part('.tag')
        + (g.title || '') + ' ' + (g.blurb || '') + ' '
        + (g.alias || '')).toLowerCase(),
      // Everything else the section says on its own account. A hit here is
      // weaker: it brings the section onto the results page without
      // unfolding its settings, which is the Access case — "password" lives
      // on a testrow button and in prose, and the section owning the answer
      // was the one the old filter hid.
      prose: ownText(sec).toLowerCase(),
    };
    HAY.set(sec, rec);
    return rec;
  }

  // Both caches key off live DOM text, so they are dropped whenever the
  // panel repaints its help or reloads the schema.
  function forgetHaystacks() {
    HAY = new WeakMap();
    ROW_HAY = new WeakMap();
    ROW_FIELDS = null;
  }

  function rowOfField(name) {
    const el = $(name);
    if (!el) return null;
    return el.closest('.row') || el.closest('.check') || el.closest('.prow');
  }

  // A result whose prerequisite is off is a setting that cannot do anything
  // yet. Name the switch and dim the row — the alternative is what shipped:
  // the operator edits it, saves, and the panel reports success while the
  // value sits behind a closed door.
  function markPrerequisite(row) {
    let off = null;
    rowFields(row).forEach((f) => {
      const m = SCHEMA.fields[f];
      if (!m || !m.needs || off) return;
      if (needsMet(m.needs)) return;
      const dep = m.needs[0];
      off = (SCHEMA.fields[dep] || {}).label || dep;
    });
    row.classList.toggle('offrow', !!off);
    if (off) row.dataset.needsOff = off;
    else delete row.dataset.needsOff;
  }

  (function bindSettingsSearch() {
    const box = $('settingsSearch');
    if (!box) return;
    let timer = null;

    const restore = (sections) => {
      sections.forEach((sec) => {
        sec.querySelectorAll('.row, label.check, .prow, .permrow')
          .forEach((r) => {
            r.style.removeProperty('display');
            r.classList.remove('offrow');
            delete r.dataset.needsOff;
          });
        sec.style.removeProperty('display');
        const crumb = sec.querySelector(':scope > summary > .crumb');
        if (crumb) crumb.remove();
        if (sec.dataset.searchOpened) {
          sec.open = false;
          delete sec.dataset.searchOpened;
        }
      });
      // Clearing hands the rows back with removeProperty, which also wipes
      // what applyVisibility had hidden — so the link-out address, the
      // custom call label and every other `needs` row sat visible for a
      // switch that was off until the next field edit. Re-apply the rules.
      applyVisibility();
    };

    // The page name, written into the summary of every section a result
    // stands in. This is the whole answer to "and where is that" — the bands
    // are hidden in a results view, so without it a result names a section
    // and stops.
    const breadcrumb = (sec) => {
      const head = sec.querySelector(':scope > summary');
      if (!head) return;
      const page = PAGE_TITLES[pageOfSection(sec)] || '';
      let crumb = head.querySelector(':scope > .crumb');
      if (!crumb) {
        crumb = document.createElement('span');
        crumb.className = 'crumb';
        head.insertBefore(crumb, head.querySelector('.secname'));
      }
      crumb.textContent = page;
    };

    const apply = () => {
      const needle = (box.value || '').trim().toLowerCase();
      const sections = [...document.querySelectorAll('details.sec')];
      // Search is a RESULTS VIEW over every page: while a needle is typed,
      // paintPage lifts the page filter (and parks the dashboard), and the
      // filtering below decides what shows. Clearing the box hands the panel
      // back to whichever page the operator was on.
      document.body.classList.toggle('finding', !!needle);
      paintPage();
      if (!needle) {
        restore(sections);
        if ($('searchMiss')) $('searchMiss').hidden = true;
        if ($('searchCount')) $('searchCount').hidden = true;
        return;
      }

      const rowsOf = (sec) =>
        [...sec.querySelectorAll('.row, label.check, .prow, .permrow')];
      const whole = new Set();
      const shown = new Set();
      const prose = new Set();
      sections.forEach((sec) => {
        const hay = secHay(sec);
        if (hitsWord(hay.name, needle)) whole.add(sec);
        else if (hitsWord(hay.prose, needle)) prose.add(sec);
        rowsOf(sec).forEach((r) => {
          if (hitsWord(rowHay(r), needle)) shown.add(r);
        });
      });
      // A prose hit brings the section onto the results page WITHOUT
      // unfolding its settings. The word was found on a button, in a
      // paragraph or in the section's own explanation — that is an answer
      // about the section, not about every row in it. Access answers
      // "password" with the Change-password button standing in a section
      // whose rows stay filtered; Caller permissions, whose prose mentions
      // the admin password in passing, no longer answers with twenty-one
      // permission rows.

      // Pull in the switch that governs anything found. A dependant without
      // its prerequisite is the trap described at the top of this block, and
      // the prerequisite is precisely the row a needle for the dependant
      // filters out — "Length (words)" never says "Mention the call on air".
      [...shown].forEach((r) => {
        rowFields(r).forEach((f) => {
          const need = (SCHEMA.fields[f] || {}).needs;
          const gov = need && rowOfField(need[0]);
          if (gov) shown.add(gov);
        });
      });

      let count = 0, found = 0;
      const pages = [];
      sections.forEach((sec) => {
        const all = whole.has(sec);
        let any = all || prose.has(sec);
        rowsOf(sec).forEach((r) => {
          const on = all || shown.has(r);
          r.style.display = on ? '' : 'none';
          if (on) {
            count += rowFields(r).length || 1;
            markPrerequisite(r);
          } else {
            r.classList.remove('offrow');
            delete r.dataset.needsOff;
          }
          any = any || on;
        });
        sec.style.display = any ? '' : 'none';
        if (!any) return;
        found++;
        const page = pageOfSection(sec);
        if (page && pages.indexOf(page) === -1) pages.push(page);
        breadcrumb(sec);
        if (!sec.open) {
          sec.open = true;
          sec.dataset.searchOpened = '1';
        }
      });

      // Say so when nothing matched — a page of collapsed nothing read as
      // the panel being broken, not as a miss. `found` rather than `count`:
      // a section reached through its prose shows with its rows still
      // filtered, so a real answer can carry no settings at all.
      if ($('searchMiss')) $('searchMiss').hidden = !!found;
      // And say how WIDE the hit is. One setting on one page and eighteen
      // across five are different answers, and naming the pages is the map
      // the panel otherwise never draws — the bands are hidden here.
      const tally = $('searchCount');
      if (tally) {
        tally.hidden = !found;
        tally.textContent = !found ? ''
          : (count ? count + (count === 1 ? ' setting' : ' settings')
                   : found + (found === 1 ? ' section' : ' sections'))
            + ' on ' + pages.length
            + (pages.length === 1 ? ' page — ' : ' pages — ')
            + pages.map((p) => PAGE_TITLES[p] || p).join(' · ');
      }
    };

    box.oninput = () => {
      // A value that arrives while the box does not hold focus was not
      // typed: it is a password manager deciding this is the "username"
      // beside the panel's password box (operator-reported — the autofill
      // put the panel into the results view and read as the page chips
      // being broken). The vendor opt-out attributes in the markup ask
      // nicely; this is for the managers that do not listen. A human
      // typing always has focus, so nothing real is ever discarded.
      if (document.activeElement !== box && box.value) {
        box.value = '';
        return;
      }
      clearTimeout(timer); timer = setTimeout(apply, 120);
    };
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
        // the switches rather than waiting for a save — and so does the
        // Live calls card's per-tier count on the dashboard.
        if (SCHEMA.fields[f] && SCHEMA.fields[f].group === 'perms') {
          paintAsks(); paintTools(); paintTags(); paintDash();
        }
        // The Voicemail card reads the machine's who and where live too.
        if (SCHEMA.fields[f] && SCHEMA.fields[f].group === 'voicemail') {
          paintDash();
        }
        // The silent-call warning is a question about three switches on two
        // different pages, so it has to follow all three as they are pressed
        // — an operator diagnosing exactly this is toggling them, and a
        // warning that only tells the truth after a save is worse than none.
        if (f === 'swipe_player' || f === 'tune_in_on_call'
            || f === 'tune_in_audible') paintDash();
        // The On air page's rows feed the dashboard cluster and their own
        // section tag; paintTags ends by calling paintDash. The Caller
        // permissions greying follows these switches too, but the
        // applyVisibility call above already repaints that.
        if (SCHEMA.fields[f] && SCHEMA.fields[f].group === 'airdoors') {
          paintTags();
        }
        // The slot cards and the shelf's used-for chips read the sound
        // fields live — a URL being typed, or the set changing name.
        if (f === 'sound_pack' || f.indexOf('sound_') === 0) window.Panel.sounds.paintSlotCards();
        // The card in the frame follows the form, not the save button. That
        // is the entire point: you find out what "DJ photo off" looks like
        // before you commit it to everyone who rings. The tab strip's
        // element tally follows the same edits.
        if (isLookField(f)) { queuePreview(); paintCardCounts(); }
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
      // "optional" is the third answer the operator asked for by name: the
      // rows with no chip read as unknowns next to the STATION ADMIN ones.
      // It never goes coral — the tool works without the credentials, they
      // only sharpen it — and the tooltip says exactly that.
      const optional = SCHEMA.fields[f].admin === 'optional';
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
        host.appendChild(tag);
      }
      tag.textContent = optional ? 'Station admin optional' : 'Station admin';
      tag.classList.toggle('missing', !have && !optional);
      tag.title = optional
        ? (have
          ? 'Works on its own; the stored station admin credentials also let '
            + 'it retry phrasing before reporting a miss.'
          : 'Works without the station admin credentials — storing them under '
            + 'Station adds a retry pass before reporting a miss.')
        : have
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
    // The door and the tier are two questions (operator's ask, 2026-08-15).
    // A stored guest code elevates whoever types it in every mode that lets
    // anyone below admin in — INCLUDING an open line, where the three tiers
    // now coexist: strangers, code-holders, and the operator. Admin-only is
    // the exception, because nobody arrives under admin there at all.
    if (tier === 'guest') {
      // The same three-part rule as auth.caller_tier: a code to type, a door
      // that admits somebody under admin, and — on an OPEN line only — the
      // guest tier switched on. A code-gated door is itself the tier.
      const tierOn = $('guest_tier') ? $('guest_tier').checked
                                     : resolved.guest_tier !== false;
      if (!guestConfigured || access === 'admin') return false;
      return access === 'guest' || tierOn;
    }
    // `open` callers only exist while the line lets somebody in without a
    // code. On auto that is "until a guest code is set".
    if (access === 'open') return true;
    if (access === 'auto') return !guestConfigured;
    return false;
  }

  function tierWhyNot(tier) {
    const access = ($('front_access') && $('front_access').value)
      || resolved.front_access || 'auto';
    if (tier === 'guest' && access !== 'admin' && !guestConfigured) {
      return 'No guest code set — nobody can be this caller yet. Set one under '
        + 'Access and code-holders become their own tier, open line or not.';
    }
    if (tier === 'guest' && access === 'open') {
      return 'The guest tier is switched off, so a code you have set does not '
        + 'elevate anyone. Tick GUEST CODE under Access to turn it back on.';
    }
    if (tier === 'open' && access === 'guest') {
      return 'The line is code-gated, so nobody arrives without a code — there '
        + 'are no callers at this level to give anything to.';
    }
    return 'The line is closed to callers at this level, so nobody can be '
      + 'this caller. Change Call-in access under Access.';
  }

  // Call-in access as three ticks, and they are THE DOOR — who may ring at
  // all — not the tiers. The doors are still one choice apiece (0.10.66: the
  // line is code-gated or open, never both), but a stored guest code now
  // elevates on an open line too, so all three CALLER TIERS can be live at
  // once and each can carry its own permissions. Admin is always a door.
  // The hidden select stays the stored field — these cells drive it the way
  // the kill switch drives calls_paused — so Save and the schema never learn
  // a new shape.
  const ACCESS_CELLS = [
    ['open', 'Anyone', 'Strangers can ring with no code at all.'],
    ['guest', 'Guest code', 'A code you set makes whoever types it their own tier, with its own permissions. Tick this WITHOUT Anyone and the code becomes the only way in.'],
    ['admin', 'Admin', 'Always in — your admin password opens the phone and this panel whatever else is set.'],
  ];

  // The two ticks -> the two stored fields. Kept in one place because the
  // mapping is the whole feature: three of the four combinations share a door
  // value and are told apart by the tier switch.
  function writeAccess() {
    const wrap = $('accessCells');
    const box = (m) => wrap.querySelector('input[data-mode="' + m + '"]');
    const anyone = !!(box('open') && box('open').checked);
    const guest = !!(box('guest') && box('guest').checked);
    const sel = $('front_access');
    const tier = $('guest_tier');
    sel.value = anyone ? 'open' : guest ? 'guest' : 'admin';
    // On a code-gated line the tier is the door, so it rides with it; on an
    // open line it is the operator's own choice; with neither ticked there is
    // no caller under admin to be a guest.
    tier.checked = guest;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    tier.dispatchEvent(new Event('change', { bubbles: true }));
    paintSecurity();
  }
  function decorateAccess() {
    const wrap = $('accessCells');
    if (!wrap || wrap.dataset.built) return;
    wrap.dataset.built = '1';
    // TWO INDEPENDENT TICKS, and admin as the one that is always in. The
    // operator's model, stated twice: "guest can be on and anyone can be off
    // or vice versa" (2026-08-16). All four combinations are real:
    //
    //   anyone ✓ guest ✓   strangers ring through, a code makes you a guest
    //   anyone ✓ guest ✗   open line, the stored code is inert
    //   anyone ✗ guest ✓   code-gated: no code, no call
    //   anyone ✗ guest ✗   the phone is closed to callers
    //
    // They write TWO fields between them — the door (front_access) and
    // whether a code elevates (guest_tier) — because three of these four
    // collapse onto one door value and the remaining distinction is the tier.
    // Drawn as an exclusive choice before, which is what made the operator
    // report they could not have both.
    ACCESS_CELLS.forEach(([mode, word, why]) => {
      const cell = document.createElement('label');
      cell.className = 'acell';
      cell.title = why;
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.dataset.mode = mode;
      if (mode === 'admin') box.disabled = true;   // always in, never a choice
      box.onchange = writeAccess;
      cell.appendChild(box);
      const w = document.createElement('span');
      w.textContent = word;
      cell.appendChild(w);
      wrap.appendChild(cell);
    });
  }
  function paintAccess() {
    const wrap = $('accessCells');
    const sel = $('front_access');
    if (!wrap || !sel) return;
    // `auto` is the stored value on an un-migrated install and is not one of
    // the three modes; it behaves as open-until-a-code-exists, so show it as
    // the door it currently IS rather than leaving every box blank.
    const mode = sel.value === 'auto'
      ? (guestConfigured ? 'guest' : 'open') : sel.value;
    const tierOn = $('guest_tier') ? $('guest_tier').checked : true;
    // Read back the pair, not the door alone: ANYONE is the door being open,
    // GUEST CODE is the tier being live — which on a code-gated line is
    // implied by the door and on an open line is its own switch.
    const state = { open: mode === 'open',
                    guest: mode === 'guest' || (mode === 'open' && tierOn),
                    admin: true };
    wrap.querySelectorAll('input').forEach((box) => {
      box.checked = !!state[box.dataset.mode];
    });

    // WHICH CALLERS THIS ACTUALLY PRODUCES — the question the door control was
    // being read as answering. The door is one choice; the tiers are what
    // comes out of it, and on an open line with a code set that is all three
    // at once. Each one is a column in Caller permissions, which is where the
    // operator sets what it may do.
    const out = $('accessTiers');
    if (!out) return;
    const guestLive = state.guest && guestConfigured;
    const tiers = [];
    if (state.open) tiers.push('anyone');
    if (guestLive) tiers.push('guest');
    tiers.push('admin');
    const why = mode === 'admin'
      ? 'the line is closed to callers'
      : state.guest && !guestConfigured
        ? 'no code is set yet — use Set guest code below, or nobody can be a guest'
        : mode === 'guest'
          ? 'the code is the only way in, so there is no “anyone” tier'
          : guestLive
            ? 'all three, each with its own permissions'
            : 'the guest tier is off, so a code you have set stays inert';
    out.textContent = 'Callers you can get: ' + tiers.join(' · ') + ' — ' + why;
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
        if (plabel) plabel.appendChild(hint);
        else if (inline || check) anchor.appendChild(hint);
        else anchor.insertAdjacentElement('afterend', hint);
      } else if (hint.dataset.fromSchema !== f) {
        // The hint is already SPOKEN FOR by another field sharing this row,
        // and first writer wins. The Access row holds two hidden fields —
        // the door and whether a code elevates — so the second one silently
        // overwrote the first, and the longest explanation on the page (what
        // Call-in access actually does) was never on screen at all. Same
        // family as the duplicate-id trap: one anchor, two claimants.
        return;
      }
      // Stamped with the OWNER, not a flag: `fromSchema` is read as a
      // boolean everywhere else, and a field name is just as truthy.
      hint.dataset.fromSchema = f;
      writeLinked(hint, meta.help);
    });
    // The help just rewritten is part of what the finder matches, so the
    // haystacks built from it are now stale.
    forgetHaystacks();
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
    if (fresh.beneath) beneath = fresh.beneath;
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
  // The seam the split files live on. afetch/showResult are the shared
  // machinery; markClean and the two getters exist for panel-sounds.js,
  // which reads the panel's /live copy and the schema — mutable bindings
  // that cannot cross a file boundary as bare names. panel-sounds.js
  // publishes Panel.sounds back (loadSounds, paintSlotCards,
  // buildSlotCards) for the call sites below; it loads after this file,
  // and every caller runs post-load, so the reference is always there.
  window.Panel = {
    afetch, showResult, markClean,
    getLive: () => live,
    schemaFields: () => SCHEMA.fields,
  };

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

  // The only thing that checks an ear at all. /test/speed measures local
  // Whisper for real but records a flat 400ms estimate for any cloud
  // provider without calling it, so before this a wrong Deepgram key gave a
  // green panel and a green speed test, and the first symptom was a caller
  // being misheard on air.
  //
  // The sample is SYNTHESIZED rather than recorded from the operator's
  // microphone (their call): no permission prompt, nothing that depends on
  // the room, and the same sentence every run so two results compare.
  $('testSttBtn').onclick = async () => {
    const btn = $('testSttBtn'), out = $('sttResult');
    btn.disabled = true;
    out.className = 'result on';
    out.textContent = 'Speaking a line, then listening back…';
    try {
      const d = await afetch('/test/stt', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft()),
      }).then((r) => r.json());
      if (!d.ok) {
        // Say WHICH engine failed. A voice that cannot speak fails this
        // test without the ear being touched, and sending the operator to
        // debug the wrong section is worse than no test.
        showResult(out, false, d.stage === 'voice'
          ? d.error
          : 'The ear failed: ' + d.error
            + '\n' + (d.provider || '?') + ' · ' + (d.model || '?'));
        return;
      }
      const acc = d.accuracy;
      const verdict = acc >= 90 ? '\n✓ Heard it.'
        : acc >= 70 ? '\n⚠ Mostly heard it — names and numbers are where a '
                      + 'call goes wrong.'
        : '\n✗ Badly misheard. Check the model, or try a cloud ear.';
      // Realtime factor matters for the same reason it does on the voice
      // test: an ear slower than the audio cannot keep up with a call.
      const slow = d.rtf != null && d.rtf >= 1.0;
      showResult(out, acc >= 70 && !slow,
        d.provider + ' · ' + d.model
        + '\nsaid:  ' + d.said
        + '\nheard: ' + (d.heard || '(nothing)')
        + '\n' + acc + '% of the words, ' + d.ms + 'ms for '
        + d.audioSeconds + 's of audio'
        + (d.rtf != null ? ' (' + d.rtf + 'x realtime'
           + (slow ? ' — slower than the call' : '') + ')' : '')
        + verdict + (d.note ? '\n' + d.note : ''));
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
      // Both numbers come from the server, which is the only side that knows
      // what a call will actually tolerate — the panel used to hold a bare
      // 1500 and grade against nothing else, so a model that could never
      // finish a turn was reported as one that would "feel laggy".
      const desired = d.desiredMs || 1500;
      const budget = d.budgetMs || 10000;
      const slow = d.firstTokenMs > desired;
      const hopeless = d.firstTokenMs >= budget;
      // A second round is the one that catches a provider which passes the
      // easy test and then dies mid-conversation, so it counts toward the
      // verdict rather than sitting underneath it as a note.
      const carries = d.followUp !== 'failed';
      showResult(out, d.toolCalling && carries && !slow,
        withNote(d.provider + ' / ' + d.model +
        '\nfirst token ' + d.firstTokenMs + 'ms, total ' + d.totalMs + 'ms' +
        (d.measuredWith ? '\n' + d.measuredWith : '') +
        '\ntool calling: ' + (d.toolCalling ? '✓ works' : '✗ model did not call the tool')
        + (d.toolCalling ? ' (' + (d.parallelTools ? 'two at once' : 'one at a time') + ')' : '') +
        (d.followUp === 'skipped' ? '' :
          '\ncarrying the conversation: ' + (
            d.followUp === 'ok' ? '✓ answered the follow-up'
              : d.followUp === 'silent' ? '⚠ replied with nothing'
                : d.followUp === 'tools-again'
                  ? '⚠ reached for the same tools again instead of saying what they returned'
                  : '✗ ' + (d.followUpError || 'the provider rejected the follow-up'))) +
        (d.reply ? '\nreply: ' + d.reply : '') +
        (hopeless
          ? '\n✗ Over the ' + Math.round(budget / 1000) + 's a call allows — the turn '
            + 'is thrown away and the caller hears the trouble line instead of a reply. '
            + 'Try a smaller model, or a cloud provider.'
          : slow
            ? '\n⚠ Above the ' + desired + 'ms target, so the caller hears a pause before '
              + 'every reply. Calls still complete: this box waits up to '
              + Math.round(budget / 1000) + 's.'
            : '') +
        (d.followUp === 'failed'
          ? '\n✗ It answers once, then refuses the next request — every '
            + 'conversation will lose a reply. Try another model.' : '') +
        (d.toolCalling ? '' : '\n✗ Without tool calling the DJ can never submit a request.'), d));
    } catch (e) { showResult(out, false, 'Failed: ' + e.message); }
    finally { btn.disabled = false; }
  };

  function stationQuery() {
    // The MCP endpoint row retired at 0.10.80 — the probe derives it from
    // the station address the same way every call does.
    const q = new URLSearchParams();
    if ($('station_base_url').value) q.set('station_base_url', $('station_base_url').value);
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
      // The draft endpoint travels like reloadVoices' does, so a URL that
      // hasn't been saved yet still gets ITS models into the dropdown —
      // paste, reload, pick, save, in that order.
      const q = new URLSearchParams({ fresh: '1' });
      if ($('llm_base_url').value) q.set('llm_base_url', $('llm_base_url').value);
      const o = await afetch('/settings/options?' + q.toString()).then((r) => r.json());
      // Providers too, not just models: a key saved since page load means a
      // provider this dropdown has never heard of (0.10.85).
      options = o; paintProviderChoices(); syncModels();
      const liveL = Object.keys(o.modelsDiscovered || {}).filter((p) => o.modelsDiscovered[p]);
      // The key verdict (operator's ask, 0.10.85): a saved key whose model
      // list would not read is almost always a wrong key — say which,
      // instead of leaving the silence to be noticed.
      const silent = Object.keys(o.modelsDiscovered || {}).filter((p) =>
        PROVIDER_KEY[p] && (o.llmProviders || []).indexOf(p) !== -1
        && !o.modelsDiscovered[p]);
      showResult(out, liveL.length > 0 && !silent.length,
        (liveL.length ? 'Keys answering: ' + liveL.join(', ')
                      : 'No provider answered — add a key and try again.')
        + (silent.length
            ? '\nNot answering: ' + silent.join(', ') + ' — a key is saved '
              + 'but its model list would not read. Check the key.'
            : ''));
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
    stadium:    { hp: 300, lp: 5000, grit: 35 },
    intercom:   { hp: 800, lp: 2600, grit: 45 },
    shortwave:  { hp: 600, lp: 2200, grit: 30 },
    lofi:       { hp: 60,  lp: 6500, grit: 8 },
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

  // Reassigned inside the guard below; declared here so the per-DJ list's
  // own Test buttons (built elsewhere) can reach the one test path — two
  // implementations of "render a line and colour it" would drift.
  let runFxTest = async () => {};

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

    runFxTest = async (kind, btn, voiceOverride) => {
      const out = $('ttsResult');
      btn.disabled = true;
      out.className = 'result on';
      out.textContent = 'Rendering one line, then playing it through the '
        + (kind === 'none' ? 'clean path' : kind + ' effect') + '\u2026';
      try {
        const body = draft();
        const voice = voiceOverride || ($('fxVoice') && $('fxVoice').value);
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

  // ------------------------------------------------- per-DJ voice effects
  // The staged-greetings shape: one row per persona, its own colour, saved
  // the moment it is picked — a costume for a character is a decision, not
  // a draft. Painted only when the section opens, like the greeting list.
  async function loadFxDjList() {
    const host = $('fxDjList');
    if (!host) return;
    let current = {};
    try {
      const r = await afetch('/settings/voice-effects');
      if (r.ok) current = (await r.json()).effects || {};
    } catch (e) { /* the list still paints, showing the shared default */ }
    host.innerHTML = '';
    const kinds = (SCHEMA.fields.voice_effect
      && SCHEMA.fields.voice_effect.choices) || [];
    (options.personas || []).forEach((p) => {
      const li = document.createElement('li');
      li.className = 'vmrow';
      const who = document.createElement('span');
      who.className = 'sname';
      who.textContent = p.name;
      const sel = document.createElement('select');
      const none = document.createElement('option');
      none.value = '';
      none.textContent = 'Shared setting — the pick above';
      sel.appendChild(none);
      kinds.forEach((c) => {
        const o = document.createElement('option');
        o.value = c[0]; o.textContent = c[1] || c[0];
        sel.appendChild(o);
      });
      sel.value = current[p.id] || '';
      sel.onchange = async () => {
        const out = $('fxResultNote');
        try {
          const r = await afetch('/settings/voice-effects', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ personaId: p.id, effect: sel.value }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.error || 'refused');
          current[p.id] = sel.value;
          showResult(out, true, sel.value
            ? p.name + ' now wears ' + sel.value + ' on every call.'
            : p.name + ' follows the shared setting again.');
        } catch (e) {
          showResult(out, false, 'Could not save ' + p.name + '’s effect — '
            + e.message);
          sel.value = current[p.id] || '';
        }
      };
      // Hear THIS DJ through THIS row's pick — unsaved selection included,
      // in the persona's own voice via the same one test path the section's
      // buttons use. The voices ride the voicemail status; fetched once.
      const hear = document.createElement('button');
      hear.className = 'btnquiet';
      hear.textContent = 'Test';
      hear.title = 'Render one line in ' + p.name + '’s voice and play it '
        + 'through the selected effect';
      hear.onclick = async () => {
        if (!vmPersonas.length) await loadVmStatus();
        const per = vmPersonas.find((v) => v.id === p.id) || {};
        const kind = sel.value
          || ($('voice_effect') && $('voice_effect').value)
          || resolved.voice_effect || 'none';
        runFxTest(kind, hear, per.voice || '');
      };
      li.append(who, sel, hear);
      host.appendChild(li);
    });
  }

  const fxSec = document.querySelector('details.sec[data-group="effects"]');
  if (fxSec) {
    fxSec.addEventListener('toggle', () => { if (fxSec.open) loadFxDjList(); });
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
      // The verdict lands on the beep's own card too — the fault colour on
      // the thing that is broken, not only a paragraph below the grid. By
      // data-slot: the cards are built at runtime, and the widget contract
      // test rightly refuses ids that exist in no markup.
      const beepCard = document.querySelector('.slotcard[data-slot="vm_beep"]');
      if (beepCard) beepCard.classList.toggle('bad', !!(beep.set && !beep.ok));
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
        // The server read livekit.yaml's rtc flags (0.10.88): the one
        // advertising misconfiguration every other stage sails past —
        // use_external_ip false with no node_ip means the container address
        // goes out as the media address, and only the browser probe fails,
        // ten seconds later, with a guess.
        if (env.rtc && !env.rtc.ok) {
          return { status: 'warn',
                   detail: env.livekit.url + ' · credentials OK — but '
                     + env.rtc.detail };
        }
        return { status: 'pass', detail: env.livekit.url + ' · credentials OK'
          + (env.rtc ? ' · ' + env.rtc.detail : '') };
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
        // Earlier deployments of this box leave dead rows behind at the
        // station (a Docker-internal address, a previous host IP): each one
        // burns one of the station's 16 hook slots and takes a failed POST
        // on every event. The server can only flag them — a second Talk Wave
        // on the same station is legitimate, so deleting is the operator's
        // call, in the station's own Webhooks tab.
        const strays = ((env.webhook && env.webhook.lookalikes) || []).length;
        const strayNote = strays
          ? ' · ' + strays + ' other hook(s) at the station point at a /hooks/station address — likely stale rows from earlier deployments; remove them in the station Webhooks tab'
          : '';
        if (!env.webhook?.registered) {
          return { status: 'warn', detail: (env.webhook?.detail || 'not registered') + polling + strayNote };
        }
        const d = await afetch('/hooks/test', { method: 'POST' })
          .then((r) => r.json()).catch(() => null);
        if (!d) return { status: 'warn', detail: 'registered, delivery untested' + strayNote };
        return d.ok
          ? { status: strays ? 'warn' : 'pass', detail: d.detail + strayNote }
          : { status: 'warn', detail: d.detail + polling + strayNote };
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
        // The metric stays; what changed is that it is now graded against what
        // a call tolerates rather than against one hardcoded number. A tester
        // read "6185ms — the call will lag" off this line while every turn on
        // his box was timing out and the caller heard the apology (2026-08-13).
        const desired = d.desiredMs || 1500;
        const budget = d.budgetMs || 10000;
        const measured = d.measuredWith ? ' · ' + d.measuredWith : '';
        if (d.firstTokenMs >= budget) {
          return { status: 'fail',
            detail: d.model + ' · tools OK, but ' + d.firstTokenMs + 'ms to first token is over '
              + 'the ' + Math.round(budget / 1000) + 's a call allows — every turn times out and '
              + 'the caller hears the trouble line. A smaller model, or a cloud one, is the fix'
              + measured };
        }
        if (d.firstTokenMs > desired) {
          return { status: 'warn',
            detail: d.model + ' · tools OK · ' + d.firstTokenMs + 'ms to first token, above the '
              + desired + 'ms target — calls complete (this box waits up to '
              + Math.round(budget / 1000) + 's) but the caller hears a pause before every reply'
              + measured };
        }
        return { status: 'pass',
          detail: d.model + ' · tools OK · ' + d.firstTokenMs + 'ms' + measured };
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
      // An estimated stage (cloud STT, whose network round trip we don't
      // measure) wears ≈ and an "est." tag so it never reads as a measured
      // number beside the real ones (0.10.58 review).
      const mark = choke ? '!' : st.estimate ? '≈' : '✓';
      const label = st.estimate ? st.name + ' · est.' : st.name;
      row(choke ? 'choke' : (st.estimate ? 'oneoff' : ''),
          mark, st.ms + 'ms', label, st.note);
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

    // The server answers several stages in ONE round trip below, and on a
    // slow station that request can run tens of seconds — during which every
    // row used to sit inert and the whole check read as hung
    // (operator-reported, 0.10.88). The rows that ride the batch say so
    // while it runs; a stage's own turn still gets its "checking…".
    PIPELINE.forEach((s, i) => {
      if (s.run.length > 0) {
        rows[i].status = 'running';
        rows[i].detail = 'in the server batch — a slow station stretches this…';
      }
    });
    renderStages(rows);
    out.textContent = 'Running — the server walks its checks in one batch; '
      + 'a station that answers slowly stretches this to ~30s.';

    let env = {};
    try {
      env = await afetch('/test/env', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft()),
      }).then((r) => r.json());
    } catch (e) { env = {}; }
    // The batch is home: quiet the placeholders before the walk.
    rows.forEach((r) => {
      if (r.status === 'running') { r.status = 'pending'; r.detail = ''; }
    });

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

  // The per-moment preview buttons became the ▶ on each slot card — one
  // family of controls instead of a row above and a row below it.


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
