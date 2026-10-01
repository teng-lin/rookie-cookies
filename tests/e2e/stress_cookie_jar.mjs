// Firefox can retain Max-Age=0 tombstones in its jar and moz_cookies. Clear
// the disposable jar through the browser and restore every surviving row so
// raw-inventory extraction can verify physical absence of deleted identities.
export async function physicallyDeleteStressCookies(context, hosts, round) {
  const forbidden = new Set();
  for (const [hostIndex, host] of hosts.entries()) {
    for (let index = 1; index <= round + 1; index += 1) {
      forbidden.add(`${host}\0/\0stress_${hostIndex}_${index}`);
    }
  }
  const survivors = (await context.cookies()).filter(
    ({ domain, path, name }) =>
      !forbidden.has(`${domain.replace(/^\./, "")}\0${path}\0${name}`),
  );
  // A name-filtered clear has also intermittently retained Firefox tombstones.
  // Churn is paused by the caller, so restoring the complete jar is safe.
  await context.clearCookies();
  if (survivors.length > 0) await context.addCookies(survivors);
}
