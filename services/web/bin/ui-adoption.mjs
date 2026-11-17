import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const rootDir = fileURLToPath(new URL("../../../", import.meta.url));
const baselinePath = "packages/ui/adoption-baseline.json";
const rawControls = new Set(["button", "input", "select", "textarea", "dialog"]);
const controlComponents = new Set([
  "Button", "ChipButton", "Input", "Select", "Textarea", "CodeEditor",
  "ConfirmDialog", "FormDialog", "InfoDialog", "ThemeToggle",
]);
const alternativeUi = /^(?:@radix-ui\/|@headlessui\/|@base-ui(?:-components)?\/|@react-aria\/|@mui\/|@material-ui\/|@chakra-ui\/|@mantine\/|@ark-ui\/|@fluentui\/|@ariakit\/|@ant-design\/|@heroui\/|@nextui-org\/|(?:antd|ariakit|daisyui|flowbite-react|react-aria|react-aria-components|react-bootstrap|react-select|semantic-ui-react|primereact|radix-ui|evergreen-ui|grommet|shadcn|shadcn-ui|tailwindcss-animate)(?:\/|$))/;
const uiSource = (module) => module.split("?")[0];
const defaultExports = JSON.parse(readFileSync(join(rootDir, "packages/ui/package.json"), "utf8")).exports;

function importSpecifier(node) {
  if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) return node.moduleSpecifier;
  if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
    return node.moduleReference.expression;
  }
  if (ts.isCallExpression(node) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
       (ts.isIdentifier(node.expression) && node.expression.text === "require"))) {
    return node.arguments[0];
  }
}

function fingerprint(node, sourceFile) {
  const tokens = [];
  function collect(part) {
    const children = part.getChildren(sourceFile);
    if (children.length) {
      children.forEach(collect);
    } else if (part.end > part.getStart(sourceFile)) {
      const value = ts.isStringLiteral(part) ? part.text : part.getText(sourceFile);
      tokens.push([part.kind, ts.isJsxText(part) ? value.replace(/\s+/g, " ").trim() : value]);
    }
  }
  collect(node);
  return createHash("sha256").update(JSON.stringify(tokens)).digest("hex");
}

export function analyzeSource(source, file, publicExports = defaultExports) {
  const sourceFile = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  const options = { noResolve: true, noLib: true, allowJs: true, types: [], jsx: ts.JsxEmit.Preserve };
  const host = ts.createCompilerHost(options);
  host.getSourceFile = (name) => name === file ? sourceFile : undefined;
  const checker = ts.createProgram([file], options, host).getTypeChecker();
  const bindings = new Map();
  const components = [];
  const controls = [];
  const violations = [];
  const line = (node) => sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;

  function record(node, kind, name, message) {
    violations.push({
      file, line: line(node), kind, name,
      signature: fingerprint(node, sourceFile),
      message,
    });
  }

  for (const statement of sourceFile.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue;
    const module = uiSource(statement.moduleSpecifier.text);
    const exportsKey = module === "@ap/ui" ? "." : module.replace(/^@ap\/ui\//, "./");
    if (!Object.hasOwn(publicExports, exportsKey) || statement.importClause?.isTypeOnly) continue;
    const named = statement.importClause?.namedBindings;
    if (named && ts.isNamespaceImport(named)) {
      bindings.set(checker.getSymbolAtLocation(named.name), { namespace: true });
    }
    if (named && ts.isNamedImports(named)) {
      for (const entry of named.elements) {
        if (!entry.isTypeOnly) {
          bindings.set(checker.getSymbolAtLocation(entry.name), { name: (entry.propertyName ?? entry.name).text });
        }
      }
    }
  }

  function visit(node) {
    const specifier = importSpecifier(node);
    if (specifier && ts.isStringLiteralLike(specifier)) {
      const module = uiSource(specifier.text);
      if (alternativeUi.test(module)) {
        record(node, "alternative-library", module, `Import UI primitives from @ap/ui instead of ${module}`);
      } else if ((module.startsWith("@ap/ui/") && !Object.hasOwn(publicExports, "." + module.slice(6))) ||
                 (module.startsWith(".") && /(?:^|\/)packages\/ui(?:\/|$)/.test(resolve(dirname(file), module).split(sep).join("/")))) {
        record(node, "private-ui-import", module, `Use a public @ap/ui export instead of ${module}`);
      }
    }

    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName;
      let component;
      if (ts.isIdentifier(tag)) {
        if (rawControls.has(tag.text)) {
          record(node, "raw-control", tag.text, `Replace raw <${tag.text}> with an @ap/ui primitive`);
        }
        component = bindings.get(checker.getSymbolAtLocation(tag))?.name;
      } else if (ts.isPropertyAccessExpression(tag) && ts.isIdentifier(tag.expression)) {
        if (bindings.get(checker.getSymbolAtLocation(tag.expression))?.namespace) component = tag.name.text;
      }
      if (component && /^[A-Z]/.test(component)) {
        const site = { file, line: line(node), component };
        components.push(site);
        if (controlComponents.has(component)) controls.push(site);
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return { components, controls, violations };
}

function findSources(directory) {
  if (!existsSync(directory)) return [];
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory() &&
        !/^(?:__.*__|tests?|specs?|fixtures?|stories|node_modules)$/.test(entry.name)) {
      files.push(...findSources(path));
    }
    if (entry.isFile() && /\.[cm]?[jt]sx?$/.test(entry.name) &&
        !/\.(?:d|stories|story|test|spec|fixture)\.[cm]?[jt]sx?$/.test(entry.name)) {
      files.push(path);
    }
  }
  return files;
}

export function scanAdoption(root = rootDir) {
  const appPaths = readdirSync(join(root, "apps"), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => join(root, "apps", entry.name, "frontend", "src"));
  const files = [join(root, "services/web/src"), ...appPaths].flatMap(findSources).sort();
  const publicExports = JSON.parse(readFileSync(join(root, "packages/ui/package.json"), "utf8")).exports;
  const report = { files: files.length, components: [], controls: [], violations: [] };
  for (const file of files) {
    const analysis = analyzeSource(readFileSync(file, "utf8"), relative(root, file).split(sep).join("/"), publicExports);
    report.components.push(...analysis.components);
    report.controls.push(...analysis.controls);
    report.violations.push(...analysis.violations);
  }
  const compliantSites = report.controls.length;
  const rawSites = report.violations.filter((entry) => entry.kind === "raw-control").length;
  const totalSites = compliantSites + rawSites;
  return { ...report, compliantSites, rawSites, adoptionPercent: totalSites ? compliantSites * 100 / totalSites : null };
}

function baselineKey({ file, kind, signature }) {
  return JSON.stringify([file, kind, signature]);
}

export function shrinkBaseline(violations, baseline) {
  const observed = new Map();
  for (const violation of violations) {
    const key = baselineKey(violation);
    observed.set(key, (observed.get(key) ?? 0) + 1);
  }
  return baseline
    .map((entry) => ({ ...entry, count: Math.min(entry.count, observed.get(baselineKey(entry)) ?? 0) }))
    .filter((entry) => entry.count > 0);
}

export function compareBaseline(violations, baseline) {
  const remaining = new Map();
  const errors = [];
  for (const entry of baseline) {
    const key = baselineKey(entry);
    if (remaining.has(key) || !Number.isSafeInteger(entry.count) || entry.count < 1) {
      errors.push(`Invalid or duplicate baseline entry: ${entry.file} ${entry.name} (${entry.signature})`);
      continue;
    }
    remaining.set(key, { ...entry });
  }
  let baselineSites = 0;
  for (const violation of violations) {
    const entry = remaining.get(baselineKey(violation));
    if (entry?.count > 0) {
      entry.count--;
      baselineSites++;
    } else {
      errors.push(`${violation.file}:${violation.line}: ${violation.message}`);
    }
  }
  for (const entry of remaining.values()) {
    if (entry.count > 0) {
      errors.push(`Stale baseline: ${entry.file} ${entry.name} (${entry.signature}). Reduce its count by ${entry.count}, or remove the entry if its count reaches zero.`);
    }
  }
  return { baselineSites, errors };
}

export function checkAdoption(root = rootDir) {
  const analysis = scanAdoption(root);
  const baseline = JSON.parse(readFileSync(join(root, baselinePath), "utf8"));
  return { ...analysis, ...compareBaseline(analysis.violations, baseline) };
}

function main() {
  const option = process.argv[2];
  if (process.argv.length > 3 || (option && !["--json", "--shrink-baseline"].includes(option))) {
    throw new Error("Usage: node ui-adoption.mjs [--json | --shrink-baseline]");
  }
  if (option === "--shrink-baseline") {
    const path = join(rootDir, baselinePath);
    const baseline = JSON.parse(readFileSync(path, "utf8"));
    const shrunk = shrinkBaseline(scanAdoption().violations, baseline);
    if (JSON.stringify(shrunk) !== JSON.stringify(baseline)) {
      writeFileSync(path, JSON.stringify(shrunk, null, 2) + "\n");
    }
  }
  const report = checkAdoption();
  if (option === "--json") {
    console.log(JSON.stringify(report, null, 2));
  } else {
    const rate = report.adoptionPercent === null ? "n/a" : `${report.adoptionPercent.toFixed(1)}%`;
    console.log(`@ap/ui JSX control sites: ${report.compliantSites}/${report.compliantSites + report.rawSites} (${rate}) across ${report.files} source files. ${report.baselineSites} baseline violations.`);
    for (const error of report.errors) console.error(error);
  }
  if (report.errors.length) process.exitCode = 1;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main();
}
