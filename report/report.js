(() => {
  'use strict';

  const payload = JSON.parse(document.getElementById('report-data').textContent);
  const meta = payload.meta || {};

  const PAGE = 100;
  const EXPORT_MAX = 50;
  const DAY_MS = 86400000;
  const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const NL_MONTHS = ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];

  const $ = (id) => document.getElementById(id);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* ---------- Formatting ---------- */

  const toUTC = (s) => {
    const [y, m, d] = s.slice(0, 10).split('-').map(Number);
    return Date.UTC(y, m - 1, d);
  };
  const dayMonth = (s) => {
    const d = new Date(toUTC(s));
    return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  };
  const weekday = (s) => WEEKDAYS[new Date(toUTC(s)).getUTCDay()];
  const fmtDate = (s) => `${weekday(s)} ${dayMonth(s)}`;
  const fmtStamp = (iso) => (iso ? `${dayMonth(iso)} ${iso.slice(0, 4)}, ${iso.slice(11, 16)}` : '');
  const fmtDur = (min) => (min > 0 ? `${Math.floor(min / 60)}h ${String(min % 60).padStart(2, '0')}m` : '');
  const fmtPrice = (n) => `€ ${Math.round(n)}`;
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

  function legEnd(dateTime, code, startDateTime) {
    if (!dateTime) return '';
    let text = `${dateTime.slice(11, 16)} ${code || ''}`.trim();
    if (startDateTime) {
      const days = Math.round((toUTC(dateTime) - toUTC(startDateTime)) / DAY_MS);
      if (days > 0) text += ` +${days}`;
    }
    return text;
  }

  function stopsText(stops, via, long) {
    if (!stops) return 'Direct';
    const places = (via || []).map(([code, country, layover]) => {
      const wait = layover > 0 ? ` ${fmtDur(layover)}` : '';
      return long ? `${code} (${country})${wait}` : `${code}${wait}`;
    });
    return `${plural(stops, 'stop')} ${places.join(', ')}`.trim();
  }

  /* ---------- Data ---------- */

  const flights = payload.rows.map((row) => {
    const f = {};
    payload.fields.forEach((key, i) => { f[key] = row[i]; });
    f.oneWay = f.r_from === null;
    f.days = Math.round((toUTC(f.ret) - toUTC(f.dep)) / DAY_MS);
    f.pair = `${f.dep}_${f.ret}`;
    f.route = `${f.o_from} to ${f.o_to}`;
    f.total = (f.o_dur || 0) + (f.r_dur || 0);
    f.stops = (f.o_stops || 0) + (f.r_stops || 0);
    f.direct = f.stops === 0;
    f.carriers = [f.o_air, f.r_air].filter(Boolean).flatMap((names) => names.split(' + '));
    return f;
  });

  const SORTS = {
    price: { label: 'Price', get: (f) => f.price },
    dep: { label: 'Departure date', get: (f) => f.dep },
    ret: { label: 'Return date', get: (f) => f.ret },
    days: { label: 'Trip length', get: (f) => f.days },
    out_dep: { label: 'Outbound departure time', get: (f) => (f.o_dep ? f.o_dep.slice(11) : null) },
    out_dur: { label: 'Outbound duration', get: (f) => f.o_dur || null },
    back_dur: { label: 'Return duration', get: (f) => f.r_dur || null },
    total: { label: 'Total duration', get: (f) => f.total || null },
    stops: { label: 'Stops', get: (f) => f.stops },
    airline: { label: 'Airline', get: (f) => f.o_air || null },
  };

  const state = {
    route: '', airline: '', direct: false,
    maxPrice: null, maxOut: null, maxBack: null, maxTotal: null,
    pair: '', sort: 'price', dir: 'asc', shown: PAGE,
  };
  let base = flights;     // every filter except the selected date pair
  let visible = flights;  // what the board lists

  /* ---------- Filtering and sorting ---------- */

  function matches(f) {
    if (state.route && f.route !== state.route) return false;
    if (state.airline && !f.carriers.includes(state.airline)) return false;
    if (state.direct && !f.direct) return false;
    if (state.maxPrice !== null && f.price > state.maxPrice) return false;
    if (state.maxOut !== null && f.o_dur > state.maxOut * 60) return false;
    if (state.maxBack !== null && f.r_dur > state.maxBack * 60) return false;
    if (state.maxTotal !== null && f.total > state.maxTotal * 60) return false;
    return true;
  }

  function compare(a, b) {
    const get = SORTS[state.sort].get;
    const va = get(a);
    const vb = get(b);
    let order = 0;
    if (va === null && vb !== null) return 1;   // unknown values sink in either direction
    if (vb === null && va !== null) return -1;
    if (va !== null && vb !== null) {
      order = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
    }
    if (order !== 0) return state.dir === 'asc' ? order : -order;
    return a.price - b.price;
  }

  function refresh() {
    base = flights.filter(matches);
    visible = state.pair ? base.filter((f) => f.pair === state.pair) : base.slice();
    visible.sort(compare);
    state.shown = PAGE;
    $('rows').replaceChildren();
    renderSummary();
    renderGrid();
    renderBoard();
    writeHash();
  }

  /* ---------- Summary ---------- */

  function renderSummary() {
    const count = visible.length;
    $('stat-count').textContent = count === flights.length ? String(count) : `${count} of ${flights.length}`;
    $('stat-routes').textContent = String(new Set(visible.map((f) => f.route)).size);
    $('stat-avg').textContent = count ? fmtPrice(visible.reduce((sum, f) => sum + f.price, 0) / count) : 'None';
    $('stat-direct').textContent = String(visible.filter((f) => f.direct).length);

    const best = visible.reduce((low, f) => (low === null || f.price < low.price ? f : low), null);
    if (!best) {
      $('best-price').textContent = 'No match';
      $('best-dates').textContent = 'Nothing passes the current filters.';
      $('best-airline').textContent = '';
      return;
    }
    $('best-price').textContent = fmtPrice(best.price);
    $('best-dates').textContent = `${fmtDate(best.dep)} to ${fmtDate(best.ret)}, ${plural(best.days, 'day')}`;
    const airlines = [...new Set(best.carriers)].join(' + ');
    $('best-airline').textContent = `${airlines}, ${best.direct ? 'direct both ways' : plural(best.stops, 'stop')}`;
  }

  /* ---------- Fare grid ---------- */

  const gridCells = new Map();

  function buildGrid() {
    const deps = [...new Set(flights.map((f) => f.dep))].sort();
    const lengths = [...new Set(flights.map((f) => f.days))].sort((a, b) => a - b);
    const searched = new Map(flights.map((f) => [`${f.dep}|${f.days}`, f]));

    const head = el('tr');
    const corner = el('th');
    corner.append(el('span', 'visually-hidden', 'Trip length by departure date'));
    head.append(corner);
    deps.forEach((dep) => {
      const th = el('th');
      th.scope = 'col';
      th.append(el('span', '', weekday(dep)), el('span', '', dayMonth(dep)));
      head.append(th);
    });
    const thead = el('thead');
    thead.append(head);

    const tbody = el('tbody');
    lengths.forEach((days) => {
      const tr = el('tr');
      const th = el('th', '', plural(days, 'day'));
      th.scope = 'row';
      tr.append(th);
      deps.forEach((dep) => {
        const td = el('td');
        const sample = searched.get(`${dep}|${days}`);
        if (sample) {
          const button = el('button');
          button.type = 'button';
          button.dataset.pair = sample.pair;
          button.addEventListener('click', () => {
            state.pair = state.pair === sample.pair ? '' : sample.pair;
            refresh();
          });
          td.append(button);
          gridCells.set(sample.pair, { td, button, dep, days });
        }
        tr.append(td);
      });
      tbody.append(tr);
    });
    $('grid').replaceChildren(thead, tbody);
  }

  function renderGrid() {
    const cheapest = new Map();
    base.forEach((f) => {
      const low = cheapest.get(f.pair);
      if (low === undefined || f.price < low) cheapest.set(f.pair, f.price);
    });
    const ranked = [...cheapest.values()].sort((a, b) => a - b);
    const lowest = ranked[0];

    gridCells.forEach(({ td, button, dep, days }, pair) => {
      const price = cheapest.get(pair);
      const selected = state.pair === pair;
      const where = `${fmtDate(dep)}, ${plural(days, 'day')}`;
      button.className = '';
      button.setAttribute('aria-pressed', String(selected));
      td.classList.toggle('has-fare', price !== undefined);
      if (price === undefined) {
        button.textContent = '';
        button.disabled = !selected;
        button.setAttribute('aria-label', `${where}, no fare under these filters`);
        return;
      }
      const rank = ranked.length > 1 ? ranked.indexOf(price) / (ranked.length - 1) : 0;
      button.disabled = false;
      button.textContent = String(Math.round(price));
      button.setAttribute('aria-label', `${where}, cheapest ${fmtPrice(price)}`);
      if (price === lowest) button.classList.add('is-best');
      else button.classList.add(rank < 0.25 ? 'heat-4' : rank < 0.5 ? 'heat-3' : rank < 0.75 ? 'heat-2' : 'heat-1');
    });
  }

  /* ---------- Board ---------- */

  function cell(label, className, lines) {
    const td = el('td', className);
    td.dataset.label = label;
    lines.forEach((line) => {
      const item = typeof line === 'string' ? { text: line } : line;
      const span = el('span', item.cls ? `ln ${item.cls}` : 'ln', item.text);
      if (item.title) span.title = item.title;
      td.append(span);
    });
    return td;
  }

  function renderRow(f) {
    const tr = el('tr');
    const back = !f.oneWay;
    const book = f.book.map(([price, agents]) => `${fmtPrice(price)} ${agents}`);
    tr.append(
      cell('Price', 'c-price', [
        { text: fmtPrice(f.price), cls: 'price' },
        { text: plural(f.days, 'day'), cls: 'ln--muted' },
      ]),
      cell('Dates', 'c-date', [fmtDate(f.dep), fmtDate(f.ret)]),
      cell('Departs', 'c-dep', [legEnd(f.o_dep, f.o_from), back ? legEnd(f.r_dep, f.r_from) : '']),
      cell('Arrives', 'c-arr', [legEnd(f.o_arr, f.o_to, f.o_dep), back ? legEnd(f.r_arr, f.r_to, f.r_dep) : '']),
      cell('Duration', 'c-dur', [fmtDur(f.o_dur), fmtDur(f.r_dur)]),
      cell('Stops', 'c-stops', [
        { text: stopsText(f.o_stops, f.o_via), title: stopsText(f.o_stops, f.o_via, true) },
        back ? { text: stopsText(f.r_stops, f.r_via), title: stopsText(f.r_stops, f.r_via, true) } : '',
      ]),
      cell('Airline', 'c-air', [
        { text: f.o_air || '', title: f.o_air || '' },
        { text: f.r_air || '', title: f.r_air || '' },
      ]),
      cell('Total', 'c-total', [fmtDur(f.total)]),
      cell('Book via', 'c-book', book.slice(0, 2).map((text) => ({ text, title: book.join(', ') }))),
    );
    return tr;
  }

  function renderBoard() {
    const rows = $('rows');
    const shown = Math.min(state.shown, visible.length);
    const fragment = document.createDocumentFragment();
    for (let i = rows.children.length; i < shown; i += 1) fragment.append(renderRow(visible[i]));
    rows.append(fragment);

    const empty = visible.length === 0;
    $('flights').hidden = empty;
    $('empty').hidden = !empty;
    if (empty) {
      $('empty-why').textContent = `Loosen a limit, pick another date, or clear the filters to see all ${plural(flights.length, 'option')} again.`;
    }

    let count = empty ? '' : `Showing ${shown} of ${plural(visible.length, 'flight')}`;
    if (state.pair && gridCells.has(state.pair)) {
      const [dep, ret] = state.pair.split('_');
      count += `${count ? ', ' : ''}${fmtDate(dep)} to ${fmtDate(ret)}`;
    }
    $('count').textContent = count;
    $('pair-clear').hidden = !state.pair;
    $('export').disabled = empty;

    const left = visible.length - shown;
    $('more').hidden = left <= 0;
    $('more').textContent = `Show ${Math.min(PAGE, left)} more`;

    $('sort').value = state.sort;
    $('dir').textContent = state.dir === 'asc' ? 'Ascending' : 'Descending';
    document.querySelectorAll('#flights th[data-sort]').forEach((th) => {
      if (th.dataset.sort === state.sort) th.setAttribute('aria-sort', state.dir === 'asc' ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
    });
  }

  function resort() {
    visible.sort(compare);
    state.shown = PAGE;
    $('rows').replaceChildren();
    renderBoard();
    writeHash();
  }

  /* ---------- Controls ---------- */

  const NUMBER_FIELDS = { maxPrice: 'f-price', maxOut: 'f-out', maxBack: 'f-back', maxTotal: 'f-total' };

  function readControls() {
    state.route = $('f-route').value;
    state.airline = $('f-airline').value;
    state.direct = $('f-direct').checked;
    Object.entries(NUMBER_FIELDS).forEach(([key, id]) => {
      const value = parseFloat($(id).value);
      state[key] = Number.isFinite(value) && value > 0 ? value : null;
    });
  }

  function clearFilters() {
    $('filter-form').reset();
    state.pair = '';
    readControls();
    refresh();
  }

  function onFilterChange() {
    readControls();
    refresh();
  }

  function debounce(fn, wait) {
    let timer = 0;
    return () => {
      clearTimeout(timer);
      timer = setTimeout(fn, wait);
    };
  }

  function fillSelect(select, values) {
    values.forEach((value) => {
      const option = el('option', '', value);
      option.value = value;
      select.append(option);
    });
  }

  function buildControls() {
    const routes = [...new Set(flights.map((f) => f.route))].sort();
    fillSelect($('f-route'), routes);
    $('route-field').hidden = routes.length < 2;
    fillSelect($('f-airline'), [...new Set(flights.flatMap((f) => f.carriers))].sort());

    Object.entries(SORTS).forEach(([key, sort]) => {
      const option = el('option', '', sort.label);
      option.value = key;
      $('sort').append(option);
    });

    document.querySelectorAll('#flights th[data-sort]').forEach((th) => {
      const button = el('button', 'th-sort', th.textContent);
      button.type = 'button';
      const caret = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      caret.setAttribute('viewBox', '0 0 10 10');
      caret.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', 'M5 1.5 9 8.5H1z');
      path.setAttribute('fill', 'currentColor');
      caret.append(path);
      button.append(caret);
      button.addEventListener('click', () => {
        const key = th.dataset.sort;
        state.dir = state.sort === key && state.dir === 'asc' ? 'desc' : 'asc';
        state.sort = key;
        resort();
      });
      th.replaceChildren(button);
    });

    ['f-route', 'f-airline', 'f-direct'].forEach((id) => $(id).addEventListener('change', onFilterChange));
    const typed = debounce(onFilterChange, 160);
    Object.values(NUMBER_FIELDS).forEach((id) => $(id).addEventListener('input', typed));
    $('filter-form').addEventListener('submit', (event) => event.preventDefault());

    $('clear').addEventListener('click', clearFilters);
    $('empty-clear').addEventListener('click', clearFilters);
    $('pair-clear').addEventListener('click', () => { state.pair = ''; refresh(); });
    $('sort').addEventListener('change', () => { state.sort = $('sort').value; state.dir = 'asc'; resort(); });
    $('dir').addEventListener('click', () => { state.dir = state.dir === 'asc' ? 'desc' : 'asc'; resort(); });
    $('more').addEventListener('click', () => { state.shown += PAGE; renderBoard(); });
    $('export').addEventListener('click', exportImage);

    // The filters fold behind a disclosure on narrow screens and stay open on wide ones.
    const narrow = matchMedia('(max-width: 60rem)');
    const fold = () => { $('filters').open = !narrow.matches; };
    fold();
    narrow.addEventListener('change', fold);
  }

  /* ---------- State in the URL ---------- */

  const HASH_KEYS = {
    route: 'route', airline: 'airline', maxPrice: 'price', maxOut: 'out', maxBack: 'back',
    maxTotal: 'total', pair: 'dates',
  };

  function writeHash() {
    const params = new URLSearchParams();
    Object.entries(HASH_KEYS).forEach(([key, name]) => {
      if (state[key] !== null && state[key] !== '') params.set(name, state[key]);
    });
    if (state.direct) params.set('direct', '1');
    if (state.sort !== 'price' || state.dir !== 'asc') params.set('sort', `${state.sort}.${state.dir}`);
    const hash = params.toString();
    try {
      history.replaceState(null, '', hash ? `#${hash}` : location.pathname + location.search);
    } catch (error) { /* some file viewers refuse history changes; the page still works */ }
  }

  function readHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const pick = (select, value) => {
      if ([...select.options].some((option) => option.value === value)) select.value = value;
    };
    pick($('f-route'), params.get('route') || '');
    pick($('f-airline'), params.get('airline') || '');
    $('f-direct').checked = params.get('direct') === '1';
    Object.entries(NUMBER_FIELDS).forEach(([key, id]) => { $(id).value = params.get(HASH_KEYS[key]) || ''; });
    const pair = params.get('dates') || '';
    state.pair = gridCells.has(pair) ? pair : '';
    const [sort, dir] = (params.get('sort') || '').split('.');
    if (SORTS[sort]) {
      state.sort = sort;
      state.dir = dir === 'desc' ? 'desc' : 'asc';
    }
    readControls();
  }

  /* ---------- Page furniture ---------- */

  function fact(label, value) {
    const row = el('div');
    row.append(el('dt', '', label), el('dd', '', value));
    return row;
  }

  function renderFurniture() {
    const routeCounts = new Map();
    flights.forEach((f) => routeCounts.set(f.route, (routeCounts.get(f.route) || 0) + 1));
    const mainRoute = [...routeCounts.entries()].sort((a, b) => b[1] - a[1])[0];
    if (mainRoute) {
      $('route').textContent = mainRoute[0];
      document.title = `${mainRoute[0]} fares, skyscannerplus`;
    }

    if (flights.length) {
      const lengths = flights.map((f) => f.days);
      const deps = flights.map((f) => f.dep).sort();
      const shortest = Math.min(...lengths);
      const longest = Math.max(...lengths);
      const span = shortest === longest ? plural(shortest, 'day') : `${shortest} to ${longest} days`;
      const first = deps[0];
      const last = deps[deps.length - 1];
      const leaving = first === last ? dayMonth(first) : `${dayMonth(first)} to ${dayMonth(last)}`;
      $('sub').textContent = `Return trips of ${span}, leaving ${leaving} ${last.slice(0, 4)}.`;
    }

    const facts = $('facts');
    if (meta.adults) {
      const children = (meta.children_ages || []).length;
      const party = [plural(meta.adults, 'adult')];
      if (children) party.push(children === 1 ? '1 child' : `${children} children`);
      facts.append(fact('Travellers', party.join(', ')));
    }
    if (meta.cabin) facts.append(fact('Cabin', meta.cabin));
    if (meta.searched) facts.append(fact('Searched', fmtStamp(meta.searched)));

    $('foot-generated').textContent = `Generated ${fmtStamp(meta.generated)} from ${plural(flights.length, 'flight option')} across ${plural(meta.pairs || gridCells.size, 'date pair')}.`;
    if (meta.partial_pairs) {
      $('foot-partial').hidden = false;
      $('foot-partial').textContent = `${plural(meta.partial_pairs, 'date pair')} did not finish loading during the search, so their lists may be incomplete.`;
    }
  }

  /* ---------- Image export (Dutch, for sharing) ---------- */

  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function nlDateTime(s) {
    if (!s) return '?';
    const [, m, d] = s.slice(0, 10).split('-').map(Number);
    return `${d} ${NL_MONTHS[m - 1]} ${s.slice(11, 16)}`.trim();
  }
  const nlDur = (min) => (min > 0 ? `${Math.floor(min / 60)}u${String(min % 60).padStart(2, '0')}` : '?');

  function nlStops(stops, via) {
    if (!stops) return 'Direct';
    const places = (via || []).map(([code, , layover]) => (layover > 0 ? `${code} (${nlDur(layover)})` : code));
    return `${stops} ${stops === 1 ? 'stop' : 'stops'}: ${places.join(', ')}`;
  }

  function filterSummary() {
    const parts = [];
    if (state.maxPrice !== null) parts.push(`max € ${state.maxPrice}`);
    if (state.maxOut !== null) parts.push(`heen max ${state.maxOut}u`);
    if (state.maxBack !== null) parts.push(`terug max ${state.maxBack}u`);
    if (state.maxTotal !== null) parts.push(`totaal max ${state.maxTotal}u vliegen`);
    if (state.direct) parts.push('alleen direct');
    if (state.airline) parts.push(state.airline);
    parts.push('retour, prijs per persoon');
    return parts.join(', ');
  }

  function exportList() {
    const seen = new Set();
    return visible.filter((f) => {
      const key = `${f.id}|${f.price}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function drawExport(list, total) {
    const W = 1200;
    const HEAD = 116;
    const ROW = 92;
    const FOOT = 56;
    const PAD = 32;
    const SCALE = 2;
    const H = HEAD + list.length * ROW + FOOT;
    const face = token('--font-display');
    const ink = token('--color-ink');
    const paper = token('--color-paper');
    const muted = token('--color-muted');
    const onInkMuted = token('--color-on-ink-muted');
    const amber = token('--color-accent-2');
    const rule = token('--color-rule');

    const canvas = document.createElement('canvas');
    canvas.width = W * SCALE;
    canvas.height = H * SCALE;
    const ctx = canvas.getContext('2d');
    ctx.scale(SCALE, SCALE);
    ctx.textBaseline = 'alphabetic';

    const text = (value, x, y, { size = 14, weight = 400, color = ink, maxX = W - PAD } = {}) => {
      ctx.font = `${weight} ${size}px ${face}`;
      ctx.fillStyle = color;
      let out = String(value);
      if (x + ctx.measureText(out).width > maxX) {
        while (out.length && x + ctx.measureText(`${out}…`).width > maxX) out = out.slice(0, -1);
        out += '…';
      }
      ctx.fillText(out, x, y);
    };

    ctx.fillStyle = token('--color-paper-3');
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = ink;
    ctx.fillRect(0, 0, W, HEAD);

    const route = list[0] ? list[0].route.replace(' to ', ' naar ') : '';
    text(`${route}, ${list.length} beste opties`, PAD, 54, { size: 30, weight: 800, color: paper });
    text(filterSummary(), PAD, 86, { size: 16, color: onInkMuted });

    const COL = { rank: PAD, price: 84, leg: 250, dep: 320, arr: 470, dur: 620, stops: 700, air: 960 };
    const leg = (y, label, from, to, dur, stops, via, airline) => {
      text(label, COL.leg, y, { weight: 800, color: muted });
      text(nlDateTime(from), COL.dep, y, { maxX: COL.arr - 12 });
      text(nlDateTime(to), COL.arr, y, { maxX: COL.dur - 12 });
      text(nlDur(dur), COL.dur, y, { weight: 800, maxX: COL.stops - 12 });
      text(nlStops(stops, via), COL.stops, y, { maxX: COL.air - 16 });
      text(airline || '', COL.air, y, { color: muted });
    };

    list.forEach((f, i) => {
      const top = HEAD + i * ROW;
      if (i % 2 === 1) {
        ctx.fillStyle = paper;
        ctx.fillRect(0, top, W, ROW);
      }
      text(`#${i + 1}`, COL.rank, top + 40, { size: 15, weight: 800, color: muted, maxX: COL.price - 8 });
      text(fmtPrice(f.price), COL.price, top + 44, { size: 28, weight: 800, maxX: COL.leg - 12 });
      text(`${f.days} dagen`, COL.price, top + 68, { weight: 500, color: muted, maxX: COL.leg - 12 });
      leg(top + 38, 'Heen', f.o_dep, f.o_arr, f.o_dur, f.o_stops, f.o_via, f.o_air);
      if (!f.oneWay) leg(top + 66, 'Terug', f.r_dep, f.r_arr, f.r_dur, f.r_stops, f.r_via, f.r_air);
      ctx.fillStyle = rule;
      ctx.fillRect(PAD, top + ROW - 1, W - 2 * PAD, 1);
    });

    ctx.fillStyle = amber;
    ctx.fillRect(0, HEAD - 4, W, 4);

    const fetched = meta.searched || meta.generated;
    const when = fetched ? `${nlDateTime(fetched).replace(/ (\d\d:\d\d)$/, ` ${fetched.slice(0, 4)} $1`)}` : '';
    const foot = (total > list.length ? `Top ${list.length} van ${total} resultaten. ` : '')
      + `Skyscanner-prijzen opgehaald op ${when}. Prijzen kunnen inmiddels veranderd zijn.`;
    text(foot, PAD, H - 22, { size: 12, color: muted });
    return canvas;
  }

  async function exportImage() {
    const list = exportList();
    const button = $('export');
    if (!list.length) return;
    button.setAttribute('aria-busy', 'true');
    try {
      const face = token('--font-display');
      await Promise.all([400, 500, 800].map((weight) => document.fonts.load(`${weight} 16px ${face}`))).catch(() => {});
      const canvas = drawExport(list.slice(0, EXPORT_MAX), list.length);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `vluchten_${new Date().toISOString().slice(0, 10)}.png`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    } finally {
      button.removeAttribute('aria-busy');
    }
  }

  /* ---------- Start ---------- */

  buildGrid();
  buildControls();
  renderFurniture();
  readHash();
  refresh();
  window.addEventListener('hashchange', () => { readHash(); refresh(); });
})();
