import assert from "node:assert/strict";
import test from "node:test";

import { physicallyDeleteStressCookies } from "./stress_cookie_jar.mjs";

test("stress deletion removes prior tombstones and preserves every other row", async () => {
  const hosts = ["seed.rookie-0.test", "seed.rookie-1.test"];
  const cookie = (domain, name, path = "/") => ({
    domain,
    path,
    name,
    value: name,
    secure: true,
    httpOnly: true,
    sameSite: "Lax",
    expires: 4_102_444_800,
  });
  const survivors = [
    { ...cookie(hosts[0], "stress_0_0"), value: "updated-1" },
    cookie(hosts[0], "stress_0_3"),
    { ...cookie(hosts[0], "stress_0_round_1"), value: "added-1" },
    { ...cookie(hosts[1], "stress_1_0"), value: "updated-1" },
    { ...cookie(hosts[1], "stress_1_round_1"), value: "added-1" },
    cookie(hosts[0], "stress_shared"),
    cookie(hosts[1], "stress_0_1"),
    cookie(hosts[0], "stress_0_1", "/other"),
    cookie("churn.rookie-0.test", "churn_0"),
    cookie("unrelated.test", "stress_0_1"),
  ];
  let jar = [
    ...survivors,
    cookie(hosts[0], "stress_0_1"),
    cookie(hosts[0], "stress_0_2"),
    cookie(`.${hosts[1]}`, "stress_1_1"),
    cookie(hosts[1], "stress_1_2"),
  ];
  let cleared = false;
  await physicallyDeleteStressCookies(
    {
      cookies: async () => jar,
      async clearCookies(...args) {
        assert.deepEqual(args, []);
        jar = [];
        cleared = true;
      },
      async addCookies(rows) {
        assert.equal(cleared, true);
        jar.push(...rows);
      },
    },
    hosts,
    1,
  );
  assert.deepEqual(jar, survivors);
});

test("stress deletion clears storage even when the browser hides expired rows", async () => {
  const survivors = [
    {
      domain: "seed.rookie-0.test",
      path: "/",
      name: "stress_0_0",
      value: "updated-0",
    },
    {
      domain: "seed.rookie-0.test",
      path: "/",
      name: "stress_0_round_0",
      value: "added-0",
    },
  ];
  let cleared = false;
  let restored;
  await physicallyDeleteStressCookies(
    {
      cookies: async () => survivors,
      async clearCookies() {
        cleared = true;
      },
      async addCookies(rows) {
        restored = rows;
      },
    },
    ["seed.rookie-0.test"],
    0,
  );
  assert.equal(cleared, true);
  assert.deepEqual(restored, survivors);
});

test("stress deletion waits for both mutation headers on every host before clearing", async () => {
  const hosts = ["seed.rookie-0.test", "seed.rookie-1.test"];
  const row = (hostIndex, name, value) => ({
    domain: hosts[hostIndex],
    path: "/",
    name,
    value,
  });
  const survivors = [
    row(0, "stress_0_0", "updated-1"),
    row(0, "stress_0_round_1", "added-1"),
    row(1, "stress_1_0", "updated-1"),
    row(1, "stress_1_round_1", "added-1"),
  ];
  const snapshots = [
    // Wrong values and paths must not count as committed mutation headers.
    [...survivors.slice(0, 3), row(1, "stress_1_round_1", "added-0")],
    [...survivors.slice(0, 3), { ...survivors[3], path: "/other" }],
    [...survivors.slice(0, 2), row(1, "stress_1_0", "updated-0"), survivors[3]],
    survivors,
  ];
  let reads = 0;
  let rawTombstone = false;
  let restored;
  await physicallyDeleteStressCookies(
    {
      async cookies() {
        const snapshot = snapshots[reads++];
        // Model a late deletion header creating a row hidden by cookies().
        if (reads === snapshots.length) rawTombstone = true;
        return snapshot;
      },
      async clearCookies() {
        assert.equal(reads, snapshots.length, "clear must follow all header commits");
        assert.equal(rawTombstone, true);
        rawTombstone = false;
      },
      async addCookies(rows) {
        restored = rows;
      },
    },
    hosts,
    1,
    2000,
  );
  assert.equal(rawTombstone, false);
  assert.deepEqual(restored, survivors);
});

test("uncommitted mutation times out without clearing the jar", async () => {
  await assert.rejects(
    physicallyDeleteStressCookies(
      {
        cookies: async () => [],
        async clearCookies() {
          assert.fail("uncommitted jar must stay intact");
        },
        async addCookies() {
          assert.fail("uncommitted jar must stay intact");
        },
      },
      ["seed.rookie-0.test"],
      0,
      0,
    ),
    /mutation 0 headers never settled/,
  );
});
