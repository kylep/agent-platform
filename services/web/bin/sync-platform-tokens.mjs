#!/usr/bin/env node
import postcss from 'postcss';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

export function extractTokens(css, themeCSS) {
  const primitives = {}, dark = {}, light = {};
  postcss.parse(themeCSS).walkDecls(d => { if (d.prop.startsWith('--')) primitives[d.prop] = d.value; });
  postcss.parse(css).walkDecls(d => {
    if (!d.prop.startsWith('--')) return;
    if (d.parent.type === 'atrule' && d.parent.name === 'theme') primitives[d.prop] = d.value;
    else if (d.parent.selector === ':root') dark[d.prop] = d.value;
    else if (d.parent.selector === '[data-theme="light"]') light[d.prop] = d.value;
  });
  function value(key, mode, seen = new Set()) {
    if (seen.has(key)) throw new Error(`Token cycle: ${key}`);
    seen.add(key);
    const raw = (mode === 'light' ? light[key] : undefined) ?? dark[key] ?? primitives[key];
    if (!raw) throw new Error(`Missing token: ${key}`);
    return raw.replace(/var\((--[\w-]+)\)/g, (_, reference) => value(reference, mode, new Set(seen)));
  }
  const px = key => {
    const raw = value(key, 'dark');
    if (!/^\d*\.?\d+(px|rem)$/.test(raw)) throw new Error(`Expected px/rem: ${key}`);
    return parseFloat(raw) * (raw.endsWith('rem') ? 16 : 1);
  };
  const color = {};
  for (const key of Object.keys(dark).filter(k => k.startsWith('--ds-')).sort()) {
    const pair = Object.fromEntries(['dark','light'].map(mode => [mode,value(key,mode)]));
    for (const v of Object.values(pair)) if (!/^#[\da-f]{3}(?:[\da-f]{3})?(?:[\da-f]{2})?$/i.test(v)) throw new Error(`Designer needs a literal color: ${key}=${v}`);
    color[key.slice(5)] = pair;
  }
  const spacing = px('--spacing');
  const family = key => value(key, 'dark').split(',')[0].trim().replace(/^"|"$/g,'');
  return {
    schema: 1,
    source_sha256: createHash('sha256').update(css).update(themeCSS).digest('hex'),
    tokens: {
      color,
      space: Object.fromEntries(Object.entries({xxs:.5,xs:1,s:2,m:4,l:6,xl:8}).map(([k,n])=>[k,n*spacing])),
      radius: Object.fromEntries(['sm','md','lg'].map(k=>[k,px(`--radius-${k}`)])),
      type: {
        body: {family:family('--font-sans'),size:px('--text-sm'),weight:Number(value('--font-weight-normal','dark')),line:20/14},
        small: {family:family('--font-sans'),size:px('--text-xs'),weight:Number(value('--font-weight-normal','dark')),line:16/12},
        mono: {family:family('--font-mono'),size:13,weight:Number(value('--font-weight-normal','dark')),line:1.625},
      },
    },
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL('../../..', import.meta.url));
  const css = readFileSync(resolve(root,'packages/ui/src/tokens.css'),'utf8');
  const themeCSS = readFileSync(resolve(root,'node_modules/tailwindcss/theme.css'),'utf8');
  const output = resolve(root,'services/backend/agentplatform/ui_designer/platform_tokens.json');
  const generated = JSON.stringify(extractTokens(css,themeCSS),null,2)+'\n';
  if (process.argv.includes('--write')) { writeFileSync(output,generated); console.log('Updated Designer tokens from @ap/ui.'); }
  else {
    if (readFileSync(output,'utf8') !== generated) throw new Error('Designer token drift. Run npm run sync:designer-tokens -w web and review the component-library revision and parity snapshots.');
    console.log('Designer tokens match @ap/ui.');
  }
}
