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
    cookie(hosts[0], "stress_0_0"),
    cookie(hosts[0], "stress_0_3"),
    cookie(hosts[0], "stress_0_round_1"),
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
  let cleared = false;
  await physicallyDeleteStressCookies(
    {
      cookies: async () => [],
      async clearCookies() {
        cleared = true;
      },
      async addCookies() {
        assert.fail("empty jar should not need restoration");
      },
    },
    ["seed.rookie-0.test"],
    0,
  );
  assert.equal(cleared, true);
});
