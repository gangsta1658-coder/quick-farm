#!/usr/bin/env python3
"""Verify a bot-registered Quick account can sign in and reach the workspace.
Usage: login_check.py <local> [--residential]
Password comes from accounts.json (written by register.py); never printed.
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import register as R
from playwright.sync_api import sync_playwright

def main():
    local = sys.argv[1]
    accs = json.load(open(R.ACCOUNTS_FILE)) if os.path.exists(R.ACCOUNTS_FILE) else {}
    rec = accs.get(local)
    if not rec: raise SystemExit(f'no account record for {local}')
    email_addr, password = rec['email'], rec['password']
    raw = os.environ.get('QUICK_BOT_PROXY', '').strip()
    proxy = R.playwright_proxy(raw)
    exe = os.path.expanduser('~/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome')
    kw = {'executable_path': exe} if os.path.exists(exe) else {}
    if proxy:
        kw['proxy'] = proxy
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **kw,
            ignore_default_args=['--enable-automation'],
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox',
                  '--ignore-certificate-errors'])
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 860}, locale='en-US')
        ctx.route('**/*', lambda route: route.abort()
                  if route.request.resource_type in ('image', 'media', 'font')
                  else route.continue_())
        page = ctx.new_page()
        # Enter via the Quick landing page like register.py does — the
        # captured platform workflow URL expires and lands on an AWS
        # error page ("It's not you, it's us").
        page.goto('https://aws.amazon.com/quick/', timeout=120000, wait_until='domcontentloaded')
        page.wait_for_timeout(4000)
        href = page.evaluate("""() => {
            const a = document.querySelector('[data-testid="create-account-button"]')
                || [...document.querySelectorAll('a')].find(x => /Try Quick for free/i.test(x.textContent));
            if (a) { a.click(); return a.href; }
            return null;
        }""")
        if not href: raise SystemExit('signup button not found')
        page.wait_for_timeout(5000)
        pages = ctx.pages
        if len(pages) > 1:
            page = pages[-1]
        page.wait_for_load_state('domcontentloaded', timeout=90000)
        page.wait_for_timeout(4000)
        email_in = R.wait_step(page, ['input[type="email"]', 'input[name="email"]',
                                      'input[placeholder*="example.com" i]'])
        if not email_in: raise SystemExit('email input not found')
        R.type_like_human(email_in, email_addr)
        if not R.click_continue(page): raise SystemExit('continue after email failed')
        pw = R.wait_step(page, ['input[type="password"]'], text_needles=('password',))
        pw_in = R.first_visible(page, ['input[type="password"]'])
        if not pw_in: raise SystemExit('password input not found')
        R.type_like_human(pw_in, password)
        if not R.click_continue(page): raise SystemExit('continue after password failed')
        ok = False
        for _ in range(40):
            page.wait_for_timeout(5000)
            url = page.url
            m = re.search(r'quick\.aws\.com/sn/account/(\d+)', url)
            if m:
                print(f'[ok] signed in; account_id={m.group(1)} url={url}', flush=True)
                ok = True; break
            if 'quick.aws.com' in url:
                print(f'[ok] reached quick.aws.com: {url}', flush=True)
                ok = True; break
            try:
                txt = page.inner_text('body').lower()
            except Exception:
                txt = ''
            if 'your account is ready' in txt:
                print(f'[ok] bootstrap page (account exists): {url}', flush=True)
                ok = True; break
            if 'verification code' in txt or 'verify your email' in txt:
                code = R.latest_otp(local, time.time() - 600, email_addr=email_addr)
                if code:
                    otp_in = R.first_visible(page, ['input[placeholder*="6-digit" i]',
                                                    'input[type="text"]', 'input[type="tel"]'])
                    if otp_in:
                        R.type_like_human(otp_in, code)
                        R.click_continue(page)
        R.shot(page, f'{local}-login-check')
        if not ok:
            print(f'[fail] final url={page.url}', flush=True)
        browser.close()

if __name__ == '__main__':
    main()
