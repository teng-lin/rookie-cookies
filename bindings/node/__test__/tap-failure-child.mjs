import test from 'ava';

test('intentional reporter failure', t => {
  t.is(1, 2);
});
