// Fix game score pill visibility and move the game instruction text clear of the zone labels (find frames by name)
const V = {};
for (const v of await figma.variables.getLocalVariablesAsync('COLOR')) V[v.name] = v;
const P = n => figma.variables.setBoundVariableForPaint({ type: 'SOLID', color: { r: 0, g: 0, b: 0 } }, 'color', V[n]);
await Promise.all([{ family: 'Nunito', style: 'ExtraBold' }, { family: 'Nunito', style: 'Bold' }].map(f => figma.loadFontAsync(f)));
const mutated = [];
for (const fr of figma.currentPage.children.filter(n => n.type === 'FRAME' && /^Play · (Green|Red)/.test(n.name))) {
  const c = fr.children.find(n => n.name.startsWith('Widget/Red light'));
  for (const p of c.children.filter(n => n.name === 'Score right' || n.name === 'Score wrong')) { p.fills = [P('ui/card-inner')]; mutated.push(p.id); }
  const ins = c.children.find(n => n.type === 'TEXT' && (n.characters.startsWith('Get to') || n.characters.startsWith('Freeze')));
  ins.x = 24; ins.y = 158; ins.fontSize = 17; mutated.push(ins.id);
}
return { mutated };