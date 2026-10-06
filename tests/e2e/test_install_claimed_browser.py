"""The silent-install catalog must stay 1:1 with nightly hosted extra browsers."""

from __future__ import annotations

import ast
import glob
import hashlib
import importlib.util
import inspect
import io
import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_browser_coverage as coverage


MODULE_PATH = Path(__file__).with_name("install_claimed_browser.py")
SPEC = importlib.util.spec_from_file_location("install_claimed_browser", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)

# Read the branches install_spec() dispatches on straight out of its source so
# this stays in step with the installer. A catalog entry naming a kind the
# dispatch does not know fails to install and only surfaces as a red nightly
# cell.
def install_kinds() -> frozenset[str]:
    tree = ast.parse(inspect.getsource(INSTALL.install_spec))
    kinds = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for comparator in node.comparators:
            operands = (
                comparator.elts
                if isinstance(comparator, ast.Tuple)
                else [comparator]
            )
            kinds.update(
                operand.value
                for operand in operands
                if isinstance(operand, ast.Constant)
                and isinstance(operand.value, str)
            )
    return frozenset(kinds)


INSTALL_KINDS = install_kinds()

# Seeded by e2e.yml without this installer. The claimed-browser workflow now
# owns every other real-browser cell, including Playwright-distributed
# Chromium, image/Playwright Edge, and normal-profile Safari.
PREINSTALLED = frozenset(
    {
        ("linux", "chrome"),
        ("linux", "firefox"),
        ("macos", "chrome"),
        ("macos", "firefox"),
        ("windows", "chrome"),
        ("windows", "firefox"),
    }
)


class InstallCatalogTests(unittest.TestCase):
    def test_every_catalog_cell_is_nightly_hosted(self) -> None:
        catalog = {(row["platform"], row["browser"]) for row in INSTALL.matrix()}
        extra = coverage.NIGHTLY_HOSTED - PREINSTALLED
        self.assertEqual(catalog, extra)

    def test_windows_brave_includes_per_user_localappdata(self) -> None:
        exe = INSTALL.HOSTS["brave"]["windows"]["exe"]
        self.assertTrue(
            any("LocalAppData" in path and "brave.exe" in path for path in exe)
        )

    def test_opera_gx_macos_lists_opera_binary(self) -> None:
        exe = INSTALL.HOSTS["opera_gx"]["macos"]["exe"]
        self.assertTrue(any(path.endswith("/Opera") for path in exe))

    def test_native_engine_cells_use_vendor_drivers(self) -> None:
        safari = INSTALL.HOSTS["safari"]["macos"]
        internet_explorer = INSTALL.HOSTS["internet_explorer"]["windows"]
        self.assertEqual(safari["kind"], "system_browser")
        self.assertIn("/Applications/Safari.app/Contents/MacOS/Safari", safari["exe"])
        self.assertEqual(internet_explorer["kind"], "internet_explorer")
        self.assertFalse(INSTALL.HOSTS["internet_explorer"]["hosted"])
        self.assertEqual(internet_explorer["runner"], "windows-2022")
        self.assertIn("iedriver-win32", internet_explorer["exe"][0])
        self.assertTrue(
            any(
                path.endswith("IEDriverServer.exe") for path in internet_explorer["exe"]
            )
        )
        self.assertTrue(
            any(path.endswith("msedge.exe") for path in internet_explorer["edge_exe"])
        )

    def test_chromium_and_edge_have_official_playwright_install_fallbacks(self) -> None:
        for platform in INSTALL.RUNNERS:
            self.assertEqual(
                INSTALL.HOSTS["chromium"][platform]["kind"],
                "playwright_browser",
            )
            self.assertEqual(
                INSTALL.HOSTS["edge"][platform]["kind"],
                "playwright_channel",
            )

    def test_playwright_installer_uses_resolved_npx_shim(self) -> None:
        npx = r"C:\Program Files\nodejs\npx.CMD"
        with (
            mock.patch.object(INSTALL.shutil, "which", return_value=npx),
            mock.patch.object(INSTALL, "run") as run,
        ):
            INSTALL.install_playwright_product("chromium")
        run.assert_called_once_with(
            [npx, "playwright", "install", "chromium"],
            cwd=INSTALL.ROOT / "tests/e2e",
        )

    def test_winget_installer_does_not_query_the_region_gated_store(self) -> None:
        completed = INSTALL.subprocess.CompletedProcess([], 0)
        with mock.patch.object(
            INSTALL.subprocess, "run", return_value=completed
        ) as run:
            INSTALL.install_winget("Yandex.Browser")

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--source") + 1], "winget")
        self.assertIn("--accept-source-agreements", command)

    def test_brew_refreshes_stale_metadata_and_retries_once(self) -> None:
        """A stale cask triggers one refresh and at most one installation retry."""
        with mock.patch.object(
            INSTALL.subprocess, "run",
            side_effect=[
                subprocess.CompletedProcess([], 1),
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 1),
            ],
        ) as run:
            INSTALL.install_brew("vivaldi")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["brew", "install", "--cask", "vivaldi"],
                ["brew", "update"],
                ["brew", "install", "--cask", "vivaldi"],
            ],
        )

    def test_brew_success_does_not_refresh_or_retry(self) -> None:
        """A successful installation needs no metadata refresh or second attempt."""
        with mock.patch.object(
            INSTALL.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run:
            INSTALL.install_brew("vivaldi")
        self.assertEqual(run.call_count, 1)

    def test_brew_warning_after_install_does_not_refresh_or_retry(self) -> None:
        """An installed executable takes precedence over Homebrew's warning exit."""
        exe = ["/Applications/Vivaldi.app/Contents/MacOS/Vivaldi"]
        with (
            mock.patch.object(
                INSTALL.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 1),
            ) as run,
            mock.patch.object(INSTALL, "find_exe", return_value=exe[0]),
        ):
            INSTALL.install_brew("vivaldi", exe)
        self.assertEqual(run.call_count, 1)

    def test_brew_refresh_failure_stops_the_retry(self) -> None:
        """Propagate refresh failures instead of retrying with stale metadata."""
        with mock.patch.object(
            INSTALL.subprocess, "run",
            side_effect=[
                subprocess.CompletedProcess([], 1),
                subprocess.CalledProcessError(1, ["brew", "update"]),
            ],
        ) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                INSTALL.install_brew("vivaldi")
        self.assertEqual(run.call_count, 2)

    def test_opera_brew_uses_mirror_only_after_both_attempts_fail(self) -> None:
        for cask in ("opera", "opera-gx"):
            with (
                self.subTest(cask=cask),
                mock.patch.object(
                    INSTALL.subprocess, "run",
                    side_effect=[
                        subprocess.CompletedProcess([], 1),
                        subprocess.CompletedProcess([], 0),
                        subprocess.CompletedProcess([], 1),
                        subprocess.CompletedProcess([], 0),
                    ],
                ) as run,
                mock.patch.object(INSTALL, "cache_opera_brew_download") as mirror,
            ):
                INSTALL.install_brew(cask)
                self.assertEqual(
                    [call.args[0] for call in run.call_args_list],
                    [
                        ["brew", "install", "--cask", cask],
                        ["brew", "update"],
                        ["brew", "install", "--cask", cask],
                        ["brew", "install", "--cask", cask],
                    ],
                )
                mirror.assert_called_once_with(cask, run.call_args.kwargs["env"])

    def test_opera_brew_skips_mirror_when_an_attempt_installed_the_binary(self) -> None:
        exe = ["/Applications/Opera.app/Contents/MacOS/Opera"]
        for results, found in (
            ([0], []),
            ([1], [exe[0]]),
            ([1, 0, 0], [None]),
            ([1, 0, 1], [None, exe[0]]),
        ):
            with (
                self.subTest(results=results),
                mock.patch.object(
                    INSTALL.subprocess, "run",
                    side_effect=[subprocess.CompletedProcess([], rc) for rc in results],
                ),
                mock.patch.object(INSTALL, "find_exe", side_effect=found),
                mock.patch.object(INSTALL, "cache_opera_brew_download") as mirror,
            ):
                INSTALL.install_brew("opera", exe)
                mirror.assert_not_called()

    def test_opera_mirror_caches_the_exact_refreshed_cask_artifact(self) -> None:
        payload = b"verified Opera DMG"
        for cask, relative, url_hash in (
            (
                "opera", "opera/desktop/136.0.6008.80/mac/Opera_136.0.6008.80_Setup.dmg",
                "36db6a859d8c0f3865d3921eea315d64ac879a55af3f43b95c0006e358a1a2b0",
            ),
            (
                "opera-gx", "opera_gx/136.0.6008.76/mac/Opera_GX_136.0.6008.76_Setup.dmg",
                "0cf0a0966f1e26709ba40afb3717fff8560cf9740cc9084e994db58c1636bd2c",
            ),
        ):
            with self.subTest(cask=cask), tempfile.TemporaryDirectory() as tmp:
                cache = Path(tmp) / "downloads" / f"{url_hash}--{relative.rsplit('/', 1)[-1]}"
                cache.parent.mkdir()
                incomplete = Path(str(cache) + ".incomplete")
                incomplete.write_bytes(b"failed origin download")
                metadata = {"casks": [{
                    "url": "https://get.geo.opera.com/pub/" + relative,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }]}
                env = {"HOMEBREW_NO_AUTO_UPDATE": "1"}

                def brew_output(command, **kwargs):
                    if command == ["brew", "info", "--json=v2", "--cask", cask]:
                        return json.dumps(metadata)
                    if command == ["brew", "--cache"]:
                        return tmp + "\n"
                    # A cask-specific cache lookup must never get a chance to
                    # contact the broken origin before the mirror download.
                    raise subprocess.CalledProcessError(
                        6, command, stderr="Could not resolve host: get.geo.opera.com"
                    )

                with (
                    mock.patch.object(
                        INSTALL.subprocess, "check_output",
                        side_effect=brew_output,
                    ) as output,
                    mock.patch.object(
                        INSTALL.urllib.request, "urlopen", return_value=io.BytesIO(payload),
                    ) as request,
                ):
                    INSTALL.cache_opera_brew_download(cask, env)

                self.assertEqual(cache.read_bytes(), payload)
                self.assertEqual(set(cache.parent.iterdir()), {cache, incomplete})
                self.assertEqual(incomplete.read_bytes(), b"failed origin download")
                request.assert_called_once_with(
                    "https://ftp.opera.com/pub/" + relative, timeout=120
                )
                self.assertEqual(output.call_args_list, [
                    mock.call(
                        ["brew", "info", "--json=v2", "--cask", cask], env=env, text=True
                    ),
                    mock.call(
                        ["brew", "--cache"], env=env, text=True
                    ),
                ])

    def test_opera_mirror_failure_preserves_cache_and_clears_staging(self) -> None:
        for failure in ("download", "checksum"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                cache = Path(tmp) / "downloads" / (
                    "7850cd66f8ed5f585ae04d57804bf05e7ec59478acb55146712593acc121e5af--Opera.dmg"
                )
                cache.parent.mkdir()
                cache.write_bytes(b"existing cache")
                metadata = {"casks": [{
                    "url": "https://get.geo.opera.com/pub/opera/desktop/1/mac/Opera.dmg",
                    "sha256": "0" * 64,
                }]}

                response = io.BytesIO(b"partial or corrupt DMG")
                if failure == "download":
                    response.read = mock.Mock(
                        side_effect=[b"partial DMG", TimeoutError("download interrupted")]
                    )

                error = OSError if failure == "download" else SystemExit
                with (
                    mock.patch.object(
                        INSTALL.subprocess, "check_output",
                        side_effect=[json.dumps(metadata), tmp],
                    ),
                    mock.patch.object(
                        INSTALL.urllib.request, "urlopen", return_value=response,
                    ),
                    self.assertRaises(error),
                ):
                    INSTALL.cache_opera_brew_download("opera", {})
                self.assertEqual(cache.read_bytes(), b"existing cache")
                self.assertEqual(list(cache.parent.iterdir()), [cache])

    def test_opera_mirror_rejects_unexpected_url_or_missing_checksum(self) -> None:
        for url, checksum in (
            ("https://example.com/Opera.dmg", "0" * 64),
            ("https://get.geo.opera.com/pub/opera/Opera.dmg", "no_check"),
            ("https://get.geo.opera.com/pub/opera/Opera.dmg", None),
            (None, "0" * 64),
            ("https://get.geo.opera.com/pub/opera/", "0" * 64),
        ):
            metadata = {"casks": [{"url": url, "sha256": checksum}]}
            with (
                self.subTest(url=url, checksum=checksum),
                mock.patch.object(
                    INSTALL.subprocess, "check_output", return_value=json.dumps(metadata)
                ) as output,
                mock.patch.object(INSTALL.urllib.request, "urlopen") as request,
                self.assertRaisesRegex(SystemExit, "unexpected Opera cask"),
            ):
                INSTALL.cache_opera_brew_download("opera", {})
            self.assertEqual(output.call_count, 1)
            request.assert_not_called()

    def test_find_exe_resolves_globs_and_app_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_macos = root / "Opera GX.app" / "Contents" / "MacOS"
            app_macos.mkdir(parents=True)
            binary = app_macos / "Opera"
            binary.write_bytes(b"fake-browser\n")
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
            found = INSTALL.find_exe(
                [
                    str(app_macos / "Opera GX"),
                    str(root / "*.app" / "Contents" / "MacOS" / "Opera"),
                ]
            )
            self.assertEqual(Path(found).resolve(), binary.resolve())

    def test_find_exe_expands_recursive_globs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "Packages" / "DuckDuckGo.DesktopBrowser_1" / "Local"
            nested.mkdir(parents=True)
            exe = nested / "DuckDuckGo.exe"
            exe.write_bytes(b"fake-browser\n")
            found = INSTALL.find_exe(
                [str(root / "Packages" / "DuckDuckGo*" / "**" / "DuckDuckGo.exe")]
            )
            self.assertEqual(Path(found).resolve(), exe.resolve())

    def test_is_launchable_rejects_empty_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "Arc.exe"
            empty.write_bytes(b"")
            self.assertFalse(INSTALL.is_launchable(empty))
            real = Path(tmp) / "brave.exe"
            real.write_bytes(b"fake-browser\n")
            self.assertTrue(INSTALL.is_launchable(real))

    def test_catalog_skips_windowsapps_aliases(self) -> None:
        for browser, meta in INSTALL.HOSTS.items():
            for path in meta.get("windows", {}).get("exe", []):
                self.assertNotIn("WindowsApps", path, browser)

    def test_package_activated_products_are_not_in_the_install_catalog(self) -> None:
        catalog = {(row["platform"], row["browser"]) for row in INSTALL.matrix()}
        self.assertNotIn(("macos", "arc"), catalog)
        self.assertNotIn(("windows", "arc"), catalog)
        self.assertNotIn(("windows", "duckduckgo"), catalog)

    def test_macos_librewolf_bypasses_the_disabled_homebrew_cask(self) -> None:
        # Homebrew disabled the cask on 2026-09-01 over the Gatekeeper check,
        # so the macOS cell must install the published DMG directly.
        spec = INSTALL.HOSTS["librewolf"]["macos"]
        self.assertEqual(spec["kind"], "librewolf_dmg")
        self.assertIn(
            "/Applications/LibreWolf.app/Contents/MacOS/librewolf", spec["exe"]
        )

    def test_librewolf_version_comes_from_the_release_feed(self) -> None:
        payload = io.BytesIO(b'{"tag_name": "155.0-1"}')
        with mock.patch.object(
            INSTALL.urllib.request, "urlopen", return_value=payload
        ):
            self.assertEqual(INSTALL.librewolf_latest_version(), "155.0-1")
        self.assertEqual(
            INSTALL.LIBREWOLF_MACOS_DMG.format(version="155.0-1", arch="arm64"),
            "https://dl.librewolf.net/librewolf/155.0-1/"
            "librewolf-155.0-1-macos-arm64-package.dmg",
        )

    def test_librewolf_version_rejects_an_unusable_release_tag(self) -> None:
        payload = io.BytesIO(b'{"tag_name": "nightly"}')
        with mock.patch.object(
            INSTALL.urllib.request, "urlopen", return_value=payload
        ):
            with self.assertRaises(SystemExit):
                INSTALL.librewolf_latest_version()

    def test_remove_app_bundle_never_follows_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed = root / "real" / "Contents"
            installed.mkdir(parents=True)
            (installed / "Info.plist").write_bytes(b"<plist/>\n")

            linked = root / "Linked.app"
            linked.symlink_to(installed.parent)
            INSTALL.remove_app_bundle(linked)
            self.assertFalse(linked.is_symlink())
            # Only the link goes; whatever it pointed at is not ours to delete.
            self.assertTrue((installed / "Info.plist").is_file())

            dangling = root / "Dangling.app"
            dangling.symlink_to(root / "gone")
            INSTALL.remove_app_bundle(dangling)
            self.assertFalse(dangling.is_symlink())

            INSTALL.remove_app_bundle(installed.parent)
            self.assertFalse(installed.parent.exists())

            INSTALL.remove_app_bundle(root / "Missing.app")

    def librewolf_staging(self) -> set[str]:
        return set(glob.glob(f"{tempfile.gettempdir()}/rookie-librewolf-*"))

    def test_librewolf_dmg_clears_staging_when_the_download_fails(self) -> None:
        before = self.librewolf_staging()
        with (
            mock.patch.object(INSTALL, "librewolf_latest_version", return_value="1-1"),
            mock.patch.object(
                INSTALL.urllib.request, "urlretrieve", side_effect=OSError("boom")
            ),
            self.assertRaises(OSError),
        ):
            INSTALL.install_librewolf_dmg()
        self.assertEqual(self.librewolf_staging() - before, set())

    def test_librewolf_dmg_detach_does_not_mask_the_install_failure(self) -> None:
        # A wedged disk image must not decide what the caller sees, and it must
        # not keep the download around either.
        before = self.librewolf_staging()

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ditto" or cmd[:2] == ["hdiutil", "detach"]:
                raise subprocess.CalledProcessError(1, cmd)

        with (
            mock.patch.object(INSTALL, "librewolf_latest_version", return_value="1-1"),
            mock.patch.object(INSTALL.urllib.request, "urlretrieve"),
            mock.patch.object(INSTALL, "run", side_effect=fake_run),
            mock.patch.object(INSTALL.Path, "is_dir", return_value=True),
            mock.patch.object(
                INSTALL.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1),
            ),
            self.assertRaises(subprocess.CalledProcessError) as raised,
        ):
            INSTALL.install_librewolf_dmg()

        self.assertEqual(raised.exception.cmd[0], "ditto")
        self.assertEqual(self.librewolf_staging() - before, set())

    def test_every_catalog_kind_has_an_installer_branch(self) -> None:
        for browser, meta in INSTALL.HOSTS.items():
            for platform in INSTALL.RUNNERS:
                spec = meta.get(platform)
                if spec is None:
                    continue
                self.assertIn(
                    spec["kind"],
                    INSTALL_KINDS,
                    f"{browser}/{platform}",
                )

    def test_vivaldi_and_yandex_are_real_hosted_cells(self) -> None:
        catalog = {(row["platform"], row["browser"]) for row in INSTALL.matrix()}
        for platform in INSTALL.RUNNERS:
            self.assertIn((platform, "vivaldi"), catalog)
        self.assertIn(("macos", "yandex"), catalog)
        self.assertIn(("windows", "yandex"), catalog)


if __name__ == "__main__":
    unittest.main()
