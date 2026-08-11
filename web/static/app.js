'use strict';

const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};
const svg = (id, cls) => {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  if (cls) s.setAttribute('class', cls);
  const u = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  u.setAttribute('href', '#' + id);
  s.append(u);
  return s;
};

// Where this app is served from. Anchored to app.js's own URL rather than to
// location, so it is right whether the site sits at / or under a subpath, and
// whether or not the URL has a trailing slash. Every asset and API URL is built
// from it — see WEB-PLAN.md Phase 2.
const BASE = new URL('../', document.currentScript?.src || location.href).href;
const url = (path) => new URL(String(path).replace(/^\//, ''), BASE).href;

// The single backend seam. Phase 3 swaps in a worker-backed implementation of
// this same one-method interface; nothing else in this file needs to know which
// one is live, which is the whole point of routing every call through here.
const HttpBackend = {
  async call(path, body) {
    const opt = body
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : {};
    const r = await fetch(url(path), opt);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },

  // Screenshot uploads. Separate because they are multipart rather than JSON,
  // and because a backend without OCR answers them without a network call.
  async upload(path, blob, name = 'shot.png') {
    const fd = new FormData();
    fd.append('file', blob, name);
    const r = await fetch(url(path), { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
};

// The same interface, answered by CPython-on-WASM in a worker instead of by a
// server. Identical dispatch() underneath, so a verdict cannot differ between
// the two — see WEB-PLAN.md.
const PyodideBackend = (() => {
  let worker = null;
  let seq = 0;
  const pending = new Map();
  let settle;
  const ready = new Promise((res, rej) => { settle = { res, rej }; });

  const start = (onStatus) => {
    worker = new Worker(url('static/worker.js'), { type: 'module' });
    worker.onmessage = (ev) => {
      const m = ev.data || {};
      if (m.type === 'status') return onStatus?.(m.text, m.detail);
      if (m.type === 'ready') return settle.res();
      if (m.type === 'fatal') return settle.rej(new Error(m.detail));
      const p = pending.get(m.id);
      if (!p) return;
      pending.delete(m.id);
      if (m.ok) p.resolve(m.data);
      else p.reject(new Error(m.detail || `request failed (${m.status})`));
    };
    worker.onerror = (e) => settle.rej(new Error(e.message || 'worker failed to start'));
    return ready;
  };

  return {
    start,
    ready,
    async call(path, body) {
      await ready;
      const id = ++seq;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        worker.postMessage({ id, path, body: body ?? {} });
      });
    },
    // No OCR engine here by design. The scan controls are hidden anyway, so
    // this only ever fires if something calls it directly.
    async upload() {
      throw new Error('This build cannot read screenshots');
    },
  };
})();

let backend = HttpBackend;
const setBackend = (b) => { backend = b; };

const api = (path, body) => backend.call(path, body);
const apiUpload = (path, blob, name) => backend.upload(path, blob, name);

// Which backend serves this page. The hosted build ships the meta tag; the
// query parameter is for exercising the worker against the local server.
const usePyodide =
  new URLSearchParams(location.search).get('backend') === 'pyodide' ||
  document.querySelector('meta[name="du-backend"]')?.content === 'pyodide';

const slug = (s) => (s || '').toLowerCase().replace(/\s+/g, '-');

// Cosmic Fragments reach into the thousands, so they are displayed with
// separators. A type=number input rejects commas outright, hence a text input
// that formats on the way out and strips on the way in.
const fmtNum = (n) => (Number(n) || 0).toLocaleString('en-US');
const parseNum = (s) => {
  const digits = String(s ?? '').replace(/[^0-9]/g, '');
  return digits ? parseInt(digits, 10) : 0;
};

// Real Path / Element icons, downloaded by `python -m data.icons`. Falls back to
// a lettered badge so the UI still reads correctly if they are absent.
function pathIcon(path, big) {
  const d = el('span', 'pathicon' + (big ? ' lg' : ''));
  d.title = path || '';
  if (!path) return d;
  d.dataset.path = slug(path);
  const img = new Image();
  img.src = url(`static/icons/path/${slug(path)}.png`);
  img.alt = path;
  img.onerror = () => { img.remove(); d.append(el('span', 'letter', path[0])); };
  d.append(img);
  return d;
}

function elementIcon(element) {
  const d = el('span', 'elicon');
  d.title = element || '';
  if (!element) return d;
  d.dataset.el = slug(element);
  const img = new Image();
  img.src = url(`static/icons/element/${slug(element)}.png`);
  img.alt = element;
  img.onerror = () => { img.remove(); d.append(el('span', 'letter', element[0])); };
  d.append(img);
  return d;
}

// Character portraits, downloaded by `python -m data.icons` alongside the Path
// and Element art. Team chips carry no id (they store name/path/element), so the
// id is looked up by name — and the same lettered-badge fallback as everywhere
// else covers a character whose art has not landed upstream yet.
const charId = (name) => (META?.characters || []).find((c) => c.name === name)?.id;

function charPortrait(idOrName, cls) {
  const id = typeof idOrName === 'number' ? idOrName : charId(idOrName);
  const name = typeof idOrName === 'number'
    ? ((META?.characters || []).find((c) => c.id === idOrName)?.name || '')
    : idOrName;
  const d = el('span', 'portrait' + (cls ? ' ' + cls : ''));
  d.title = name;
  if (!id) {
    d.append(el('span', 'letter', (name || '?')[0]));
    return d;
  }
  const img = new Image();
  img.src = url(`static/icons/character/${id}.png`);
  img.alt = name;
  img.onerror = () => { img.remove(); d.append(el('span', 'letter', (name || '?')[0])); };
  d.append(img);
  return d;
}

const rarityTag = (r) => {
  if (!r) return null;
  return el('span', 'tag ' + r.toLowerCase(), r);
};

// ---------------------------------------------------------------- state
let META = null;
let RUN = {
  mask_id: null, wishpower_level: 0, plane: 1, difficulty: 1,
  domain_index: 1, domain_total: 13,
  team: [],
  owned_blessings: [], enhanced_blessings: [],
  owned_curios: [], owned_weighted: [], equipped_weighted: [], weighted_slots: 2,
  owned_equations: [],
  owned_miracles: [], miracle_resets: 0, door_redraws: 0,
  fragments: 0, heat: 0, heat_max: 0, heat_per_enhance: 1,
  heat_costs: { Common: 1, Rare: 2, Legendary: 3 },
  store_prices: { Common: 100, Rare: 180, Legendary: 300 },
  blessing_prices: { Common: 80, Rare: 120, Legendary: 180 }, notes: '',
};
let OFFER = [];
const CACHE = new Map();

const ownedKey = (kind) => ({
  blessing: 'owned_blessings', curio: 'owned_curios', weighted_curio: 'owned_weighted',
  equation: 'owned_equations', miracle: 'owned_miracles',
}[kind]);

const KIND_LABEL = {
  blessing: 'blessing', curio: 'curio', weighted_curio: 'weighted curio',
  equation: 'equation', miracle: 'Miracle',
};

// One click from "this is the best option" to "I took it". Ranking and tracking
// were two separate jobs before, which meant doing the same work twice.
function take(entry) {
  const key = ownedKey(entry.kind);
  if (!key) return false;
  if (RUN[key].includes(entry.id)) return false;
  RUN[key].push(entry.id);
  CACHE.set(entry.kind + ':' + entry.id, entry);
  renderOwned(); refreshRun();
  // After the save, or the save's own "saved 10:45" would immediately replace it.
  save().then(() => {
    $('#status').textContent = `took ${entry.name}, now in What I own`;
  });
  return true;
}

const isOwned = (kind, id) => (RUN[ownedKey(kind)] || []).includes(id);

/** "Take" control for a ranked card. Reflects ownership rather than firing blind. */
function takeButton(entry, onTaken, primary = true) {
  const owned = isOwned(entry.kind, entry.id);
  const btn = el('button', owned ? 'ghost taken' : (primary ? 'primary take' : 'ghost take'));
  btn.append(svg('i-check'));
  btn.append(el('span', null, owned ? `Held, in your ${KIND_LABEL[entry.kind]}s` : 'Take this'));
  btn.disabled = owned;
  btn.onclick = () => {
    if (!take(entry)) return;
    btn.className = 'ghost taken';
    btn.disabled = true;
    btn.lastChild.textContent = `Held, in your ${KIND_LABEL[entry.kind]}s`;
    if (onTaken) onTaken();
  };
  return btn;
}

const setStatus = (msg) => { $('#status').textContent = msg; };

/** `force_snapshot` bypasses history coalescing, for a save that is a
 *  deliberate jump rather than part of an edit burst — importing a run is one,
 *  the same way restoring is. Without it an import inside COALESCE_SECONDS of
 *  another save would replace the previous run with no snapshot kept. */
const save = async (opts = {}) => {
  try {
    await api('/api/run/save', { ...RUN, ...opts });
    setStatus('saved ' + new Date().toLocaleTimeString());
  } catch (e) { setStatus('save failed: ' + e.message); }
};

// ------------------------------------------------------------ position
// Setup, the Wishpower tab and the position bar all edit the same level.
const WISH_INPUTS = ['#wishpower', '#wishLevel', '#wishBar'];

function setWishpower(v) {
  RUN.wishpower_level = Math.max(0, v || 0);
  WISH_INPUTS.forEach((sel) => { $(sel).value = RUN.wishpower_level; });
}

function updatePosNote() {
  const total = RUN.domain_total, idx = RUN.domain_index;
  const left = Math.max(0, total - idx + 1);

  // Domain track — position at a glance, with Plane boundaries from the
  // variant's step layout so the boss steps are visible.
  const track = $('#track');
  track.innerHTML = '';
  const variant = (META?.run_lengths || []).find((v) => v.domains === total);
  const bosses = new Set();
  if (variant) {
    let acc = 0;
    variant.steps.forEach((s) => { acc += s; bosses.add(acc); });
  }
  for (let i = 1; i <= Math.min(total, 40); i++) {
    const pip = el('i');
    if (i < idx) pip.className = 'done';
    if (i === idx) pip.className = 'now';
    if (bosses.has(i)) pip.className += ' boss';
    pip.title = `Domain ${i}` + (bosses.has(i) ? ': Plane boss' : '');
    track.append(pip);
  }

  const note = $('#posNote');
  note.innerHTML = '';
  if (left <= 3 && (RUN.fragments > 0 || RUN.heat > 0)) {
    const bits = [];
    if (RUN.fragments) bits.push(`${fmtNum(RUN.fragments)} fragments`);
    if (RUN.heat) bits.push(`${RUN.heat} Heat`);
    note.className = 'posnote alert';
    note.append(svg('i-warn'));
    note.append(el('span', null,
      `${left} Domain${left === 1 ? '' : 's'} left, ${bits.join(' and ')} still unspent. None of it carries over.`));
  } else {
    note.className = 'posnote';
    note.textContent = `${left} Domain${left === 1 ? '' : 's'} remaining`;
  }
}

// ---------------------------------------------------------------- search
/** Arrow-key + Enter navigation over a `.results` list.
 *
 * Every picker in the app renders the same shape — a box of clickable rows
 * under a text input — so this lives here once instead of being reimplemented
 * per surface: the offer, What I own, the store shelf, Occurrence options, the
 * character search and the door beacon picker all get identical keys.
 *
 * Rows are identified by having an `onclick`, which is what keeps the "no
 * match" and "search failed" lines out of the rotation — they are not choices.
 *
 * Returns `reset()`. The owning render must call it whenever it rebuilds the
 * list, or the highlight survives into a list where it points at a different
 * row than the one the user was looking at.
 */
function attachKeyNav(input, box) {
  let at = -1;
  const rows = () => Array.from(box.children).filter((r) => r.onclick);
  const paint = () => {
    const list = rows();
    list.forEach((r, n) => r.classList.toggle('active', n === at));
    if (at >= 0) list[at]?.scrollIntoView({ block: 'nearest' });
  };
  const reset = () => { at = -1; paint(); };

  input.addEventListener('keydown', (ev) => {
    const list = rows();
    if (!list.length) return;
    if (ev.key === 'ArrowDown') {
      at = (at + 1) % list.length;
    } else if (ev.key === 'ArrowUp') {
      at = (at <= 0 ? list.length : at) - 1;
    } else if (ev.key === 'Enter') {
      // Enter with nothing highlighted takes the top hit. Typing a name and
      // pressing Enter is the whole point of this; requiring Down first would
      // make the keyboard slower than the mouse it replaces.
      ev.preventDefault();
      (list[at] || list[0]).click();
      // Repaint, not just `at = -1`. Most pickers clear the box in their own
      // onclick so the row is gone either way, but the ones that do not would
      // keep a highlight on a row Enter no longer targets.
      at = -1;
      paint();
      return;
    } else if (ev.key === 'Escape') {
      box.innerHTML = '';
      at = -1;
      return;
    } else {
      return;
    }
    // Without this the caret jumps to either end of the input on every arrow.
    ev.preventDefault();
    paint();
  });
  return reset;
}

function attachSearch(inputSel, resultsSel, kindFn, onPick) {
  const input = $(inputSel), box = $(resultsSel);
  const resetNav = attachKeyNav(input, box);
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { box.innerHTML = ''; resetNav(); return; }
    timer = setTimeout(async () => {
      // A failure here used to reject unhandled and leave the box empty, which
      // is indistinguishable from "still typing" — that is how a broken search
      // on the hosted build went unreported. Say so instead.
      let results;
      try {
        ({ results } = await api(`/api/search?q=${encodeURIComponent(q)}&kind=${kindFn()}`));
      } catch (err) {
        console.error('search failed', err);
        box.innerHTML = '';
        box.append(el('div', 'sub', 'search failed: ' + (err.message || err)));
        resetNav();
        return;
      }
      box.innerHTML = '';
      if (!results.length) {
        box.append(el('div', 'sub', 'no match'));
        resetNav();
        return;
      }
      results.forEach((e) => {
        const row = el('div');
        if (e.path) row.append(pathIcon(e.path));
        const body = el('div', 'body');
        const head = el('div');
        head.append(el('span', null, e.name));
        const rt = rarityTag(e.rarity);
        if (rt) { head.append(document.createTextNode(' ')); head.append(rt); }
        // A row standing for several must say so — folding an escalating
        // gamble's five stages into one line is only honest if the line admits
        // it is one line.
        if (e.repeats > 1) {
          head.append(document.createTextNode(' '));
          head.append(el('span', 'tag', `x${e.repeats}`));
        }
        body.append(head);
        body.append(el('div', 'sub', (e.desc || '').slice(0, 110)));
        if (e.repeat_note) body.append(el('div', 'sub', e.repeat_note));
        row.append(body);
        row.onclick = () => {
          onPick(e);
          input.value = '';
          box.innerHTML = '';
          resetNav();
          // Keep focus so several options can be entered in a row without
          // reaching for the mouse between each one.
          input.focus();
        };
        box.append(row);
      });
      resetNav();
    }, 130);
  });
}

// ---------------------------------------------------------------- setup
// Compare mode: tick the Masks RNG actually offered, rather than browsing all nine.
let COMPARE = false;
let OFFERED_MASKS = [];

function renderMasks() {
  const box = $('#masks');
  box.innerHTML = '';
  META.masks.forEach((m) => {
    const picked = COMPARE ? OFFERED_MASKS.includes(m.id) : RUN.mask_id === m.id;
    const d = el('div', 'mask' + (picked ? ' sel' : ''));
    const head = el('b', null, m.name);
    if (COMPARE && picked) head.textContent = '✓ ' + m.name;
    d.append(head);
    d.append(el('p', null, m.tagline || m.flavour));
    if (m.wishpower) d.append(el('div', 'wp', m.wishpower));
    d.onclick = () => {
      if (COMPARE) {
        const i = OFFERED_MASKS.indexOf(m.id);
        if (i >= 0) OFFERED_MASKS.splice(i, 1);
        else if (OFFERED_MASKS.length < 4) OFFERED_MASKS.push(m.id);
        $('#rankMasks').hidden = OFFERED_MASKS.length < 2;
        renderMasks();
      } else {
        RUN.mask_id = (RUN.mask_id === m.id ? null : m.id);
        renderMasks();
        maskChanged();
        save();
      }
    };
    box.append(d);
  });
}

function setupMaskCompare() {
  const toggle = $('#compareToggle');
  toggle.onclick = () => {
    COMPARE = !COMPARE;
    OFFERED_MASKS = [];
    toggle.textContent = COMPARE
      ? 'Done comparing, go back to picking'
      : 'RNG offered me a few, compare them';
    $('#maskHint').textContent = COMPARE
      ? 'Tick the Masks the game offered you (2 to 4), then rank them.'
      : 'You choose one at the start, and it shapes the whole run.';
    $('#rankMasks').hidden = true;
    $('#maskRanking').innerHTML = '';
    renderMasks();
  };

  $('#rankMasks').onclick = async () => {
    const data = await api('/api/masks/rank', { run: RUN, mask_ids: OFFERED_MASKS });
    const box = $('#maskRanking');
    box.innerHTML = '';
    data.warnings.forEach((w) => box.append(warnBox(w)));

    const max = Math.max(...data.results.map((r) => r.score), 1);
    data.results.forEach((r, i) => {
      const card = el('div', 'pick' + (i === 0 ? ' top' : ''));
      const h = el('h3');
      h.append(el('span', 'rank', `${i + 1}`));
      h.append(el('span', null, r.name));
      h.append(el('span', 'score', r.score.toFixed(0)));
      card.append(h);

      const bar = el('div', 'bar');
      const fill = el('i');
      fill.style.width = '0%';
      bar.append(fill);
      card.append(bar);
      requestAnimationFrame(() => { fill.style.width = Math.max(0, (r.score / max) * 100) + '%'; });

      card.append(el('div', 'desc', r.tagline));
      if (r.wishpower) card.append(el('div', 'desc', r.wishpower));

      const fbox = el('div', 'factors');
      r.factors.forEach((f) => fbox.append(factorRow(f)));
      card.append(fbox);

      const use = el('button', 'primary', 'Use ' + r.name);
      use.style.marginTop = '10px';
      use.onclick = () => {
        RUN.mask_id = r.id;
        COMPARE = false;
        OFFERED_MASKS = [];
        $('#compareToggle').textContent = 'RNG offered me a few, compare them';
        $('#maskHint').textContent = 'You choose one at the start, and it shapes the whole run.';
        $('#rankMasks').hidden = true;
        box.innerHTML = '';
        renderMasks();
        maskChanged();
        save();
      };
      card.append(use);
      box.append(card);
    });
  };
}

function renderTeam() {
  const box = $('#team');
  box.innerHTML = '';
  if (!RUN.team.length) {
    box.append(el('div', 'hint', 'No characters yet, so synergy scoring stays generic until you add some.'));
  }
  RUN.team.forEach((c, i) => {
    const chip = el('div', 'chip');
    chip.append(charPortrait(c.name));
    chip.append(pathIcon(c.path));
    chip.append(elementIcon(c.element));
    chip.append(el('span', null, c.name));
    const x = el('button', null, '×');
    x.title = 'remove';
    x.onclick = () => { RUN.team.splice(i, 1); renderTeam(); save(); };
    chip.append(x);
    box.append(chip);
  });
}

function setupCharSearch() {
  const input = $('#charSearch'), box = $('#charResults');
  const resetNav = attachKeyNav(input, box);
  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    box.innerHTML = '';
    if (q.length < 1) { resetNav(); return; }
    META.characters.filter((c) => c.name.toLowerCase().includes(q)).slice(0, 12).forEach((c) => {
      const row = el('div');
      row.append(charPortrait(c.id));
      row.append(pathIcon(c.path));
      row.append(elementIcon(c.element));
      const body = el('div', 'body');
      body.append(el('div', null, c.name));
      body.append(el('div', 'sub', `${c.path} · ${c.element}`));
      row.append(body);
      row.onclick = () => {
        if (RUN.team.length >= 4) RUN.team.shift();
        RUN.team.push({ name: c.name, path: c.path, element: c.element });
        input.value = ''; box.innerHTML = ''; resetNav(); renderTeam(); save();
      };
      box.append(row);
    });
    resetNav();
  });
}

// The game labels its difficulties in Roman numerals, so the number typed here
// has to be findable on that rail. Anything above V (Difficulty X, the
// Astronomical Division ladder) is not in the pinned data at all -- see the
// static hint on the Setup tab.
const DIFFICULTY_NUMERAL = ['', 'I', 'II', 'III', 'IV', 'V'];

function updateDifficultyNote() {
  const variants = (META.run_lengths || []).filter((v) => v.difficulties.includes(RUN.difficulty));
  const totals = variants.map((v) => v.domains);
  const numeral = DIFFICULTY_NUMERAL[RUN.difficulty];
  const named = numeral ? `Difficulty ${RUN.difficulty} (${numeral} in game)` : `Difficulty ${RUN.difficulty}`;
  $('#difficultyNote').textContent = totals.length
    ? `${named} runs ${totals.join(' or ')} Domains. Set the total from the game's own counter.`
    : '';
}

// ---------------------------------------------------------------- decide
function renderOffer() {
  const box = $('#offerList');
  box.innerHTML = '';
  OFFER.forEach((e, i) => {
    const chip = el('div', 'chip');
    if (e.path) chip.append(pathIcon(e.path));
    chip.append(el('span', null, e.name));
    const x = el('button', null, '×');
    x.onclick = () => { OFFER.splice(i, 1); renderOffer(); };
    chip.append(x);
    box.append(chip);
  });
  $('#rankBtn').disabled = OFFER.length < 1;
}

function emptyState(msg) {
  const d = el('div', 'empty');
  d.append(svg('i-empty'));
  d.append(el('div', null, msg));
  return d;
}

function factorRow(f) {
  const row = el('div', 'factor');
  row.append(el('span', 'fname', f.name));
  row.append(el('span', 'pts ' + (f.points > 0 ? 'pos' : f.points < 0 ? 'neg' : ''),
    (f.points > 0 ? '+' : '') + f.points.toFixed(1)));
  row.append(el('span', 'note', f.note || ''));
  return row;
}

function warnBox(text) {
  const d = el('div', 'warn');
  d.append(svg('i-warn'));
  d.append(el('span', null, text));
  return d;
}

function renderRanking(data) {
  const box = $('#ranking');
  box.innerHTML = '';
  const r = data.run || {};
  if (r.domains_left != null) {
    box.append(el('div', 'hint',
      `Domain ${r.domain_index}/${r.domain_total} · ${r.domains_left} left · ` +
      `~${r.picks_remaining} picks · fragment scarcity ${r.fragment_scarcity}` +
      (r.endgame ? ' · endgame' : '')));
  }
  data.warnings.forEach((w) => box.append(warnBox(w)));

  const max = Math.max(...data.results.map((x) => x.score), 1);
  data.results.forEach((x, i) => {
    const card = el('div', 'pick' + (i === 0 && !x.blocked ? ' top' : '') + (x.blocked ? ' blocked' : ''));
    const h = el('h3');
    h.append(el('span', 'rank', `${i + 1}`));
    if (x.path) h.append(pathIcon(x.path, true));
    h.append(el('span', null, x.name));
    const rt = rarityTag(x.rarity);
    if (rt) h.append(rt);
    h.append(el('span', 'score', x.score.toFixed(0)));
    card.append(h);

    const bar = el('div', 'bar');
    const fill = el('i');
    fill.style.width = '0%';
    bar.append(fill);
    card.append(bar);
    requestAnimationFrame(() => { fill.style.width = Math.max(0, (x.score / max) * 100) + '%'; });

    if (x.desc) card.append(el('div', 'desc', x.desc));
    if (x.blocked) card.append(el('div', 'blockmsg', x.blocked));

    const fbox = el('div', 'factors');
    x.factors.filter((f) => Math.abs(f.points) > 0.05).forEach((f) => fbox.append(factorRow(f)));
    card.append(fbox);

    if (x.equation_drivers && x.equation_drivers.length) {
      const d = x.equation_drivers[0];
      // "Completes X" is a claim about an Equation you hold. For one you do not,
      // the honest line is conditional -- the run can end without it ever arriving.
      const line = d.completes
        ? (d.held ? `Completes ${d.name}.`
                  : `Would complete ${d.name} (${d.rarity}), which you do not hold yet.`)
        : `${d.distance} more ${x.path} for ${d.name} (${d.rarity})`
          + (d.held ? '.' : ', not held yet.');
      card.append(el('div', 'desc', line));
    }

    // Taking a pick is the whole point of ranking it, so the action lives on the
    // card rather than in a separate tab you have to retype the name into.
    const actions = el('div', 'actions');
    actions.append(takeButton(x, () => {
      // The whole offer is spent once you have chosen from it, not just the
      // line you took: the other two cards of a 1-of-3 are gone either way.
      // Dropping only the taken one left the losers sitting in the chip list to
      // be re-ranked at the next Domain against a run that had moved on. This
      // matches the Wishpower hand, which has always cleared itself.
      OFFER = [];
      renderOffer();
    }, i === 0 && !x.blocked));
    card.append(actions);
    box.append(card);
  });
}

// ------------------------------------------------------------- verdicts
function renderVerdicts(box, verdicts, heading, decorate) {
  box.innerHTML = '';
  if (heading) box.append(el('div', 'hint', heading));
  if (!verdicts.length) { box.append(emptyState('Nothing to weigh yet.')); return; }
  verdicts.forEach((v) => {
    const broke = v.affordable === false;
    const d = el('div', 'verdict' + (v.recommended ? ' rec' : '') +
      (v.action === 'skip' ? ' skip' : '') + (broke ? ' broke' : ''));
    const h = el('h4');
    h.append(el('span', 'act', v.action));
    // Which Path a Blessing is on is the first thing you check about it, so it
    // travels with the name everywhere the name appears.
    if (v.path) h.append(pathIcon(v.path));
    h.append(el('span', null, v.target));
    if (v.cost) h.append(el('span', 'tag', `${fmtNum(v.cost)} ${v.currency}`));
    // A score of -98 only means "you have no Heat", which the words say better.
    h.append(broke
      ? el('span', 'val cant', 'cannot afford')
      : el('span', 'val', (v.score >= 0 ? '+' : '') + v.score.toFixed(2)));
    d.append(h);
    if (v.reasons?.length) {
      const ul = el('ul');
      v.reasons.forEach((r) => ul.append(el('li', null, r)));
      d.append(ul);
    }
    if (decorate) decorate(v, d);
    box.append(d);
  });
}

// ------------------------------------------------------------------ door
// The entry form mirrors "Select Next Destination": one card per Domain drawn,
// each with a type, a level and its beacons. Beacons used to be unreachable from
// the UI — the array was always sent empty — which left half of the door scoring
// dead, and they are frequently the whole decision.

let DOORS = [];
let DOMAIN_TYPES = [];
let BEACONS = [];

function beaconById(id) {
  return BEACONS.find((b) => b.id === id);
}

/** The line the game prints in the bar above the cards, rebuilt from our data. */
function doorHeader(door) {
  const wrap = el('div', 'doorhead');
  const type = DOMAIN_TYPES.find((d) => d.name === door.name);
  wrap.append(el('div', 'dtype', type?.desc || door.name));
  const list = el('div', 'dbeacons');
  if (!door.beacons.length) {
    list.append(el('div', 'none', 'No beacons available'));
  } else {
    door.beacons.forEach((id) => {
      const b = beaconById(id);
      if (!b) return;
      const row = el('div', 'bline' + (b.polarity === 'Negative' ? ' bad' : ''));
      row.append(el('b', null, b.name + ':'));
      row.append(el('span', null, ' ' + b.effect));
      list.append(row);
    });
  }
  wrap.append(list);
  return wrap;
}

function renderDoors() {
  const box = $('#doorList');
  box.innerHTML = '';
  DOORS.forEach((d, i) => {
    const card = el('div', 'doorcard');

    const head = el('div', 'row');
    head.style.margin = '0 0 8px';
    const sel = document.createElement('select');
    DOMAIN_TYPES.forEach((t) => {
      const o = document.createElement('option');
      o.value = t.name;
      o.textContent = t.name + (t.hidden ? ' (hidden)' : '');
      sel.append(o);
    });
    sel.value = d.name;
    sel.onchange = () => { d.name = sel.value; renderDoors(); };
    head.append(sel);

    const lv = document.createElement('input');
    lv.type = 'number'; lv.min = '1'; lv.max = '5'; lv.style.width = '64px';
    lv.title = 'Domain level, as printed on the card';
    lv.value = d.level || '';
    lv.placeholder = 'Lv';
    lv.onchange = () => { d.level = parseInt(lv.value || '0', 10) || null; renderDoors(); };
    head.append(el('span', 'hint', 'Lv'));
    head.append(lv);

    const x = el('button', 'ghost', 'Remove');
    x.style.marginLeft = 'auto';
    x.onclick = () => { DOORS.splice(i, 1); renderDoors(); };
    head.append(x);
    card.append(head);

    card.append(doorHeader(d));

    // Beacon picker: filtered list, since 54 of them is too many for a select.
    const picker = el('div', 'bpicker');
    const filter = document.createElement('input');
    filter.placeholder = 'Add a beacon by name or effect…';
    filter.autocomplete = 'off';
    const results = el('div', 'results');
    // Same keys as every other picker. The input is rebuilt on each
    // renderDoors(), so the listener goes with it rather than accumulating.
    const resetNav = attachKeyNav(filter, results);
    const draw = () => {
      const q = filter.value.trim().toLowerCase();
      results.innerHTML = '';
      if (!q) { resetNav(); return; }
      BEACONS.filter((b) => !d.beacons.includes(b.id) &&
        (b.name.toLowerCase().includes(q) || (b.effect || '').toLowerCase().includes(q)))
        .slice(0, 8).forEach((b) => {
          const row = el('div');
          const body = el('div', 'body');
          const line = el('div');
          line.append(el('span', null, b.name));
          if (b.polarity === 'Negative') {
            line.append(document.createTextNode(' '));
            line.append(el('span', 'tag negative', 'negative'));
          }
          body.append(line);
          body.append(el('div', 'sub', b.effect || ''));
          row.append(body);
          row.onclick = () => {
            d.beacons.push(b.id);
            renderDoors();
          };
          results.append(row);
        });
      resetNav();
    };
    filter.addEventListener('input', draw);
    picker.append(filter);
    picker.append(results);

    if (d.beacons.length) {
      const chips = el('div', 'chips');
      d.beacons.forEach((id, bi) => {
        const b = beaconById(id);
        const chip = el('div', 'chip');
        chip.append(el('span', null, b ? b.name : `beacon ${id}`));
        const rm = el('button', null, '×');
        rm.onclick = () => { d.beacons.splice(bi, 1); renderDoors(); };
        chip.append(rm);
        chips.append(chip);
      });
      picker.append(chips);
    }
    card.append(picker);
    box.append(card);
  });

  if (!DOORS.length) {
    box.append(el('div', 'hint', 'No cards yet. Add one per Domain the draw offered you.'));
  }
  $('#rankDoors').disabled = DOORS.length < 1;
}

function renderDoorResult(data) {
  const box = $('#doorResult');
  box.innerHTML = '';
  const p = data.position;
  box.append(el('div', 'hint',
    `Domain ${p.domain}/${p.total} · ${p.left} left · fragment scarcity ${p.fragment_scarcity}` +
    (p.endgame ? ' · endgame: spend, do not save' : '')));
  data.warnings.forEach((w) => box.append(warnBox(w)));

  const all = data.doors.map((d) => d.score)
    .concat(data.redraw?.available ? [data.redraw.score] : []);
  const max = Math.max(...all.map(Math.abs), 0.01);

  data.doors.forEach((d, i) => {
    const card = el('div', 'pick' + (d.recommended ? ' top' : ''));
    const h = el('h3');
    h.append(el('span', 'rank', `${i + 1}`));
    h.append(el('span', null, d.name));
    if (d.level) h.append(el('span', 'tag', 'Lv ' + d.level));
    h.append(el('span', 'score', (d.score >= 0 ? '+' : '') + d.score.toFixed(2)));
    card.append(h);

    const bar = el('div', 'bar');
    const fill = el('i');
    fill.style.width = '0%';
    bar.append(fill);
    card.append(bar);
    requestAnimationFrame(() => { fill.style.width = Math.max(0, (d.score / max) * 100) + '%'; });

    if (d.desc) card.append(el('div', 'desc', d.desc));
    // Beacons get their own rows with the points they contributed, so a door that
    // won on its beacons rather than its type says so.
    d.beacons.forEach((b) => {
      const row = el('div', 'factor');
      row.append(el('span', 'fname', b.name));
      row.append(el('span', 'pts ' + (b.value > 0 ? 'pos' : b.value < 0 ? 'neg' : ''),
        (b.value > 0 ? '+' : '') + b.value.toFixed(2)));
      row.append(el('span', 'note', b.effect));
      card.append(row);
    });

    const ul = el('ul');
    ul.style.cssText = 'margin:6px 0 0;padding-left:18px;color:var(--dim);font-size:12px';
    d.reasons.forEach((r) => ul.append(el('li', null, r)));
    if (ul.children.length) card.append(ul);
    box.append(card);
  });

  const rd = data.redraw;
  if (!rd) return;
  const card = el('div', 'pick reshuffle' + (rd.recommended ? ' top' : '') +
    (rd.available ? '' : ' blocked'));
  const h = el('h3');
  h.append(svg('i-shuffle', 'rsicon'));
  h.append(el('span', null, rd.target));
  if (rd.available) h.append(el('span', 'score', (rd.score >= 0 ? '+' : '') + rd.score.toFixed(2)));
  card.append(h);
  const ul = el('ul');
  rd.reasons.forEach((r) => ul.append(el('li', null, r)));
  card.append(ul);
  if (rd.available) {
    const btn = el('button', 'ghost');
    btn.append(svg('i-shuffle'));
    btn.append(el('span', null, 'I redrew, count one down'));
    btn.onclick = () => {
      RUN.door_redraws = Math.max(0, RUN.door_redraws - 1);
      $('#doorRedraws').value = RUN.door_redraws;
      DOORS = [];
      box.innerHTML = '';
      renderDoors(); save();
      $('#status').textContent = `redrew, ${RUN.door_redraws} redraw(s) left`;
    };
    const actions = el('div', 'actions');
    actions.append(btn);
    card.append(actions);
  }
  box.append(card);
}

async function setupDoors() {
  const data = await api('/api/domains');
  DOMAIN_TYPES = data.domains;
  BEACONS = data.beacons;

  $('#addDoor').onclick = () => {
    // Lv 1 rather than blank: it is the commonest card by a distance, and a
    // blank level scores as "no level at all" without saying so. Correcting a
    // wrong default costs the same keystroke as filling an empty one, and only
    // one of the two is ever right by accident.
    DOORS.push({ name: DOMAIN_TYPES[0]?.name || '', beacons: [], level: 1 });
    renderDoors();
  };
  $('#clearDoors').onclick = () => { DOORS = []; renderDoors(); $('#doorResult').innerHTML = ''; };
  $('#rankDoors').onclick = async () => {
    renderDoorResult(await api('/api/waypoint', {
      run: RUN, doors: DOORS, redraws_remaining: RUN.door_redraws,
    }));
  };
  renderDoors();
}

// ----------------------------------------------------------------- spend
let OPTIONS = [];
const OPT_COSTS = {};

function renderOptions() {
  const box = $('#optList');
  box.innerHTML = '';
  OPTIONS.forEach((o, i) => {
    const chip = el('div', 'chip');
    chip.append(el('span', null, o.name.slice(0, 44) + (OPT_COSTS[o.id] ? `, ${OPT_COSTS[o.id]}` : '')));
    const cost = document.createElement('input');
    cost.type = 'number'; cost.min = '0'; cost.placeholder = 'cost';
    cost.style.width = '74px';
    cost.title = 'The cost on your screen. The number in the data is a placeholder';
    cost.value = OPT_COSTS[o.id] || '';
    cost.onchange = () => {
      const v = parseInt(cost.value || '0', 10);
      if (v) OPT_COSTS[o.id] = v; else delete OPT_COSTS[o.id];
    };
    chip.append(cost);
    const x = el('button', null, '×');
    x.onclick = () => { OPTIONS.splice(i, 1); delete OPT_COSTS[o.id]; renderOptions(); };
    chip.append(x);
    box.append(chip);
  });
  $('#askOffer').disabled = OPTIONS.length < 1;
}

/** Offer the rest of the Occurrence once one of its lines has been identified. */
async function offerOptionSet(picked) {
  const box = $('#optSiblings');
  box.innerHTML = '';
  let data;
  try {
    data = await api('/api/options/set?option_id=' + picked.id);
  } catch (e) {
    // The siblings are an extra, so a failure stays quiet on screen — but not in
    // the console, which is where a whole route being unreachable should show.
    console.error('option set lookup failed', e);
    return;
  }
  const rest = data.options.filter((o) => !OPTIONS.some((x) => x.id === o.id));
  if (!rest.length) return;

  const wrap = el('div', 'siblings');
  const head = el('div', 'row');
  head.style.margin = '0 0 6px';
  head.append(el('span', 'hint',
    `${rest.length} more option(s) look like they belong to the same Occurrence. ` +
    `Add the ones actually on your screen. This infers the grouping from how the data ` +
    `is laid out, so check it before you trust it.`));
  wrap.append(head);

  const addAll = el('button', 'ghost', 'Add all of them');
  addAll.onclick = () => {
    rest.forEach((o) => { if (!OPTIONS.some((x) => x.id === o.id)) OPTIONS.push(o); });
    box.innerHTML = '';
    renderOptions();
  };

  rest.forEach((o) => {
    const row = el('div', 'ocropt');
    row.append(el('span', null, o.name));
    row.append(el('span', 'note', o.desc));
    if (o.effects?.includes('leave')) row.append(el('span', 'tag', 'stop'));
    if (o.risk === 'high') row.append(el('span', 'tag negative', 'gamble'));
    row.onclick = () => {
      if (!OPTIONS.some((x) => x.id === o.id)) OPTIONS.push(o);
      row.remove();
      renderOptions();
    };
    wrap.append(row);
  });
  wrap.append(addAll);
  box.append(wrap);
}

/** Record enhances as actually performed: mark them, and spend the Heat.
 *
 * Marking without deducting left the counter stale, so the next plan believed
 * the Heat was still there and over-committed it. */
/** Correct the record: this was already enhanced, and no Heat is spent doing it.
 *
 * The bench recommends work you have already done whenever a previous enhance
 * went unrecorded, and the only fix used to be "Done" — which deducts Heat you
 * never spent, so fixing the records quietly corrupted the counter instead. */
async function markAlreadyEnhanced(id, name) {
  if (!RUN.enhanced_blessings.includes(id)) RUN.enhanced_blessings.push(id);
  renderOwned(); refreshRun();
  await save();
  $('#status').textContent =
    `${name || 'blessing'} marked already enhanced, no Heat spent, ${RUN.heat} left`;
  $('#askWorkbench').click();
}

async function applyEnhances(steps) {
  if (!steps.length) return;
  let spent = 0;
  steps.forEach((s) => {
    if (!RUN.enhanced_blessings.includes(s.id)) RUN.enhanced_blessings.push(s.id);
    spent += s.cost;
  });
  RUN.heat = Math.max(0, RUN.heat - spent);
  $('#heat').value = RUN.heat;
  updatePosNote();
  renderOwned();
  await save();
  $('#status').textContent =
    `${steps.length === 1 ? steps[0].name : steps.length + ' enhanced'}, ` +
    `${spent} Heat spent, ${RUN.heat} left`;
  $('#askWorkbench').click();
}

// --------------------------------------------------------------- the store
// A shelf is entered card by card, because the Curio names are the one thing on
// a store screen that is searchable — unlike the Wishpower pool, where 136 rows
// share three names between them.
let SHELF = [];
let SHELF_KIND = 'curio';

// The two shops are separate screens with separate price lists, so the tables
// are kept apart rather than merged behind one "price by rarity".
const priceKey = () => (SHELF_KIND === 'blessing' ? 'blessing_prices' : 'store_prices');
const shelfPrice = (c) => (RUN[priceKey()] || {})[c.rarity] || 0;

const SHELF_COPY = {
  curio: {
    placeholder: "Type a Curio on the shelf, e.g. 'Sealing Wax'…",
    note: 'Herta sells Curios, one at a time.',
    prices: 'Nothing datamined a price table, so these come from play. A 1-star reads '
      + '100 and a 2-star 180, both confirmed. Legendary is a guess. Whatever you type on '
      + 'a card always wins.',
  },
  blessing: {
    placeholder: "Type a Blessing on the shelf, e.g. 'Nova Burst'…",
    note: 'Blessings, with Batch Select, so the answer is a set.',
    prices: 'All three prices come off one Blessing Store screen. The ordinary Blessing '
      + 'scorer judges these, so Equation progress and Path concentration decide it, the '
      + 'same two things the counters along the top of that screen show you.',
  },
};

function applyShelfKind() {
  const copy = SHELF_COPY[SHELF_KIND];
  $('#storeSearch').placeholder = copy.placeholder;
  $('#storeKindNote').textContent = copy.note;
  $('#storePriceNote').textContent = copy.prices;
  [['priceCommon', 'Common'], ['priceRare', 'Rare'], ['priceLegendary', 'Legendary']]
    .forEach(([id, rarity]) => {
      const v = (RUN[priceKey()] || {})[rarity];
      if (v != null) $('#' + id).value = v;
    });
}

function renderShelf() {
  const box = $('#storeList');
  box.innerHTML = '';
  SHELF.forEach((c, i) => {
    const chip = el('div', 'chip');
    if (c.path) chip.append(pathIcon(c.path));
    chip.append(el('span', null, c.name.slice(0, 34)));
    const rt = rarityTag(c.rarity);
    if (rt) chip.append(rt);
    const cost = document.createElement('input');
    cost.type = 'number'; cost.min = '0'; cost.placeholder = 'price';
    cost.style.width = '74px';
    cost.title = 'The price printed on the card, filled in from its rarity';
    cost.value = c.cost || '';
    cost.onchange = () => { c.cost = parseInt(cost.value || '0', 10); };
    chip.append(cost);
    const x = el('button', null, '×');
    x.onclick = () => { SHELF.splice(i, 1); renderShelf(); };
    chip.append(x);
    box.append(chip);
  });
  $('#askStore').disabled = SHELF.length < 1;
}

/** The verdict banner: what to do, in one sentence, before any of the working. */
function storeHeadline(data) {
  const cls = { buy: 'plan', refresh: 'plan', pass: 'plan pass' }[data.recommendation];
  const card = el('div', cls);
  const h = el('h4');
  h.append(svg(data.recommendation === 'pass' ? 'i-no' : 'i-store', 'planicon'));
  h.append(el('span', null, data.headline));
  card.append(h);
  card.append(el('div', 'sub',
    `Domain ${RUN.domain_index}/${RUN.domain_total} · ${data.domains_left} left · ` +
    `${fmtNum(data.fragments)} fragments · scarcity ${data.fragment_scarcity} · ` +
    `a card needs ${data.floor.toFixed(2)} to be worth buying at all`));
  if (data.passing.length) {
    card.append(el('div', 'sub',
      `Hard pass on ${data.passing.length} of ${data.items.length}: ${data.passing.join(', ')}.`));
  }
  if (data.held?.length) {
    card.append(el('div', 'sub',
      `Already in your bag, so not weighed: ${data.held.join(', ')}.`));
  }
  return card;
}

/** The Batch Select answer: which *set* to buy, and what it costs together. */
function storePlan(data, onBought) {
  const p = data.plan;
  if (!p?.steps?.length) return null;
  const card = el('div', 'plan');
  const h = el('h4');
  h.append(svg('i-store', 'planicon'));
  h.append(el('span', null,
    `Batch: buy ${p.steps.length} for ${fmtNum(p.spend)} fragments`));
  card.append(h);
  p.steps.forEach((s) => {
    const row = el('div', 'planrow');
    row.append(el('span', 'cost', `${fmtNum(s.cost)}`));
    if (s.path) row.append(pathIcon(s.path));
    row.append(el('span', 'nm', s.name));
    const rt = rarityTag(s.rarity);
    if (rt) row.append(rt);
    row.append(el('span', 'val', s.value.toFixed(2)));
    const entry = SHELF.find((c) => c.id === s.id);
    if (entry) {
      const done = el('button', 'ghost done');
      done.append(svg('i-check'));
      done.append(el('span', null, 'Bought'));
      done.title = `Add to what you own and spend ${s.cost} fragments`;
      done.onclick = () => onBought([s]);
      row.append(done);
    }
    card.append(row);
  });
  if (p.steps.length > 1) {
    const all = el('button', 'primary doneall');
    all.append(svg('i-check'));
    all.append(el('span', null, `Bought all ${p.steps.length} for ${fmtNum(p.spend)} fragments`));
    all.onclick = () => onBought(p.steps.slice());
    card.append(all);
  }
  p.notes.forEach((n) => card.append(el('div', 'sub', n)));
  return card;
}

/** "I refreshed" for any verdict that costs fragments to re-draw.
 *
 * The Door redraw and the Wishpower reshuffle have always had this button, but
 * both of those spend a *counter*. The two that spend fragments — the store
 * shelf and the Occurrence reroll — had none, so refreshing in game left the
 * balance wrong until you noticed and corrected it by hand, and every verdict
 * in between was priced against money you no longer had.
 *
 * `clear` is not optional. A refresh replaces the goods outright, so the old
 * listing and the verdicts describing it are gone: leaving them would rank a
 * shelf that no longer exists, and print "Buy Free Tonality" above a screen
 * showing something else entirely.
 *
 * Returns null when the button does not apply, so callers can `if (!btn) return`.
 */
function refreshedButton(v, clear, countSel) {
  if (v.action !== 'refresh') return null;
  const cost = v.cost || 0;
  // `Verdict.affordable` defaults to true and the Occurrence reroll never sets
  // it, so the balance is checked here rather than trusted from the payload.
  if (cost > RUN.fragments) return null;

  const btn = el('button', 'ghost');
  btn.append(svg('i-shuffle'));
  btn.append(el('span', null,
    cost ? `I refreshed, spend ${fmtNum(cost)}` : 'I refreshed'));
  btn.title = 'Record the refresh: deducts the cost and clears the old listing';
  btn.onclick = () => {
    RUN.fragments = Math.max(0, RUN.fragments - cost);
    $('#fragments').value = fmtNum(RUN.fragments);
    if (countSel) {
      const left = $(countSel).value;
      if (left !== '') $(countSel).value = Math.max(0, parseInt(left, 10) - 1);
    }
    clear();
    updatePosNote(); save();
    $('#status').textContent = cost
      ? `refreshed, ${fmtNum(cost)} spent, ${fmtNum(RUN.fragments)} left`
      : 'refreshed. Enter what the new screen is offering';
  };
  return btn;
}

/** Record a purchase: own it, spend the fragments, drop it from the shelf. */
function buyFromShelf(steps) {
  let spent = 0;
  steps.forEach((s) => {
    const entry = SHELF.find((c) => c.id === s.id);
    if (!entry) return;
    if (take(entry)) spent += s.cost || 0;
    const i = SHELF.findIndex((c) => c.id === s.id);
    if (i >= 0) SHELF.splice(i, 1);
  });
  RUN.fragments = Math.max(0, RUN.fragments - spent);
  $('#fragments').value = fmtNum(RUN.fragments);
  renderShelf(); updatePosNote(); save();
  $('#status').textContent =
    `${steps.length === 1 ? steps[0].name : steps.length + ' bought'}, `
    + `${fmtNum(spent)} fragments spent, ${fmtNum(RUN.fragments)} left`;
  // Re-rank what is left, or the headline still recommends what you just bought.
  if (SHELF.length) $('#askStore').click(); else $('#storeResult').innerHTML = '';
}

function setupStore() {
  [['priceCommon', 'Common'], ['priceRare', 'Rare'], ['priceLegendary', 'Legendary']]
    .forEach(([id, rarity]) => {
      const inp = $('#' + id);
      inp.addEventListener('change', () => {
        const key = priceKey();
        RUN[key] = Object.assign({}, RUN[key], { [rarity]: parseInt(inp.value || '0', 10) });
        // Re-price only the cards still showing their prefill, so a corrected
        // price typed on a chip is never silently overwritten.
        SHELF.forEach((c) => { if (c.cost === c.prefill) { c.cost = shelfPrice(c); c.prefill = c.cost; } });
        renderShelf();
        save();
      });
    });

  $('#storeKind').addEventListener('change', () => {
    SHELF_KIND = $('#storeKind').value;
    // The two shelves are different screens, so switching clears rather than
    // carrying Curios into a Blessing Store where they cannot be bought.
    SHELF.length = 0;
    $('#storeResult').innerHTML = '';
    applyShelfKind();
    renderShelf();
  });
  applyShelfKind();

  attachSearch('#storeSearch', '#storeResults', () => SHELF_KIND, (e) => {
    if (SHELF.some((c) => c.id === e.id)) return;
    const c = Object.assign({}, e);
    c.cost = shelfPrice(c);
    c.prefill = c.cost;
    SHELF.push(c);
    renderShelf();
  });

  $('#askStore').onclick = async () => {
    const left = $('#storeRefreshLeft').value;
    const data = await api('/api/store', {
      run: RUN,
      kind: SHELF_KIND,
      items: SHELF.map((c) => ({ id: c.id, cost: c.cost || 0 })),
      refresh_cost: parseInt($('#storeRefresh').value || '0', 10),
      refreshes_left: left === '' ? null : parseInt(left, 10),
    });
    const box = $('#storeResult');
    box.innerHTML = '';
    if (data.endgame_advice) box.append(warnBox(data.endgame_advice));
    box.append(storeHeadline(data));

    // Buying is the same action as owning it, so it happens here rather than
    // being retyped into What I own. The control goes directly under the
    // headline and names its target: parked at the end of the list it sat
    // beneath the *worst* card on the shelf and read as taking that one.
    const plan = storePlan(data, buyFromShelf);
    if (plan) {
      box.append(plan);
    } else {
      const recBuy = data.verdicts.find((v) => v.recommended && v.action === 'buy');
      const entry = recBuy && SHELF.find((c) => c.name === recBuy.target);
      if (entry) {
        const actions = el('div', 'actions');
        const btn = takeButton(entry, null);
        if (!btn.disabled) {
          btn.lastChild.textContent =
            `Bought ${entry.name} for ${fmtNum(entry.cost || 0)} fragments`;
          btn.onclick = () => buyFromShelf([{ id: entry.id, name: entry.name, cost: entry.cost }]);
        }
        actions.append(btn);
        box.append(actions);
      }
    }

    const rest = el('div');
    renderVerdicts(rest, data.verdicts,
      'Every card is weighed against walking out. The skip line is a competitor, not a fallback.',
      (v, card) => {
        const btn = refreshedButton(v, () => {
          SHELF = [];
          renderShelf();
          $('#storeResult').innerHTML = '';
        }, '#storeRefreshLeft');
        if (!btn) return;
        const actions = el('div', 'actions');
        actions.append(btn);
        card.append(actions);
      });
    box.append(rest);
  };
}

function setupSpend() {
  // Rarity sets the Heat price, so these have to be editable — they are reported
  // from play rather than read out of the game files.
  [['heatCommon', 'Common'], ['heatRare', 'Rare'], ['heatLegendary', 'Legendary']]
    .forEach(([id, rarity]) => {
      const inp = $('#' + id);
      inp.value = RUN.heat_costs?.[rarity] ?? inp.value;
      inp.addEventListener('change', () => {
        RUN.heat_costs = Object.assign({}, RUN.heat_costs,
          { [rarity]: parseInt(inp.value || '0', 10) });
        save();
      });
    });

  $('#askWorkbench').onclick = async () => {
    const data = await api('/api/workbench', { run: RUN });
    const box = $('#workbenchResult');
    box.innerHTML = '';
    if (data.endgame_advice) box.append(warnBox(data.endgame_advice));

    // The plan first: with costs of 1/2/3 the best single enhance is often not
    // the best opening move, so a ranked list read top-down misleads.
    const p = data.plan;
    const card = el('div', 'plan');
    const h = el('h4');
    h.append(svg('i-heat', 'planicon'));
    h.append(el('span', null, p.steps.length
      ? `Spend ${p.spend} of ${p.heat} Heat on ${p.steps.length} enhance${p.steps.length === 1 ? '' : 's'}`
      : `Nothing worth spending ${p.heat} Heat on`));
    card.append(h);
    p.steps.forEach((s) => {
      const row = el('div', 'planrow');
      row.append(el('span', 'cost', `${s.cost} Heat`));
      if (s.path) row.append(pathIcon(s.path));
      row.append(el('span', 'nm', s.name));
      const rt = rarityTag(s.rarity);
      if (rt) row.append(rt);
      row.append(el('span', 'val', s.value.toFixed(2)));
      // Tick it off here, where you are standing when you do it — that is what
      // stops the next bench recommending the same work again.
      const done = el('button', 'ghost done');
      done.append(svg('i-check'));
      done.append(el('span', null, 'Done'));
      done.title = `I just enhanced it. Mark it and spend ${s.cost} Heat`;
      done.onclick = () => applyEnhances([s]);
      row.append(done);
      // ...and the other half of the same problem: a Blessing that was enhanced
      // earlier and never recorded gets recommended again, and "Done" would
      // charge you Heat for work already paid for.
      const already = el('button', 'ghost done', '');
      already.append(el('span', null, 'Already'));
      already.title = 'I enhanced this earlier. Correct the record, spend no Heat';
      already.onclick = () => markAlreadyEnhanced(s.id, s.name);
      row.append(already);
      card.append(row);
    });

    if (p.steps.length > 1) {
      const all = el('button', 'primary doneall');
      all.append(svg('i-check'));
      all.append(el('span', null,
        `Done all ${p.steps.length}, spend ${p.spend} Heat`));
      all.title = 'Mark every step enhanced and deduct the whole spend';
      all.onclick = () => applyEnhances(p.steps.slice());
      card.append(all);
    }
    p.notes.forEach((n) => card.append(el('div', 'sub', n)));
    box.append(card);

    const rest = el('div');
    renderVerdicts(rest, data.verdicts, data.note, (v, card) => {
      // Every ranked enhance is a candidate for "I already did this one" too —
      // the plan only ever shows the affordable set, and the one you forgot to
      // record is as likely to be further down.
      if (v.action !== 'upgrade' || !v.entry_id) return;
      const actions = el('div', 'actions');
      const already = el('button', 'ghost');
      already.append(svg('i-check'));
      already.append(el('span', null, 'Already enhanced, no Heat'));
      already.title = 'Correct the record without spending Heat';
      already.onclick = () => markAlreadyEnhanced(v.entry_id, v.target);
      actions.append(already);
      card.append(actions);
    });
    box.append(rest);
  };

  attachSearch('#optSearch', '#optResults', () => 'option', (e) => {
    if (!OPTIONS.some((o) => o.id === e.id)) OPTIONS.push(e);
    renderOptions();
    offerOptionSet(e);
  });

  $('#askOffer').onclick = async () => {
    const data = await api('/api/offer', {
      run: RUN,
      option_ids: OPTIONS.map((o) => o.id),
      costs: OPT_COSTS,
      refresh_cost: parseInt($('#refreshCost').value || '0', 10),
    });
    const box = $('#offerResult');
    renderVerdicts(box, data.verdicts,
      `You hold ${fmtNum(data.fragments)} fragments · scarcity ${data.fragment_scarcity}`,
      (v, card) => {
        const btn = refreshedButton(v, () => {
          // The reroll replaces the whole screen, so the lines, their prices
          // and the sibling offer all belong to an Occurrence that is gone.
          OPTIONS = [];
          Object.keys(OPT_COSTS).forEach((k) => delete OPT_COSTS[k]);
          renderOptions();
          $('#optSiblings').innerHTML = '';
          box.innerHTML = '';
        });
        if (!btn) return;
        const actions = el('div', 'actions');
        actions.append(btn);
        card.append(actions);
      });
    if (data.endgame_advice) box.prepend(warnBox(data.endgame_advice));
  };

  setupDrop('#optDrop', async (blob) => {
    const box = $('#optLines');
    box.innerHTML = '<div class="hint">reading…</div>';
    let lines;
    try {
      ({ lines } = await apiUpload('/api/ocr/options', blob));
    } catch (e) {
      box.innerHTML = '';
      box.append(el('div', 'hint', 'read failed'));
      return;
    }
    box.innerHTML = '';
    const useful = lines.filter((l) => l.cost || l.heat);
    useful.forEach((l) => {
      box.append(el('div', 'hint',
        `read: "${l.text.slice(0, 70)}"` +
        (l.cost ? ` → cost ${l.cost}` : '') +
        (l.heat ? ` → Heat ${l.heat}${l.heat_max ? '/' + l.heat_max : ''}` : '')));
      if (l.heat) { $('#heat').value = l.heat; RUN.heat = l.heat; }
      if (l.heat_max) { $('#heatMax').value = l.heat_max; RUN.heat_max = l.heat_max; }
    });
    if (!useful.length) box.append(el('div', 'hint', 'no costs found in that image'));
    const costs = lines.map((l) => l.cost).filter(Boolean);
    OPTIONS.forEach((o, i) => { if (costs[i]) OPT_COSTS[o.id] = costs[i]; });
    renderOptions(); updatePosNote(); save();
  });
}

// ------------------------------------------------------------- wishpower
// The pool is browsed, not searched. 136 of the 286 Miracles share three names
// between them, so typing a name cannot get you to the right row — you have to
// recognise the effect, which means seeing the list.

let POOL = [];
let MIRACLES = [];          // the hand currently being weighed
let RARITY = 'all';

// The table's rarity codes, in the words the game puts on screen.
const MIRACLE_RARITY = { Common: 'Ordinary', Rare: 'Rare', Epic: 'Extraordinary', Core: 'Core' };
const RARITY_CLASS = { Common: 'common', Rare: 'rare', Epic: 'legendary', Core: 'legendary' };

function renderRarityFilter() {
  const box = $('#rarityFilter');
  box.innerHTML = '';
  const counts = { all: POOL.length };
  POOL.forEach((m) => { counts[m.rarity] = (counts[m.rarity] || 0) + 1; });
  [['all', 'All'], ['Common', 'Ordinary'], ['Rare', 'Rare'],
   ['Epic', 'Extraordinary'], ['Core', 'Core']].forEach(([key, label]) => {
    if (key !== 'all' && !counts[key]) return;
    const b = el('button', 'filterchip' + (RARITY === key ? ' on' : ''),
      `${label} ${counts[key] || 0}`);
    b.onclick = () => { RARITY = key; renderRarityFilter(); renderPool(); };
    box.append(b);
  });
}

function renderPool() {
  const box = $('#miraclePool');
  const q = $('#miracleFilter').value.trim().toLowerCase();
  box.innerHTML = '';
  const rows = POOL.filter((m) =>
    (RARITY === 'all' || m.rarity === RARITY) &&
    (!q || m.effect.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)));

  if (!rows.length) {
    box.append(emptyState(POOL.length ? 'Nothing in the pool matches that.'
                                      : 'Pick your Mask on the Setup tab to load its pool.'));
    return;
  }
  rows.slice(0, 120).forEach((m) => {
    const row = el('div', 'poolrow' + (MIRACLES.some((x) => x.id === m.id) ? ' picked' : ''));
    row.append(el('span', 'tag ' + (RARITY_CLASS[m.rarity] || 'common'),
      MIRACLE_RARITY[m.rarity] || m.rarity));
    row.append(el('span', 'eff', m.effect));
    if (m.universal) row.append(el('span', 'tag', 'any mask'));
    row.onclick = () => {
      const i = MIRACLES.findIndex((x) => x.id === m.id);
      if (i >= 0) MIRACLES.splice(i, 1);
      else if (MIRACLES.length < 5) MIRACLES.push(m);
      renderPool(); renderMiracleOffer();
    };
    box.append(row);
  });
  if (rows.length > 120) {
    box.append(el('div', 'hint', `${rows.length - 120} more. Narrow the filter to see them.`));
  }
}

function renderMiracleOffer() {
  const box = $('#miracleOffer');
  box.innerHTML = '';
  MIRACLES.forEach((m, i) => {
    const chip = el('div', 'chip');
    chip.append(el('span', 'tag ' + (RARITY_CLASS[m.rarity] || 'common'),
      MIRACLE_RARITY[m.rarity] || m.rarity));
    chip.append(el('span', null, m.effect.slice(0, 52) + (m.effect.length > 52 ? '…' : '')));
    const x = el('button', null, '×');
    x.onclick = () => { MIRACLES.splice(i, 1); renderPool(); renderMiracleOffer(); };
    chip.append(x);
    box.append(chip);
  });
  $('#rankMiracles').disabled = MIRACLES.length < 1;
}

/** The Mask decides the pool, so changing it invalidates what is on screen. */
function maskChanged() {
  POOL = [];
  MIRACLES = [];
  $('#miracleRanking').innerHTML = '';
  if ($('#tab-wish').classList.contains('active')) loadPool();
}

async function loadPool() {
  try {
    const data = await api('/api/miracles/pool', RUN);
    POOL = data.miracles;
    $('#poolNote').textContent = data.mask
      ? `${data.count} Miracles the game can offer ${data.mask.name}`
      : `${data.count} Miracles across every Mask. Set your Mask on Setup to narrow this`;
  } catch (e) {
    POOL = [];
    $('#poolNote').textContent = 'pool unavailable: ' + e.message;
  }
  renderRarityFilter(); renderPool(); renderMiracleOffer();
}

function renderMiracleRanking(data) {
  const box = $('#miracleRanking');
  box.innerHTML = '';
  const r = data.run;
  box.append(el('div', 'hint',
    `Domain ${r.domain_index}/${r.domain_total} · ${r.domains_left} left · ` +
    `Wishpower Lv ${r.wishpower_level}` + (r.endgame ? ' · endgame' : '')));
  data.warnings.forEach((w) => box.append(warnBox(w)));

  const all = data.results.map((x) => x.score).concat(
    data.reshuffle.available ? [data.reshuffle.score] : []);
  const max = Math.max(...all, 1);

  data.results.forEach((x, i) => {
    const card = el('div', 'pick' + (x.recommended ? ' top' : ''));
    const h = el('h3');
    h.append(el('span', 'rank', `${i + 1}`));
    h.append(el('span', 'tag ' + (RARITY_CLASS[x.rarity] || 'common'),
      MIRACLE_RARITY[x.rarity] || x.rarity));
    h.append(el('span', null, x.name));
    h.append(el('span', 'score', x.score.toFixed(0)));
    card.append(h);

    const bar = el('div', 'bar');
    const fill = el('i');
    fill.style.width = '0%';
    bar.append(fill);
    card.append(bar);
    requestAnimationFrame(() => { fill.style.width = Math.max(0, (x.score / max) * 100) + '%'; });

    card.append(el('div', 'desc', x.effect));
    // When rerolling wins, the best of the hand still needs naming — you may not
    // want to spend a reset, and then this is the one to take.
    if (i === 0 && data.reshuffle.recommended) {
      card.append(el('div', 'desc runnerup',
        'Best of this hand. Take it if you would rather keep your reshuffles.'));
    }
    const fbox = el('div', 'factors');
    x.factors.forEach((f) => fbox.append(factorRow(f)));
    card.append(fbox);

    // Taking it is only half the decision — a third of the pool then makes you
    // designate a card, and that is where a good Miracle gets thrown away.
    if (x.targeting) {
      const t = el('div', 'needstarget');
      t.append(svg('i-warn'));
      t.append(el('span', null,
        `Then you designate ${x.targeting.count} Domain, and ${x.targeting.consequence}.` +
        (x.targeting.restricted_to.length
          ? ` Only ${x.targeting.restricted_to.join(', ')} Domains are eligible.` : '')));
      card.append(t);
    }

    const actions = el('div', 'actions');
    actions.append(takeButton({ id: x.id, kind: 'miracle', name: x.name, rarity: x.rarity,
                                desc: x.effect }, () => {
      MIRACLES = [];
      renderPool(); renderMiracleOffer();
      if (x.targeting) targetFor(x);
    }, x.recommended));
    if (x.targeting) {
      const which = el('button', 'ghost');
      which.append(el('span', null, 'Which Domain?'));
      which.onclick = () => targetFor(x);
      actions.append(which);
    }
    card.append(actions);
    box.append(card);
  });

  // Reshuffling competes with the hand rather than sitting outside it — the same
  // rule the spend advice follows for "take nothing".
  const rs = data.reshuffle;
  const card = el('div', 'pick reshuffle' + (rs.recommended ? ' top' : '') +
    (rs.available ? '' : ' blocked'));
  const h = el('h3');
  h.append(svg('i-shuffle', 'rsicon'));
  h.append(el('span', null, rs.target));
  if (rs.available) h.append(el('span', 'score', (rs.score >= 0 ? '+' : '') + rs.score.toFixed(0)));
  card.append(h);
  const ul = el('ul');
  rs.reasons.forEach((x) => ul.append(el('li', null, x)));
  card.append(ul);
  if (rs.available) {
    const btn = el('button', 'ghost');
    btn.append(svg('i-shuffle'));
    btn.append(el('span', null, 'I reshuffled, count one down'));
    btn.onclick = () => {
      RUN.miracle_resets = Math.max(0, RUN.miracle_resets - 1);
      $('#miracleResets').value = RUN.miracle_resets;
      MIRACLES = [];
      box.innerHTML = '';
      renderPool(); renderMiracleOffer(); save();
      $('#status').textContent = `reshuffled, ${RUN.miracle_resets} reset(s) left`;
    };
    const actions = el('div', 'actions');
    actions.append(btn);
    card.append(actions);
  }
  box.append(card);
}

// ------------------------------------------------- designating a Domain
// The follow-up screen ("Select Waypoint Pass"): the Miracle is taken, and now
// you pick which card it lands on. Answered by type with no data entry, or
// exactly if you type your draw pile in.

let PILE = [];
let INTENT = 'sacrifice';
let RESTRICT = [];
let TARGET_MIRACLE = null;

function renderIntent() {
  const box = $('#intentPick');
  box.innerHTML = '';
  [['sacrifice', 'Losing a Domain'], ['invest', 'Improving a Domain']].forEach(([key, label]) => {
    const b = el('button', 'filterchip' + (INTENT === key ? ' on' : ''), label);
    b.onclick = () => {
      INTENT = key; TARGET_MIRACLE = null; RESTRICT = [];
      renderIntent(); askTargets();
    };
    box.append(b);
  });
}

function renderPile() {
  const box = $('#pileList');
  box.innerHTML = '';
  PILE.forEach((c, i) => {
    const chip = el('div', 'chip');
    chip.append(el('span', null,
      `${c.name}${c.level ? ' Lv' + c.level : ''}${c.beacon_count ? ' ·' + c.beacon_count + '⬦' : ''}`));
    const x = el('button', null, '×');
    x.onclick = () => { PILE.splice(i, 1); renderPile(); askTargets(); };
    chip.append(x);
    box.append(chip);
  });
  if (!PILE.length) {
    box.append(el('div', 'hint',
      'No pile entered, so this answers by Domain type. Add your actual cards for an exact answer.'));
  }
}

async function askTargets() {
  const box = $('#targetResult');
  let data;
  try {
    data = await api('/api/deck/targets', {
      run: RUN, cards: PILE, intent: INTENT,
      restricted_to: RESTRICT, miracle_id: TARGET_MIRACLE,
    });
  } catch (e) {
    box.innerHTML = '';
    box.append(el('div', 'hint', 'could not rank: ' + e.message));
    return;
  }

  box.innerHTML = '';
  data.warnings.forEach((w) => box.append(warnBox(w)));
  const heading = data.intent === 'sacrifice'
    ? 'Give up the top one. It is the least valuable to this run.'
    : 'Put it on the top one. It is the most valuable to this run.';
  box.append(el('div', 'hint', heading + (data.by_type ? ' (by Domain type)' : '')));

  data.cards.forEach((c, i) => {
    const row = el('div', 'targetrow' + (i === 0 ? ' pick' : ''));
    row.append(el('span', 'rank', String(i + 1)));
    row.append(el('span', 'nm', c.name + (c.level ? ` Lv${c.level}` : '')));
    // Without this, two "Combat Lv2" rows are indistinguishable — and the one
    // carrying three beacons is the one you must not throw away.
    row.append(el('span', 'bc' + (c.beacon_count ? '' : ' none'),
      c.beacon_count ? `⬦ ${c.beacon_count}` : 'none'));
    row.append(el('span', 'val', c.score.toFixed(2)));
    row.append(el('span', 'note', (c.reasons[0] || '')));
    box.append(row);
  });
  if (data.tiebreak) box.append(el('div', 'hint', data.tiebreak));
}

function setupTargets() {
  renderIntent();
  DOMAIN_TYPES.forEach((t) => {
    const o = document.createElement('option');
    o.value = t.name; o.textContent = t.name;
    $('#pileType').append(o);
  });
  $('#addPile').onclick = () => {
    PILE.push({
      name: $('#pileType').value,
      level: parseInt($('#pileLevel').value || '0', 10) || null,
      beacons: [],
      beacon_count: parseInt($('#pileBeacons').value || '0', 10),
    });
    renderPile(); askTargets();
  };
  $('#clearPile').onclick = () => { PILE = []; renderPile(); askTargets(); };
  renderPile();
  askTargets();
}

/** Point the designation helper at a specific Miracle's follow-up choice. */
function targetFor(miracle) {
  TARGET_MIRACLE = miracle.id;
  INTENT = miracle.targeting.intent;
  RESTRICT = miracle.targeting.restricted_to || [];
  $('#targetNote').textContent =
    `for "${miracle.effect.slice(0, 64)}${miracle.effect.length > 64 ? '…' : ''}"` +
    (RESTRICT.length ? ` · only ${RESTRICT.join(', ')}` : '');
  renderIntent();
  askTargets();
  $('#targetResult').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function setupWishpower() {
  $('#miracleFilter').addEventListener('input', renderPool);
  $('#clearMiracles').onclick = () => {
    MIRACLES = [];
    $('#miracleFilter').value = '';
    $('#miracleRanking').innerHTML = '';
    renderPool(); renderMiracleOffer();
  };
  $('#rankMiracles').onclick = async () => {
    const data = await api('/api/miracles/rank', {
      run: RUN, miracle_ids: MIRACLES.map((m) => m.id),
      resets_remaining: RUN.miracle_resets,
    });
    renderMiracleRanking(data);
  };
}

// ------------------------------------------------------------------- OCR
function setupDrop(sel, handler) {
  const zone = $(sel);
  if (!zone) return;
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('hot'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('hot'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault(); zone.classList.remove('hot');
    const f = [...e.dataTransfer.files].find((f) => f.type.startsWith('image/'));
    if (f) handler(f);
  });
  zone.onclick = () => {
    const inp = document.createElement('input');
    inp.type = 'file'; inp.accept = 'image/*';
    inp.onchange = () => inp.files[0] && handler(inp.files[0]);
    inp.click();
  };
  zone._handler = handler;
}

function renderOcrCards(cards) {
  const box = $('#ocrCards');
  box.innerHTML = '';
  if (!cards.length) {
    box.append(emptyState('No text found. Try a tighter crop of the choice screen.'));
    return;
  }
  cards.forEach((c) => {
    const card = el('div', 'ocrcard' + (c.ambiguous ? ' needs' : ''));
    const seen = el('div', 'seen');
    seen.append(document.createTextNode('read: '));
    seen.append(el('code', null, c.observed.text || '(no name line)'));
    if (c.observed.path) seen.append(document.createTextNode(' · ' + c.observed.path));
    card.append(seen);
    if (c.ambiguous) card.append(el('div', 'hint', c.note || 'Several match. Pick the right one.'));

    c.candidates.forEach((cand, i) => {
      const opt = el('div', 'ocropt');
      if (cand.path) opt.append(pathIcon(cand.path));
      opt.append(el('span', null, cand.name));
      const rt = rarityTag(cand.rarity);
      if (rt) opt.append(rt);
      opt.append(el('span', 'conf', cand.score.toFixed(0)));
      if (i === 0 && !c.ambiguous) opt.style.fontWeight = '600';
      opt.onclick = () => {
        if (!OFFER.some((o) => o.id === cand.id)) OFFER.push(cand);
        CACHE.set(cand.kind + ':' + cand.id, cand);
        card.remove();
        renderOffer();
      };
      card.append(opt);
    });
    box.append(card);
  });
}

async function sendImage(blob) {
  $('#ocrCards').innerHTML = '<div class="hint">reading…</div>';
  try {
    const path = '/api/ocr?kind=' + encodeURIComponent($('#offerKind').value);
    renderOcrCards((await apiUpload(path, blob)).cards);
  } catch (e) {
    $('#ocrCards').innerHTML = '';
    $('#ocrCards').append(el('div', 'hint', 'OCR failed: ' + e.message));
  }
}

// ------------------------------------------------------------- inventory
function setupInventory() {
  setupDrop('#invDrop', async (blob) => {
    const box = $('#invResult');
    box.innerHTML = '<div class="hint">reading…</div>';
    let scan;
    try {
      scan = await apiUpload('/api/ocr/inventory', blob, 'inv.png');
    } catch (e) {
      box.innerHTML = '';
      box.append(el('div', 'hint', 'read failed'));
      return;
    }

    const rec = await api('/api/inventory/reconcile', {
      run: RUN, scanned: scan.found, complete: $('#invComplete').checked,
    });

    box.innerHTML = '';
    box.append(el('div', 'hint', `${scan.boxes_read} text boxes read`));
    rec.notes.forEach((n) => box.append(warnBox(n)));

    const list = el('div', 'diff');
    rec.diffs.forEach((d) => {
      d.added.forEach((it) => {
        const row = el('div', 'diffrow');
        row.append(el('span', 'badge add', 'add'));
        if (it.path) row.append(pathIcon(it.path));
        row.append(el('span', null, `${it.name}`));
        row.append(el('span', 'tag', d.kind));
        list.append(row);
      });
      d.removed.forEach((it) => {
        const row = el('div', 'diffrow');
        row.append(el('span', 'badge rem', 'remove'));
        if (it.path) row.append(pathIcon(it.path));
        row.append(el('span', null, `${it.name}`));
        list.append(row);
      });
    });
    if (list.children.length) box.append(list);

    if (scan.ambiguous?.length) {
      const amb = el('div', 'ambig');
      amb.append(el('h4', null, `${scan.ambiguous.length} unclear. Pick the right one`));
      scan.ambiguous.slice(0, 8).forEach((a) => {
        amb.append(el('div', 'seen', `read: "${a.observed}"`));
        a.candidates.forEach((c) => {
          const opt = el('div', 'ocropt');
          if (c.path) opt.append(pathIcon(c.path));
          opt.append(el('span', null, c.name));
          opt.append(el('span', 'conf', c.score.toFixed(0)));
          opt.onclick = () => {
            const key = ownedKey(c.kind);
            if (key && !RUN[key].includes(c.id)) RUN[key].push(c.id);
            CACHE.set(c.kind + ':' + c.id, c);
            opt.parentElement.remove();
            renderOwned(); refreshRun(); save();
          };
          amb.append(opt);
        });
      });
      box.append(amb);
    }

    if (rec.requires_confirmation) {
      const btn = el('button', 'primary', 'Apply these changes');
      btn.onclick = () => {
        rec.diffs.forEach((d) => {
          const key = ownedKey(d.kind);
          if (!key) return;
          d.added.forEach((it) => { if (!RUN[key].includes(it.id)) RUN[key].push(it.id); });
          if (rec.complete) {
            const drop = new Set(d.removed.map((it) => it.id));
            RUN[key] = RUN[key].filter((i) => !drop.has(i));
          }
        });
        box.innerHTML = '';
        box.append(el('div', 'hint', 'applied'));
        renderOwned(); refreshRun(); save();
      };
      box.append(btn);
    }
  });
}

// ---------------------------------------------------------------- run tab
async function refreshRun() {
  const data = await api('/api/equations', RUN);

  const t = el('table');
  const head = el('tr');
  ['Path', 'Held', 'Building toward', 'Needs'].forEach((h) => head.append(el('th', null, h)));
  t.append(head);
  data.paths.forEach((p) => {
    const tr = el('tr', p.count >= 3 ? 'committed' : '');
    const c1 = el('td');
    const cell = el('div', 'pathcell');
    cell.append(pathIcon(p.path));
    cell.append(el('span', null, p.path));
    c1.append(cell);
    tr.append(c1);
    const c2 = el('td', 'n');
    c2.append(el('span', 'countpill' + (p.count >= 3 ? ' hot' : ''), String(p.count)));
    tr.append(c2);
    tr.append(el('td', null, p.next_target ? p.next_target.name : 'none'));
    tr.append(el('td', 'n', p.next_target ? String(p.next_target.distance) : 'n/a'));
    t.append(tr);
  });
  $('#pathTable').innerHTML = '';
  $('#pathTable').append(t);

  $('#unreachableNote').textContent = data.unreachable_paths.length
    ? `This theme offers no ${data.unreachable_paths.join(' or ')} blessings, so equations needing them cannot be built. ~${data.picks_remaining} blessing picks left.`
    : `~${data.picks_remaining} blessing picks left.`;

  const renderEq = (sel, list, empty) => {
    const box = $(sel);
    box.innerHTML = '';
    if (!list.length) { box.append(emptyState(empty)); return; }
    list.forEach((s) => {
      const d = el('div', 'eq' + (s.is_boundary ? ' boundary' : ''));
      const head = el('div', 'head');
      s.requires.forEach((r) => head.append(pathIcon(r.path)));
      head.append(el('b', null, s.name));
      head.append(el('span', 'tag ' + (s.is_boundary ? 'legendary' : s.rarity.toLowerCase()),
        s.rarity_label || s.rarity));
      d.append(head);
      d.append(el('div', 'req',
        s.requires.map((r) => `${r.path} ${r.count}`).join(' + ') +
        (s.active ? ', active' : `, ${s.distance} away`)));
      if (!s.active) {
        const total = s.requires.reduce((a, r) => a + r.count, 0);
        const bar = el('div', 'eqbar');
        const fill = el('i');
        fill.style.width = Math.max(0, ((total - s.distance) / total) * 100) + '%';
        bar.append(fill);
        d.append(bar);
      }
      box.append(d);
    });
  };
  renderEq('#activeEq', data.active, 'None active yet.');
  renderEq('#reachEq', data.reachable, 'Nothing in reach with the picks remaining.');
}

// ----------------------------------------------------------------- owned
// The list used to be flat chips built from a client-side cache, which meant an
// item added by OCR or restored from disk showed as "blessing 617042". The
// server now resolves and groups everything, so this only draws it.

function dropOwned(kind, id) {
  const key = ownedKey(kind);
  if (!key) return;
  RUN[key] = RUN[key].filter((i) => i !== id);
  // A socket cannot hold something you no longer have. live_weighted() filters
  // this server-side too, but leaving the stale id here would resurrect it the
  // moment the same curio came back.
  if (kind === 'weighted_curio') {
    RUN.equipped_weighted = RUN.equipped_weighted.filter((i) => i !== id);
  }
  renderOwned(); refreshRun(); save();
}

// ----------------------------------------------------------- run rating
/** How the run is going: a score out of 100 against a reference run at the
 *  same Domain. The pair is the point — the absolute number on Domain 3 is
 *  meaninglessly low on its own, so the reference is drawn on the same bar
 *  rather than printed somewhere beside it. */
function renderRating(r) {
  const card = $('#ratingCard'), box = $('#ratingBody');
  card.hidden = !r;
  if (!r) return;
  box.innerHTML = '';

  const head = el('div', 'rathead ' + r.band);
  const score = el('div', 'ratscore');
  score.append(el('span', 'n', String(r.strength)));
  score.append(el('span', 'd', '/ 100'));
  head.append(score);

  const words = el('div', 'ratwords');
  words.append(el('div', 'verdict', r.label));
  words.append(el('div', 'sub',
    `Domain ${r.domain_index} of ${r.domain_total} · a reference run here scores ` +
    `${r.reference}`));
  head.append(words);
  box.append(head);

  // One bar, two marks: the fill is you, the notch is the reference.
  const bar = el('div', 'ratbar');
  const fill = el('i', 'fill ' + r.band);
  fill.style.width = Math.min(100, r.strength) + '%';
  bar.append(fill);
  const mark = el('i', 'ref');
  mark.style.left = Math.min(100, r.reference) + '%';
  mark.title = `reference run at this Domain: ${r.reference}`;
  bar.append(mark);
  box.append(bar);

  const reading = el('div', 'ratreading');
  r.reading.forEach((line) => reading.append(el('div', 'line', line)));
  box.append(reading);

  // The working, so a verdict you disagree with can be argued with.
  const tbl = el('div', 'ratfactors');
  r.factors.forEach((f) => {
    const row = el('div', 'ratrow' + (f.gap < -1.5 ? ' weak' : f.gap > 1.5 ? ' strong' : ''));
    row.append(el('span', 'fname', f.name));
    const track = el('span', 'ftrack');
    const got = el('i', 'got');
    got.style.width = Math.max(0, (f.points / f.weight) * 100) + '%';
    track.append(got);
    if (f.key !== 'drag') {
      const rp = el('i', 'refmark');
      rp.style.left = Math.min(100, (f.ref_points / f.weight) * 100) + '%';
      track.append(rp);
    }
    row.append(track);
    row.append(el('span', 'fpts', `${f.points.toFixed(0)}`));
    row.append(el('span', 'fref', f.key === 'drag' ? '' : `vs ${f.ref_points.toFixed(0)}`));
    row.append(el('span', 'fnote', f.note));
    tbl.append(row);
  });
  box.append(tbl);

  box.append(el('p', 'hint ratcaveat', r.caveat));
}

// The lists the owned view is a picture of. Reset empties exactly these and
// nothing else: Mask, team and difficulty are chosen once and re-picking them is
// a separate job, so wiping them here would cost more than it saved.
const OWNED_KEYS = ['owned_blessings', 'owned_curios', 'owned_weighted',
  'equipped_weighted', 'owned_equations', 'owned_miracles', 'enhanced_blessings'];

let OWN_UNDO = null;

/** Empty every owned list. Destructive and unprompted-by-the-game, so it is
 *  behind a confirm and keeps one snapshot — a mis-click at Domain 15 would
 *  otherwise throw away the whole run's tracking. */
function resetOwned() {
  OWN_UNDO = {};
  OWNED_KEYS.forEach((k) => { OWN_UNDO[k] = RUN[k] || []; RUN[k] = []; });
  const n = Object.values(OWN_UNDO).reduce((a, l) => a + l.length, 0);
  showOwnReset('idle');
  $('#ownResetUndo').hidden = false;
  renderOwned(); refreshRun();
  save().then(() => {
    $('#status').textContent = `cleared ${n} tracked item${n === 1 ? '' : 's'}. Undo sits on the card`;
  });
}

function undoResetOwned() {
  if (!OWN_UNDO) return;
  OWNED_KEYS.forEach((k) => { RUN[k] = OWN_UNDO[k]; });
  OWN_UNDO = null;
  $('#ownResetUndo').hidden = true;
  renderOwned(); refreshRun(); save();
}

/** 'idle' shows the Reset button, 'confirm' shows the yes/cancel pair. */
function showOwnReset(mode) {
  $('#ownReset').hidden = mode === 'confirm';
  $('#ownResetConfirm').hidden = mode !== 'confirm';
}

function setupOwnReset() {
  $('#ownReset').onclick = () => showOwnReset('confirm');
  $('#ownResetNo').onclick = () => showOwnReset('idle');
  $('#ownResetYes').onclick = resetOwned;
  $('#ownResetUndo').onclick = undoResetOwned;
  $('#ownHistory').onclick = () => {
    const panel = $('#historyPanel');
    panel.hidden = !panel.hidden;
    if (!panel.hidden) renderHistory();
  };
  $('#ownExport').onclick = exportRun;
  $('#ownImport').onclick = () => $('#ownImportFile').click();
  $('#ownImportFile').onchange = (ev) => {
    const file = ev.target.files?.[0];
    ev.target.value = '';                 // so re-picking the same file fires
    if (file) importRun(file);
  };
}

// ------------------------------------------------------- export / import
// On the hosted build a run lives in one browser's IndexedDB and nowhere else:
// clearing site data loses it, and there is no other copy. This is the way a
// run moves between the local app and the hosted one, or off a machine before
// it gets wiped.

function exportRun() {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
  const blob = new Blob([JSON.stringify(RUN, null, 1)], { type: 'application/json' });
  const a = el('a');
  a.href = URL.createObjectURL(blob);
  a.download = `du-run-${stamp}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  setStatus(`Exported this run as ${a.download}`);
}

async function importRun(file) {
  let incoming;
  try {
    incoming = JSON.parse(await file.text());
  } catch (e) {
    setStatus('That file is not valid JSON.');
    return;
  }
  // Loose on purpose — RunState.from_dict ignores keys it does not know, so the
  // useful check is "does this look like a run at all", not a field-by-field
  // schema. Refusing a slightly-old export would be worse than accepting it.
  if (!incoming || typeof incoming !== 'object' || Array.isArray(incoming)
      || !('owned_blessings' in incoming || 'domain_total' in incoming)) {
    setStatus('That does not look like a DU run export.');
    return;
  }

  // Saving snapshots whatever it replaces, and a save that loses something
  // forces a snapshot regardless of coalescing — so an import that turns out to
  // be the wrong file is recoverable from Earlier saves.
  RUN = Object.assign(RUN, incoming);
  await save({ force_snapshot: true });
  await refreshRun();
  await renderOwned();
  setStatus('Run imported. The previous one is under Earlier saves.');
}

// --------------------------------------------------------- earlier saves
/** Snapshots kept on disk, one per destructive or non-trivial save. The Undo
 *  button beside Reset only lives as long as the tab does; this is the version
 *  of recovery that survives closing the browser. */
async function renderHistory() {
  const box = $('#historyList');
  box.innerHTML = '';
  box.append(el('div', 'hint', 'reading…'));

  let snapshots;
  try {
    ({ snapshots } = await api('/api/run/history'));
  } catch (e) {
    box.innerHTML = '';
    box.append(el('div', 'hint', 'could not read the history: ' + e.message));
    return;
  }

  box.innerHTML = '';
  if (!snapshots.length) {
    box.append(emptyState('No earlier saves yet. One is kept each time a save '
      + 'replaces something different.'));
    return;
  }

  const now = new Date();
  snapshots.forEach((s) => {
    const when = new Date(s.saved_at);
    const row = el('div', 'histrow');

    const time = el('div', 'when');
    time.append(el('b', null, when.toLocaleTimeString()));
    const days = Math.floor((now - when) / 86400000);
    time.append(el('span', 'ago', days >= 1
      ? `${days} day${days === 1 ? '' : 's'} ago` : when.toLocaleDateString()));
    row.append(time);

    const what = el('div', 'what');
    const c = s.counts;
    what.append(el('div', 'line',
      `${c.owned_blessings} blessings · ${c.owned_equations} equations · ` +
      `${c.owned_curios + c.owned_weighted} curios · ${c.owned_miracles} miracles`));
    what.append(el('div', 'sub',
      `Domain ${s.domain_index} of ${s.domain_total} · ${c.team} in team`));
    row.append(what);

    const btn = el('button', 'ghost');
    btn.textContent = 'Restore';
    btn.title = 'Make this the current run. This keeps what it replaces too.';
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = 'restoring…';
      try {
        RUN = Object.assign(RUN, await api('/api/run/restore', { file: s.file }));
      } catch (e) {
        btn.disabled = false;
        btn.textContent = 'Restore';
        $('#status').textContent = 'restore failed: ' + e.message;
        return;
      }
      // The restore replaced the whole run, so every field on screen is stale.
      syncInputs();
      OWN_UNDO = null;
      $('#ownResetUndo').hidden = true;
      renderMasks(); renderTeam(); renderOwned(); refreshRun();
      updatePosNote(); updateDifficultyNote(); renderHistory();
      // The Miracle pool is per-Mask, and a restore can change the Mask.
      maskChanged();
      $('#status').textContent =
        `restored the save from ${when.toLocaleTimeString()}`;
    };
    row.append(btn);
    box.append(row);
  });
}

/** Push RUN back out to every input that mirrors it. Needed after a restore,
 *  which replaces the whole object rather than one field. */
function syncInputs() {
  const fields = {
    plane: 'plane', difficulty: 'difficulty', domainIndex: 'domain_index',
    domainTotal: 'domain_total', fragments: 'fragments', heat: 'heat',
    heatMax: 'heat_max', miracleResets: 'miracle_resets', doorRedraws: 'door_redraws',
  };
  Object.entries(fields).forEach(([id, key]) => {
    const inp = $('#' + id);
    if (!inp) return;
    inp.value = inp.classList.contains('num-wide') ? fmtNum(RUN[key]) : RUN[key];
  });
  setWishpower(RUN.wishpower_level);
  $('#runLength').value = RUN.domain_total;
  [['heatCommon', 'Common'], ['heatRare', 'Rare'], ['heatLegendary', 'Legendary']]
    .forEach(([id, rarity]) => {
      const inp = $('#' + id);
      if (inp && RUN.heat_costs?.[rarity] != null) inp.value = RUN.heat_costs[rarity];
    });
  if ($('#storeKind')) applyShelfKind();
}

// ------------------------------------------------- the Weighted Curio pool
// 17 in the theme, and the equip screen labels its tiles with icons rather than
// names — so this is browsed and ticked, the same call as the Wishpower pool.
let WEIGHTED = [];

/** Show the tick-list only for the kind it belongs to. */
function showWeightedPool() {
  const on = $('#ownKind').value === 'weighted_curio';
  $('#weightedPool').hidden = !on;
  $('#ownSearch').hidden = on;
  $('#ownResults').hidden = on;
  if (on) renderWeightedPool();
}

async function loadWeighted() {
  if (WEIGHTED.length) return;
  const { weighted } = await api('/api/weighted');
  WEIGHTED = weighted;
  weighted.forEach((w) => CACHE.set('weighted_curio:' + w.id, w));
}

// Ticking is faster than the round trip that re-ranks, so renders overlap. Two
// rules keep that honest: nothing is cleared until the awaiting is done (an
// empty-then-append across an await let four quick ticks stack four copies of
// the list, 68 rows for a 17-entry pool), and a render that has been overtaken
// throws its result away rather than painting a stale ranking over a newer one.
let WEIGHTED_RENDER = 0;

async function renderWeightedPool() {
  const token = ++WEIGHTED_RENDER;
  await loadWeighted();

  // Every render re-posts the live run, so the scores are against the team,
  // Domain and inventory as they stand right now rather than as they stood when
  // the tab was opened. The pool is scored whether or not anything is ticked —
  // "which of these is worth having" is the question you open the list with.
  let plan = null;
  try {
    plan = await api('/api/weighted/rank', RUN);
  } catch (e) {
    // A ranking that could not be fetched must say so: an unsorted list with no
    // numbers is indistinguishable from a pool where nothing scores.
    if (token === WEIGHTED_RENDER) $('#weightedStamp').textContent = 'could not score: ' + e.message;
  }
  if (token !== WEIGHTED_RENDER) return;

  $('#weightedSlots').value = RUN.weighted_slots;
  const box = $('#weightedList');
  const rows = plan?.pool?.length ? plan.pool : WEIGHTED;
  const rec = new Set(plan?.best_available || []);
  if (plan) {
    $('#weightedStamp').textContent =
      `scored against the run as of ${new Date().toLocaleTimeString()}`;
  }
  box.innerHTML = '';

  // The tick is the socket. Only socketing does anything in the run, and by late
  // run you own most of the theme — so tracking what you hold separately was two
  // lists to maintain for one decision.
  // Read the sockets off the payload, not off `equipped_weighted`: an empty
  // equipped list means "the first N held" (`live_weighted()`), which a restored
  // snapshot or an OCR scan can still produce. Reading the raw field there would
  // show zero ticks under a panel reading "Sockets: A + B".
  const socketedIds = new Set(plan?.equipped || RUN.equipped_weighted);

  rows.forEach((w) => {
    const socketed = socketedIds.has(w.id);
    const scored = w.score != null;
    const row = el('label', 'poolrow' + (socketed ? ' held' : '')
      + (rec.has(w.id) ? ' rec' : ''));
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = socketed;
    cb.onchange = () => toggleWeighted(w.id, cb.checked);
    row.append(cb);

    const body = el('div', 'body');
    const head = el('div', 'line');
    head.append(el('span', 'nm', w.name));
    (w.gate_paths || []).forEach((p) => head.append(pathIcon(p)));
    (w.gate_elements || []).forEach((e) => head.append(elementIcon(e)));
    if (socketed) {
      head.append(el('span', 'tag legendary', 'in a socket'));
    }
    if (scored && w.gate_fit === 0) {
      head.append(el('span', 'tag negative', 'team cannot trigger'));
    } else if (rec.has(w.id)) {
      // A highlight means "there is an empty socket and this is the best thing
      // for it" — the engine sizes `best_available` by the free sockets, so a
      // full set highlights nothing. The label has to say which claim it is
      // making: "best for this team" is only true when nothing is socketed,
      // since the list is sorted by that very score and with something ticked
      // the best for the team is the row above, already in. A suggestion either
      // way — the catalog cannot know which of these the run has been given.
      const tag = el('span', 'tag',
        socketedIds.size ? 'best for a free socket' : 'best for this team');
      tag.title = socketedIds.size
        ? 'Highest scoring one not already socketed, and you have a socket free for it'
        : 'Highest scoring in the theme for your team';
      head.append(tag);
    }
    if (scored) head.append(el('span', 'val', w.score.toFixed(2)));
    body.append(head);
    body.append(el('div', 'sub', (w.desc || '').slice(0, 150)));
    // Why it scores what it scores — the gate line is the whole verdict for the
    // ones reading 0.00, and without it the sort order looks arbitrary.
    if (w.reasons?.length) body.append(el('div', 'sub why', w.reasons.join(' · ')));
    row.append(body);
    box.append(row);
  });
}

/** Put a Weighted Curio in a socket, or take it out.
 *
 * Both lists move together. `live_weighted()` intersects the equipped list with
 * the owned one, so writing only `equipped_weighted` would silently zero every
 * socket in the rating — the intersection is what stops a socket surviving the
 * curio being dropped, and it cannot tell the two mistakes apart.
 *
 * The sockets are the scarce thing, so filling the last one has to say what it
 * displaced rather than silently refusing the click.
 */
function toggleWeighted(id, on) {
  // An empty equipped list means "the first N held", so it is materialised
  // before the first edit — otherwise the first tick evicts sockets the panel
  // is naming, silently, and unticking one of them looks like it did nothing.
  if (!RUN.equipped_weighted.length && RUN.owned_weighted.length) {
    RUN.equipped_weighted = RUN.owned_weighted.slice(0, Math.max(0, RUN.weighted_slots));
  }
  RUN.owned_weighted = RUN.owned_weighted.filter((i) => i !== id);
  RUN.equipped_weighted = RUN.equipped_weighted.filter((i) => i !== id);
  if (on) {
    RUN.owned_weighted.push(id);
    RUN.equipped_weighted.push(id);
    while (RUN.equipped_weighted.length > Math.max(0, RUN.weighted_slots)) {
      const dropped = RUN.equipped_weighted.shift();
      RUN.owned_weighted = RUN.owned_weighted.filter((i) => i !== dropped);
      const name = (CACHE.get('weighted_curio:' + dropped) || {}).name;
      $('#status').textContent = `sockets full, so this took out ${name || 'the oldest'}`;
    }
  }
  renderWeightedPool(); renderOwned(); refreshRun(); save();
}

/** Mark a Blessing enhanced / not, which is what removes it from the Workbench. */
function setEnhanced(id, on) {
  const has = RUN.enhanced_blessings.includes(id);
  if (on && !has) RUN.enhanced_blessings.push(id);
  if (!on && has) RUN.enhanced_blessings = RUN.enhanced_blessings.filter((i) => i !== id);
  renderOwned(); save();
}

function ownedRow(item, kind) {
  const row = el('div', 'ownrow' + (item.unknown ? ' unknown' : '') +
    (item.enhanced ? ' enhanced' : ''));
  if (item.path) row.append(pathIcon(item.path));
  const body = el('div', 'body');
  const head = el('div', 'line');
  head.append(el('span', 'nm', item.name));
  const rt = rarityTag(item.rarity);
  if (rt) head.append(rt);
  if (item.is_negative) head.append(el('span', 'tag negative', 'negative'));
  if (item.dead) head.append(el('span', 'tag negative', 'team cannot trigger'));
  // A listed Weighted Curio is a socketed one, so there is no Equip button
  // here — the tick in the pool is the socket. An inventory scan can still
  // record one you hold without socketing it, and that has to look different
  // from the ones actually doing something.
  if (kind === 'weighted_curio' && !item.equipped) {
    head.append(el('span', 'tag', 'not socketed'));
  }
  // Enhancing is one-shot in this theme, so this is a state, not a counter.
  if (item.enhanceable) {
    const tog = el('button', 'enh' + (item.enhanced ? ' on' : ''),
      item.enhanced ? '✦ Enhanced' : 'Mark enhanced');
    tog.title = item.enhanced
      ? 'At max level, so the Workbench will not offer it again. Click to undo.'
      : 'Mark as already enhanced so the Workbench stops recommending it';
    tog.onclick = () => setEnhanced(item.id, !item.enhanced);
    head.append(tog);
  }
  body.append(head);
  if (item.desc) body.append(el('div', 'sub', item.desc));
  if (item.gate_note) body.append(el('div', 'sub', 'gate: ' + item.gate_note));
  row.append(body);
  const x = el('button', 'drop', '×');
  x.title = 'no longer held';
  x.onclick = () => dropOwned(kind, item.id);
  row.append(x);
  return row;
}

// Which folds are open, by key. Deliberately *not* in RUN: it is a fact about
// the screen, not about the run, and saving it would burn a history slot per
// click. It has to survive a re-render though — renderOwned() re-runs on every
// Equip, Mark enhanced and drop, so without this the block you are working in
// would fold up under the cursor between the click and the result.
const OWN_OPEN = new Set();

/** Make `head` fold `bodyEl`, remembering the state under `key`.
 *
 * Everything starts closed. What the tab owes you when you open it is the shape
 * of the run — counts, notes, the socket verdict — and forty blessing rows bury
 * that. Folding is one level deep everywhere: the Blessings header stays a plain
 * header because its Path blocks are the fold units, so no row is ever more than
 * one click away.
 */
function fold(head, bodyEl, key) {
  const open = OWN_OPEN.has(key);
  head.classList.add('foldhead');
  head.classList.toggle('open', open);
  head.prepend(el('span', 'caret', '▸'));
  head.tabIndex = 0;
  head.setAttribute('role', 'button');
  head.setAttribute('aria-expanded', String(open));
  bodyEl.hidden = !open;
  const toggle = () => {
    const now = !OWN_OPEN.has(key);
    if (now) OWN_OPEN.add(key); else OWN_OPEN.delete(key);
    head.classList.toggle('open', now);
    head.setAttribute('aria-expanded', String(now));
    bodyEl.hidden = !now;
  };
  head.onclick = toggle;
  head.onkeydown = (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); }
  };
}

/** A section of the inventory. `g.body` is the foldable part; anything appended
 * to `g` itself (the Weighted socket verdict) stays visible when it is closed —
 * a recommendation nobody can see is a recommendation nobody can overrule. */
function ownedGroup(title, count, subtitle, wide, foldKey) {
  const g = el('section', 'owngroup' + (wide ? ' wide' : ''));
  const h = el('h3');
  h.append(el('span', 'gname', title));
  h.append(el('span', 'gcount', String(count)));
  if (subtitle) h.append(el('span', 'gsub', subtitle));
  g.append(h);
  g.body = el('div', 'gbody');
  g.append(g.body);
  if (foldKey) fold(h, g.body, foldKey);
  return g;
}

async function renderOwned() {
  const body = $('#ownBody'), sum = $('#ownSummary'), notes = $('#ownNotes');
  let data;
  try {
    data = await api('/api/owned', RUN);
  } catch (e) {
    body.innerHTML = '';
    body.append(el('div', 'hint', 'could not read inventory: ' + e.message));
    return;
  }

  renderRating(data.rating);

  // Headline counts first — the question "what am I holding?" starts with how much.
  const t = data.totals;

  const held = OWNED_KEYS.reduce((a, k) => a + (RUN[k] || []).length, 0);
  // Any re-render means something else happened; a confirm left open from
  // before is stale, so it folds back rather than sitting armed.
  showOwnReset('idle');
  $('#ownReset').disabled = !held;
  $('#ownReset').title = held
    ? 'Empty every list on this tab: Blessings, Equations, Curios, Miracles and the enhanced marks'
    : 'Nothing tracked yet';
  // Anything picked up after a reset means the run has moved on, and restoring
  // the snapshot would silently delete it. Retire the offer instead.
  if (OWN_UNDO && held) { OWN_UNDO = null; $('#ownResetUndo').hidden = true; }
  sum.innerHTML = '';
  const tiles = [
    ['Blessings', t.blessings,
      `${t.paths_used} Path${t.paths_used === 1 ? '' : 's'} · ${t.enhanced} enhanced`],
    ['Equations', t.equations, `${t.equations_active} live`],
    ['Curios', t.curios, ''],
    // Socketed, not held: only a socketed one does anything, and a tile reading
    // 10 above a header reading "2/2 equipped" is the tab contradicting itself.
    ['Weighted', t.weighted, `of ${t.weighted_slots} socket${t.weighted_slots === 1 ? '' : 's'}`],
    ['Miracles', t.miracles, ''],
  ];
  tiles.forEach(([label, n, sub]) => {
    const tile = el('div', 'tile' + (n ? '' : ' zero'));
    tile.append(el('span', 'n', String(n)));
    tile.append(el('span', 'l', label));
    if (sub && n) tile.append(el('span', 's', sub));
    sum.append(tile);
  });

  notes.innerHTML = '';
  data.notes.forEach((n) => notes.append(warnBox(n)));

  body.innerHTML = '';
  const empty = !t.blessings && !t.curios && !t.weighted_held && !t.equations && !t.miracles;
  if (empty) {
    body.append(emptyState('Nothing tracked yet. Rank an offer on the Decide tab and press Take.'));
    return;
  }

  // Blessings, grouped by Path and ordered by how committed you are to each.
  if (data.paths.length) {
    const g = ownedGroup('Blessings by Path', t.blessings,
      `about ${data.picks_remaining} picks left in the run`, true);
    const grid = el('div', 'pathgrid');
    g.body.append(grid);
    data.paths.forEach((p) => {
      const block = el('div', 'pathblock' + (p.committed ? ' committed' : ''));
      const head = el('div', 'phead');
      head.append(pathIcon(p.path));
      head.append(el('b', null, p.path));
      head.append(el('span', 'countpill' + (p.committed ? ' hot' : ''), String(p.count)));
      head.append(el('span', 'gsub', p.note));
      block.append(head);
      // The head carries the count and the note, so a folded Path still says
      // everything the Paths table says — what is behind the click is which
      // blessings, which is the detail you go looking for rather than read.
      const rows = el('div', 'pathrows');
      p.entries.forEach((e) => rows.append(ownedRow(e, 'blessing')));
      block.append(rows);
      fold(head, rows, 'path:' + p.path);
      grid.append(block);
    });
    body.append(g);
  }

  if (data.equations.length) {
    const live = data.equations.filter((e) => e.active).length;
    const g = ownedGroup('Equations', data.equations.length,
      live ? `${live} active right now` : 'none active yet', false, 'equations');
    data.equations.forEach((e) => {
      const row = el('div', 'ownrow eqrow' + (e.active ? ' live' : ''));
      const body2 = el('div', 'body');
      const head = el('div', 'line');
      (e.requires || []).forEach((r) => head.append(pathIcon(r.path)));
      head.append(el('span', 'nm', e.name));
      head.append(el('span', 'tag ' + (e.active ? 'legendary' : 'common'),
        e.active ? 'active' : `${e.distance} away`));
      body2.append(head);
      if (e.requires) {
        body2.append(el('div', 'sub',
          e.requires.map((r) => `${r.path} ${r.count}`).join(' + ')));
      }
      if (e.desc) body2.append(el('div', 'sub', e.desc));
      row.append(body2);
      const x = el('button', 'drop', '×');
      x.onclick = () => dropOwned('equation', e.id);
      row.append(x);
      g.body.append(row);
    });
    body.append(g);
  }

  if (data.curios.length) {
    const neg = data.curios.filter((c) => c.is_negative).length;
    const g = ownedGroup('Curios', data.curios.length,
      neg ? `${neg} negative` : '', false, 'curios');
    data.curios.forEach((c) => g.body.append(ownedRow(c, 'curio')));
    body.append(g);
  }

  // Renders on the verdict, not only on the contents: with nothing socketed
  // there are no rows, and keying the section off them left "you have 2 empty
  // sockets" with nowhere to appear — the tab showed a grey 0 tile and said
  // nothing about two slots doing nothing all run. Empty sockets are exactly
  // the state worth telling someone about.
  if (data.weighted.length || data.weighted_plan?.notes?.length) {
    const plan = data.weighted_plan || {};
    const live = data.weighted.filter((w) => w.equipped).length;
    const dead = data.weighted.filter((w) => w.dead && w.equipped).length;
    // The count is the sockets filled. It used to be everything recorded, which
    // is what put a 10 next to "2/2 equipped".
    const g = ownedGroup('Weighted curios', live,
      `in ${plan.slots ?? RUN.weighted_slots} socket(s)`
      + (dead ? ` · ${dead} your team cannot trigger` : ''), false, 'weighted');

    // Which two to socket is the only decision on that screen, so the answer
    // goes above the list rather than being left to be inferred from it — and
    // above the fold, since a verdict behind a click cannot be overruled.
    if (plan.notes?.length || plan.ranked?.length) {
      const rec = el('div', 'plan');
      const h = el('h4');
      h.append(svg('i-bag', 'planicon'));
      // What is actually in the sockets. There is deliberately no "equip the
      // recommended set" button any more: with the tick *being* the socket, a
      // button like that would write catalog entries into sockets this run may
      // never have been given.
      h.append(el('span', null, plan.ranked?.length
        ? 'Sockets: ' + plan.ranked.map((w) => w.name).join(' + ')
        : 'Nothing socketed'));
      rec.append(h);
      plan.notes.forEach((n) => rec.append(el('div', 'sub', n)));
      g.insertBefore(rec, g.body);
    }
    data.weighted.forEach((w) => g.body.append(ownedRow(w, 'weighted_curio')));
    body.append(g);
  }

  if (data.miracles.length) {
    const g = ownedGroup('Wishpower Miracles', data.miracles.length, '', false, 'miracles');
    data.miracles.forEach((m) => g.body.append(ownedRow(m, 'miracle')));
    body.append(g);
  }
}

// ------------------------------------------------------- how it scores
// The engine's own constants, rendered. Nothing on this tab is typed into the
// page: /api/explain reads every figure out of the module that uses it, so
// tuning a weight updates the explanation of that weight in the same edit.
// A transparency tab that could drift from the engine would be worse than none.
let EXPLAINED = false;

function explainTable(t) {
  const wrap = el('div', 'extable' + (t.cols.length === 2 ? ' pairs' : ''));
  if (t.caption) wrap.append(el('p', 'excap', t.caption));
  const table = el('table');
  const thead = el('thead');
  const hr = el('tr');
  t.cols.forEach((c) => hr.append(el('th', null, c)));
  thead.append(hr);
  table.append(thead);
  const tb = el('tbody');
  t.rows.forEach((row) => {
    const tr = el('tr');
    row.forEach((cell, i) => {
      // Column 0 names the thing; a lone numeric column is the value. Numbers are
      // tabular so a column of weights can be compared by eye, which is most of
      // why anyone opens this tab.
      const num = i > 0 && typeof cell === 'number';
      tr.append(el('td', num ? 'n val' : (i === 0 ? 'exname' : ''), String(cell)));
    });
    tb.append(tr);
  });
  table.append(tb);
  wrap.append(table);
  if (t.note) wrap.append(el('p', 'exnote', t.note));
  return wrap;
}

function renderExplainGroups(groups) {
  const host = $('#explainGroups');
  const nav = $('#explainNav');
  host.innerHTML = '';
  nav.innerHTML = '';

  // The hand-written cards sit outside the generated list, so they are linked
  // by hand — the nav is the table of contents for the whole tab, not just for
  // the part the API produced.
  const link = (id, label, prov) => {
    const a = el('a', 'exlink');
    a.href = '#' + id;
    a.append(el('span', null, label));
    if (prov) a.append(el('span', 'prov ' + prov, prov));
    a.onclick = (ev) => {
      ev.preventDefault();
      $('#' + id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    return a;
  };

  groups.forEach((g) => {
    const card = el('div', 'card');
    card.id = 'ex-' + g.key;
    const h = el('h2');
    h.append(svg('i-rank'));
    h.append(el('span', null, g.title));
    h.append(el('span', 'prov ' + g.provenance, g.provenance));
    card.append(h);
    card.append(el('p', 'exsrc', g.source));
    g.tables.forEach((t) => card.append(explainTable(t)));
    host.append(card);
    nav.append(link(card.id, g.title, g.provenance));
  });
  nav.append(link('ex-honesty', 'How sure is any of this', ''));
}

function renderExplainNow(now) {
  const host = $('#explainNow');
  host.innerHTML = '';
  const pos = now.position;
  const head = el('div', 'nowhead');
  head.append(el('b', null, `Domain ${pos.domain}`));
  head.append(el('span', null,
    `${pos.domains_left} left · ~${pos.picks_remaining} blessing picks`
    + (pos.endgame ? ' · endgame' : '')));
  host.append(head);

  const table = el('table');
  const tb = el('tbody');
  now.rows.forEach(([name, value, note]) => {
    const tr = el('tr');
    tr.append(el('td', 'exname', name));
    tr.append(el('td', 'n val', value));
    tb.append(tr);
    const nr = el('tr', 'noterow');
    const td = el('td', 'exnote');
    td.colSpan = 2;
    td.textContent = note;
    nr.append(td);
    tb.append(nr);
  });
  table.append(tb);
  host.append(table);
}

async function renderExplain() {
  // The constants never change while the app is running, so they are fetched
  // once; the "right now" panel is re-rendered on every visit because the run
  // moves under it.
  const data = await api('/api/explain', RUN);
  if (!EXPLAINED) {
    renderExplainGroups(data.groups);
    EXPLAINED = true;
  }
  renderExplainNow(data.now);
}

// ---------------------------------------------------------------- boot
/** Bare-minimum inline markdown: `**bold**` and `` `code` ``.
 *
 * Built as nodes rather than by assigning innerHTML — the changelog is prose
 * written by hand and there is no reason for a stray angle bracket in it to be
 * able to inject markup into the page. */
function inlineMd(text) {
  const frag = document.createDocumentFragment();
  String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/).forEach((part) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) frag.append(el('b', null, part.slice(2, -2)));
    else if (/^`[^`]+`$/.test(part)) frag.append(el('code', null, part.slice(1, -1)));
    else if (part) frag.append(document.createTextNode(part));
  });
  return frag;
}

// Same guard, and for the same reason, as renderWeightedPool above: clearing
// before an await lets two overlapping renders each empty the box and then each
// append, so the tab shows every release twice. Two quick clicks on the
// Changelog tab did it, since switchTab fires this without awaiting it. A
// duplicated changelog reads as two separate builds having shipped the same
// thing, which is a worse lie than a slow tab.
let LOG_RENDER = 0;

async function renderChangelog() {
  const token = ++LOG_RENDER;
  const meta = (n) => document.querySelector(`meta[name="${n}"]`)?.content || '';
  const build = meta('du-build');
  // Local runs are served straight off the working tree, so there is no build
  // id to show and claiming one would be a fiction.
  $('#logBuild').textContent = build
    ? `You are running build ${build}${meta('du-built') ? ` · built ${meta('du-built')}` : ''}`
      + `${meta('du-source') ? ` · game data ${meta('du-source')}` : ''}`
    : 'Running locally from the working tree, so there is no build id.';

  const box = $('#logBody');
  let data;
  try {
    data = await api('/api/changelog');
  } catch (e) {
    if (token !== LOG_RENDER) return;
    box.innerHTML = '';
    box.append(el('div', 'hint', 'could not load the changelog: ' + e.message));
    return;
  }
  // Nothing is cleared until the awaiting is done, and a render that has been
  // overtaken throws its result away rather than painting it over a newer one.
  if (token !== LOG_RENDER) return;
  box.innerHTML = '';
  if (data.note) box.append(el('div', 'hint', data.note));
  if (!data.entries?.length) {
    box.append(emptyState('Nothing recorded yet.'));
    return;
  }
  data.entries.forEach((entry, i) => {
    const sec = el('div', 'logentry');
    const h = el('h3', null, entry.title);
    // The top section is what this build shipped; it is dated by the build
    // itself, since a record written after the deploy could only describe the
    // one before it.
    if (i === 0 && /unreleased/i.test(entry.title)) {
      h.textContent = meta('du-built')
        ? `This build · ${meta('du-built')}` : 'Not yet published';
    }
    sec.append(h);
    // Categories, the way patch notes are laid out. A release written before
    // they existed parses into one untitled group, which renders exactly as it
    // always did rather than growing an empty heading.
    (entry.groups || [{ title: '', items: entry.items }]).forEach((group) => {
      if (group.title) sec.append(el('h4', null, group.title));
      const ul = el('ul', 'explainlist');
      group.items.forEach((it) => {
        const li = document.createElement('li');
        li.append(inlineMd(it));
        ul.append(li);
      });
      sec.append(ul);
    });
    box.append(sec);
  });
}

function switchTab(name) {
  document.querySelectorAll('.tabs button').forEach((x) => x.classList.remove('active'));
  document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
  const btn = document.querySelector(`.tabs button[data-tab="${name}"]`);
  if (btn) btn.classList.add('active');
  const sec = $('#tab-' + name);
  if (sec) sec.classList.add('active');
  if (name === 'run') refreshRun();
  // The pool is scored against the team and the Domain, both edited on other
  // tabs, so coming back re-scores rather than showing what was true on the way
  // out. renderOwned() covers the socket verdict; showWeightedPool() the list.
  if (name === 'owned') { renderOwned(); showWeightedPool(); }
  if (name === 'wish' && !POOL.length) loadPool();
  if (name === 'how') renderExplain().catch((e) => { $('#status').textContent = e.message; });
  if (name === 'log') renderChangelog().catch((e) => { $('#status').textContent = e.message; });
}

// A real screen, not a spinner. The first visit downloads and initialises a
// Python runtime, which takes seconds and looks like a hang if unexplained —
// so it says what it is doing and that it happens once.
async function startBackend() {
  if (!usePyodide) return;
  const screen = $('#booting');
  const step = $('#bootStep');
  const detail = $('#bootDetail');
  screen.hidden = false;
  try {
    await PyodideBackend.start((text, extra) => {
      step.textContent = text + '…';
      detail.textContent = extra || '';
    });
    setBackend(PyodideBackend);
    screen.hidden = true;
  } catch (e) {
    step.textContent = 'Could not start the engine';
    detail.textContent = e.message;
    throw e;
  }
}

// Offline caching, hosted build only. Deliberately not registered locally: a
// cache-first worker sitting between you and your own edits turns every
// frontend change into a debugging session.
function registerServiceWorker() {
  if (!usePyodide || !('serviceWorker' in navigator)) return;
  // updateViaCache 'none': without it the browser may serve sw.js itself from
  // the HTTP cache for up to 24 hours, so a publish would not even be *noticed*
  // for a day. The worker script is 3 KB; revalidating it every load is free.
  navigator.serviceWorker.register(url('sw.js'), { updateViaCache: 'none' }).catch((e) => {
    console.warn('service worker not registered:', e.message);   // never fatal
  });
}

// ------------------------------------------------------- "what this is"
// Shown until dismissed. Kept in localStorage rather than in the run, so it is
// a property of this browser and never travels in an export.
function setupIntro() {
  const card = $('#intro');
  if (!card) return;
  if (localStorage.getItem('du-intro-dismissed') !== '1') card.hidden = false;
  $('#introClose').onclick = () => {
    card.hidden = true;
    localStorage.setItem('du-intro-dismissed', '1');
  };
}

async function boot() {
  await startBackend();
  registerServiceWorker();
  setupIntro();
  META = await api('/api/meta');
  const patch = META.meta.source_title.replace(/^OSPROD\w+?(\d+\.\d+\.\d+).*$/, '$1');
  const m = $('#meta');
  m.innerHTML = '';
  m.append(el('b', null, META.meta.theme.replace('Divergent Universe: ', '')));
  m.append(document.createTextNode(
    ` · patch ${patch} · ${META.counts.blessings} blessings · ${META.counts.equations} equations · ` +
    `${META.counts.curios} curios · ${META.counts.masks} masks`));

  // Which build a friend is actually on. The patch number above says which game
  // data; this says which copy of the app, so "did my update reach you?" has an
  // answer that is not a guess.
  const build = document.querySelector('meta[name="du-build"]')?.content;
  const source = document.querySelector('meta[name="du-source"]')?.content;
  m.title = [
    `game data: patch ${patch}`,
    source ? `upstream commit: ${source}` : null,
    build ? `app build: ${build}` : 'app build: local (not a published build)',
  ].filter(Boolean).join('\n');
  if (build) {
    const tag = el('span', 'buildstamp', ` · build ${build.slice(0, 7)}`);
    m.append(tag);
  }

  try {
    const saved = await api('/api/run/load');
    if (saved && (saved.mask_id || saved.owned_blessings?.length || saved.team?.length)) {
      RUN = Object.assign(RUN, saved);
    }
  } catch (e) { /* first run */ }

  ['plane', 'difficulty'].forEach((id) => {
    const inp = $('#' + id);
    inp.value = RUN[id];
    inp.addEventListener('change', (ev) => {
      RUN[id] = parseInt(ev.target.value || '0', 10);
      updateDifficultyNote(); save(); refreshRun();
    });
  });

  // Wishpower level is set at the start and then ticks up all run, so it has to
  // be editable wherever you happen to be looking when it changes: Setup, the
  // Wishpower tab, and the position bar beside the other running counters.
  // Three inputs, one value — they write through a single setter so they cannot
  // drift apart.
  WISH_INPUTS.forEach((sel) => {
    const inp = $(sel);
    inp.value = RUN.wishpower_level;
    inp.addEventListener('change', (ev) => {
      setWishpower(parseInt(ev.target.value || '0', 10));
      save();
    });
  });
  $('#miracleResets').value = RUN.miracle_resets;
  $('#miracleResets').addEventListener('change', (ev) => {
    RUN.miracle_resets = parseInt(ev.target.value || '0', 10);
    save();
  });
  $('#doorRedraws').value = RUN.door_redraws;
  $('#doorRedraws').addEventListener('change', (ev) => {
    RUN.door_redraws = parseInt(ev.target.value || '0', 10);
    save();
  });

  const posFields = {
    domainIndex: 'domain_index', domainTotal: 'domain_total',
    fragments: 'fragments', heat: 'heat', heatMax: 'heat_max',
  };
  Object.entries(posFields).forEach(([id, key]) => {
    const inp = $('#' + id);
    const formatted = inp.classList.contains('num-wide');
    inp.value = formatted ? fmtNum(RUN[key]) : RUN[key];

    inp.addEventListener('input', () => {
      // While typing, read the digits but leave the text alone — reformatting
      // mid-edit would shunt the caret around.
      RUN[key] = formatted ? parseNum(inp.value) : parseInt(inp.value || '0', 10);
      updatePosNote();
    });
    inp.addEventListener('blur', () => {
      if (formatted) inp.value = fmtNum(RUN[key]);
    });
    inp.addEventListener('change', () => {
      if (formatted) inp.value = fmtNum(RUN[key]);
      save(); refreshRun();
    });
  });

  const rl = $('#runLength');
  (META.run_lengths || []).forEach((v) => {
    const o = document.createElement('option');
    o.value = v.domains;
    o.textContent = `${v.domains} · D${v.difficulties.join('/')}`;
    rl.append(o);
  });
  rl.value = RUN.domain_total;
  rl.addEventListener('change', () => {
    RUN.domain_total = parseInt(rl.value, 10);
    $('#domainTotal').value = RUN.domain_total;
    updatePosNote(); save(); refreshRun();
  });

  renderMasks(); renderTeam(); setupCharSearch(); setupOwnReset(); renderOwned();
  setupMaskCompare();
  updatePosNote(); updateDifficultyNote();

  attachSearch('#offerSearch', '#offerResults', () => $('#offerKind').value, (e) => {
    if (!OFFER.some((o) => o.id === e.id)) OFFER.push(e);
    CACHE.set(e.kind + ':' + e.id, e);
    renderOffer();
  });
  attachSearch('#ownSearch', '#ownResults', () => $('#ownKind').value, (e) => {
    const key = ownedKey(e.kind);
    if (key && !RUN[key].includes(e.id)) RUN[key].push(e.id);
    CACHE.set(e.kind + ':' + e.id, e);
    renderOwned(); refreshRun(); save();
  });

  $('#ownKind').addEventListener('change', showWeightedPool);
  $('#weightedSlots').addEventListener('change', () => {
    RUN.weighted_slots = Math.max(0, parseInt($('#weightedSlots').value || '0', 10));
    renderWeightedPool(); renderOwned(); save();
  });
  // The scores are against the team, the Domain and what the run holds, all of
  // which are edited on other tabs. Switching back re-scores on its own; this is
  // for the case where nothing on this tab changed but the run did.
  $('#weightedRescore').onclick = () => {
    $('#weightedStamp').textContent = 're-scoring…';
    renderWeightedPool();
  };
  showWeightedPool();

  $('#clearOffer').onclick = () => {
    OFFER = []; renderOffer();
    $('#ranking').innerHTML = ''; $('#ocrCards').innerHTML = '';
  };
  $('#rankBtn').onclick = async () => {
    const data = await api('/api/rank',
      { run: RUN, kind: $('#offerKind').value, ids: OFFER.map((o) => o.id) });
    renderRanking(data);
  };

  await setupDoors();
  setupSpend();
  setupStore();
  setupWishpower();
  setupTargets();          // needs DOMAIN_TYPES, so after setupDoors
  setupInventory();

  // Every screenshot control starts hidden and is revealed only if the backend
  // actually has an OCR engine. The hosted build has none by design (WEB-PLAN.md),
  // so these must not merely fail on click — #optDrop used to be visible and
  // unguarded, which is precisely that failure.
  try {
    const { available } = await api('/api/ocr/status');
    document.body.classList.toggle('no-ocr', !available);
    if (available) {
      ['#ocrPanel', '#invPanel', '#optDrop'].forEach((s) => { $(s).hidden = false; });
    }
  } catch (e) {
    // OCR optional — leave every scan control hidden, and the prose describing
    // them with it.
    document.body.classList.add('no-ocr');
  }

  setupDrop('#dropzone', sendImage);

  document.querySelectorAll('.tabs button').forEach((b) => {
    b.onclick = () => switchTab(b.dataset.tab);
  });

  // Ctrl+V routes to the scan zone on whichever tab is visible.
  document.addEventListener('paste', (ev) => {
    const item = [...(ev.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
    if (!item) return;
    const active = document.querySelector('.tab.active');
    const zone = active?.querySelector('.dropzone');
    if (zone && zone._handler) zone._handler(item.getAsFile());
  });

  // Number keys jump between tabs, unless you are typing.
  const TABS = ['setup', 'decide', 'door', 'spend', 'wish', 'owned', 'run', 'how', 'log'];
  document.addEventListener('keydown', (ev) => {
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName)) return;
    const i = parseInt(ev.key, 10);
    if (i >= 1 && i <= TABS.length) switchTab(TABS[i - 1]);
  });

  refreshRun();
}

boot().catch((e) => { $('#status').textContent = 'error: ' + e.message; });
