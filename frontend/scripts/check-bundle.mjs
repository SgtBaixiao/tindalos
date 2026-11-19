import { readFileSync, writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const manifest = JSON.parse(readFileSync('dist/.vite/manifest.json', 'utf8'));
const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry);
if (!entryKey) throw new Error('Build manifest has no entry. Run npm run build first.');
const reached = new Set();
function walk(key) {
  if (reached.has(key)) return;
  reached.add(key);
  for (const dependency of manifest[key].imports ?? []) walk(dependency);
}
walk(entryKey);
if ([...reached].some((key) => /WorkbenchView/.test(key)))
  throw new Error('Workbench must remain a deferred route.');
const entryFiles = [
  ...new Set([...reached].flatMap((key) => [manifest[key].file, ...(manifest[key].css ?? [])])),
];
function size(file) {
  const contents = readFileSync(`dist/${file}`);
  return { file, bytes: contents.length, gzip_bytes: gzipSync(contents, { level: 9 }).length };
}
const measured = entryFiles.map(size);
const total = (ext, field) =>
  measured.filter((file) => file.file.endsWith(ext)).reduce((sum, file) => sum + file[field], 0);
const budgets = { js_bytes: 230000, js_gzip_bytes: 75000, css_bytes: 16000, css_gzip_bytes: 5000 };
const actual = {
  js_bytes: total('.js', 'bytes'),
  js_gzip_bytes: total('.js', 'gzip_bytes'),
  css_bytes: total('.css', 'bytes'),
  css_gzip_bytes: total('.css', 'gzip_bytes'),
};
for (const [key, limit] of Object.entries(budgets))
  if (actual[key] > limit) throw new Error(`${key}: ${actual[key]} exceeds ${limit}`);
const routes = Object.fromEntries(
  Object.entries(manifest)
    .filter(([, value]) => value.isDynamicEntry)
    .map(([key, value]) => [key, [value.file, ...(value.css ?? [])].map(size)]),
);
const report = {
  stage: 'after-ui-refactor',
  compression: 'gzip level 9',
  files: measured,
  totals: actual,
  budgets,
  routes,
};
if (process.argv.includes('--write'))
  writeFileSync('../benchmarks/ui-build-after.json', JSON.stringify(report, null, 2) + '\n');
console.log(
  JSON.stringify({ entry: actual, budgets, deferredRoutes: Object.keys(routes).length }, null, 2),
);
