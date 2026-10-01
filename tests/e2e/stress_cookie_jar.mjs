/**
 * Wait for every host's mutation headers, then physically remove tombstones.
 * Firefox can retain expired rows in its jar and moz_cookies. With churn paused
 * in a disposable profile, clear the jar and restore all surviving cookies.
 * @param {import("playwright").BrowserContext} context Disposable browser context.
 * @param {string[]} hosts Stress hosts in their corpus index order.
 * @param {number} round Current mutation round.
 * @param {number} timeoutMs Maximum wait for updated and added cookies.
 */
export async function physicallyDeleteStressCookies(
  context,
  hosts,
  round,
  timeoutMs = 15000,
) {
  const forbidden = new Set();
  const required = new Map();
  for (const [hostIndex, host] of hosts.entries()) {
    required.set(`${host}\0/\0stress_${hostIndex}_0`, `updated-${round}`);
    required.set(
      `${host}\0/\0stress_${hostIndex}_round_${round}`,
      `added-${round}`,
    );
    for (let index = 1; index <= round + 1; index += 1) {
      forbidden.add(`${host}\0/\0stress_${hostIndex}_${index}`);
    }
  }

  // DOMContentLoaded does not order Firefox's parent-process cookie commits.
  // The added cookie is the last Set-Cookie header in each mutation response;
  // observe both it and the update before clearing so no late header can
  // recreate a tombstone or overwrite the restored snapshot.
  const deadline = Date.now() + timeoutMs;
  let cookies;
  while (true) {
    cookies = await context.cookies();
    const pending = new Map(required);
    for (const { domain, path, name, value } of cookies) {
      const identity = `${domain.replace(/^\./, "")}\0${path}\0${name}`;
      if (pending.get(identity) === value) pending.delete(identity);
    }
    if (pending.size === 0) break;
    if (Date.now() >= deadline) {
      throw new Error(
        `Firefox stress mutation ${round} headers never settled; missing=${JSON.stringify([...pending.keys()])}`,
      );
    }
    await new Promise((accept) => setTimeout(accept, 100));
  }
  const survivors = cookies.filter(
    ({ domain, path, name }) =>
      !forbidden.has(`${domain.replace(/^\./, "")}\0${path}\0${name}`),
  );
  // A name-filtered clear has also intermittently retained Firefox tombstones.
  // Churn is paused by the caller, so restoring the complete jar is safe.
  await context.clearCookies();
  if (survivors.length > 0) await context.addCookies(survivors);
}
