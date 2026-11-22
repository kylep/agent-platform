// Remove partial screens, then build the six Home screens with eyes drawn directly
for (const n of [...figma.currentPage.children]) if (n.type !== 'COMPONENT') n.remove();
const V = {};
for (const v of await figma.variables.getLocalVariablesAsync('COLOR')) V[v.name] = v;
const P = n => figma.variables.setBoundVariableForPaint({ type: 'SOLID', color: { r: 0, g: 0, b: 0 } }, 'color', V[n]);
const F = { d: { family: 'Fredoka', style: 'SemiBold' }, dm: { family: 'Fredoka', style: 'Medium' }, b: { family: 'Nunito', style: 'Bold' }, bx: { family: 'Nunito', style: 'ExtraBold' }, mono: { family: 'JetBrains Mono', style: 'Bold' } };
await Promise.all(Object.values(F).map(f => figma.loadFontAsync(f)));
const navHome = await figma.getNodeByIdAsync('2:46');
const created = [];
const T = (parent, s, o = {}) => {
  const t = figma.createText(); t.fontName = F[o.f || 'b']; t.characters = s; t.fontSize = o.size || 14;
  t.fills = [P(o.c || 'ui/text')];
  if (o.ls) t.letterSpacing = { unit: 'PERCENT', value: o.ls };
  if (o.w) { t.textAutoResize = 'HEIGHT'; t.resize(o.w, t.height); t.textAlignHorizontal = o.align || 'LEFT'; }
  parent.appendChild(t); t.x = o.x || 0; t.y = o.y || 0; return t;
};
const card = (parent, name, x, w) => { const c = figma.createFrame(); c.name = name; c.resize(w, 308); c.x = x; c.y = 6; c.cornerRadius = 16; c.fills = [P('ui/card')]; c.clipsContent = true; parent.appendChild(c); return c; };
const label = (c, s, extra) => { T(c, s, { f: 'bx', size: 11, c: 'ui/muted', ls: 12, x: 16, y: 14 }); if (extra) T(c, extra, { f: 'bx', size: 11, c: 'ui/muted', ls: 4, x: 16, y: 14, w: c.width - 32, align: 'RIGHT' }); };
const screen = (name, state, col, row) => {
  const f = figma.createFrame(); f.name = name; f.resize(480, 320); f.x = col * 560; f.y = row * 440; f.fills = [P(state)]; f.clipsContent = true;
  const nav = navHome.createInstance(); f.appendChild(nav); nav.x = 0; nav.y = 0;
  const cap = figma.createText(); cap.fontName = F.b; cap.characters = name; cap.fontSize = 16; cap.fills = [{ type: 'SOLID', color: { r: 0.25, g: 0.27, b: 0.3 } }]; cap.x = f.x; cap.y = f.y - 30;
  created.push(f.id, cap.id); return f;
};
const svg = (parent, s, x, y, name) => { const n = figma.createNodeFromSvg(s); n.name = name; parent.appendChild(n); n.x = x; n.y = y; return n; };
const ell = (parent, w, h, x, y, color, name) => { const e = figma.createEllipse(); e.resize(w, h); e.fills = [P(color)]; e.name = name; parent.appendChild(e); e.x = x; e.y = y; return e; };
const eyes = (f, x, w, dx, dy, mood) => {
  const c = card(f, 'Widget/Eyes', x, w);
  const sc = w > 300 ? 1.45 : 1, ew = 78 * sc, eh = 96 * sc, gap = 22 * sc, total = ew * 2 + gap, top = w > 300 ? 54 : 84;
  for (let i = 0; i < 2; i++) {
    const g = figma.createFrame(); g.name = i ? 'Eye right' : 'Eye left'; g.fills = []; g.clipsContent = false; g.resize(ew, eh); c.appendChild(g); g.x = (w - total) / 2 + i * (ew + gap); g.y = top;
    if (mood === 'sleep') { const lid = figma.createRectangle(); lid.resize(ew, 10 * sc); lid.cornerRadius = 5 * sc; lid.fills = [P('ui/eye')]; lid.name = 'Closed lid'; g.appendChild(lid); lid.y = eh / 2; continue; }
    ell(g, ew, eh, 0, 0, 'ui/eye', 'Sclera');
    ell(g, 40 * sc, 40 * sc, (26 + dx) * sc, (34 + dy) * sc, 'ui/pupil', 'Pupil');
    ell(g, 11 * sc, 11 * sc, (47 + dx) * sc, (40 + dy) * sc, 'ui/eye', 'Glint');
  }
  if (mood === 'sleep') T(c, 'z z z', { f: 'd', size: 22, c: 'ui/muted', x: w - 70, y: 54 });
  else { const mw = 44 * sc; svg(c, `<svg width="${mw}" height="${16 * sc}" viewBox="0 0 44 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 3 Q22 18 41 3" stroke="#F7F7F2" stroke-width="4" stroke-linecap="round"/></svg>`, (w - mw) / 2, top + eh + 20, 'Smile'); }
  return c;
};
const meeting = (f, x, kind) => {
  const c = card(f, 'Widget/Meeting', x, 209);
  const A = { free: ['NEXT MEETING', '2:30', 'PM', 'in 42 min', 'Until 3:00 · 30 min', '2 more today', 'FREE', 'state/free'],
    soon: ['NEXT MEETING', '2:30', 'PM', 'in 4 min', 'Until 3:00 · 30 min', 'Get ready', 'SOON', 'state/soon'],
    now: ['MEETING', 'Now!', '', 'started 2:30', 'Until 3:00 · 30 min', '', 'STARTING', 'state/now'],
    mid: ['IN MEETING', '18', 'min left', 'ends 3:00 PM', '', 'Next: 4:15 PM', 'BUSY', 'state/in-meeting'],
    off: ['CALENDAR', 'Offline', '', 'Mac not connected', 'Shows blue until', 'it reconnects', 'OFFLINE', 'state/offline'] }[kind];
  label(c, A[0]);
  const big = T(c, A[1], { f: 'd', size: A[1].length > 4 ? 44 : 60, x: 16, y: 34 });
  if (A[2]) T(c, A[2], { f: 'dm', size: 20, c: 'ui/muted', x: 16 + big.width + 6, y: big.y + big.height - 36 });
  T(c, A[3], { f: 'bx', size: 20, x: 16, y: 116 });
  if (kind === 'mid') {
    const tr = figma.createRectangle(); tr.resize(177, 10); tr.cornerRadius = 5; tr.fills = [P('ui/card-inner')]; c.appendChild(tr); tr.x = 16; tr.y = 156;
    const fl = figma.createRectangle(); fl.resize(177 * 0.4, 10); fl.cornerRadius = 5; fl.fills = [P('ui/text')]; c.appendChild(fl); fl.x = 16; fl.y = 156;
    T(c, '2:30', { f: 'b', size: 12, c: 'ui/muted', x: 16, y: 172 }); T(c, '3:00', { f: 'b', size: 12, c: 'ui/muted', x: 16, y: 172, w: 177, align: 'RIGHT' });
  } else if (A[4]) { const ln = figma.createRectangle(); ln.resize(177, 1); ln.fills = [P('ui/line')]; c.appendChild(ln); ln.x = 16; ln.y = 152; T(c, A[4], { f: 'b', size: 15, c: 'ui/muted', x: 16, y: 164 }); }
  if (A[5]) T(c, A[5], { f: 'b', size: 15, c: 'ui/muted', x: 16, y: kind === 'off' ? 184 : 210 });
  const chip = figma.createAutoLayout('HORIZONTAL', { name: 'State chip', paddingLeft: 12, paddingRight: 12, paddingTop: 6, paddingBottom: 6, cornerRadius: 12 });
  chip.fills = [P(A[7])]; c.appendChild(chip); chip.x = 16; chip.y = 258;
  const ct = figma.createText(); ct.fontName = F.bx; ct.characters = A[6]; ct.fontSize = 13; ct.letterSpacing = { unit: 'PERCENT', value: 10 }; ct.fills = [P('ui/text')]; chip.appendChild(ct);
  return c;
};
const wedge = (c, cx, cy, r, extra = '') => {
  const s = a => Math.sin(a * Math.PI / 180), k = a => Math.cos(a * Math.PI / 180);
  const pt = (rr, a) => `${(r + rr * s(a)).toFixed(1)} ${(r - rr * k(a)).toFixed(1)}`;
  const arc = rr => `<path d="M${pt(rr, -60)} A${rr} ${rr} 0 0 1 ${pt(rr, 60)}" stroke="#2C3440" stroke-width="1.5" fill="none"/>`;
  const body = `<path d="M${r} ${r} L${pt(r, -60)} A${r} ${r} 0 0 1 ${pt(r, 60)} Z" fill="#1B2029" stroke="#2C3440" stroke-width="1.5"/>` + arc(r / 3) + arc(2 * r / 3) + extra;
  return svg(c, `<svg width="${2 * r}" height="${r}" viewBox="0 0 ${2 * r} ${r}" fill="none" xmlns="http://www.w3.org/2000/svg">${body}</svg>`, cx - r, cy - r, 'Radar wedge');
};
const dot = (c, x, y, color, d = 14) => ell(c, d, d, x - d / 2, y - d / 2, color, 'Person');
const radar = (f, x) => {
  const c = card(f, 'Widget/Radar', x, 209);
  label(c, 'RADAR', '2 PEOPLE');
  const r = 92, cx = 104.5, cy = 236;
  const s = a => Math.sin(a * Math.PI / 180), k = a => Math.cos(a * Math.PI / 180);
  const desk = `<path d="M${r} ${r} L${(r + 30 * s(-60)).toFixed(1)} ${(r - 30 * k(-60)).toFixed(1)} A30 30 0 0 1 ${(r + 30 * s(60)).toFixed(1)} ${(r - 30 * k(60)).toFixed(1)} Z" fill="#3DDC84" fill-opacity="0.22"/>`;
  wedge(c, cx, cy, r, desk);
  T(c, '2 m', { f: 'b', size: 10, c: 'ui/muted', x: cx + 4, y: cy - r / 3 - 14 }); T(c, '4 m', { f: 'b', size: 10, c: 'ui/muted', x: cx + 4, y: cy - 2 * r / 3 - 14 }); T(c, '6 m', { f: 'b', size: 10, c: 'ui/muted', x: cx + 4, y: cy - r - 14 });
  dot(c, cx - 8, cy - 20, 'ui/p1'); dot(c, cx + 46, cy - 70, 'ui/p2');
  const robot = figma.createRectangle(); robot.resize(18, 6); robot.cornerRadius = 3; robot.fills = [P('ui/text')]; c.appendChild(robot); robot.x = cx - 9; robot.y = cy + 4;
  T(c, 'Desk zone', { f: 'b', size: 12, c: 'ui/good', x: 16, y: 262 });
  T(c, '● You', { f: 'b', size: 12, c: 'ui/p1', x: 16, y: 280 }); T(c, 'Visitor ●', { f: 'b', size: 12, c: 'ui/p2', x: 16, y: 280, w: 177, align: 'RIGHT' });
  return c;
};
const timer = (f, x) => {
  const c = card(f, 'Widget/Desk timer', x, 209);
  label(c, 'AT DESK');
  T(c, '1:42:07', { f: 'mono', size: 38, x: 16, y: 38 });
  T(c, 'this stretch', { f: 'b', size: 14, c: 'ui/muted', x: 16, y: 88 });
  const ln = figma.createRectangle(); ln.resize(177, 1); ln.fills = [P('ui/line')]; c.appendChild(ln); ln.x = 16; ln.y = 124;
  const row = (y, k, v) => { T(c, k, { f: 'bx', size: 11, c: 'ui/muted', ls: 12, x: 16, y }); T(c, v, { f: 'mono', size: 22, x: 16, y: y + 16 }); };
  row(138, 'TODAY', '5:12:40'); row(194, 'TRIP', '23:18:05');
  const b = figma.createAutoLayout('HORIZONTAL', { name: 'Reset trip', paddingLeft: 12, paddingRight: 12, paddingTop: 7, paddingBottom: 7, cornerRadius: 12 });
  b.fills = [P('ui/card-inner')]; c.appendChild(b); b.x = 16; b.y = 260;
  const bt = figma.createText(); bt.fontName = F.b; bt.characters = 'Hold to reset trip'; bt.fontSize = 13; bt.fills = [P('ui/muted')]; b.appendChild(bt);
  return c;
};
const X1 = 50, X2 = 265, W1 = 209, W2 = 424;
let f;
f = screen('Home · Free (eyes + next meeting)', 'state/free', 0, 0); eyes(f, X1, W1, 10, 4); meeting(f, X2, 'free');
f = screen('Home · Soon (radar + desk timer)', 'state/soon', 1, 0); radar(f, X1); timer(f, X2);
f = screen('Home · Meeting starting (eyes + meeting)', 'state/now', 2, 0); eyes(f, X1, W1, -14, 10); meeting(f, X2, 'now');
f = screen('Home · In meeting (eyes 2-wide)', 'state/in-meeting', 0, 1); eyes(f, X1, W2, 16, -6);
f = screen('Home · In meeting (eyes + meeting)', 'state/in-meeting', 1, 1); eyes(f, X1, W1, 0, 0); meeting(f, X2, 'mid');
f = screen('Home · Offline, nobody here', 'state/offline', 2, 1); eyes(f, X1, W1, 0, 0, 'sleep'); meeting(f, X2, 'off');
return { created };