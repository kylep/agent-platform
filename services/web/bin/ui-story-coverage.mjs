import ts from 'typescript';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, relative } from 'node:path';

const readJSON = (path) => JSON.parse(readFileSync(path, 'utf8'));

// Uppercase runtime exports are the public visual API. Types, hooks, and
// lowercase helpers do not inflate the denominator. Include subpath exports
// as well as the barrel so a new primitive cannot escape registration.
export function publicComponents(root) {
  const pkg = readJSON(resolve(root, 'packages/ui/package.json'));
  const modules = Object.entries(pkg.exports)
    .filter(([, target]) => typeof target === 'string' && /\.tsx?$/.test(target))
    .map(([subpath, target]) => [subpath === '.' ? '@ap/ui' : `@ap/ui${subpath.slice(1)}`, resolve(root, 'packages/ui', target)]);
  const program = ts.createProgram(modules.map(([, path]) => path), {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler, skipLibCheck: true,
  });
  const checker = program.getTypeChecker();
  const components = new Map();
  for (const [specifier, path] of modules) {
    const source = program.getSourceFile(path);
    const module = source && checker.getSymbolAtLocation(source);
    if (!module) throw new Error(`Cannot inspect public module ${specifier}: ${path}`);
    for (const symbol of checker.getExportsOfModule(module)) {
      const name = symbol.getName();
      const typeOnly = symbol.declarations?.length && symbol.declarations.every(d =>
        ts.isExportSpecifier(d) && (d.isTypeOnly || d.parent.parent.isTypeOnly));
      if (typeOnly) continue;
      const target = symbol.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(symbol) : symbol;
      if (!/^[A-Z]/.test(name) || !(target.flags & ts.SymbolFlags.Value)) continue;
      if (!components.has(name)) components.set(name, []);
      components.get(name).push(specifier);
    }
  }
  return components;
}

export function validateCoverage(manifest, components, index) {
  const errors = [];
  const entries = index.entries ?? {};
  const seen = new Set();
  const covered = new Set();
  let totalStates = 0;
  let coveredStates = 0;
  if (manifest.schema !== 1 || !Array.isArray(manifest.components)) {
    return { errors: ['Invalid story-coverage.json: expected schema 1 and components array'], totalComponents: components.size, coveredComponents: 0, totalStates: 0, coveredStates: 0 };
  }
  for (const component of manifest.components) {
    if (seen.has(component.name)) errors.push(`Duplicate component registration: ${component.name}`);
    seen.add(component.name);
    const validExport = components.get(component.name)?.includes(component.import);
    if (!validExport) {
      errors.push(`Not a public visual export: ${component.import} / ${component.name}`);
    }
    if (!Array.isArray(component.states) || !component.states.length) {
      errors.push(`${component.name}: declare at least one meaningful state`);
      continue;
    }
    const states = new Set();
    let complete = true;
    for (const state of component.states) {
      totalStates++;
      if (typeof state.name !== 'string' || !state.name.trim() || states.has(state.name)) {
        errors.push(`${component.name}: empty or duplicate state name ${state.name}`);
        complete = false;
      }
      states.add(state.name);
      if (entries[state.story]?.type !== 'story') {
        errors.push(`${component.name} / ${state.name}: missing story ${state.story}`);
        complete = false;
      } else coveredStates++;
    }
    if (complete && validExport) covered.add(component.name);
  }
  for (const name of components.keys()) {
    if (!seen.has(name)) errors.push(`Public component missing stories: ${name}`);
  }
  return { errors, totalComponents: components.size, coveredComponents: covered.size, totalStates, coveredStates };
}

export function checkStoryCoverage(root) {
  const manifest = readJSON(resolve(root, 'packages/ui/story-coverage.json'));
  const indexPath = resolve(root, 'services/web/storybook-static/index.json');
  if (!existsSync(indexPath)) throw new Error('Build Storybook first: npm run build:storybook -w web');
  const index = readJSON(indexPath);
  const result = validateCoverage(manifest, publicComponents(root), index);
  // An old build must not hide a deleted/renamed story export. The command
  // workflow rebuilds first; this also gives direct callers a useful failure.
  for (const id of new Set((manifest.components ?? []).flatMap(c => (c.states ?? []).map(s => s.story)))) {
    const entry = index.entries[id];
    if (!entry || entry.type !== 'story') continue;
    const path = resolve(root, 'services/web', entry.importPath);
    if (!existsSync(path)) {
      result.errors.push(`Stale Storybook build: ${relative(root, path)} is missing`);
      continue;
    }
    const source = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const hasExport = source.statements.some(node =>
      ts.isVariableStatement(node) && node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword) &&
      node.declarationList.declarations.some(d => ts.isIdentifier(d.name) && d.name.text === entry.exportName));
    if (!hasExport) result.errors.push(`Stale Storybook build: export ${entry.exportName} missing from ${relative(root, path)}`);
  }
  return result;
}
