import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

// Resolve the reporter's own YAML instance, including when the project's
// parser is js-yaml 5 (which no longer has an ESM default export). supertap 3
// needs js-yaml 4's default export and still calls safeDump rather than dump.
const avaRequire = createRequire(import.meta.resolve('ava'));
const reporterRequire = createRequire(avaRequire.resolve('supertap'));
const manifestPath = reporterRequire.resolve('js-yaml/package.json');
const manifest = reporterRequire('js-yaml/package.json');
// Patch the ESM instance used by supertap, rather than its separate CJS build.
const entry = manifest.exports['.'].import;
const { default: yaml } = await import(new URL(entry, pathToFileURL(manifestPath)).href);
yaml.safeDump = yaml.dump;

await import(new URL('cli.js', import.meta.resolve('ava')).href);
