// Clean partial state, then create single-mode colour variables and local components: Nav, Eye, icons
for (const c of await figma.variables.getLocalVariableCollectionsAsync()) c.remove();
for (const n of [...figma.currentPage.children]) n.remove();
const hex = h => { const n = parseInt(h.slice(1), 16); return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255 }; };
const col = figma.variables.createVariableCollection('Desk Buddy');
const m = col.modes[0].modeId;
const vals = {
  'state/free': '#1E7A47', 'state/soon': '#C9861A', 'state/now': '#C8372D', 'state/in-meeting': '#4A1F24', 'state/offline': '#1C2638',
  'ui/card': '#11141A', 'ui/card-inner': '#1B2029', 'ui/line': '#2C3440', 'ui/text': '#F3F5F8', 'ui/muted': '#8E98A8',
  'ui/nav': '#0B0D11', 'ui/good': '#3DDC84', 'ui/bad': '#FF5A4E', 'ui/p1': '#4FD1E8', 'ui/p2': '#FF6FA8', 'ui/p3': '#FFD447', 'ui/eye': '#F7F7F2', 'ui/pupil': '#151820',
};
const vars = {};
for (const [name, h] of Object.entries(vals)) {
  const v = figma.variables.createVariable(name, col, 'COLOR');
  v.scopes = name.startsWith('state/') ? ['FRAME_FILL', 'SHAPE_FILL'] : (name === 'ui/text' || name === 'ui/muted') ? ['TEXT_FILL', 'SHAPE_FILL', 'STROKE_COLOR'] : ['FRAME_FILL', 'SHAPE_FILL', 'STROKE_COLOR'];
  v.setValueForMode(m, hex(h));
  vars[name] = v;
}
const paint = name => figma.variables.setBoundVariableForPaint({ type: 'SOLID', color: { r: 0, g: 0, b: 0 } }, 'color', vars[name]);
const icons = {
  home: '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  play: '<line x1="6" x2="10" y1="11" y2="11"/><line x1="8" x2="8" y1="9" y2="13"/><line x1="15" x2="15.01" y1="12" y2="12"/><line x1="18" x2="18.01" y1="10" y2="10"/><path d="M17.32 5H6.68a4 4 0 0 0-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2 2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.151A4 4 0 0 0 17.32 5z"/>',
  gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  sound: '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M19.364 18.364a9 9 0 0 0 0-12.728"/>',
};
const iconComps = {};
let x = 0;
for (const [name, body] of Object.entries(icons)) {
  const svg = figma.createNodeFromSvg(`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" stroke="#F3F5F8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`);
  const c = figma.createComponent();
  c.name = `icon/${name}`; c.resize(24, 24); c.fills = [];
  for (const ch of [...svg.children]) c.appendChild(ch);
  svg.remove();
  c.x = x; c.y = -400; x += 40;
  iconComps[name] = c;
}
const eye = figma.createComponent();
eye.name = 'Eye'; eye.resize(78, 96); eye.fills = []; eye.x = 200; eye.y = -400; eye.clipsContent = false;
const sclera = figma.createEllipse(); sclera.name = 'Sclera'; sclera.resize(78, 96); sclera.fills = [paint('ui/eye')]; eye.appendChild(sclera);
const pupil = figma.createEllipse(); pupil.name = 'Pupil'; pupil.resize(40, 40); pupil.x = 26; pupil.y = 34; pupil.fills = [paint('ui/pupil')]; eye.appendChild(pupil);
const glint = figma.createEllipse(); glint.name = 'Glint'; glint.resize(11, 11); glint.x = 47; glint.y = 40; glint.fills = [paint('ui/eye')]; eye.appendChild(glint);
eye.description = 'One eye. Firmware moves Pupil+Glint together toward the tracked person (about ±18 px x, ±14 px y).';
const navIds = {};
for (const active of ['home', 'play', 'gear']) {
  const nav = figma.createComponent();
  nav.name = `Nav/${active}`; nav.resize(44, 320); nav.x = 320 + Object.keys(navIds).length * 60; nav.y = -400;
  nav.fills = [paint('ui/nav')];
  const spots = [['home', 10], ['play', 56], ['gear', 102], ['sound', 274]];
  for (const [icon, y] of spots) {
    const slot = figma.createFrame(); slot.name = `btn/${icon}`; slot.resize(36, 36); slot.x = 4; slot.y = y; slot.cornerRadius = 10;
    slot.fills = icon === active ? [paint('ui/card-inner')] : [];
    const inst = iconComps[icon].createInstance(); inst.x = 6; inst.y = 6; slot.appendChild(inst);
    if (icon !== active) inst.opacity = 0.55;
    nav.appendChild(slot);
  }
  nav.description = 'Left nav, 44 px. Pages: Home, Play, Settings. Bottom speaker: tap to mute/unmute.';
  navIds[active] = nav.id;
}
return { vars: Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, v.id])), icons: Object.fromEntries(Object.entries(iconComps).map(([k, v]) => [k, v.id])), eye: eye.id, nav: navIds };