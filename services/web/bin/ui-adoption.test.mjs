import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { analyzeSource, checkAdoption, compareBaseline, shrinkBaseline } from "./ui-adoption.mjs";

const file = "services/web/src/Example.tsx";

test("counts aliased and namespace imports and respects lexical shadowing", () => {
  const result = analyzeSource(`
    import { Button as Action, Banner } from "@ap/ui";
    import * as Fields from "@ap/ui/field";
    import type { ChipButton } from "@ap/ui/chip";
    function Other(Action) { return <Action />; }
    function Another(Fields) { return <Fields.Input />; }
    export const site = <><Action /><Fields.Input /><Banner /><ChipButton /><Local.Button /></>;
  `, file);
  assert.deepEqual(result.components.map(({ component }) => component), ["Button", "Input", "Banner"]);
  assert.deepEqual(result.controls.map(({ component }) => component), ["Button", "Input"]);
  assert.deepEqual(result.violations, []);
});

test("finds all audited JSX controls and ignores text, comments, and custom elements", () => {
  const result = analyzeSource(`
    // <button /> import { Select } from "@mui/material"
    const markup = "<input />";
    const demo = <><button /><input /><select /><textarea /><dialog /><custom-button /></>;
  `, file);
  assert.deepEqual(result.violations.map(({ name }) => name), ["button", "input", "select", "textarea", "dialog"]);
});

test("finds alternative and private UI imports across module syntax", () => {
  const result = analyzeSource(`
    import { Dialog } from "@radix-ui/react-dialog";
    import "@ap/ui/tokens.css";
    import theme from "@ap/ui/report-kit.css?raw";
    export { default } from "@mui/material/Button";
    const component = import("@ap/ui/src/button");
    const legacy = require("../../../packages/ui/src/button");
  `, file);
  assert.deepEqual(result.violations.map(({ kind, name }) => [kind, name]), [
    ["alternative-library", "@radix-ui/react-dialog"],
    ["alternative-library", "@mui/material/Button"],
    ["private-ui-import", "@ap/ui/src/button"],
    ["private-ui-import", "../../../packages/ui/src/button"],
  ]);
});

test("allows only the exact baseline multiplicity and ignores line and formatting changes", () => {
  const { violations } = analyzeSource('const old = <button type="button" />;', file);
  const baseline = [{ ...violations[0], count: 1 }];
  const formatted = analyzeSource('\n\nconst old = <button\n type="button"\n/>;', file);
  assert.deepEqual(compareBaseline(formatted.violations, baseline), { baselineSites: 1, errors: [] });

  const duplicated = analyzeSource('const old = <button type="button" />;\nconst extra = <button type="button" />;', file);
  assert.deepEqual(compareBaseline(duplicated.violations, baseline), {
    baselineSites: 1,
    errors: [`${file}:2: Replace raw <button> with an @ap/ui primitive`],
  });

  const changed = analyzeSource('const old = <button type="submit" />;', file);
  const comparison = compareBaseline(changed.violations, baseline);
  assert.equal(comparison.baselineSites, 0);
  assert.match(comparison.errors[0], /Replace raw <button>/);
  assert.match(comparison.errors[1], /Stale baseline.*Reduce its count by 1/);
  assert.match(compareBaseline([], [...baseline, ...baseline]).errors[0], /duplicate baseline entry/);
});

test("scans production sources across workspaces and reports audited JSX percentages", (t) => {
  const root = mkdtempSync(join(tmpdir(), "ui-adoption-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  function write(path, content) {
    const absolute = join(root, path);
    mkdirSync(dirname(absolute), { recursive: true });
    writeFileSync(absolute, content);
  }
  write("packages/ui/package.json", JSON.stringify({ exports: { "./button": "./src/button.tsx" } }));
  write("packages/ui/adoption-baseline.json", "[]");
  write("packages/ui/src/button.tsx", "const site = <button />;");
  write(file, 'import { Button } from "@ap/ui/button"; const site = <Button />;');
  write("services/web/src/Example.test.tsx", "const site = <dialog />;");
  write("services/web/src/Example.stories.tsx", "const site = <select />;");
  write("services/web/src/fixtures/Example.tsx", "const site = <input />;");
  write("apps/sample/frontend/src/Example.tsx", "const site = <textarea />;");
  const result = checkAdoption(root);
  assert.equal(result.files, 2);
  assert.equal(result.compliantSites, 1);
  assert.equal(result.rawSites, 1);
  assert.equal(result.adoptionPercent, 50);
  assert.deepEqual(result.errors, [
    "apps/sample/frontend/src/Example.tsx:1: Replace raw <textarea> with an @ap/ui primitive",
  ]);
});

test("shrinking can remove old sites and can never add new sites or increase a count", () => {
  const { violations: old } = analyzeSource("const demo = <><button /><input /><dialog /></>;", file);
  const baseline = old.map((violation, index) => ({ ...violation, count: index === 0 ? 2 : 1 }));
  const { violations: current } = analyzeSource("const demo = <><button /><input /><input /><select /></>;", file);
  const shrunk = shrinkBaseline(current, baseline);
  assert.deepEqual(shrunk, [{ ...baseline[0], count: 1 }, baseline[1]]);
  assert.equal(compareBaseline(current, shrunk).errors.length, 2);
});
