"""Interactive login test — opens Edge, waits for you to log in, extracts token.

Usage:
    python test_login_interactive.py [provider]

Run this script. A browser window will open. Log in (scan QR code or
enter password). The script will show what cookies and storage it finds,
and report whether it extracted a token.
"""

import sys
import time
import json
from panupdate.auth.cdp_login import (
    SELENIUM_LOGIN_CONFIGS,
    run_cdp_login,
    _DUMP_ALL_JS,
    _STORAGE_JS,
    _extract_token_from_dump,
    _find_msedge,
    _cdp_pages,
    CDPClient,
)
import subprocess
import os
import tempfile
import secrets


def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "baidu"
    if provider not in SELENIUM_LOGIN_CONFIGS:
        print(f"Unknown provider: {provider}")
        print(f"Available: {list(SELENIUM_LOGIN_CONFIGS.keys())}")
        sys.exit(1)

    config = SELENIUM_LOGIN_CONFIGS[provider]
    print(f"=== Interactive Login Test: {provider} ===")
    print(f"URL: {config.login_url}")
    print(f"Expected token: {config.token_name}")
    print()

    # Step 1: Launch browser via CDP
    edge = _find_msedge()
    port = secrets.randbelow(7000) + 9223
    user_dir = os.path.join(tempfile.gettempdir(), f"panupdate_test_{os.getpid()}")
    os.makedirs(user_dir, exist_ok=True)

    print(f"Launching Edge (port={port})...")
    cmd = [
        edge,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        config.login_url,
    ]
    browser = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Browser PID: {browser.pid}")
    print()

    # Step 2: Connect CDP
    print("Waiting for CDP...")
    page_id = None
    for i in range(30):
        time.sleep(0.5)
        exit_code = browser.poll()
        if exit_code is not None and exit_code != 0:
            print(f"ERROR: Edge exited with code {exit_code}")
            browser.terminate()
            sys.exit(1)
        pages = _cdp_pages("127.0.0.1", port)
        for p in pages:
            if p.get("type") == "page" and "chrome-extension" not in p.get("url", ""):
                page_id = p["id"]
                break
        if page_id:
            print(f"CDP ready! Page ID: {page_id[:20]}...")
            break
    else:
        print("ERROR: CDP not available after 15s")
        browser.terminate()
        sys.exit(1)

    client = CDPClient()
    if not client.connect("127.0.0.1", port, page_id):
        print("ERROR: WebSocket connection failed")
        browser.terminate()
        sys.exit(1)
    print("WebSocket connected!")
    print()

    # Step 3: Wait for login
    print("=" * 50)
    print("PLEASE LOG IN NOW (scan QR code or enter password)")
    print("The script will poll every 2 seconds...")
    print("=" * 50)
    print()

    deadline = time.time() + 120  # 2 minute timeout
    poll_count = 0
    while time.time() < deadline:
        time.sleep(2)
        poll_count += 1

        if browser.poll() is not None:
            print("Browser closed by user.")
            break

        dump_lines = []
        cookies_found = 0

        # Get cookies via CDP
        try:
            result = client.recv(
                client.send("Network.getCookies", {}), timeout=10
            )
            cookies = result.get("result", {}).get("cookies", [])
            cookies_found = len(cookies)
            for ck in cookies:
                name = ck.get("name", "")
                value = ck.get("value", "")
                if name and value:
                    dump_lines.append(f"COOKIE:{name}={value}")
        except Exception as ex:
            print(f"  [CDP cookie error: {ex}]")

        # Get localStorage via JS
        try:
            ls_dump = client.evaluate(_STORAGE_JS)
            if ls_dump:
                dump_lines.append(ls_dump)
        except Exception as ex:
            print(f"  [JS error: {ex}]")

        dump = "\n".join(dump_lines)
        token = _extract_token_from_dump(dump, config.provider)

        print(f"  Poll #{poll_count}: {cookies_found} cookies, "
              f"token={'FOUND!' if token else 'not yet'}")

        # Show all cookie names found
        if cookies_found > 0 and poll_count == 1:
            print(f"  Cookie names: {[c.get('name','') for c in result.get('result',{}).get('cookies',[])]}")

        if token:
            print()
            print("=" * 50)
            print(f"TOKEN EXTRACTED SUCCESSFULLY!")
            print(f"Token: {token[:50]}{'...' if len(token) > 50 else ''}")
            print(f"Length: {len(token)}")
            print("=" * 50)

            # Save full dump for debugging
            with open("test_login_dump.txt", "w", encoding="utf-8") as f:
                f.write(dump)
            print("Full storage dump saved to: test_login_dump.txt")
            break

    else:
        print()
        print("TIMEOUT - no token found within 120 seconds.")
        # Save whatever we found
        with open("test_login_dump_final.txt", "w", encoding="utf-8") as f:
            f.write(dump if dump else "(empty)")
        print(f"Final dump ({len(dump)} chars) saved to: test_login_dump_final.txt")

    # Cleanup
    print()
    print("Cleaning up...")
    client.close()
    browser.terminate()
    try:
        browser.wait(5)
    except:
        pass
    import shutil
    shutil.rmtree(user_dir, ignore_errors=True)
    print("Done.")


if __name__ == "__main__":
    main()
