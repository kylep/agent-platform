import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';

const repo = fileURLToPath(new URL('../../..', import.meta.url));
const inventory = JSON.parse(readFileSync(new URL('./routes.json', import.meta.url), 'utf8'));
const clean = path => path === '/' ? path : path.replace(/\/$/, '');
const declared = [];
for (const source of ['console', 'stockmarket', 'tcms', 'judgment']) {
  const file = source === 'console' ? 'services/web/src/App.tsx' : `apps/${source}/frontend/src/App.tsx`;
  const ast = ts.createSourceFile(file, readFileSync(`${repo}/${file}`, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  function visit(node) {
    if ((ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) && node.tagName.getText(ast) === 'Route') {
      const path = node.attributes.properties.find(attr => ts.isJsxAttribute(attr) && attr.name.getText(ast) === 'path');
      if (path?.initializer && ts.isStringLiteral(path.initializer)) {
        const value = path.initializer.text;
        const base = source === 'console' ? '' : `/apps/${source}`;
        const prefix = source === 'stockmarket' && value !== '/' ? `${base}/#` : base;
        declared.push({source, pattern:clean(`${prefix}${value}`), file});
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
}
declared.push({source:'running',pattern:'/apps/running',file:'apps/running/frontend/src/App.tsx'});
const entries = [...inventory.routes, ...inventory.aliases];
const missing = declared.filter(route => !entries.some(entry => entry.source === route.source && clean(entry.pattern) === route.pattern));
const slugs = inventory.routes.map(route => route.slug);
const failures = [];
if (new Set(slugs).size !== slugs.length) failures.push('Duplicate capture slugs.');
for (const route of missing) failures.push(`Missing ${route.source} route ${route.pattern} (${route.file})`);
for (const alias of inventory.aliases) {
  if (!slugs.includes(alias.canonical)) failures.push(`Missing alias destination ${alias.canonical}`);
}
if (failures.length) throw new Error(failures.join('\n'));
console.log(`${declared.length}/${declared.length} owned route patterns covered by ${inventory.routes.length} screen captures and ${inventory.aliases.length} aliases.`);
