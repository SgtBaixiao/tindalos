import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

const root = path.resolve('src');
function files(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const name = path.join(dir, entry.name);
    return entry.isDirectory() ? files(name) : /\.tsx?$/.test(name) ? [name] : [];
  });
}
const allowed = {
  ui: ['ui'],
  lib: ['lib'],
  store: ['store', 'lib'],
  features: ['features', 'lib', 'ui'],
  components: ['components', 'lib', 'store', 'ui'],
};
const graph = new Map();
const errors = [];
const relative = (file) => path.relative(root, file);
for (const file of files(root)) {
  const source = ts.createSourceFile(
    file,
    readFileSync(file, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
  );
  const dependencies = [];
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement) && !ts.isExportDeclaration(statement)) continue;
    if (!statement.moduleSpecifier || !ts.isStringLiteral(statement.moduleSpecifier)) continue;
    const specifier = statement.moduleSpecifier.text;
    if (!specifier.startsWith('.') || specifier.endsWith('.css')) continue;
    const base = path.resolve(path.dirname(file), specifier);
    const target = ['', '.ts', '.tsx', '/index.ts', '/index.tsx']
      .map((ext) => base + ext)
      .find((candidate) => existsSync(candidate) && /\.tsx?$/.test(candidate));
    if (!target) {
      errors.push(`${relative(file)}: unresolved import ${specifier}`);
      continue;
    }
    const from = relative(file).split(path.sep)[0];
    const to = relative(target).split(path.sep)[0];
    if (allowed[from] && !allowed[from].includes(to))
      errors.push(`${relative(file)} → ${relative(target)}: forbidden dependency`);
    const typeOnly = ts.isImportDeclaration(statement)
      ? statement.importClause?.isTypeOnly ||
        (statement.importClause &&
          !statement.importClause.name &&
          statement.importClause.namedBindings &&
          ts.isNamedImports(statement.importClause.namedBindings) &&
          statement.importClause.namedBindings.elements.every((element) => element.isTypeOnly))
      : statement.isTypeOnly;
    if (!typeOnly) dependencies.push(target);
  }
  graph.set(file, dependencies);
}
const visited = new Set();
const active = [];
function visit(file) {
  if (active.includes(file)) {
    errors.push(
      `Runtime cycle: ${[...active.slice(active.indexOf(file)), file].map(relative).join(' → ')}`,
    );
    return;
  }
  if (visited.has(file)) return;
  active.push(file);
  for (const dependency of graph.get(file) ?? []) visit(dependency);
  active.pop();
  visited.add(file);
}
for (const file of graph.keys()) visit(file);
const eager = new Set();
function entryDependencies(file) {
  if (eager.has(file)) return;
  eager.add(file);
  for (const dependency of graph.get(file) ?? []) entryDependencies(dependency);
}
entryDependencies(path.join(root, 'main.tsx'));
for (const file of ['App.tsx', 'store/useGraphStore.ts', 'lib/scriptGraph.ts']) {
  if (eager.has(path.join(root, file)))
    errors.push(`Entry eagerly imports ${file}; keep graph code behind the workbench route.`);
}
if (errors.length) {
  console.error(errors.join('\n'));
  process.exitCode = 1;
} else console.log(`Module directions and runtime cycles checked: ${graph.size} source files.`);
