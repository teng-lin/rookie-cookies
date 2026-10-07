import test from 'ava';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';

const cwd = fileURLToPath(new URL('..', import.meta.url));

test('TAP failures include YAML diagnostics without crashing the reporter', t => {
  const result = spawnSync(process.execPath, [
    fileURLToPath(new URL('../scripts/test.mjs', import.meta.url)),
    '--tap',
    '__test__/tap-failure-child.mjs',
  ], { cwd, encoding: 'utf8', timeout: 60000 });

  t.falsy(result.error);
  t.is(result.status, 1, result.stderr);
  t.regex(result.stdout, /not ok 1 - intentional reporter failure/, result.stderr);
  const diagnostics = result.stdout.match(/  ---\n([\s\S]*?)  \.\.\./);
  t.truthy(diagnostics);
  const error = yaml.load(diagnostics[1]);
  t.is(error.name, 'AssertionError');
  t.is(error.assertion, 't.is()');
  t.regex(Object.values(error.details).join('\n'), /- 1\n\+ 2/);
  t.notRegex(result.stdout + result.stderr, /safeDump is removed|uncaught exception/i);
});
