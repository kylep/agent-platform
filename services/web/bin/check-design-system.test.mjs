import { test } from 'node:test';
import assert from 'node:assert/strict';
import { baselineGrowth, markdown } from './check-design-system.mjs';

const site = {file: 'src/Page.tsx', kind: 'raw-control', name: 'button', signature: 'abc', count: 2};
test('the historical baseline can shrink but cannot move or grow', () => {
  assert.deepEqual(baselineGrowth([site], [{...site, count: 1}]), []);
  assert.deepEqual(baselineGrowth([site], []), []);
  for (const changed of [{...site, count: 3}, {...site, file: 'src/New.tsx'}, {...site, signature: 'xyz'}]) {
    assert.equal(baselineGrowth([site], [changed]).length, 1);
  }
});

test('failed checks produce an honest report instead of a success percentage', () => {
  const report = markdown({adoption: null, stories: null, errors: ['missing build']});
  assert.match(report, /Check failed/);
  assert.match(report, /missing build/);
  assert.doesNotMatch(report, /100%/);
});
