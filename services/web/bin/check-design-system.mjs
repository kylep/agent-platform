#!/usr/bin/env node
import { readFileSync, writeFileSync, mkdirSync, appendFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { checkAdoption } from './ui-adoption.mjs';
import { checkDesignerCoverage } from './ui-designer-coverage.mjs';
import { checkStoryCoverage } from './ui-story-coverage.mjs';

export function baselineGrowth(previous, current) {
  const key = e => JSON.stringify([e.file, e.kind, e.signature]);
  const allowed = new Map(previous.map(e => [key(e), e.count]));
  return current.filter(e => e.count > (allowed.get(key(e)) ?? 0))
    .map(e => `Baseline grew: ${e.file} / ${e.name} (${e.count} sites). Use @ap/ui; do not grandfather new violations.`);
}

function checkBaselineHistory(root, ref) {
  if (!ref) return [];
  if (!/^[a-f0-9]{40}$/.test(ref)) throw new Error('UI_BASE_REF must be a full Git commit SHA');
  const path = 'packages/ui/adoption-baseline.json';
  const commit = spawnSync('git', ['cat-file', '-e', `${ref}^{commit}`], {cwd: root, encoding: 'utf8'});
  if (commit.status !== 0) throw new Error(`Cannot inspect baseline base commit ${ref}; fetch its history first`);
  const prior = spawnSync('git', ['show', `${ref}:${path}`], {cwd: root, encoding: 'utf8'});
  // The first adoption-gate commit creates the baseline. Subsequent commits
  // may only remove entries or lower counts; moving a violation is new debt.
  if (prior.status !== 0) {
    const tree = spawnSync('git', ['ls-tree', ref, '--', path], {cwd: root, encoding: 'utf8'});
    if (tree.status === 0 && !tree.stdout.trim()) return [];
    throw new Error(`Cannot read adoption baseline at ${ref}`);
  }
  return baselineGrowth(JSON.parse(prior.stdout), JSON.parse(readFileSync(resolve(root, path), 'utf8')));
}

export function run(root, { baseRef = process.env.UI_BASE_REF } = {}) {
  const report = { adoption: null, stories: null, designer: null, errors: [] };
  for (const [name, check] of [['adoption', checkAdoption], ['stories', checkStoryCoverage], ['designer', checkDesignerCoverage]]) {
    try {
      report[name] = check(root);
      report.errors.push(...report[name].errors);
    } catch (error) { report.errors.push(`${name}: ${error.message}`); }
  }
  try { report.errors.push(...checkBaselineHistory(root, baseRef)); }
  catch (error) { report.errors.push(error.message); }
  return report;
}

export function markdown(report) {
  const { adoption: a, stories: s, designer: d } = report;
  return [
    '## Design system coverage', '', '| Measure | Result |', '| --- | --- |',
    `| Direct control adoption | ${a ? `${a.compliantSites}/${a.compliantSites + a.rawSites} (${a.adoptionPercent === null ? "n/a" : a.adoptionPercent.toFixed(1)}%)` : 'Check failed'} |`,
    `| Grandfathered violation sites | ${a?.baselineSites ?? 'Check failed'} |`,
    `| Public visual components with registered stories | ${s ? `${s.coveredComponents}/${s.totalComponents}` : 'Check failed'} |`,
    `| Registered states resolving to stories | ${s ? `${s.coveredStates}/${s.totalStates}` : 'Check failed'} |`,
    `| Public components mapped in UI Designer | ${d ? `${d.coveredComponents}/${d.totalComponents}` : 'Check failed'} |`,
    `| Designer visual variants | ${d?.variants ?? 'Check failed'} |`,
    `| Gate errors | ${report.errors.length} |`, '',
    'Adoption counts direct JSX control sites in console and app source, not runtime usage. Registered state coverage is a declared contract, not exhaustive prop or code coverage. Story render, accessibility, interaction, and screenshot tests run separately.', '',
    ...report.errors.map(e => `- ${e.replaceAll('\n', ' ')}`), '',
  ].join('\n');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL('../../..', import.meta.url));
  const report = run(root);
  const output = resolve(root, '.ap/design-system-report.json');
  mkdirSync(dirname(output), {recursive: true});
  writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
  const summary = markdown(report);
  writeFileSync(resolve(root, '.ap/design-system-report.md'), summary);
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, summary);
  console.log(summary);
  console.log('Detailed report: .ap/design-system-report.json');
  process.exitCode = report.errors.length ? 1 : 0;
}
