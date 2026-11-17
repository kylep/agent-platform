import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { publicComponents, validateCoverage, checkStoryCoverage } from './ui-story-coverage.mjs';

const components = new Map([['Button', ['@ap/ui', '@ap/ui/button']]]);
const registration = { name: 'Button', import: '@ap/ui/button', states: [{ name: 'default', story: 'ui-button--default' }] };
const index = { entries: { 'ui-button--default': { type: 'story' } } };

test('coverage uses public components as denominator, and resolves real stories', () => {
  assert.equal(validateCoverage({schema: 1, components: [registration]}, components, index).coveredComponents, 1);
  assert.match(validateCoverage({schema: 1, components: []}, components, index).errors.join(), /missing stories: Button/);
  const missing = validateCoverage({schema: 1, components: [registration]}, components, {entries: {}});
  assert.equal(missing.coveredStates, 0);
  assert.equal(missing.coveredComponents, 0);
  assert.match(missing.errors.join(), /missing story/);
});

test('reject docs entries, invalid exports, and duplicate or empty state declarations', () => {
  const bad = {...registration, import: '@ap/ui/private', states: [...registration.states, ...registration.states]};
  assert.equal(validateCoverage({schema: 1, components: [{...registration, import: '@ap/ui/private'}]}, components, index).coveredComponents, 0);
  const result = validateCoverage({schema: 1, components: [bad, {...registration, states: []}]}, components,
    {entries: {'ui-button--default': {type: 'docs'}}});
  for (const message of ['Not a public', 'duplicate state', 'Duplicate component', 'at least one', 'missing story']) {
    assert.ok(result.errors.some(e => e.includes(message)), message);
  }
});

test('discovers subpath-only components but excludes types and hooks; catches a stale built index', () => {
  const root = mkdtempSync(join(tmpdir(), 'ap-ui-coverage-'));
  const put = (path, value) => writeFileSync(join(root, path), typeof value === 'string' ? value : JSON.stringify(value));
  try {
    mkdirSync(join(root, 'packages/ui/src'), {recursive: true});
    mkdirSync(join(root, 'services/web/storybook-static'), {recursive: true});
    put('packages/ui/package.json', {exports: {'.': './src/index.ts', './button': './src/button.tsx'}});
    put('packages/ui/src/index.ts', 'export type Props = {text: string}; export type { Button as TypeOnlyButton } from "./button"; export function useTheme() {}');
    put('packages/ui/src/button.tsx', 'export function Button() { return null; }');
    put('packages/ui/src/button.stories.tsx', 'export const Default = {};');
    assert.deepEqual([...publicComponents(root)], [['Button', ['@ap/ui/button']]]);
    put('packages/ui/story-coverage.json', {schema: 1, components: [registration]});
    put('services/web/storybook-static/index.json', {entries: {'ui-button--default': {type: 'story', importPath: '../../packages/ui/src/button.stories.tsx', exportName: 'Default'}}});
    assert.deepEqual(checkStoryCoverage(root).errors, []);
    put('packages/ui/src/button.stories.tsx', 'export const Renamed = {};');
    assert.match(checkStoryCoverage(root).errors.join(), /Stale Storybook build/);
  } finally { rmSync(root, {recursive: true, force: true}); }
});
