# Troubleshooting

Platform quirks that show up as empty results, permission errors, or password
prompts. For the recommended 0.6.0 API,
see [bindings/python/README.md](../bindings/python/README.md),
[bindings/node/README.md](../bindings/node/README.md), and
[rookie-rs/README.md](../rookie-rs/README.md).

## Password prompt

On Linux, Chromium-based browsers may prompt through Secret Service / KWallet
when the library needs a keyring password. On macOS, Chromium Keychain access
can also surface a system prompt the first time a consumer process is allowed
to read the item.

## Session cookies

Pass `include_session=True` / `includeSession: true` /
`.include_session()` / `--include-session` to `read` or `jar` when you need a
Gecko browser's declared session store (see
[ADR 0004](adr/0004-read-is-the-recommended-entry.md)). Profile selection is
independent: omit it for the legacy-first profile, or name a profile to select
that profile. Naming a profile alone does not open the session store.

Some Chromium setups historically restarted the browser to reach locked
databases. Prefer non-disruptive extraction; do not assume a process restart is
available or desirable. Session cookies may still disappear when the browser
process exits.

## Windows Defender / antivirus flags the native module

App-Bound `v20` recovery's unprivileged path spawns a browser process and
reflectively injects into it to call `IElevator.DecryptData` — the same
general shape real infostealer malware uses against Chrome's App-Bound key,
so some antivirus engines flag the native module (`rookie_cookies.pyd` /
`rookie-cookies.node` / the CLI binary) on heuristics alone. On Windows,
check first:

```powershell
Get-MpThreatDetection | Where-Object { $_.Resources -match "rookie" }
```

If Defender quarantined the file (`ActionSuccess: True`, a `ThreatName` like
`Trojan:Win64/InfoStealer!AMTB`), the module may be silently missing or
re-deleted on every reinstall, which looks like `v20` extraction failing or
hanging rather than an AV issue. This can happen even when an elevated CI
runner never reproduces it, since Defender's SYSTEM-context policy can differ
from a normal desktop session's.

Best fix: submit the flagged file to Microsoft's
[false positive portal](https://www.microsoft.com/en-us/wdsi/filesubmission)
so the signature gets corrected upstream for everyone. That review takes
time, so to unblock yourself immediately, add a narrow Defender exclusion —
scoped to the package directory only, never the whole venv or a whole
drive — then reinstall, since the already-quarantined file needs to be
restored, not just unblocked going forward.

**1. Add the exclusion.** This needs an elevated (Run as Administrator)
PowerShell, since reading or writing exclusions requires admin rights:

```powershell
# Python
Add-MpPreference -ExclusionPath "<path to your venv>\Lib\site-packages\rookie_cookies"

# Node.js
Add-MpPreference -ExclusionPath "<path to your project>\node_modules\rookie-cookies"

# CLI (exclude the installed binary's directory, not just the exe)
Add-MpPreference -ExclusionPath "<install dir containing rookie-cookies.exe>"
```

Verify it took (also requires admin):

```powershell
Get-MpPreference | Select-Object -ExpandProperty ExclusionPath
```

**2. Reinstall**, so the restored file is written after the exclusion exists
rather than being quarantined again on the way in:

```powershell
# pip
pip install --force-reinstall --no-deps rookie-cookies

# uv
uv pip install --force-reinstall --no-deps rookie-cookies

# npm
npm install rookie-cookies --force

# CLI: re-download or re-extract the binary into the excluded directory
```

**3. Confirm it actually loads** (Python example — adjust for your binding):

```powershell
python -c "import rookie_cookies; print('OK:', rookie_cookies.__file__)"
```

If that import succeeds and `v20` extraction still fails, the cause is
something else — recheck `Get-MpThreatDetection` for a *new* detection first,
since some Defender updates re-flag a previously-excluded path's contents
after a reinstall changes the file's hash.

## macOS Safari permission denied

Recent macOS versions restrict
`~/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies`.
Grant **Full Disk Access** under System Settings for the terminal or app that
runs the extraction.

## Unsupported platforms (for example Android)

Copy the cookie database off the device, then point the CLI at that file:

```console
find /data/data -type f -name Cookies
rookie-cookies from-path <Cookies path>
```

On Windows Chromium paths, select credentials explicitly with exactly one of
`--browser-id`, `--local-state-path` (a `Local State` file), or
`--plaintext-only`.

## Manually import website cookies in a browser

To push extracted cookies back into a browser console on an already-open origin.
`document.cookie` cannot create **HttpOnly** cookies; skip those or set them
through a proper client API.

```python
import json
from datetime import datetime, timezone

import rookie_cookies


def create_js_code(cookies):
    js_code = ""
    for cookie in cookies:
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if not name or cookie.get("http_only"):
            continue
        parts = [f"{name}={value}", f"path={cookie.get('path') or '/'}"]
        expires = cookie.get("expires")
        if expires:
            stamped = datetime.fromtimestamp(int(expires), tz=timezone.utc)
            parts.append("expires=" + stamped.strftime("%a, %d %b %Y %H:%M:%S GMT"))
        assignment = ";".join(parts) + ";"
        js_code += f"document.cookie = {json.dumps(assignment)};\n"
    js_code += "location.reload()\n"
    return js_code


# Prefer read/jar for session import; named helpers remain for compatibility.
rows = rookie_cookies.read(
    browser="firefox", profile="default-release", include_session=True
).as_list()
# Or domain-filter the compatibility flat list:
# rows = rookie_cookies.brave(["github.com"])
print(create_js_code(rows))
```

Run the printed script only on the matching origin. Never paste real cookie
values into issues, chat logs, or shared documents.
