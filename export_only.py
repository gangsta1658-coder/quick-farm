#!/usr/bin/env python3
"""Log in to an existing Quick account and mint its gateway JSON.

Usage: export_only.py <mailbox-local> [--residential]
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import register as R
from playwright.sync_api import sync_playwright

def main():
    local = sys.argv[1]
    data = json.load(open(R.ACCOUNTS_FILE))
    rec = data[local]
    email_addr, password = rec['email'], rec['password']

    raw_proxy = os.environ.get('QUICK_BOT_PROXY', '').strip()
    launch_proxy = R.playwright_proxy(raw_proxy)
    exe = os.path.expanduser('~/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome')
    with sync_playwright() as p:
        launch_kw = {'executable_path': exe} if os.path.exists(exe) else {}
        if launch_proxy:
            launch_kw['proxy'] = launch_proxy
        browser = p.chromium.launch(
            headless=True, **launch_kw,
            ignore_default_args=['--enable-automation'],
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox',
                  '--ignore-certificate-errors'])
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 860}, locale='en-US')
        page = ctx.new_page()
        page.goto('https://aws.amazon.com/quick/', wait_until='domcontentloaded', timeout=90000)
        page.wait_for_timeout(4000)
        href = page.evaluate("""() => {
            const a = document.querySelector('[data-testid="create-account-button"]')
                || [...document.querySelectorAll('a')].find(x => /Try Quick for free/i.test(x.textContent));
            if (a) { a.click(); return a.href; }
            return null;
        }""")
        if not href: raise SystemExit('signup button not found')
        page.wait_for_timeout(5000)
        if len(ctx.pages) > 1: page = ctx.pages[-1]
        R.mini_login(page, local, email_addr, password)
        page.wait_for_timeout(4000)
        print(f'[login] at {page.url}', flush=True)
        try:
            path = R.export_gateway_key(ctx, page, local, email_addr, password,
                                        rec, proxy_server=raw_proxy or None)
            print('EXPORTED:', path, flush=True)
        except Exception as e:
            rec['gateway_key_error'] = str(e)[:300]
            print('EXPORT FAILED:', e, flush=True)
            R.save_account(local, rec)
            browser.close()
            return 1
        R.save_account(local, rec)
        browser.close()
    return 0

if __name__ == '__main__':
    sys.exit(main())
