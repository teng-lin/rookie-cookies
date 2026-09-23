"""Job-layer `read` / `extract` / `jar` / `from_path` / `profiles` / `report` bindings."""

from __future__ import annotations

import http.cookiejar
import json
import sqlite3
import unittest
from contextlib import closing

import rookie_cookies

from export_contract import (
    current_platform,
    preferred_root,
    registry_entries,
    seed_browser,
)
from test_report_api import (
    _UNDECRYPTABLE,
    _chrome_root,
    _seed_chrome,
    _seed_chromium_profile,
    _synthetic_home,
)

_COOKIE_KEYS = {
    "domain",
    "path",
    "secure",
    "http_only",
    "same_site",
    "expires",
    "name",
    "value",
}


class ExtractJobTest(unittest.TestCase):
    def test_domain_filtering_and_named_helper_parity(self) -> None:
        with _synthetic_home() as home:
            root = _seed_chrome(home)
            with closing(sqlite3.connect(root / "Default" / "Network" / "Cookies")) as db:
                db.executemany(
                    "INSERT INTO cookies VALUES (?, '/', 0, 0, ?, 'value', X'', 0, 0)",
                    [
                        ("example.test", "exact"),
                        (".sub.EXAMPLE.test", "subdomain"),
                        ("notexample.test", "suffix-trap"),
                        ("example.test.evil.test", "prefix-trap"),
                        ("other.test", "other"),
                        ("exa_mple.test", "literal-underscore"),
                        ("exa%mple.test", "literal-percent"),
                    ],
                )
                db.commit()
            cases = [
                (
                    None,
                    {
                        "session",
                        "exact",
                        "subdomain",
                        "suffix-trap",
                        "prefix-trap",
                        "other",
                        "literal-underscore",
                        "literal-percent",
                    },
                ),
                ([], set()),
                (["example.test"], {"session", "exact", "subdomain"}),
                ([".EXAMPLE.TEST."], {"session", "exact", "subdomain"}),
                (
                    ["example.test", "other.test"],
                    {"session", "exact", "subdomain", "other"},
                ),
                (["example.test", "example.test"], {"session", "exact", "subdomain"}),
                (["missing.test"], set()),
                (["exa_mple.test"], {"literal-underscore"}),
                (["exa%mple.test"], {"literal-percent"}),
                (["example.test' OR 1=1 --"], set()),
            ]
            for domains, expected in cases:
                with self.subTest(domains=domains):
                    legacy = rookie_cookies.chrome(domains=domains)
                    for profile in (None, "Default"):
                        rows = rookie_cookies.extract(
                            browser="chrome",
                            profile=profile,
                            domains=domains,
                            app_bound="disabled",
                        )
                        self.assertEqual({row["name"] for row in rows}, expected)
                        self.assertEqual(len(rows), len(expected))
                        self.assertEqual(
                            sorted(rows, key=lambda row: row["name"]),
                            sorted(legacy, key=lambda row: row["name"]),
                        )
                        for row in rows:
                            self.assertEqual(set(row), _COOKIE_KEYS)

    def test_filter_preserves_profile_selection(self) -> None:
        with _synthetic_home() as home:
            _seed_chrome(home)
            profile = next(
                item
                for item in rookie_cookies.profiles("chrome")
                if item["profile"]["display_name"] == "Profile 1"
            )["profile"]
            for selector in ("Profile 1", profile["profile_id"], profile["path"]):
                with self.subTest(selector=selector):
                    rows = rookie_cookies.extract(
                        browser="chrome", profile=selector, domains=["example.test"]
                    )
                    self.assertEqual([row["value"] for row in rows], ["profile-value"])

    def test_gecko_session_filter_is_independent_of_profile_selection(self) -> None:
        with _synthetic_home() as home:
            seed_browser(home, "firefox")
            root, _, _ = preferred_root(
                registry_entries(current_platform())["firefox"], home
            )
            profile = root / "Profiles" / "contract-release"
            (profile / "sessionstore.js").write_text(
                json.dumps(
                    {
                        "windows": [
                            {
                                "cookies": [
                                    {
                                        "host": ".sub.example.test",
                                        "path": "/",
                                        "name": "session-match",
                                        "value": "session-value",
                                    },
                                    {
                                        "host": ".other.test",
                                        "path": "/",
                                        "name": "session-excluded",
                                        "value": "excluded-value",
                                    },
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            for selector in (None, "contract-release"):
                for include_session in (False, True):
                    with self.subTest(
                        profile=selector, include_session=include_session
                    ):
                        rows = rookie_cookies.extract(
                            browser="firefox",
                            profile=selector,
                            domains=["example.test"],
                            include_session=include_session,
                        )
                        expected = (
                            {"contract", "session-match"}
                            if include_session
                            else {"contract"}
                        )
                        self.assertEqual({row["name"] for row in rows}, expected)
                        self.assertEqual(
                            rookie_cookies.extract(
                                browser="firefox",
                                profile=selector,
                                domains=[],
                                include_session=include_session,
                            ),
                            [],
                        )

    def test_extract_retains_expired_cookies_and_read_stays_unfiltered(self) -> None:
        with _synthetic_home() as home:
            root = _seed_chrome(home, profiles=("Default",))
            with closing(sqlite3.connect(root / "Default" / "Network" / "Cookies")) as db:
                db.execute(
                    "INSERT INTO cookies VALUES "
                    "('other.test', '/', 0, 11644473601000000, 'expired', 'value', X'', 0, 0)"
                )
                db.commit()
            rows = rookie_cookies.extract(browser="chrome", domains=["other.test"])
            self.assertEqual([row["name"] for row in rows], ["expired"])
            snapshot = rookie_cookies.read(browser="chrome", include_expired=True)
            self.assertEqual({row["name"] for row in snapshot}, {"session", "expired"})
            with self.assertRaises(TypeError):
                rookie_cookies.read(browser="chrome", domains=["other.test"])

    def test_request_validation_and_stopped_errors(self) -> None:
        with self.assertRaises(TypeError):
            rookie_cookies.extract()
        with self.assertRaises(TypeError):
            rookie_cookies.extract("chrome")
        with _synthetic_home() as home:
            _seed_chrome(home)
            for domains in ("example.test", [123]):
                with self.subTest(domains=domains), self.assertRaises(TypeError):
                    rookie_cookies.extract(browser="chrome", domains=domains)
            for options, code in (
                ({"browser": "not-a-browser"}, "unknown_browser"),
                ({"profile": "missing-profile"}, "unknown_profile"),
                ({"select": "all"}, "conflicting_profile_selection"),
                (
                    {"profile": "Default", "select": "all"},
                    "conflicting_profile_selection",
                ),
            ):
                with self.subTest(options=options):
                    with self.assertRaises(rookie_cookies.RookieRequestError) as caught:
                        rookie_cookies.extract(
                            **{"browser": "chrome", "domains": [], **options}
                        )
                    self.assertEqual(caught.exception.code, code)
            for options in (
                {"app_bound": "invalid"},
                {"timeout": -1},
                {"timeout": float("nan")},
                {"timeout": float("inf")},
            ):
                with (
                    self.subTest(options=options),
                    self.assertRaises(rookie_cookies.RookieRequestError),
                ):
                    rookie_cookies.extract(browser="chrome", **options)
            handle = rookie_cookies.CancellationHandle()
            handle.cancel()
            for options, reason in (
                ({"timeout": 0}, "timed_out"),
                ({"cancellation": handle}, "cancelled"),
            ):
                with self.subTest(options=options):
                    with self.assertRaises(rookie_cookies.RookieStoppedError) as caught:
                        rookie_cookies.extract(
                            browser="chrome", domains=["example.test"], **options
                        )
                    self.assertEqual(caught.exception.stop_reason, reason)


class JobApiTest(unittest.TestCase):
    def test_read_requires_browser(self) -> None:
        with self.assertRaises(TypeError):
            rookie_cookies.read()  # type: ignore[call-arg]

    def test_no_top_level_header(self) -> None:
        self.assertFalse(hasattr(rookie_cookies, "header"))
        self.assertNotIn("header", rookie_cookies.__all__)

    def test_as_list_schema_and_iter(self) -> None:
        with _synthetic_home() as home:
            _seed_chrome(home)
            result = rookie_cookies.read(browser="chrome", include_expired=True)
        rows = result.as_list()
        self.assertGreater(len(result), 0)
        self.assertTrue(result)
        for row, iterated in zip(rows, result):
            self.assertEqual(set(row), _COOKIE_KEYS)
            self.assertEqual(row, iterated)
            self.assertIsInstance(row["same_site"], int)

    def test_no_profile_read_set_equals_chrome(self) -> None:
        with _synthetic_home() as home:
            _seed_chrome(home)
            via_chrome = rookie_cookies.chrome()
            via_read = rookie_cookies.read(browser="chrome", include_expired=True).as_list()

        def key(row: dict) -> tuple:
            return (row["domain"], row["path"], row["name"], row["value"])

        self.assertEqual(sorted(via_chrome, key=key), sorted(via_read, key=key))

    def test_jar_is_not_url_filtered(self) -> None:
        with _synthetic_home() as home:
            _seed_chrome(home)
            jar = rookie_cookies.jar(browser="chrome", include_expired=True)
        self.assertIsInstance(jar, http.cookiejar.CookieJar)
        self.assertGreater(len(list(jar)), 0)

    def test_read_decrypt_failed_warning_uses_code_not_message(self) -> None:
        import sqlite3

        with _synthetic_home() as home:
            root = _chrome_root(home)
            _seed_chromium_profile(root, "Default", "plain")
            database = root / "Default" / "Network" / "Cookies"
            connection = sqlite3.connect(str(database))
            try:
                connection.execute(
                    "INSERT INTO cookies VALUES ('.example.test', '/', 0, 0, 'secret', '', ?, 0, 0)",
                    (_UNDECRYPTABLE,),
                )
                connection.commit()
            finally:
                connection.close()
            (root / "Local State").write_text("{}", encoding="utf-8")
            result = rookie_cookies.read(browser="chrome", include_expired=True)
        warning = next(item for item in result.warnings if item.code == "decrypt_failed")
        self.assertEqual(warning.count, 1)
        names = {row["name"] for row in result.as_list()}
        self.assertIn("session", names)

    def test_from_path_invalid_octets_warning_uses_code_not_message(self) -> None:
        import sqlite3
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "cookies.sqlite"
            connection = sqlite3.connect(str(db))
            try:
                connection.execute(
                    """
                    CREATE TABLE moz_cookies (
                      host TEXT NOT NULL,
                      path TEXT NOT NULL,
                      isSecure INTEGER NOT NULL,
                      expiry INTEGER NOT NULL,
                      name TEXT NOT NULL,
                      value TEXT NOT NULL,
                      isHttpOnly INTEGER NOT NULL,
                      sameSite INTEGER NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO moz_cookies VALUES ('.example.test', '/', 0, 4102444800, ?, 'x', 0, 0)",
                    ("sid\r",),
                )
                connection.commit()
            finally:
                connection.close()
            result = rookie_cookies.from_path(str(db), include_expired=True)
        warning = next(item for item in result.warnings if item.code == "invalid_octets")
        self.assertEqual(warning.count, 1)
        self.assertEqual(len(result), 0)

    def test_header_invalid_url_exposes_structured_request_error(self) -> None:
        import sqlite3
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "cookies.sqlite"
            connection = sqlite3.connect(str(db))
            try:
                connection.execute(
                    """
                    CREATE TABLE moz_cookies (
                      host TEXT NOT NULL,
                      path TEXT NOT NULL,
                      isSecure INTEGER NOT NULL,
                      expiry INTEGER NOT NULL,
                      name TEXT NOT NULL,
                      value TEXT NOT NULL,
                      isHttpOnly INTEGER NOT NULL,
                      sameSite INTEGER NOT NULL
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()
            result = rookie_cookies.from_path(str(db), include_expired=True)

        with self.assertRaises(rookie_cookies.RookieRequestError) as raised:
            result.header("not a url")
        self.assertEqual(raised.exception.kind, "request")
        self.assertEqual(raised.exception.code, "invalid_url")
        self.assertIsNone(raised.exception.stop_reason)

    def test_unknown_browser_is_request_error(self) -> None:
        with _synthetic_home():
            with self.assertRaises(rookie_cookies.RookieRequestError):
                rookie_cookies.read(browser="not-a-browser")
            with self.assertRaises(rookie_cookies.RookieRequestError):
                rookie_cookies.profiles("not-a-browser")
            with self.assertRaises(rookie_cookies.RookieRequestError):
                rookie_cookies.report("not-a-browser")

    def test_profiles_aliases_browser_profiles(self) -> None:
        with _synthetic_home() as home:
            _seed_chrome(home)
            self.assertEqual(
                rookie_cookies.profiles("chrome"),
                rookie_cookies.browser_profiles("chrome"),
            )


if __name__ == "__main__":
    unittest.main()
