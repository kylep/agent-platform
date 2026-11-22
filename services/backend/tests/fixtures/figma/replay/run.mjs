// Replays the desk-buddy build scripts (the use_figma scripts that created the file) against a
// small mock of the Figma Plugin API and writes ../desk-buddy.json in the dump shape consumed by
// agentplatform/ui_designer/figma_convert.py. Run: node run.mjs
//
// Reconstruction notes: text boxes use estimated metrics, vectors from createNodeFromSvg keep the
// svg viewBox as their box (path data in svg coordinates), auto-layout is computed for the two
// hug-sized chip/button frames. Top-level node ids are remapped to the real file's ids.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
let seq = 100;
const COMP_IDS = ['2:25', '2:32', '2:36', '2:41', '2:42', '2:46', '2:67', '2:88'];
let compSeq = 0;
const byId = new Map();
const hexToRgb = h => { const n = parseInt(h.slice(1), 16); return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255 }; };

const LH = { Nunito: 1.364, Fredoka: 1.22, 'JetBrains Mono': 1.32 };
class Node {
  constructor(type, id) {
    this.type = type; this.id = id || `m:${seq++}`; this.name = type.toLowerCase();
    this.parent = null; this.children = []; this.x = 0; this.y = 0; this._w = 100; this._h = 100;
    this.visible = true; this.opacity = 1; this.fills = []; this.strokes = []; this.effects = [];
    this.cornerRadius = 0; this.clipsContent = type === 'FRAME' || type === 'COMPONENT';
    this.layoutMode = 'NONE'; this.itemSpacing = 0;
    this.paddingLeft = this.paddingRight = this.paddingTop = this.paddingBottom = 0;
    this.primaryAxisSizingMode = 'FIXED'; this.counterAxisSizingMode = 'FIXED';
    this.primaryAxisAlignItems = 'MIN'; this.counterAxisAlignItems = 'MIN';
    this.strokeWeight = 1; this.strokeAlign = 'CENTER';
    if (type === 'TEXT') { this.fontName = { family: 'Inter', style: 'Regular' }; this.fontSize = 12; this._chars = '';
      this.textAutoResize = 'WIDTH_AND_HEIGHT'; this.textAlignHorizontal = 'LEFT'; this.textAlignVertical = 'TOP';
      this.letterSpacing = { unit: 'PIXELS', value: 0 }; this.lineHeight = { unit: 'AUTO' }; }
    byId.set(this.id, this);
  }
  get characters() { return this._chars; } set characters(v) { this._chars = v; }
  _tm() {
    const fam = this.fontName.family, s = this.fontSize, n = this._chars.length;
    const cw = s * (fam === 'JetBrains Mono' ? 0.6 : fam === 'Fredoka' ? 0.56 : 0.55);
    const ls = this.letterSpacing.unit === 'PERCENT' ? s * this.letterSpacing.value / 100 : this.letterSpacing.value;
    return { nat: n * (cw + ls), lh: s * (LH[fam] || 1.3) };
  }
  _auto() { return this.layoutMode !== 'NONE'; }
  _hugW() { const h = this.layoutMode === 'HORIZONTAL', ks = this.children.filter(c => c.visible);
    const inner = h ? ks.reduce((a, c) => a + c.width, 0) + this.itemSpacing * Math.max(0, ks.length - 1) : Math.max(0, ...ks.map(c => c.width));
    return this.paddingLeft + this.paddingRight + inner; }
  _hugH() { const h = this.layoutMode === 'HORIZONTAL', ks = this.children.filter(c => c.visible);
    const inner = h ? Math.max(0, ...ks.map(c => c.height)) : ks.reduce((a, c) => a + c.height, 0) + this.itemSpacing * Math.max(0, ks.length - 1);
    return this.paddingTop + this.paddingBottom + inner; }
  get width() {
    if (this.type === 'TEXT') { const m = this._tm(); return this.textAutoResize === 'WIDTH_AND_HEIGHT' ? m.nat : this._w; }
    if (this._auto()) { const horiz = this.layoutMode === 'HORIZONTAL'; const auto = (horiz ? this.primaryAxisSizingMode : this.counterAxisSizingMode) === 'AUTO'; if (auto) return this._hugW(); }
    return this._w; }
  get height() {
    if (this.type === 'TEXT') { const m = this._tm(); if (this.textAutoResize === 'WIDTH_AND_HEIGHT') return m.lh;
      if (this.textAutoResize === 'HEIGHT') return Math.max(1, Math.ceil(m.nat / this._w)) * m.lh; return this._h; }
    if (this._auto()) { const horiz = this.layoutMode === 'HORIZONTAL'; const auto = (horiz ? this.counterAxisSizingMode : this.primaryAxisSizingMode) === 'AUTO'; if (auto) return this._hugH(); }
    return this._h; }
  resize(w, h) { this._w = w; this._h = h; if (this.type === 'TEXT' && this.textAutoResize === 'WIDTH_AND_HEIGHT') this.textAutoResize = 'NONE'; }
  appendChild(c) { if (c.parent) c.parent.children.splice(c.parent.children.indexOf(c), 1); c.parent = this; this.children.push(c); }
  remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; byId.delete(this.id); }
  get mainComponent() { return this._main || null; }
  createInstance() {
    const inst = new Node('INSTANCE'); inst.name = this.name; inst._main = this; inst._w = this._w; inst._h = this._h; inst.fills = JSON.parse(JSON.stringify(this.fills)); inst.clipsContent = this.clipsContent;
    const clone = (src, dst) => { for (const c of src.children) { const n = new Node(c.type); Object.assign(n, JSON.parse(JSON.stringify({ ...c, parent: null, children: [] , _main: undefined })), { id: n.id, parent: dst, children: [] }); n._main = c._main; dst.children.push(n); clone(c, n); } };
    clone(this, inst); return inst;
  }
}
const root = new Node('CANVAS', '0:1'); root.name = 'Page 1';
const reg = n => { n.parent = root; root.children.push(n); return n; };
const mk = type => reg(new Node(type, type === 'COMPONENT' ? COMP_IDS[compSeq++] : undefined));

// --- variables
const collections = []; const vars = new Map();
const figma = {
  root: { children: [root] }, currentPage: root,
  createFrame: () => mk('FRAME'), createText: () => mk('TEXT'), createRectangle: () => mk('RECTANGLE'),
  createEllipse: () => mk('ELLIPSE'), createComponent: () => mk('COMPONENT'),
  createAutoLayout(dir, props = {}) { const f = mk('FRAME'); f.layoutMode = dir; f.primaryAxisSizingMode = 'AUTO'; f.counterAxisSizingMode = 'AUTO'; f.clipsContent = false; f.fills = []; Object.assign(f, props); return f; },
  getNodeByIdAsync: async id => byId.get(id) || null,
  loadFontAsync: async () => {},
  variables: {
    createVariableCollection(name) { const c = { name, id: `VariableCollectionId:${collections.length}`, modes: [{ modeId: 'm0', name: 'Mode 1' }], variableIds: [],
      renameMode(id, n) { this.modes.find(m => m.modeId === id).name = n; }, addMode(n) { const id = 'm' + this.modes.length; this.modes.push({ modeId: id, name: n }); return id; },
      remove() { for (const id of this.variableIds) vars.delete(id); collections.splice(collections.indexOf(this), 1); } }; collections.push(c); return c; },
    createVariable(name, col, type) { const id = `VariableID:2:${4 + vars.size}`; const v = { id, name, type, values: {}, scopes: [], setValueForMode(m, val) { this.values[m] = val; } }; vars.set(id, v); col.variableIds.push(id); v._col = col; return v; },
    getLocalVariablesAsync: async () => [...vars.values()],
    getLocalVariableCollectionsAsync: async () => [...collections],
    getVariableByIdAsync: async id => vars.get(id) || null,
    setBoundVariableForPaint(p, field, v) { return { ...p, color: Object.values(v.values)[0], variable: v.name, boundVariables: { color: { id: v.id } } }; },
  },
  createNodeFromSvg(svg) {
    const w = +/width="([\d.]+)"/.exec(svg)[1], h = +/height="([\d.]+)"/.exec(svg)[1];
    const rootAttr = /<svg([^>]*)>/.exec(svg)[1]; const attr = (s, k) => { const m = new RegExp(`(?:^|\\s)${k}="([^"]*)"`).exec(s); return m ? m[1] : undefined; };
    const f = mk('FRAME'); f.name = 'svg'; f.resize(w, h); f.fills = []; f.clipsContent = false;
    const body = svg.replace(/^[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '');
    for (const m of body.matchAll(/<(path|line|circle)\b([^>]*?)\/?>/g)) {
      const [, tag, a] = m; const g = k => attr(a, k) ?? attr(rootAttr, k);
      let d;
      if (tag === 'path') d = attr(a, 'd');
      else if (tag === 'line') d = `M${attr(a, 'x1')} ${attr(a, 'y1')} L${attr(a, 'x2')} ${attr(a, 'y2')}`;
      else { const cx = +attr(a, 'cx'), cy = +attr(a, 'cy'), r = +attr(a, 'r'); d = `M${cx - r} ${cy} A${r} ${r} 0 1 0 ${cx + r} ${cy} A${r} ${r} 0 1 0 ${cx - r} ${cy} Z`; }
      const v = reg(new Node('VECTOR')); v.name = 'Vector'; v.resize(w, h); v.vectorPaths = [{ windingRule: 'NONZERO', data: d }];
      const fill = attr(a, 'fill') ?? attr(rootAttr, 'fill'); const stroke = g('stroke');
      if (fill && fill !== 'none') v.fills = [{ type: 'SOLID', color: hexToRgb(fill), opacity: +(attr(a, 'fill-opacity') ?? 1) }];
      if (stroke) { v.strokes = [{ type: 'SOLID', color: hexToRgb(stroke) }]; v.strokeWeight = +g('stroke-width'); v.strokeCap = (g('stroke-linecap') || 'butt').toUpperCase().replace('BUTT', 'NONE'); v.strokeJoin = (g('stroke-linejoin') || 'miter').toUpperCase(); v.strokeAlign = 'CENTER'; }
      f.appendChild(v);
    }
    return f;
  },
};

// --- run scripts
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
for (const s of ['03', '05', '06', '08', '11']) {
  let code = fs.readFileSync(path.join(here, 'scripts', `${s}.js`), 'utf8');
  if (s === '11') { // the real radar widget id; find it by name here (ids are not reproducible)
    code = code.replace("figma.getNodeByIdAsync('4:105')", "figma.getNodeByIdAsync(globalThis.__radar)")
      .replace("await figma.getNodeByIdAsync('4:119')", "(globalThis.__deskZone)");
    const soon = root.children.find(n => n.name.startsWith('Home · Soon'));
    const radar = soon.children.find(n => n.name === 'Widget/Radar');
    globalThis.__radar = radar.id; globalThis.__deskZone = radar.children.find(n => n.type === 'TEXT' && n.characters === 'Desk zone');
  }
  const r = await new AsyncFunction('figma', code)(figma);
  console.error('ran', s, JSON.stringify(r).slice(0, 80));
}

// --- id remap to the real file (top level), from get_metadata
const real = JSON.parse(fs.readFileSync(path.join(here, 'real-toplevel.json'), 'utf8'));
const idmap = new Map();
for (const [id, type, name] of real) { const n = root.children.find(c => c.type === type && c.name === name && !idmap.has(c)); if (n) { idmap.set(n, id); } }
const rid = n => idmap.get(n) || n.id;

// --- serialize
const ser = (n, inInst) => {
  const o = { id: rid(n), name: n.type === 'TEXT' && n.name === 'text' ? n._chars : n.name, type: n.type };
  const par = n.parent;
  let x = n.x, y = n.y;
  if (par && par._auto && par._auto() && n.type !== 'CANVAS') { const h = par.layoutMode === 'HORIZONTAL'; let cur = h ? par.paddingLeft : par.paddingTop;
    for (const c of par.children) { if (c === n) break; if (c.visible) cur += (h ? c.width : c.height) + par.itemSpacing; }
    x = h ? cur : par.paddingLeft; y = h ? par.paddingTop : cur; }
  o.width = +n.width.toFixed(2); o.height = +n.height.toFixed(2);
  o.relativeTransform = [[1, 0, +x.toFixed(2)], [0, 1, +y.toFixed(2)]];
  if (!n.visible) o.visible = false; if (n.opacity !== 1) o.opacity = n.opacity;
  if (n.cornerRadius) o.cornerRadius = n.cornerRadius;
  if (['FRAME', 'COMPONENT', 'INSTANCE'].includes(n.type) && n.clipsContent) o.clipsContent = true;
  if (n.layoutMode !== 'NONE') { Object.assign(o, { layoutMode: n.layoutMode, itemSpacing: n.itemSpacing, paddingLeft: n.paddingLeft, paddingRight: n.paddingRight, paddingTop: n.paddingTop, paddingBottom: n.paddingBottom, primaryAxisAlignItems: n.primaryAxisAlignItems, counterAxisAlignItems: n.counterAxisAlignItems });
    const h = n.layoutMode === 'HORIZONTAL'; o.layoutSizingHorizontal = (h ? n.primaryAxisSizingMode : n.counterAxisSizingMode) === 'AUTO' ? 'HUG' : 'FIXED'; o.layoutSizingVertical = (h ? n.counterAxisSizingMode : n.primaryAxisSizingMode) === 'AUTO' ? 'HUG' : 'FIXED'; }
  const paint = p => { const q = { type: p.type, color: { r: +p.color.r.toFixed(4), g: +p.color.g.toFixed(4), b: +p.color.b.toFixed(4) } }; if (p.opacity !== undefined && p.opacity !== 1) q.opacity = p.opacity; if (p.variable) q.variable = p.variable; return q; };
  if (n.fills.length) o.fills = n.fills.map(paint);
  if (n.strokes.length) { o.strokes = n.strokes.map(paint); o.strokeWeight = n.strokeWeight; o.strokeAlign = n.strokeAlign; if (n.strokeCap) o.strokeCap = n.strokeCap; if (n.strokeJoin) o.strokeJoin = n.strokeJoin; }
  if (n.type === 'TEXT') { const st = n.fontName.style; const wt = { Regular: 400, Medium: 500, SemiBold: 600, Bold: 700, ExtraBold: 800 }[st] || 400;
    Object.assign(o, { characters: n._chars, textAutoResize: n.textAutoResize, textAlignHorizontal: n.textAlignHorizontal, textAlignVertical: n.textAlignVertical,
      segments: [{ start: 0, end: n._chars.length, fontFamily: n.fontName.family, fontStyle: st, fontSize: n.fontSize, fontWeight: wt, lineHeight: n.lineHeight, letterSpacing: n.letterSpacing, fills: n.fills.map(paint) }] }); }
  if (n.type === 'INSTANCE') { o.componentId = rid(n._main); o.componentName = n._main.name; }
  if (n.vectorPaths && !inInst) o.vectorPaths = n.vectorPaths;
  if (n.children.length) o.children = n.children.map(c => ser(c, inInst || n.type === 'INSTANCE'));
  return o;
};
const variables = {}; for (const v of vars.values()) { const c = Object.values(v.values)[0]; variables[v.name] = '#' + [c.r, c.g, c.b].map(k => Math.round(k * 255).toString(16).padStart(2, '0')).join(''); }
const out = { name: 'desk-buddy', pluginApi: true, variables, document: { id: '0:1', name: 'Page 1', type: 'CANVAS', children: root.children.map(c => ser(c, false)) } };
fs.writeFileSync(path.join(here, '..', 'desk-buddy.json'), JSON.stringify(out));
console.error('wrote', root.children.length, 'top-level nodes');
