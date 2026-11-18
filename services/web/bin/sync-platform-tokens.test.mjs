import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {extractTokens} from './sync-platform-tokens.mjs';
const css = readFileSync(new URL('../../../packages/ui/src/tokens.css',import.meta.url),'utf8');
const theme = readFileSync(new URL('../../../node_modules/tailwindcss/theme.css',import.meta.url),'utf8');
test('resolves semantic aliases per mode and derives primitive dimensions',()=>{
  const result=extractTokens(css,theme);
  assert.notEqual(result.tokens.color.accent.dark,result.tokens.color.accent.light);
  assert.equal(result.tokens.space.m,16);
  assert.equal(result.tokens.radius.md,4);
  assert.equal(result.tokens.type.body.family,'Inter');
  assert.ok(!JSON.stringify(result.tokens).includes('var('));
});
test('token edits change generated contract and cyclic aliases fail closed',()=>{
  const changed=extractTokens(css.replace('--radius-md: 4px','--radius-md: 6px'),theme);
  assert.equal(changed.tokens.radius.md,6);
  assert.notEqual(changed.source_sha256,extractTokens(css,theme).source_sha256);
  assert.throws(()=>extractTokens(css.replace('--ds-accent: var(--color-cyan-400)','--ds-accent: var(--ds-accent)'),theme),/cycle/);
});
test('browser and native renderer ship identical typography files and licenses',()=>{
  for(const family of ['Inter','JetBrainsMono']) {
    for(const weight of ['Regular','Medium','SemiBold','Bold']) {
      const file=`${family}-${weight}.ttf`;
      assert.deepEqual(readFileSync(new URL(`../../../packages/ui/src/fonts/${file}`,import.meta.url)),readFileSync(new URL(`../../backend/agentplatform/ui_designer/fonts/${file}`,import.meta.url)),file);
    }
    const license=`OFL-${family}.txt`;
    assert.deepEqual(readFileSync(new URL(`../../../packages/ui/src/fonts/${license}`,import.meta.url)),readFileSync(new URL(`../../backend/agentplatform/ui_designer/fonts/${license}`,import.meta.url)));
  }
});
