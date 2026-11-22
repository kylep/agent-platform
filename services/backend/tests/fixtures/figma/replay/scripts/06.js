// Build Play (green light, red light result, idle) and Settings screens; tint cards so the state colour reads clearly
const V = {};
for (const v of await figma.variables.getLocalVariablesAsync('COLOR')) V[v.name] = v;
const P = (n, op) => { const p = figma.variables.setBoundVariableForPaint({ type: 'SOLID', color: { r: 0, g: 0, b: 0 } }, 'color', V[n]); return op == null ? p : { ...p, opacity: op }; };
const F = { d: { family: 'Fredoka', style: 'SemiBold' }, dm: { family: 'Fredoka', style: 'Medium' }, b: { family: 'Nunito', style: 'Bold' }, bx: { family: 'Nunito', style: 'ExtraBold' }, mono: { family: 'JetBrains Mono', style: 'Bold' } };
await Promise.all(Object.values(F).map(f => figma.loadFontAsync(f)));
const CARD = () => P('ui/card', 0.78);
// 1) tint existing cards + nav on home screens
const mutated = [];
for (const fr of figma.currentPage.children.filter(n => n.type === 'FRAME')) {
  for (const c of fr.children) {
    if (c.type === 'FRAME' && c.name.startsWith('Widget/')) { c.fills = [CARD()]; mutated.push(c.id); }
    if (c.type === 'INSTANCE') { c.fills = [P('ui/nav', 0.55)]; mutated.push(c.id); }
  }
}
const navs = { play: await figma.getNodeByIdAsync('2:67'), gear: await figma.getNodeByIdAsync('2:88') };
const created = [];
const T = (parent, s, o = {}) => {
  const t = figma.createText(); t.fontName = F[o.f || 'b']; t.characters = s; t.fontSize = o.size || 14; t.fills = [P(o.c || 'ui/text')];
  if (o.ls) t.letterSpacing = { unit: 'PERCENT', value: o.ls };
  if (o.w) { t.textAutoResize = 'HEIGHT'; t.resize(o.w, t.height); t.textAlignHorizontal = o.align || 'LEFT'; }
  parent.appendChild(t); t.x = o.x || 0; t.y = o.y || 0; return t;
};
const card = (parent, name, x, w, y = 6, h = 308) => { const c = figma.createFrame(); c.name = name; c.resize(w, h); c.x = x; c.y = y; c.cornerRadius = 16; c.fills = [CARD()]; c.clipsContent = true; parent.appendChild(c); return c; };
const screen = (name, state, col, row, nav) => {
  const f = figma.createFrame(); f.name = name; f.resize(480, 320); f.x = col * 560; f.y = row * 440; f.fills = [P(state)]; f.clipsContent = true;
  const n = navs[nav].createInstance(); f.appendChild(n); n.x = 0; n.y = 0; n.fills = [P('ui/nav', 0.55)];
  const cap = figma.createText(); cap.fontName = F.b; cap.characters = name; cap.fontSize = 16; cap.fills = [{ type: 'SOLID', color: { r: 0.25, g: 0.27, b: 0.3 } }]; cap.x = f.x; cap.y = f.y - 30;
  created.push(f.id, cap.id); return f;
};
const svg = (parent, s, x, y, name) => { const n = figma.createNodeFromSvg(s); n.name = name; parent.appendChild(n); n.x = x; n.y = y; return n; };
const ell = (parent, w, h, x, y, color, name, op) => { const e = figma.createEllipse(); e.resize(w, h); e.fills = [P(color, op)]; e.name = name; parent.appendChild(e); e.x = x; e.y = y; return e; };
const pill = (parent, text, color, x, y, o = {}) => {
  const b = figma.createAutoLayout('HORIZONTAL', { name: o.name || 'Pill', paddingLeft: o.px || 12, paddingRight: o.px || 12, paddingTop: o.py || 6, paddingBottom: o.py || 6, cornerRadius: o.r || 12 });
  b.fills = [P(color, o.op)]; parent.appendChild(b); b.x = x; b.y = y;
  const t = figma.createText(); t.fontName = F[o.f || 'bx']; t.characters = text; t.fontSize = o.size || 13; t.fills = [P(o.tc || 'ui/text')]; b.appendChild(t); return b;
};
// three-sector wedge for the game
const sectors = (c, cx, cy, r, target, mark) => {
  const s = a => Math.sin(a * Math.PI / 180), k = a => Math.cos(a * Math.PI / 180);
  const pt = (rr, a) => `${(r + rr * s(a)).toFixed(1)} ${(r - rr * k(a)).toFixed(1)}`;
  let body = '';
  [[-60, -20], [-20, 20], [20, 60]].forEach(([a0, a1], i) => {
    const on = i === target;
    body += `<path d="M${r} ${r} L${pt(r, a0)} A${r} ${r} 0 0 1 ${pt(r, a1)} Z" fill="${on ? mark : '#1B2029'}" fill-opacity="${on ? 0.35 : 1}" stroke="${on ? mark : '#2C3440'}" stroke-width="${on ? 3 : 1.5}"/>`;
  });
  return svg(c, `<svg width="${2 * r}" height="${r}" viewBox="0 0 ${2 * r} ${r}" fill="none" xmlns="http://www.w3.org/2000/svg">${body}</svg>`, cx - r, cy - r, 'Game zones');
};
const gameCard = (f, phase) => {
  const c = card(f, 'Widget/Red light green light (2-wide)', 50, 424);
  const green = phase !== 'red';
  T(c, 'RED LIGHT, GREEN LIGHT', { f: 'bx', size: 11, c: 'ui/muted', ls: 12, x: 16, y: 14 });
  pill(c, '✓ 4', 'ui/good', 300, 10, { op: 0.25, tc: 'ui/good', size: 14, name: 'Score right' });
  pill(c, '✗ 1', 'ui/bad', 356, 10, { op: 0.25, tc: 'ui/bad', size: 14, name: 'Score wrong' });
  // light
  ell(c, 92, 92, 24, 48, green ? 'ui/good' : 'ui/bad', 'Light glow', 0.25);
  ell(c, 70, 70, 35, 59, green ? 'ui/good' : 'ui/bad', 'Light');
  T(c, green ? 'GREEN\nLIGHT' : 'RED\nLIGHT!', { f: 'd', size: 30, x: 130, y: 52 });
  T(c, green ? 'Get to the MIDDLE\nbefore it turns red!' : 'Freeze! Checking…', { f: 'bx', size: 16, c: green ? 'ui/text' : 'ui/text', x: 130, y: 124 });
  const cx = 300, cy = 290, r = 110;
  sectors(c, cx, cy, r, 1, green ? '#3DDC84' : '#FFD447');
  T(c, 'LEFT', { f: 'bx', size: 11, c: 'ui/muted', ls: 8, x: cx - 108, y: cy - 82 }); T(c, 'MIDDLE', { f: 'bx', size: 11, c: 'ui/text', ls: 8, x: cx - 22, y: cy - 128 }); T(c, 'RIGHT', { f: 'bx', size: 11, c: 'ui/muted', ls: 8, x: cx + 74, y: cy - 82 });
  const people = [[cx - 12, cy - 60, 'ui/p1', true], [cx + 62, cy - 40, 'ui/p2', false]];
  for (const [x, y, col, inZone] of people) {
    if (!green) ell(c, 26, 26, x - 13, y - 13, inZone ? 'ui/good' : 'ui/bad', 'Result ring', 0.9);
    ell(c, 16, 16, x - 8, y - 8, col, 'Kid');
  }
  if (!green) { T(c, '✓', { f: 'd', size: 18, c: 'ui/good', x: cx - 4, y: cy - 92 }); T(c, '✗', { f: 'd', size: 18, c: 'ui/bad', x: cx + 70, y: cy - 72 }); }
  const robot = figma.createRectangle(); robot.resize(18, 6); robot.cornerRadius = 3; robot.fills = [P('ui/text')]; c.appendChild(robot); robot.x = cx - 9; robot.y = cy - 2;
  pill(c, 'Tap to stop', 'ui/card-inner', 16, 266, { f: 'b', tc: 'ui/muted', size: 13 });
  return c;
};
let f;
f = screen('Play · Green light (move!)', 'state/free', 0, 2, 'play'); gameCard(f, 'green');
f = screen('Play · Red light (freeze + score)', 'state/free', 1, 2, 'play'); gameCard(f, 'red');
// idle / start
f = screen('Play · Tap to start', 'state/free', 2, 2, 'play');
{ const c = card(f, 'Widget/Red light green light (2-wide)', 50, 424);
  T(c, 'RED LIGHT, GREEN LIGHT', { f: 'bx', size: 11, c: 'ui/muted', ls: 12, x: 16, y: 14 });
  ell(c, 70, 70, 120, 70, 'ui/bad', 'Red'); ell(c, 70, 70, 234, 70, 'ui/good', 'Green');
  T(c, 'Tap to play!', { f: 'd', size: 34, x: 0, y: 160, w: 424, align: 'CENTER' });
  T(c, 'Move on green. When it turns red,\nbe standing in the zone it showed.', { f: 'b', size: 15, c: 'ui/muted', x: 0, y: 210, w: 424, align: 'CENTER' });
  T(c, 'Last game: ✓ 4  ✗ 1', { f: 'b', size: 13, c: 'ui/muted', x: 0, y: 274, w: 424, align: 'CENTER' }); }
// settings
f = screen('Settings', 'state/free', 0, 3, 'gear');
const row = (c, y, k, v, o = {}) => {
  T(c, k, { f: 'b', size: 15, x: 16, y: y + 4 });
  if (o.toggle != null) {
    const tr = figma.createFrame(); tr.name = 'Toggle'; tr.resize(44, 26); tr.cornerRadius = 13; tr.fills = [P(o.toggle ? 'ui/good' : 'ui/card-inner')]; c.appendChild(tr); tr.x = c.width - 60; tr.y = y;
    ell(tr, 20, 20, o.toggle ? 21 : 3, 3, 'ui/text', 'Knob');
  } else if (v) { const p = pill(c, v + '  ›', 'ui/card-inner', 0, y - 2, { f: 'b', size: 14, name: 'Picker' }); p.x = c.width - 16 - p.width; }
};
const slider = (c, y, k, pct) => {
  T(c, k, { f: 'b', size: 15, x: 16, y }); T(c, pct + '%', { f: 'b', size: 14, c: 'ui/muted', x: 16, y, w: c.width - 32, align: 'RIGHT' });
  const w = c.width - 32, tr = figma.createRectangle(); tr.resize(w, 8); tr.cornerRadius = 4; tr.fills = [P('ui/card-inner')]; c.appendChild(tr); tr.x = 16; tr.y = y + 28;
  const fl = figma.createRectangle(); fl.resize(w * pct / 100, 8); fl.cornerRadius = 4; fl.fills = [P('ui/text')]; c.appendChild(fl); fl.x = 16; fl.y = y + 28;
  ell(c, 22, 22, 16 + w * pct / 100 - 11, y + 21, 'ui/text', 'Thumb');
};
{ const c = card(f, 'Settings/Screen', 50, 209);
  T(c, 'SCREEN', { f: 'bx', size: 11, c: 'ui/muted', ls: 12, x: 16, y: 14 });
  row(c, 40, 'Left', 'Eyes'); row(c, 80, 'Right', 'Meeting');
  T(c, 'Tap to cycle: Eyes, Meeting,\nRadar, Desk timer, Game.\nSome take both columns.', { f: 'b', size: 12, c: 'ui/muted', x: 16, y: 116 });
  const ln = figma.createRectangle(); ln.resize(177, 1); ln.fills = [P('ui/line')]; c.appendChild(ln); ln.x = 16; ln.y = 176;
  row(c, 188, 'Eyes', null, { toggle: true }); row(c, 226, 'Meeting colours', null, { toggle: true });
  T(c, 'Rotate screen · later', { f: 'b', size: 12, c: 'ui/muted', x: 16, y: 274 }); }
{ const c = card(f, 'Settings/Sound', 265, 209);
  T(c, 'SOUND & LIGHT', { f: 'bx', size: 11, c: 'ui/muted', ls: 12, x: 16, y: 14 });
  slider(c, 40, 'Volume', 60); slider(c, 100, 'Brightness', 80);
  T(c, 'Tone', { f: 'b', size: 15, x: 16, y: 160 });
  const tones = [['Chirp', true], ['Bloop', false], ['Retro', false], ['Bell', false]];
  tones.forEach(([n, on], i) => pill(c, n, on ? 'ui/text' : 'ui/card-inner', 16 + (i % 2) * 90, 188 + Math.floor(i / 2) * 36, { f: 'b', size: 14, tc: on ? 'ui/pupil' : 'ui/text', name: 'Tone ' + n }));
  pill(c, '▶  Test sound', 'ui/card-inner', 16, 266, { f: 'b', size: 13, tc: 'ui/muted', name: 'Test' }); }
return { created, mutatedCount: mutated.length };