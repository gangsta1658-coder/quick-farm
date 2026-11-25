#!/usr/bin/env python3
"""Fully automatic gateway-JSON export, done locally in the backend.

Same browser dance the gateway flow needed, but the OAuth client lives
here (oauth_local): no gateway instance is involved at any step.

Lessons baked in from the manual runs:
  * loopback callbacks are captured via the context request event
    (route interception misses them under proxy bypass);
  * the discovery page is an SPA that bounces to the SSO login on its
    own - wait for the bounce before running the login helper, and be
    ready to run it twice;
  * the authorize step bounces to a second login, after which the
    consent card renders under the SAME login URL - detect it by the
    Allow button, never by URL;
  * the authorize callback lands on a random 127.0.0.1 port - match
    code=+state=, not a fixed port.
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import register as R
import oauth_local as OA
from playwright.sync_api import sync_playwright

KEYS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "keys")


def _wait_for(page, captured, needle=None, timeout=120, pred=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for u in captured:
            if pred and pred(u):
                return u
            if needle and needle in u:
                return u
        page.wait_for_timeout(1500)
    return None


def _visible(page, selector, timeout=800):
    try:
        return page.locator(selector).first.is_visible(timeout=timeout)
    except Exception:
        return False


def export_account(email_key, proxy_url=None, log=print):
    data = json.load(open(R.ACCOUNTS_FILE))
    rec = data[email_key]
    email_addr, password = rec["email"], rec["password"]
    local = email_key.split("@")[0]

    raw_proxy = (
        proxy_url if proxy_url is not None
        else os.environ.get("QUICK_BOT_PROXY", "")
    ).strip()
    launch_proxy = R.playwright_proxy(raw_proxy)
    exe = os.path.expanduser(
        "~/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome")
    launch_kw = {"executable_path": exe} if os.path.exists(exe) else {}
    if launch_proxy:
        launch_kw["proxy"] = launch_proxy

    captured = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, **launch_kw,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--ignore-certificate-errors"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 860}, locale="en-US")

        def _note(req):
            u = req.url
            if ("localhost" in u or "127.0.0.1" in u) and u not in captured:
                captured.append(u)
        ctx.on("request", _note)
        page = ctx.new_page()

        # 1) discovery
        _, disc_url = OA.build_discovery_url()
        log(f"[export] discovery -> {disc_url[:60]}...")
        try:
            page.goto(disc_url, timeout=90000, wait_until="domcontentloaded")
        except Exception:
            pass
        # wait for the SPA to bounce to the SSO login (or straight to cb)
        deadline = time.time() + 90
        while time.time() < deadline:
            if any("callback?nonce" in u for u in captured):
                break
            if "signin.aws" in page.url:
                break
            page.wait_for_timeout(1500)
        cb = _wait_for(page, captured, needle="callback?nonce", timeout=20)
        if not cb:
            log("[export] login bounce (1)")
            R.mini_login(page, local, email_addr, password)
            cb = _wait_for(page, captured, needle="callback?nonce",
                         timeout=120)
        if not cb:
            log("[export] login bounce (2)")
            R.mini_login(page, local, email_addr, password)
            cb = _wait_for(page, captured, needle="callback?nonce",
                         timeout=120)
        if not cb:
            browser.close()
            raise RuntimeError("discovery callback not captured; "
                               f"url={page.url}")

        # 2) PKCE flow start (local OIDC client registration)
        started = OA.start_flow(cb, proxy_url=raw_proxy or None)
        log(f"[export] flow {started['flowId']} started")
        captured.clear()

        # 3) authorize: may bounce to a second login, then consent card
        try:
            page.goto(started["authUrl"], timeout=90000,
                      wait_until="domcontentloaded")
        except Exception:
            pass
        page.wait_for_timeout(3000)
        allow = page.get_by_role("button", name="Allow access",
                                 exact=False).first
        deadline = time.time() + 180
        clicked = False
        login_tried = False
        while time.time() < deadline and not clicked:
            try:
                if allow.is_visible(timeout=800):
                    allow.click()
                    clicked = True
                    break
            except Exception:
                pass
            if not login_tried and _visible(
                    page, 'input[type="email"], input[name="email"]'):
                log("[export] authorize login bounce")
                try:
                    R.mini_login(page, local, email_addr, password)
                except Exception as e:
                    log(f"[export] authorize login: {e}")
                login_tried = True
            page.wait_for_timeout(1500)
        if not clicked:
            browser.close()
            raise RuntimeError(f"Allow access not reached; url={page.url}")

        # 4) authorize callback (random local port, code+state)
        final_cb = _wait_for(
            page, captured, timeout=90,
            pred=lambda u: "code=" in u and "state=" in u)
        if not final_cb:
            browser.close()
            raise RuntimeError("authorize callback not captured")
        cred = OA.complete_flow(started["flowId"], final_cb)
        browser.close()

    if rec.get("name") and not cred.get("name"):
        cred["name"] = rec["name"]
    out = OA.make_export_file(cred, email_addr)
    os.makedirs(KEYS_DIR, exist_ok=True)
    key_name = f"{email_addr.replace('@', '_at_')}-gateway-key.json"
    path = os.path.join(KEYS_DIR, key_name)
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    os.chmod(path, 0o600)
    rec["gateway_key"] = f"keys/{key_name}"
    rec.pop("gateway_key_error", None)
    R.save_account(email_key, rec)
    log(f"[export] saved {path}")
    return {"path": path, "account_id": cred.get("accountId"),
            "email": email_addr}
