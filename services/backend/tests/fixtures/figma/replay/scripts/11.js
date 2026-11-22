// Add desk-zone timer and reset button to radar widget
const w=await figma.getNodeByIdAsync('4:105');
const v=async id=>await figma.variables.getVariableByIdAsync(id);
const paint=async id=>figma.variables.setBoundVariableForPaint({type:'SOLID',color:{r:1,g:1,b:1}},'color',await v(id));
await figma.loadFontAsync({family:'JetBrains Mono',style:'Bold'});
await figma.loadFontAsync({family:'Nunito',style:'Bold'});
const t=figma.createText(); t.name='Desk time'; t.fontName={family:'JetBrains Mono',style:'Bold'}; t.fontSize=30; t.characters='1:42:07';
t.fills=[await paint('VariableID:2:12')]; w.appendChild(t); t.x=16; t.y=36;
const c=figma.createText(); c.name='at desk caption'; c.fontName={family:'Nunito',style:'Bold'}; c.fontSize=12; c.characters='at your desk';
c.fills=[await paint('VariableID:2:15')]; w.appendChild(c); c.x=16; c.y=76;
const b=figma.createFrame(); b.name='Reset'; b.layoutMode='HORIZONTAL'; b.primaryAxisSizingMode='AUTO'; b.counterAxisSizingMode='AUTO';
b.paddingLeft=b.paddingRight=12; b.paddingTop=b.paddingBottom=6; b.cornerRadius=14; b.fills=[await paint('VariableID:2:10')];
const bt=figma.createText(); bt.fontName={family:'Nunito',style:'Bold'}; bt.fontSize=12; bt.characters='Reset'; bt.fills=[await paint('VariableID:2:12')];
b.appendChild(bt); w.appendChild(b); b.x=w.width-16-b.width; b.y=72;
// move legend "Desk zone" label off: desk zone colour now explained by caption
const dz=await figma.getNodeByIdAsync('4:119'); dz.remove();
return {b:[b.x,b.y,b.width,b.height]};