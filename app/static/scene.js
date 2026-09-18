/**
 * A small hand-rolled 3D renderer on a 2D canvas.
 *
 * No CDN, no WebGL: the dashboard has to work on a laptop with no internet at a
 * hackathon table. Boxes are convex and opaque, so a painter's sort over all six
 * faces is exact — no backface culling needed. Translucent faces draw in a second
 * pass on top.
 */

export const VIEWS = {
  orbit: { yaw: -0.46, pitch: 0.48 },
  front: { yaw: 0.0, pitch: 0.12 },
  top: { yaw: -0.0001, pitch: 1.38 },
};

// Tallest bar, in world units. A 24-wide row swept by yaw eats vertical space, so
// keeping the stack short is what lets the scene fill a wide canvas.
const BAR_MAX = 8;

const HOURS = 24;
const SLOT = 1.0; // world units per hour
const BAR_W = 0.58; // cap the bar; the leftover slot width is air
const BAR_D = 0.58;
const TILE_W = 0.92;
const TILE_D = 1.7;
const SEG_GAP = 0.035; // the surface gap, in world units
const FLOOR_Y = -0.2;
const TILE_H = 0.07; // thin, so the tariff-tinted top face is what you see

// Room for the axis labels, which sit outside the geometry's own bounds.
const PAD = { left: 52, right: 22, top: 30, bottom: 36 };

// Per-face brightness. Flat shading reads as solid geometry without a light model.
const SHADE = { top: 1.1, bottom: 0.45, east: 0.86, west: 0.66, north: 0.8, south: 0.58 };

/* ------------------------------------------------------------------ colour */

function hexToRgb(hex) {
  const value = hex.trim().replace('#', '');
  const full = value.length === 3 ? value.split('').map((c) => c + c).join('') : value;
  const n = parseInt(full, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function shade(rgb, factor) {
  const mix = (c) =>
    factor <= 1
      ? Math.round(c * factor)
      : Math.round(c + (255 - c) * (factor - 1));
  return `rgb(${mix(rgb.r)},${mix(rgb.g)},${mix(rgb.b)})`;
}

function rgba(rgb, alpha) {
  return `rgba(${rgb.r},${rgb.g},${rgb.b},${alpha})`;
}

/** Mix two colours; t = 0 returns a, t = 1 returns b. */
function mixRgb(a, b, t) {
  return {
    r: Math.round(a.r + (b.r - a.r) * t),
    g: Math.round(a.g + (b.g - a.g) * t),
    b: Math.round(a.b + (b.b - a.b) * t),
  };
}

/* ------------------------------------------------------------- projection */

function rotate(p, center, cam) {
  const x = p.x - center.x;
  const y = p.y - center.y;
  const z = p.z - center.z;

  const cy = Math.cos(cam.yaw);
  const sy = Math.sin(cam.yaw);
  const x1 = x * cy - z * sy;
  const z1 = x * sy + z * cy;

  const cp = Math.cos(cam.pitch);
  const sp = Math.sin(cam.pitch);
  const y1 = y * cp - z1 * sp;
  const z2 = y * sp + z1 * cp;

  return { x: x1, y: y1, z: z2 + cam.dist };
}

/**
 * Fit the scene's bounding box into the canvas.
 *
 * Screen position is `offset + (x / z) * scale`, linear in scale, so fitting is
 * one division rather than a search. Using the projected min/max — rather than
 * the largest absolute value — centres on the content the viewer actually sees,
 * which matters because a rotated box is not symmetric about the origin.
 */
function fitView(bbox, center, cam, width, height) {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;

  for (const corner of bboxCorners(bbox)) {
    const r = rotate(corner, center, cam);
    if (r.z <= 0.05) continue;
    const px = r.x / r.z;
    const py = r.y / r.z;
    minX = Math.min(minX, px);
    maxX = Math.max(maxX, px);
    minY = Math.min(minY, py);
    maxY = Math.max(maxY, py);
  }
  if (!Number.isFinite(minX)) return { scale: 1, cx: 0, cy: 0, boxW: width, boxH: height };

  const boxW = Math.max(width - PAD.left - PAD.right, 40);
  const boxH = Math.max(height - PAD.top - PAD.bottom, 40);
  return {
    scale: Math.min(boxW / Math.max(maxX - minX, 1e-6), boxH / Math.max(maxY - minY, 1e-6)),
    cx: (minX + maxX) / 2,
    cy: (minY + maxY) / 2,
    boxW,
    boxH,
  };
}

function bboxCorners(b) {
  const out = [];
  for (const x of [b.minX, b.maxX]) {
    for (const y of [b.minY, b.maxY]) {
      for (const z of [b.minZ, b.maxZ]) out.push({ x, y, z });
    }
  }
  return out;
}

/* ----------------------------------------------------------------- hulls */

/** Monotone chain — gives the exact silhouette of a projected box. */
function convexHull(points) {
  if (points.length < 4) return points.slice();
  const sorted = points.slice().sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const build = (input) => {
    const stack = [];
    for (const point of input) {
      while (stack.length >= 2 && cross(stack[stack.length - 2], stack[stack.length - 1], point) <= 0) {
        stack.pop();
      }
      stack.push(point);
    }
    stack.pop();
    return stack;
  };
  return build(sorted).concat(build(sorted.slice().reverse()));
}

function pointInPolygon(px, py, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i];
    const b = polygon[j];
    if (a.y > py !== b.y > py && px < ((b.x - a.x) * (py - a.y)) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return inside;
}

/* ------------------------------------------------------------------ scene */

export function createScene(canvas, { onHover, onSelect } = {}) {
  const ctx = canvas.getContext('2d');

  const cam = { yaw: VIEWS.orbit.yaw, pitch: VIEWS.orbit.pitch, dist: 30, zoom: 1 };
  let data = null; // { hours: [...], maxEnergy, palette }
  let theme = null;
  let size = { w: 1, h: 1, dpr: 1 };
  let hovered = null;
  let selected = null;
  let emphasis = null; // 'grid' | 'solar' | 'battery' | null
  let growth = 1; // 0..1 intro animation
  let frame = null;
  let hulls = []; // per-hour screen silhouette, rebuilt every draw

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------------------------------------------------- theme read */

  function readTheme() {
    const style = getComputedStyle(canvas);
    const pick = (name, fallback) => {
      const value = style.getPropertyValue(name).trim();
      return hexToRgb(value || fallback);
    };
    theme = {
      grid: pick('--c-grid', '#2a78d6'),
      solar: pick('--c-solar', '#eda100'),
      battery: pick('--c-battery', '#1baf7a'),
      surface: pick('--surface', '#fcfcfb'),
      hairline: pick('--hairline', '#e1e0d9'),
      baseline: pick('--baseline', '#c3c2b7'),
      muted: pick('--muted', '#898781'),
      ink: pick('--ink', '#0b0b0b'),
      ink2: pick('--ink-2', '#52514e'),
      floorLow: pick('--floor-low', '#eceae4'),
      floorHigh: pick('--floor-high', '#5d5b55'),
    };
  }

  /* --------------------------------------------------------------- sizing */

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    size = { w: Math.max(rect.width, 1), h: Math.max(rect.height, 1), dpr };
    canvas.width = Math.round(size.w * dpr);
    canvas.height = Math.round(size.h * dpr);
    draw();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(canvas);

  /* -------------------------------------------------------------- geometry */

  function seriesColor(key) {
    return theme[key];
  }

  /** The stacked segments for one hour, bottom to top. */
  function segmentsFor(hour) {
    const stack = [
      { key: 'grid', value: hour.grid_kwh },
      { key: 'solar', value: hour.solar_used_kwh },
      { key: 'battery', value: hour.discharge_kwh },
    ];
    return stack.filter((segment) => segment.value > 1e-9);
  }

  function pushBox(faces, { x, y, z, w, h, d, rgb, alpha = 1, pass = 0, emphasise = 1 }) {
    if (h <= 0) return;
    const x0 = x - w / 2;
    const x1 = x + w / 2;
    const y0 = y;
    const y1 = y + h;
    const z0 = z - d / 2;
    const z1 = z + d / 2;

    const corner = (cx, cy, cz) => ({ x: cx, y: cy, z: cz });
    const quads = [
      { name: 'top', pts: [corner(x0, y1, z0), corner(x1, y1, z0), corner(x1, y1, z1), corner(x0, y1, z1)] },
      { name: 'bottom', pts: [corner(x0, y0, z0), corner(x1, y0, z0), corner(x1, y0, z1), corner(x0, y0, z1)] },
      { name: 'south', pts: [corner(x0, y0, z1), corner(x1, y0, z1), corner(x1, y1, z1), corner(x0, y1, z1)] },
      { name: 'north', pts: [corner(x0, y0, z0), corner(x1, y0, z0), corner(x1, y1, z0), corner(x0, y1, z0)] },
      { name: 'east', pts: [corner(x1, y0, z0), corner(x1, y0, z1), corner(x1, y1, z1), corner(x1, y1, z0)] },
      { name: 'west', pts: [corner(x0, y0, z0), corner(x0, y0, z1), corner(x0, y1, z1), corner(x0, y1, z0)] },
    ];

    for (const quad of quads) {
      faces.push({
        pts: quad.pts,
        fill: shade(rgb, SHADE[quad.name] * emphasise),
        alpha,
        pass,
      });
    }
  }

  function buildFaces() {
    const faces = [];
    if (!data) return faces;

    const { hours, maxEnergy, tariffRange } = data;
    const scaleY = maxEnergy > 0 ? BAR_MAX / maxEnergy : 0;

    hours.forEach((hour, index) => {
      const x = (index - (HOURS - 1) / 2) * SLOT;
      const isHot = hovered === index || selected === index;

      // Floor tile — neutral sequential ramp carrying the hour's tariff.
      const t =
        tariffRange.max > tariffRange.min
          ? (hour.tariff_bdt_per_kwh - tariffRange.min) / (tariffRange.max - tariffRange.min)
          : 0;
      const tile = mixRgb(theme.floorLow, theme.floorHigh, 0.12 + t * 0.88);
      pushBox(faces, {
        x,
        y: FLOOR_Y,
        z: 0,
        w: TILE_W,
        h: TILE_H,
        d: TILE_D,
        rgb: tile,
        emphasise: isHot ? 1.25 : 1,
      });

      // Supply stack: grid, then solar, then battery discharge.
      let y = 0;
      for (const segment of segmentsFor(hour)) {
        const h = segment.value * scaleY * growth;
        const dimmed = Boolean(emphasis) && emphasis !== segment.key;
        pushBox(faces, {
          x,
          y,
          z: 0,
          w: BAR_W,
          h: Math.max(h - SEG_GAP, 0.004),
          d: BAR_D,
          rgb: seriesColor(segment.key),
          alpha: dimmed ? 0.3 : 1,
          emphasise: isHot ? 1.16 : 1,
        });
        y += h;
      }

      // Charging shows as the part of the stack that rises above demand:
      // supply = demand + charge, so the overshoot IS the charge. Only drawn
      // where it means something, never as a plate on all 24 columns.
      if (hour.charge_kwh > 1e-9) {
        const demandY = hour.demand_kwh * scaleY * growth;
        const chargeH = hour.charge_kwh * scaleY * growth;
        pushBox(faces, {
          x,
          y: demandY,
          z: 0,
          w: BAR_W + 0.12,
          h: chargeH,
          d: BAR_D + 0.12,
          rgb: theme.battery,
          alpha: 0.22,
          pass: 1,
        });
      }
    });

    return faces;
  }

  /* ---------------------------------------------------------------- draw */

  function draw() {
    if (!theme) readTheme();
    const { w, h, dpr } = size;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!data) return;

    const bbox = {
      minX: -HOURS / 2 - 0.4,
      maxX: HOURS / 2 + 0.4,
      minY: FLOOR_Y,
      maxY: BAR_MAX + 0.5,
      minZ: -TILE_D / 2 - 0.3,
      maxZ: TILE_D / 2 + 0.3,
    };
    const center = { x: 0, y: (bbox.minY + bbox.maxY) / 2, z: 0 };

    const view = fitView(bbox, center, cam, w, h);
    const scale = view.scale * cam.zoom;
    const originX = PAD.left + view.boxW / 2 - view.cx * scale;
    const originY = PAD.top + view.boxH / 2 + view.cy * scale;

    const project = (p) => {
      const r = rotate(p, center, cam);
      const z = Math.max(r.z, 0.05);
      const s = scale / z;
      return { x: originX + r.x * s, y: originY - r.y * s, depth: z };
    };

    drawBackPlane(project);

    // Painter's algorithm: opaque pass sorted far to near, then translucent.
    const faces = buildFaces();
    const prepared = faces.map((face) => {
      const pts = face.pts.map(project);
      let depth = 0;
      for (const p of pts) depth += p.depth;
      return { ...face, pts, depth: depth / pts.length };
    });
    prepared.sort((a, b) => a.pass - b.pass || b.depth - a.depth);

    for (const face of prepared) {
      ctx.globalAlpha = face.alpha;
      ctx.beginPath();
      ctx.moveTo(face.pts[0].x, face.pts[0].y);
      for (let i = 1; i < face.pts.length; i++) ctx.lineTo(face.pts[i].x, face.pts[i].y);
      ctx.closePath();
      ctx.fillStyle = face.fill;
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    drawAnnotations(project);
    rebuildHulls(project);
  }

  function drawBackPlane(project) {
    const { maxEnergy } = data;
    const scaleY = maxEnergy > 0 ? BAR_MAX / maxEnergy : 0;
    const ticks = niceTicks(maxEnergy, 4);
    const zBack = -TILE_D / 2 - 0.3;
    const xLeft = -HOURS / 2 - 0.3;
    const xRight = HOURS / 2 + 0.3;
    const topY = Math.max(...ticks) * scaleY;

    // A wall behind the columns, so the gridlines read as lines on a surface
    // rather than as strokes floating in space.
    const wall = [
      { x: xLeft, y: 0, z: zBack },
      { x: xRight, y: 0, z: zBack },
      { x: xRight, y: topY, z: zBack },
      { x: xLeft, y: topY, z: zBack },
    ].map(project);
    ctx.beginPath();
    ctx.moveTo(wall[0].x, wall[0].y);
    for (let i = 1; i < wall.length; i++) ctx.lineTo(wall[i].x, wall[i].y);
    ctx.closePath();
    ctx.fillStyle = rgba(theme.hairline, 0.26);
    ctx.fill();

    ctx.lineWidth = 1;
    ctx.strokeStyle = shade(theme.baseline, 1);
    ctx.fillStyle = shade(theme.muted, 1);
    ctx.font = '500 11px system-ui, -apple-system, "Segoe UI", sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';

    for (const tick of ticks) {
      const y = tick * scaleY;
      const a = project({ x: xLeft, y, z: zBack });
      const b = project({ x: xRight, y, z: zBack });
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      ctx.fillText(formatNumber(tick), a.x - 8, a.y);
    }

    // The vertical axis the ticks belong to.
    const axisBottom = project({ x: xLeft, y: 0, z: zBack });
    const axisTop = project({ x: xLeft, y: topY, z: zBack });
    ctx.strokeStyle = shade(theme.baseline, 1);
    ctx.beginPath();
    ctx.moveTo(axisBottom.x, axisBottom.y);
    ctx.lineTo(axisTop.x, axisTop.y);
    ctx.stroke();

    // Hour ticks along the front edge — every third hour, so labels never collide.
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let hour = 0; hour < HOURS; hour += 3) {
      const x = (hour - (HOURS - 1) / 2) * SLOT;
      const p = project({ x, y: FLOOR_Y - 0.1, z: TILE_D / 2 + 0.15 });
      ctx.fillText(String(hour).padStart(2, '0'), p.x, p.y + 4);
    }
  }

  function drawAnnotations(project) {
    const { hours, maxEnergy, peakHour, directiveHours } = data;
    const scaleY = maxEnergy > 0 ? BAR_MAX / maxEnergy : 0;

    // Rings on hours a directive touches — position + label, never hue alone.
    ctx.strokeStyle = shade(theme.ink2, 1);
    ctx.lineWidth = 2;
    const ringY = FLOOR_Y + TILE_H + 0.005;
    for (const hour of directiveHours) {
      const x = (hour - (HOURS - 1) / 2) * SLOT;
      const corners = [
        { x: x - TILE_W / 2, y: ringY, z: -TILE_D / 2 },
        { x: x + TILE_W / 2, y: ringY, z: -TILE_D / 2 },
        { x: x + TILE_W / 2, y: ringY, z: TILE_D / 2 },
        { x: x - TILE_W / 2, y: ringY, z: TILE_D / 2 },
      ].map(project);
      ctx.beginPath();
      ctx.moveTo(corners[0].x, corners[0].y);
      for (let i = 1; i < corners.length; i++) ctx.lineTo(corners[i].x, corners[i].y);
      ctx.closePath();
      ctx.stroke();
    }

    // One direct label: the peak grid hour. Labelling every column is noise.
    if (peakHour != null && growth > 0.98) {
      const hour = hours[peakHour];
      const x = (peakHour - (HOURS - 1) / 2) * SLOT;
      const top = (hour.grid_kwh + hour.solar_used_kwh + hour.discharge_kwh) * scaleY;
      const anchor = project({ x, y: top + 0.35, z: 0 });
      const label = `peak ${formatNumber(hour.grid_kwh)} kWh`;

      ctx.font = '600 11px system-ui, -apple-system, "Segoe UI", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      const width = ctx.measureText(label).width;
      ctx.fillStyle = rgba(theme.surface, 0.88);
      roundRect(ctx, anchor.x - width / 2 - 6, anchor.y - 17, width + 12, 18, 4);
      ctx.fill();
      ctx.fillStyle = shade(theme.ink, 1);
      ctx.fillText(label, anchor.x, anchor.y - 2);
    }

    // Selection guide.
    if (selected != null) {
      const x = (selected - (HOURS - 1) / 2) * SLOT;
      const base = project({ x, y: FLOOR_Y, z: 0 });
      const tip = project({ x, y: 9.9, z: 0 });
      ctx.strokeStyle = rgba(theme.ink, 0.35);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(base.x, base.y);
      ctx.lineTo(tip.x, tip.y);
      ctx.stroke();
    }
  }

  function rebuildHulls(project) {
    const { hours, maxEnergy } = data;
    const scaleY = maxEnergy > 0 ? BAR_MAX / maxEnergy : 0;
    hulls = hours.map((hour, index) => {
      const x = (index - (HOURS - 1) / 2) * SLOT;
      const top = Math.max(
        (hour.grid_kwh + hour.solar_used_kwh + hour.discharge_kwh) * scaleY,
        0.35
      );
      const box = {
        minX: x - TILE_W / 2,
        maxX: x + TILE_W / 2,
        minY: FLOOR_Y,
        maxY: top,
        minZ: -TILE_D / 2,
        maxZ: TILE_D / 2,
      };
      const projected = bboxCorners(box).map(project);
      const depth = projected.reduce((sum, p) => sum + p.depth, 0) / projected.length;
      return { index, hull: convexHull(projected), depth };
    });
  }

  /* --------------------------------------------------------- interaction */

  function hourAt(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    let best = null;
    for (const entry of hulls) {
      if (!pointInPolygon(px, py, entry.hull)) continue;
      if (!best || entry.depth < best.depth) best = entry;
    }
    return best ? best.index : null;
  }

  let dragging = false;
  let dragMoved = false;
  let last = { x: 0, y: 0 };

  canvas.addEventListener('pointerdown', (event) => {
    dragging = true;
    dragMoved = false;
    last = { x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  });

  canvas.addEventListener('pointermove', (event) => {
    if (dragging) {
      const dx = event.clientX - last.x;
      const dy = event.clientY - last.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) dragMoved = true;
      last = { x: event.clientX, y: event.clientY };
      cam.yaw += dx * 0.008;
      cam.pitch = clamp(cam.pitch + dy * 0.006, -0.05, 1.42);
      requestDraw();
      return;
    }
    const index = hourAt(event.clientX, event.clientY);
    if (index !== hovered) {
      hovered = index;
      requestDraw();
    }
    onHover?.(index, { x: event.clientX, y: event.clientY });
  });

  const endDrag = (event) => {
    if (!dragging) return;
    dragging = false;
    try {
      canvas.releasePointerCapture(event.pointerId);
    } catch {
      /* pointer already released */
    }
    if (!dragMoved) {
      const index = hourAt(event.clientX, event.clientY);
      if (index != null) select(index);
    }
  };
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);

  canvas.addEventListener('pointerleave', () => {
    if (hovered !== null) {
      hovered = null;
      requestDraw();
    }
    onHover?.(null);
  });

  canvas.addEventListener(
    'wheel',
    (event) => {
      event.preventDefault();
      cam.zoom = clamp(cam.zoom * (event.deltaY > 0 ? 0.92 : 1.08), 0.55, 3.2);
      requestDraw();
    },
    { passive: false }
  );

  canvas.addEventListener('keydown', (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (step != null) {
      event.preventDefault();
      const next = selected == null ? (step > 0 ? 0 : HOURS - 1) : clamp(selected + step, 0, HOURS - 1);
      select(next);
      return;
    }
    if (event.key === 'Escape') select(null);
    if (event.key === 'a' || event.key === 'A') {
      cam.yaw -= 0.12;
      requestDraw();
    }
    if (event.key === 'd' || event.key === 'D') {
      cam.yaw += 0.12;
      requestDraw();
    }
  });

  function select(index) {
    selected = index;
    requestDraw();
    onSelect?.(index);
  }

  /* -------------------------------------------------------------- public */

  function requestDraw() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      draw();
    });
  }

  function animateIn() {
    if (reduceMotion) {
      growth = 1;
      requestDraw();
      return;
    }
    const started = performance.now();
    const duration = 620;
    const step = (now) => {
      const t = Math.min((now - started) / duration, 1);
      growth = 1 - Math.pow(1 - t, 3);
      draw();
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  return {
    setData(next) {
      data = next;
      selected = null;
      hovered = null;
      readTheme();
      animateIn();
    },
    setEmphasis(key) {
      emphasis = key;
      requestDraw();
    },
    setView(name) {
      const view = VIEWS[name] ?? VIEWS.orbit;
      cam.yaw = view.yaw;
      cam.pitch = view.pitch;
      cam.zoom = 1;
      requestDraw();
    },
    setSelected: select,
    getSelected: () => selected,
    refreshTheme() {
      readTheme();
      requestDraw();
    },
    destroy() {
      observer.disconnect();
    },
  };
}

/* ----------------------------------------------------------------- utils */

export function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

export function niceTicks(max, count) {
  if (!(max > 0)) return [0];
  const rough = max / count;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? magnitude * 10;
  const ticks = [];
  for (let value = 0; value <= max + step * 0.001; value += step) ticks.push(Math.round(value * 100) / 100);
  return ticks;
}

export function formatNumber(value) {
  const abs = Math.abs(value);
  const decimals = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  return Number(value.toFixed(decimals)).toLocaleString('en-US');
}

export function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
