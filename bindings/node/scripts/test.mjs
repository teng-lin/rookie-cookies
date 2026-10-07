import yaml from 'js-yaml';

// supertap 3 still calls safeDump. Adapt its API before AVA loads the TAP
// reporter so js-yaml 4 can replace the vulnerable js-yaml 3 dependency chain.
yaml.safeDump = yaml.dump;

await import(new URL('cli.js', import.meta.resolve('ava')).href);
