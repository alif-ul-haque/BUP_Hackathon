/**
 * GridWise dashboard.
 *
 * Talks to the same origin that serves this page, so the deployed API and the
 * dashboard are always the same service — no CORS, no configured base URL.
 */

import { createScene, formatNumber, niceTicks, clamp } from './scene.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const HOURS = 24;
const TOLERANCE = 0.01; // Section 11.5

const el = (id) => document.getElementById(id);

const state = {
  notes: [],
  baseNotes: [],
  input: null,
  response: null,
  selected: null,
};

let scene;

/* ------------------------------------------------------------------ theme */

function applyStoredTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem('gridwise-theme');
  } catch {
    /* private window or blocked storage — fall back to the system theme */
  }
  if (stored === 'light' || stored === 'dark') {
    document.documentElement.setAttribute('data-theme', stored);
  }
  updateThemeIcon();
}

function currentTheme() {
  const stamped = document.documentElement.getAttribute('data-theme');
  if (stamped) return stamped;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function updateThemeIcon() {
  el('theme-icon').textContent = currentTheme() === 'dark' ? '☀' : '☾';
}

el('theme-toggle').addEventListener('click', () => {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try {
    localStorage.setItem('gridwise-theme', next);
  } catch {
    /* nothing to persist to; the page still switches for this session */
  }
  updateThemeIcon();
  scene?.refreshTheme();
  if (state.response) renderSoc();
});

/* ------------------------------------------------------------- directives */

/** Mirrors `app/services/constraints.py` so the dashboard reads the plan the
 *  same way the optimizer and the judge do. */
function compileDirectives(input, interpretations) {
  const hours = [...input.hours].sort((a, b) => a.hour - b.hour);
  const battery = input.battery;

  const compiled = {
    effectiveSolar: hours.map((h) => h.solar_kwh),
    minEnergy: new Array(HOURS).fill(battery.minimum_energy_kwh),
    chargeAllowed: new Array(HOURS).fill(true),
    dischargeAllowed: new Array(HOURS).fill(true),
    maxGrid: new Array(HOURS).fill(Infinity),
    touched: new Set(),
  };

  for (const entry of interpretations ?? []) {
    if (!entry.applies || entry.directive_type === 'no_op') continue;
    const adjustment = entry.structured_adjustment;
    if (!adjustment || !Array.isArray(adjustment.hours)) continue;

    for (const hour of adjustment.hours) {
      if (!Number.isInteger(hour) || hour < 0 || hour >= HOURS) continue;
      compiled.touched.add(hour);
      switch (entry.directive_type) {
        case 'solar_reduction':
          compiled.effectiveSolar[hour] *= adjustment.factor;
          break;
        case 'minimum_battery_reserve':
          compiled.minEnergy[hour] = Math.max(compiled.minEnergy[hour], adjustment.minimum_energy_kwh);
          break;
        case 'no_charge_window':
          compiled.chargeAllowed[hour] = false;
          break;
        case 'no_discharge_window':
          compiled.dischargeAllowed[hour] = false;
          break;
        case 'max_grid_window':
          compiled.maxGrid[hour] = Math.min(compiled.maxGrid[hour], adjustment.max_grid_kwh);
          break;
        default:
          break;
      }
    }
  }
  return compiled;
}

/** Merge scenario input and returned plan into one row per hour. */
function mergeHours(input, response, compiled) {
  const byHour = new Map(input.hours.map((h) => [h.hour, h]));
  const planByHour = new Map(response.hourly_plan.map((p) => [p.hour, p]));

  return Array.from({ length: HOURS }, (_, hour) => {
    const scenario = byHour.get(hour) ?? { hour, demand_kwh: 0, solar_kwh: 0, tariff_bdt_per_kwh: 0 };
    const plan =
      planByHour.get(hour) ?? {
        hour,
        grid_kwh: 0,
        solar_used_kwh: 0,
        battery_action: 'idle',
        battery_kwh: 0,
        battery_energy_after_kwh: 0,
      };
    return {
      ...scenario,
      ...plan,
      effective_solar_kwh: compiled.effectiveSolar[hour],
      min_energy_kwh: compiled.minEnergy[hour],
      max_grid_kwh: compiled.maxGrid[hour],
      charge_allowed: compiled.chargeAllowed[hour],
      discharge_allowed: compiled.dischargeAllowed[hour],
      charge_kwh: plan.battery_action === 'charge' ? plan.battery_kwh : 0,
      discharge_kwh: plan.battery_action === 'discharge' ? plan.battery_kwh : 0,
    };
  });
}

/* ------------------------------------------------------- client-side replay */

/** A local re-walk of the plan, the way Section 11 describes the judge's replay.
 *  Advisory only — it exists so a broken plan is obvious on screen. */
function replay(input, rows) {
  const battery = input.battery;
  const problems = [];
  let energy = battery.initial_energy_kwh;

  for (const row of rows) {
    const label = `Hour ${String(row.hour).padStart(2, '0')}`;

    if (row.solar_used_kwh > row.effective_solar_kwh + TOLERANCE) {
      problems.push(`${label}: solar used exceeds effective solar`);
    }
    const supply = row.grid_kwh + row.solar_used_kwh + row.discharge_kwh;
    const sink = row.demand_kwh + row.charge_kwh;
    if (Math.abs(supply - sink) > TOLERANCE) {
      problems.push(`${label}: energy balance off by ${formatNumber(supply - sink)} kWh`);
    }

    const expected = energy + row.charge_kwh - row.discharge_kwh;
    if (Math.abs(expected - row.battery_energy_after_kwh) > TOLERANCE) {
      problems.push(`${label}: battery transition does not match the stated action`);
    }
    energy = row.battery_energy_after_kwh;

    if (energy < row.min_energy_kwh - TOLERANCE) problems.push(`${label}: battery below the required reserve`);
    if (energy > battery.capacity_kwh + TOLERANCE) problems.push(`${label}: battery above capacity`);
    if (row.charge_kwh > battery.max_charge_kwh_per_hour + TOLERANCE) problems.push(`${label}: charge above the hourly limit`);
    if (row.discharge_kwh > battery.max_discharge_kwh_per_hour + TOLERANCE) problems.push(`${label}: discharge above the hourly limit`);
    if (!row.charge_allowed && row.charge_kwh > TOLERANCE) problems.push(`${label}: charging inside a no-charge window`);
    if (!row.discharge_allowed && row.discharge_kwh > TOLERANCE) problems.push(`${label}: discharging inside a no-discharge window`);
    if (row.grid_kwh > row.max_grid_kwh + TOLERANCE) problems.push(`${label}: grid import above the directive cap`);
  }

  if (Math.abs(energy - battery.initial_energy_kwh) > TOLERANCE) {
    problems.push('End of day: battery does not return to its starting level');
  }
  return problems;
}

/* --------------------------------------------------------------- rendering */

function render() {
  const { input, response } = state;
  if (!input || !response) return;

  const compiled = compileDirectives(input, response.directive_interpretation);
  const rows = mergeHours(input, response, compiled);
  state.rows = rows;
  state.compiled = compiled;
  state.selected = null; // the scene clears its own selection on new data

  renderKpis(rows, response, compiled);
  renderScene(rows, compiled);
  renderDirectives(response.directive_interpretation);
  renderSoc();
  renderTable(rows);
  renderDetail(state.selected);

  const problems = replay(input, rows);
  const status = el('run-status');
  if (problems.length) {
    status.dataset.state = 'error';
    status.textContent = `${problems.length} replay issue(s): ${problems[0]}`;
  } else {
    delete status.dataset.state;
    status.textContent = 'Plan from the API · replays clean locally.';
  }
}

function renderKpis(rows, response, compiled) {
  const notes = state.input.operator_notes.length;
  const applied = (response.directive_interpretation ?? []).filter(
    (entry) => entry.directive_type !== 'no_op'
  ).length;
  const solarUsed = rows.reduce((sum, row) => sum + row.solar_used_kwh, 0);
  const solarAvailable = compiled.effectiveSolar.reduce((sum, value) => sum + value, 0);
  const peakRow = rows.reduce((best, row) => (row.grid_kwh > best.grid_kwh ? row : best), rows[0]);

  el('kpi-cost').textContent = `৳${formatNumber(response.total_cost_bdt)}`;
  el('kpi-grid').textContent = formatNumber(response.total_grid_kwh);
  el('kpi-peak').textContent = formatNumber(response.peak_grid_kwh);
  el('kpi-peak-note').textContent = `kWh · hour ${String(peakRow.hour).padStart(2, '0')}`;
  el('kpi-solar').textContent = formatNumber(solarUsed);
  el('kpi-solar-note').textContent =
    solarAvailable > 0
      ? `of ${formatNumber(solarAvailable)} kWh available`
      : 'kWh';
  el('kpi-directives').textContent = String(applied);
  el('kpi-directives-note').textContent = `of ${notes} operator note${notes === 1 ? '' : 's'}`;
}

function renderScene(rows, compiled) {
  const tariffs = rows.map((row) => row.tariff_bdt_per_kwh);
  const maxEnergy = Math.max(
    ...rows.map((row) => row.grid_kwh + row.solar_used_kwh + row.discharge_kwh),
    ...rows.map((row) => row.demand_kwh),
    1
  );
  let peakHour = 0;
  rows.forEach((row, index) => {
    if (row.grid_kwh > rows[peakHour].grid_kwh) peakHour = index;
  });

  el('tariff-range').textContent = `৳${formatNumber(Math.min(...tariffs))} → ৳${formatNumber(Math.max(...tariffs))}`;
  el('directive-hint').textContent = compiled.touched.size
    ? `Outlined tiles: ${compiled.touched.size} hour(s) a directive touches.`
    : 'No directive touches any hour in this scenario.';

  scene.setData({
    hours: rows,
    maxEnergy,
    peakHour,
    tariffRange: { min: Math.min(...tariffs), max: Math.max(...tariffs) },
    directiveHours: [...compiled.touched],
  });
}

/** Notes run to three lines or more; a fixed-height box would clip them. */
function autoGrow(textarea) {
  textarea.style.height = 'auto';
  textarea.style.height = `${textarea.scrollHeight + 2}px`;
}

function renderDirectives(interpretations) {
  const list = el('directives');
  list.textContent = '';
  const grow = [];

  state.notes.forEach((note, index) => {
    const entry = (interpretations ?? []).find((item) => item.note_index === index);
    const item = document.createElement('li');
    item.className = 'note-card';

    const head = document.createElement('div');
    head.className = 'note-head';

    const tag = document.createElement('span');
    tag.className = 'tag';
    const applies = Boolean(entry?.applies);
    tag.dataset.applies = String(applies);
    const dot = document.createElement('span');
    dot.className = 'dot';
    tag.append(dot, document.createTextNode(entry?.directive_type ?? 'not interpreted'));

    const idx = document.createElement('span');
    idx.className = 'note-index';
    idx.textContent = `note ${index}`;
    head.append(tag, idx);

    const textarea = document.createElement('textarea');
    textarea.value = note;
    textarea.setAttribute('aria-label', `Operator note ${index + 1}`);
    textarea.addEventListener('input', () => {
      state.notes[index] = textarea.value;
      autoGrow(textarea);
      el('run-status').textContent = 'Notes edited — run the optimization to refresh.';
      delete el('run-status').dataset.state;
    });
    grow.push(textarea);

    item.append(head, textarea);

    if (entry?.explanation) {
      const explain = document.createElement('p');
      explain.className = 'explain';
      explain.textContent = entry.explanation;
      item.append(explain);
    }
    if (entry?.structured_adjustment) {
      const pre = document.createElement('pre');
      pre.className = 'adjust';
      pre.textContent = JSON.stringify(entry.structured_adjustment);
      item.append(pre);
    }

    list.append(item);
  });

  // scrollHeight only means anything once the nodes are laid out.
  requestAnimationFrame(() => grow.forEach(autoGrow));
}

/* ------------------------------------------------------------- SoC facet */

let lastSocWidth = 0;

function renderSoc() {
  const host = el('soc-chart');
  host.textContent = '';
  const rows = state.rows;
  if (!rows) return;

  const battery = state.input.battery;
  // Draw at the container's real pixel width rather than scaling a fixed viewBox,
  // so the chart never grows absurdly tall on a wide screen and the label text
  // stays the same size at every width.
  const width = Math.max(Math.round(host.clientWidth) || 720, 300);
  lastSocWidth = width;
  const height = width < 520 ? 200 : 250;
  const margin = { top: 18, right: 58, bottom: 28, left: 52 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;

  const yMax = Math.max(battery.capacity_kwh, ...rows.map((row) => row.battery_energy_after_kwh)) * 1.04;
  const x = (index) => margin.left + ((index + 1) / HOURS) * plotW;
  const y = (value) => margin.top + plotH - (value / yMax) * plotH;

  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute(
    'aria-label',
    'Battery stored energy at the end of each hour. Exact values are in the table view below.'
  );

  const add = (name, attrs, parent = svg) => {
    const node = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    parent.append(node);
    return node;
  };

  // Gridlines — solid hairlines, one shade off the surface.
  for (const tick of niceTicks(yMax, 4)) {
    add('line', {
      x1: margin.left,
      x2: margin.left + plotW,
      y1: y(tick),
      y2: y(tick),
      stroke: 'var(--hairline)',
      'stroke-width': 1,
    });
    add('text', {
      x: margin.left - 9,
      y: y(tick) + 4,
      fill: 'var(--muted)',
      'font-size': 11,
      'text-anchor': 'end',
      style: 'font-variant-numeric: tabular-nums',
    }).textContent = formatNumber(tick);
  }

  // The forbidden band below the active reserve, stepped where a directive raises it.
  let reservePath = `M ${margin.left} ${margin.top + plotH}`;
  rows.forEach((row, index) => {
    const left = index === 0 ? margin.left : x(index - 1);
    reservePath += ` L ${left} ${y(row.min_energy_kwh)} L ${x(index)} ${y(row.min_energy_kwh)}`;
  });
  reservePath += ` L ${margin.left + plotW} ${margin.top + plotH} Z`;
  add('path', { d: reservePath, fill: 'var(--hairline)', stroke: 'none' });
  add('path', {
    d: reservePath,
    fill: 'none',
    stroke: 'var(--baseline)',
    'stroke-width': 1,
  });

  // Capacity ceiling.
  add('line', {
    x1: margin.left,
    x2: margin.left + plotW,
    y1: y(battery.capacity_kwh),
    y2: y(battery.capacity_kwh),
    stroke: 'var(--baseline)',
    'stroke-width': 1,
  });
  add('text', {
    x: margin.left + plotW + 6,
    y: y(battery.capacity_kwh) + 4,
    fill: 'var(--muted)',
    'font-size': 11,
  }).textContent = 'capacity';

  // The series: initial level, then the level after each hour.
  const points = [[margin.left, y(battery.initial_energy_kwh)]].concat(
    rows.map((row, index) => [x(index), y(row.battery_energy_after_kwh)])
  );
  add('polyline', {
    points: points.map(([px, py]) => `${px},${py}`).join(' '),
    fill: 'none',
    stroke: 'var(--c-battery)',
    'stroke-width': 2,
    'stroke-linejoin': 'round',
    'stroke-linecap': 'round',
  });

  // Start and end markers — the pair that shows end-of-day neutrality holding.
  for (const [px, py] of [points[0], points[points.length - 1]]) {
    add('circle', { cx: px, cy: py, r: 5, fill: 'var(--c-battery)', stroke: 'var(--surface)', 'stroke-width': 2 });
  }
  add('text', {
    x: margin.left + plotW + 6,
    y: y(rows[rows.length - 1].battery_energy_after_kwh) + 4,
    fill: 'var(--ink)',
    'font-size': 11,
    'font-weight': 600,
    style: 'font-variant-numeric: tabular-nums',
  }).textContent = `${formatNumber(rows[rows.length - 1].battery_energy_after_kwh)} kWh`;

  // Hour axis.
  for (let hour = 0; hour < HOURS; hour += 3) {
    add('text', {
      x: x(hour),
      y: margin.top + plotH + 18,
      fill: 'var(--muted)',
      'font-size': 11,
      'text-anchor': 'middle',
      style: 'font-variant-numeric: tabular-nums',
    }).textContent = String(hour).padStart(2, '0');
  }
  add('line', {
    x1: margin.left,
    x2: margin.left + plotW,
    y1: margin.top + plotH,
    y2: margin.top + plotH,
    stroke: 'var(--baseline)',
    'stroke-width': 1,
  });

  // Crosshair for the hour selected anywhere on the dashboard.
  if (state.selected != null) {
    add('line', {
      x1: x(state.selected),
      x2: x(state.selected),
      y1: margin.top,
      y2: margin.top + plotH,
      stroke: 'var(--muted)',
      'stroke-width': 1,
    });
    add('circle', {
      cx: x(state.selected),
      cy: y(rows[state.selected].battery_energy_after_kwh),
      r: 5,
      fill: 'var(--c-battery)',
      stroke: 'var(--surface)',
      'stroke-width': 2,
    });
  }

  // A generous hit band per hour, well past the 24px minimum.
  rows.forEach((row, index) => {
    const band = add('rect', {
      x: x(index) - plotW / HOURS / 2,
      y: margin.top,
      width: plotW / HOURS,
      height: plotH,
      fill: 'transparent',
      style: 'cursor: pointer',
    });
    band.addEventListener('pointerenter', () => showTipForHour(index, null));
    band.addEventListener('pointerleave', () => hideTip());
    band.addEventListener('click', () => scene.setSelected(index));
  });

  host.append(svg);
}

/* ------------------------------------------------------------ table view */

function renderTable(rows) {
  const wrap = el('table-wrap');
  wrap.textContent = '';

  const table = document.createElement('table');
  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  const columns = [
    'Hour',
    'Tariff ৳',
    'Demand kWh',
    'Effective solar',
    'Grid kWh',
    'Solar used',
    'Battery action',
    'Battery kWh',
    'Energy after',
    'Reserve floor',
  ];
  for (const column of columns) {
    const th = document.createElement('th');
    th.scope = 'col';
    th.textContent = column;
    headRow.append(th);
  }
  head.append(headRow);

  const body = document.createElement('tbody');
  rows.forEach((row, index) => {
    const tr = document.createElement('tr');
    tr.setAttribute('aria-selected', String(state.selected === index));
    tr.addEventListener('click', () => scene.setSelected(index));
    const values = [
      String(row.hour).padStart(2, '0'),
      formatNumber(row.tariff_bdt_per_kwh),
      formatNumber(row.demand_kwh),
      formatNumber(row.effective_solar_kwh),
      formatNumber(row.grid_kwh),
      formatNumber(row.solar_used_kwh),
      row.battery_action,
      formatNumber(row.battery_kwh),
      formatNumber(row.battery_energy_after_kwh),
      formatNumber(row.min_energy_kwh),
    ];
    values.forEach((value, column) => {
      const cell = document.createElement(column === 0 ? 'th' : 'td');
      if (column === 0) cell.scope = 'row';
      cell.textContent = value;
      tr.append(cell);
    });
    body.append(tr);
  });

  table.append(head, body);
  wrap.append(table);
}

/* ----------------------------------------------------------- hour detail */

function renderDetail(index) {
  const host = el('hour-detail');
  host.textContent = '';

  if (index == null || !state.rows) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'No hour selected.';
    host.append(empty);
    return;
  }

  const row = state.rows[index];
  const title = document.createElement('h3');
  title.style.margin = '0 0 10px';
  title.style.fontSize = '13px';
  title.textContent = `Hour ${String(row.hour).padStart(2, '0')}:00 — ${String((row.hour + 1) % 24).padStart(2, '0')}:00`;

  const list = document.createElement('dl');
  const addRow = (label, value, swatch) => {
    const dt = document.createElement('dt');
    if (swatch) {
      const chip = document.createElement('span');
      chip.className = `swatch ${swatch}`;
      dt.append(chip);
    }
    dt.append(document.createTextNode(label));
    const dd = document.createElement('dd');
    dd.textContent = value;
    list.append(dt, dd);
  };
  const rule = () => {
    const line = document.createElement('div');
    line.className = 'rule';
    list.append(line);
  };

  addRow('Demand', `${formatNumber(row.demand_kwh)} kWh`);
  addRow('Tariff', `৳${formatNumber(row.tariff_bdt_per_kwh)} / kWh`);
  rule();
  addRow('Grid import', `${formatNumber(row.grid_kwh)} kWh`, 'grid');
  addRow('Solar used', `${formatNumber(row.solar_used_kwh)} kWh`, 'solar');
  addRow(
    row.battery_action === 'charge' ? 'Battery charge' : 'Battery discharge',
    `${formatNumber(row.battery_kwh)} kWh`,
    'battery'
  );
  rule();
  addRow('Hour cost', `৳${formatNumber(row.grid_kwh * row.tariff_bdt_per_kwh)}`);
  addRow('Solar forecast', `${formatNumber(row.solar_kwh)} kWh`);
  if (Math.abs(row.effective_solar_kwh - row.solar_kwh) > TOLERANCE) {
    addRow('Effective solar', `${formatNumber(row.effective_solar_kwh)} kWh`);
  }
  addRow('Stored after', `${formatNumber(row.battery_energy_after_kwh)} kWh`);
  addRow('Reserve floor', `${formatNumber(row.min_energy_kwh)} kWh`);
  if (Number.isFinite(row.max_grid_kwh)) {
    addRow('Grid cap', `${formatNumber(row.max_grid_kwh)} kWh`);
  }
  if (!row.charge_allowed) addRow('Charging', 'blocked by directive');
  if (!row.discharge_allowed) addRow('Discharging', 'blocked by directive');

  host.append(title, list);
}

/* --------------------------------------------------------------- tooltip */

function showTipForHour(index, position) {
  const row = state.rows?.[index];
  if (!row) return;
  const tip = el('tip');
  tip.textContent = '';

  const title = document.createElement('h4');
  title.textContent = `Hour ${String(row.hour).padStart(2, '0')} · ৳${formatNumber(row.tariff_bdt_per_kwh)}/kWh`;
  const list = document.createElement('dl');
  const line = (label, value, swatch) => {
    const dt = document.createElement('dt');
    if (swatch) {
      const chip = document.createElement('span');
      chip.className = `swatch ${swatch}`;
      dt.append(chip);
    }
    dt.append(document.createTextNode(label));
    const dd = document.createElement('dd');
    dd.textContent = value;
    list.append(dt, dd);
  };
  line('Grid', `${formatNumber(row.grid_kwh)} kWh`, 'grid');
  line('Solar', `${formatNumber(row.solar_used_kwh)} kWh`, 'solar');
  if (row.discharge_kwh > 0) line('Discharge', `${formatNumber(row.discharge_kwh)} kWh`, 'battery');
  if (row.charge_kwh > 0) line('Charge', `${formatNumber(row.charge_kwh)} kWh`, 'battery');
  line('Demand', `${formatNumber(row.demand_kwh)} kWh`);
  line('Stored', `${formatNumber(row.battery_energy_after_kwh)} kWh`);

  tip.append(title, list);
  tip.hidden = false;

  const point = position ?? lastPointer;
  const rect = tip.getBoundingClientRect();
  const left = clamp(point.x + 16, 8, window.innerWidth - rect.width - 8);
  const top = clamp(point.y - rect.height - 12, 8, window.innerHeight - rect.height - 8);
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function hideTip() {
  el('tip').hidden = true;
}

let lastPointer = { x: 0, y: 0 };
window.addEventListener('pointermove', (event) => {
  lastPointer = { x: event.clientX, y: event.clientY };
});

/* ----------------------------------------------------------------- data */

async function checkHealth() {
  const pill = el('api-pill');
  const text = el('api-pill-text');
  try {
    const response = await fetch('/health', { cache: 'no-store' });
    const body = await response.json();
    if (response.ok && body.status === 'ok') {
      pill.dataset.state = 'ok';
      text.textContent = 'API healthy';
      return;
    }
    throw new Error('unhealthy');
  } catch {
    pill.dataset.state = 'down';
    text.textContent = 'API unreachable';
  }
}

function requestBody() {
  return {
    ...state.input,
    operator_notes: state.notes.map((note) => note.trim()).filter(Boolean),
  };
}

async function runOptimization() {
  const button = el('run');
  const status = el('run-status');
  if (!state.input) {
    status.dataset.state = 'error';
    status.textContent = 'No scenario loaded — paste one into “Custom scenario JSON” first.';
    return;
  }
  button.disabled = true;
  document.querySelector('.grid').classList.add('is-loading');
  status.textContent = 'Optimizing…';
  delete status.dataset.state;

  const started = performance.now();
  try {
    const response = await fetch('/optimize-energy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody()),
    });
    const body = await response.json();
    if (!response.ok) {
      status.dataset.state = 'error';
      status.textContent = `HTTP ${response.status} — ${body.detail ?? 'request rejected'}`;
      return;
    }
    state.input = { ...state.input, operator_notes: requestBody().operator_notes };
    state.notes = [...state.input.operator_notes];
    const elapsed = Math.round(performance.now() - started);
    state.response = body;
    render(); // rewrites the status line with the replay result
    if (!status.dataset.state) status.textContent = `${status.textContent} (${elapsed} ms)`;
  } catch (error) {
    status.dataset.state = 'error';
    status.textContent = `Request failed — ${error.message}`;
  } finally {
    button.disabled = false;
    document.querySelector('.grid').classList.remove('is-loading');
  }
}

/* ---------------------------------------------------------------- wiring */

function wire() {
  scene = createScene(el('scene'), {
    onHover(index, position) {
      if (index == null) hideTip();
      else showTipForHour(index, position);
    },
    onSelect(index) {
      state.selected = index;
      renderDetail(index);
      renderSoc();
      for (const row of el('table-wrap').querySelectorAll('tbody tr')) {
        row.setAttribute('aria-selected', String(Number(row.firstChild.textContent) === index));
      }
    },
  });

  el('run').addEventListener('click', () => runOptimization());

  for (const button of document.querySelectorAll('[data-view]')) {
    button.addEventListener('click', () => scene.setView(button.dataset.view));
  }

  let emphasis = null;
  for (const button of el('legend').querySelectorAll('button')) {
    button.addEventListener('click', () => {
      emphasis = emphasis === button.dataset.series ? null : button.dataset.series;
      for (const other of el('legend').querySelectorAll('button')) {
        other.setAttribute('aria-pressed', String(other.dataset.series === emphasis));
      }
      scene.setEmphasis(emphasis);
    });
  }

  el('add-note').addEventListener('click', () => {
    if (state.notes.length >= 3) return;
    state.notes.push('');
    renderDirectives(state.response?.directive_interpretation);
  });

  el('reset-notes').addEventListener('click', () => {
    state.notes = [...state.baseNotes];
    renderDirectives(state.response?.directive_interpretation);
  });

  el('table-toggle').addEventListener('click', () => {
    const wrap = el('table-wrap');
    const showing = wrap.hidden;
    wrap.hidden = !showing;
    el('table-toggle').textContent = showing ? 'Hide table' : 'Show table';
    el('table-toggle').setAttribute('aria-expanded', String(showing));
  });

  el('apply-json').addEventListener('click', async () => {
    const message = el('custom-msg');
    try {
      const parsed = JSON.parse(el('custom-json').value);
      state.input = parsed;
      state.notes = [...(parsed.operator_notes ?? [])];
      state.baseNotes = [...state.notes];
      state.selected = null;
      delete message.dataset.state;
      message.textContent = 'Applied.';
      await runOptimization();
    } catch (error) {
      message.dataset.state = 'error';
      message.textContent = `Not valid JSON — ${error.message}`;
    }
  });

  el('copy-response').addEventListener('click', async () => {
    const message = el('custom-msg');
    if (!state.response) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(state.response, null, 2));
      delete message.dataset.state;
      message.textContent = 'Response copied.';
    } catch {
      message.dataset.state = 'error';
      message.textContent = 'Clipboard unavailable in this browser.';
    }
  });

  // Re-draw the SoC chart at the new pixel width. Guarded on the width so the
  // re-render, which replaces the host's children, cannot feed itself.
  new ResizeObserver(() => {
    const host = el('soc-chart');
    if (state.rows && Math.round(host.clientWidth) !== lastSocWidth) renderSoc();
  }).observe(el('soc-chart'));

  window
    .matchMedia('(prefers-color-scheme: dark)')
    .addEventListener('change', () => {
      updateThemeIcon();
      scene?.refreshTheme();
      if (state.response) renderSoc();
    });
}

/* ------------------------------------------------------------------ boot */

async function boot() {
  applyStoredTheme();
  wire();
  await checkHealth();
}

boot();
