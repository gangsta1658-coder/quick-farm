#!/usr/bin/env python3
"""AWS Quick email-signup bot.

Uses outlook_mail (Outlook Graph/IMAP XOAUTH2 or URL-OTP mailboxes),
drives the email signup flow with Playwright, generates a password,
and records the result in accounts.json (mode 600, never printed).

Usage: register.py <email-or-local> [--name "Full Name"] [--headful]
"""
import argparse, json, os, re, secrets, string, sys, time
from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
ACCOUNTS_FILE = os.path.join(BASE, 'accounts.json')
DEBUG = os.path.join(BASE, 'debug')

FIRST = ['Alex','Sam','Chris','Jamie','Taylor','Jordan','Casey','Robin','Avery','Quinn','Skyler','Riley','Morgan','Drew','Kai','Noa']
LAST = ['Lee','Kim','Park','Chen','Wang','Lin','Liu','Zhang','Yang','Wu','Zhou','Xu','Sun','Hu','Zhu','Gao']

def gen_password():
    alpha = string.ascii_letters + string.digits + '!@#$%^&*'
    while True:
        pw = ''.join(secrets.choice(alpha) for _ in range(18))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in '!@#$%^&*' for c in pw)):
            return pw

def load_accounts():
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE) as f: return json.load(f)
    return {}

def save_account(key, rec):
    data = load_accounts()
    data[key] = rec
    fd = os.open(ACCOUNTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(data, f, indent=1, ensure_ascii=False)
    os.chmod(ACCOUNTS_FILE, 0o600)

def latest_otp(local, after_ts, timeout=180, email_addr=None):
    """Poll the mailbox for the newest AWS verification code after after_ts."""
    import outlook_mail as om
    target = email_addr or local
    if not om.has_account(target):
        raise RuntimeError(f'no mailbox configured for {target}')
    return om.latest_otp(target, after_ts, timeout)

def shot(page, tag):
    try: page.screenshot(path=os.path.join(DEBUG, f'{tag}.png'))
    except Exception: pass

def dismiss_cookie_banner(page):
    """The AWS cookie-consent banner pops up mid-flow (often seconds
    after the page renders) and steals focus; kill it on sight."""
    for name in ('Decline', 'Accept'):
        try:
            btn = page.get_by_role('button', name=name).first
            if btn.is_visible(timeout=600):
                btn.click()
                page.wait_for_timeout(400)
                return True
        except Exception:
            pass
    return False


def type_like_human(loc, text):
    """React SPA forms ignore fill(); type with real key events instead.
    Verify the value landed: the cookie banner appearing mid-typing
    steals focus and silently eats the rest of the string (a run once
    ended with just '1' in the email box)."""
    for attempt in range(3):
        try:
            dismiss_cookie_banner(loc.page)
        except Exception:
            pass
        loc.click()
        try:
            loc.press('Control+A')
        except Exception:
            pass
        loc.press_sequentially(text, delay=45)
        try:
            if loc.input_value(timeout=2000) == text:
                return
        except Exception:
            return  # cannot read back (not a plain input); accept
        print(f'[retry] typing lost focus, retyping '
              f'({attempt + 1}/3)', flush=True)
    raise RuntimeError(f'typing failed for {text!r}')

def click_continue(page):
    try:
        dismiss_cookie_banner(page)
    except Exception:
        pass
    for sel in ['button:has-text("Continue")', 'input[type="submit"]',
                'button[type="submit"]', 'a:has-text("Continue")']:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click(); return True
        except Exception: pass
    return False

def first_visible(page, selectors, timeout=20000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout): return loc
        except Exception: pass
    return None

def wait_step(page, selectors, text_needles=(), timeout=120):
    """Poll until any selector is visible or body text contains a needle.
    The signup SPA transitions slowly on small machines; fixed sleeps lose."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=800): return loc
            except Exception: pass
        if text_needles:
            try:
                body = page.inner_text('body')
                if any(n.lower() in body.lower() for n in text_needles): return True
            except Exception: pass
        page.wait_for_timeout(2000)
    return None

def playwright_proxy(raw_proxy):
    """Build Playwright proxy dict from a URL, preserving user/pass auth."""
    raw_proxy = (raw_proxy or '').strip()
    if not raw_proxy:
        return None
    from urllib.parse import urlparse, unquote
    u = urlparse(raw_proxy)
    if u.username is not None:
        return {
            'server': f'{u.scheme}://{u.hostname}:{u.port}',
            'username': unquote(u.username),
            'password': unquote(u.password or ''),
        }
    return {'server': raw_proxy}

# ---------- gateway credential export (OAuth, same browser/IP) ----------
GW_BASE = os.environ.get('QUICK_GATEWAY_URL', '')
KEYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'keys')

def gw_admin_password():
    pw = os.environ.get('QUICK_GATEWAY_ADMIN_PASSWORD')
    if pw: return pw
    raise RuntimeError('gateway admin password not found: set '
                       'QUICK_GATEWAY_ADMIN_PASSWORD')

def gw_call(method, path, token=None, body=None, proxy_server=None):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        GW_BASE + path, data=data, method=method,
        headers={'Content-Type': 'application/json',
                 **({'Authorization': 'Bearer ' + token} if token else {})})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(
            {'http': proxy_server, 'https': proxy_server})
        if proxy_server else urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=90) as r:
        return json.load(r)

def _find_slot_by_label(node, label):
    if isinstance(node, dict):
        if node.get('label') == label and node.get('slotId'):
            return node['slotId']
        for v in node.values():
            hit = _find_slot_by_label(v, label)
            if hit: return hit
    elif isinstance(node, list):
        for v in node:
            hit = _find_slot_by_label(v, label)
            if hit: return hit
    return None

def mini_login(page, local, email_addr, password):
    """Re-login inside the OAuth bounce: email -> password -> email OTP."""
    email_in = wait_step(page, ['input[type="email"]', 'input[name="email"]'],
                         timeout=45)
    if not email_in or isinstance(email_in, str):
        return  # not a login page; caller keeps waiting for the callback
    type_like_human(email_in, email_addr)
    click_continue(page)
    pw_in = wait_step(page, ['input[type="password"]'], timeout=60)
    if not pw_in or isinstance(pw_in, str):
        raise RuntimeError('mini_login: password field not found')
    otp_after = time.time()
    type_like_human(pw_in, password)
    click_continue(page)
    code_in = wait_step(page, ['input[placeholder*="digit"]',
                               'input[name="code"]',
                               'input[name*="code" i]',
                               'input[autocomplete="one-time-code"]'],
                        timeout=25)
    if code_in and not isinstance(code_in, str):
        code = latest_otp(local, otp_after - 30, email_addr=email_addr)
        if not code: raise RuntimeError('mini_login: OTP not received')
        type_like_human(code_in, code)
        click_continue(page)
    page.wait_for_timeout(3000)

def export_gateway_key(ctx, page, local, email_addr, password, rec,
                       proxy_server=None):
    """Mint the gateway credential JSON for a freshly registered account in
    the same browser (same exit IP), save it under keys/, then remove the
    pool slot so the gateway keeps nothing."""
    os.makedirs(KEYS_DIR, exist_ok=True)
    token = gw_call('POST', '/v1/admin/login', proxy_server=proxy_server,
                    body={'password': gw_admin_password()})['token']
    disc = gw_call('GET', '/v1/admin/oauth/discovery-url', token=token,
                   proxy_server=proxy_server)

    captured = []
    def _note(u):
        if ('localhost' in u or '127.0.0.1' in u) and u not in captured:
            captured.append(u)
    def _capture(route):
        _note(route.request.url)
        try: route.abort()
        except Exception: pass
    def _on_request(req):
        _note(req.url)
    ctx.route('http://localhost:18080/**', _capture)
    ctx.route('http://127.0.0.1:18080/**', _capture)
    # Route interception misses some loopback navigations (proxy bypass);
    # the context request event observes every request regardless.
    ctx.on('request', _on_request)

    def wait_capture(needle, timeout_s):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for u in captured:
                if needle in u: return u
            page.wait_for_timeout(1500)
        return None

    try:
        page.goto(disc['discoveryUrl'], timeout=90000,
                  wait_until='domcontentloaded')
    except Exception:
        pass
    page.wait_for_timeout(3000)
    shot(page, f'{local}-6-discovery')
    print(f'[export] discovery landed: {page.url}', flush=True)
    cb = wait_capture('callback?nonce', 25)
    if not cb:
        mini_login(page, local, email_addr, password)
        cb = wait_capture('callback?nonce', 120)
    if not cb:
        shot(page, f'{local}-7-nocallback')
        try:
            body = page.inner_text('body')[:200]
        except Exception:
            body = '?'
        raise RuntimeError(
            f'discovery callback not captured; url={page.url} body={body}')

    started = gw_call('POST', '/v1/admin/oauth/start', token=token,
                      proxy_server=proxy_server,
                      body={'discoveryCallbackUrl': cb, 'label': email_addr})
    captured.clear()
    try:
        page.goto(started['authUrl'], timeout=90000,
                  wait_until='domcontentloaded')
    except Exception:
        pass
    page.wait_for_timeout(3000)
    shot(page, f'{local}-8-authorize')
    print(f'[export] authorize page: {page.url}', flush=True)
    clicked = False
    for label in ('Allow access', 'Allow', 'Authorize'):
        try:
            btn = page.get_by_role('button', name=label, exact=False).first
            if btn.is_visible(timeout=3000):
                btn.click(); clicked = True; break
        except Exception:
            continue
    print(f'[export] allow clicked: {clicked}', flush=True)
    page.wait_for_timeout(2000)
    shot(page, f'{local}-9-after-allow')
    final_cb = None
    deadline = time.time() + 90
    while time.time() < deadline and not final_cb:
        for u in captured:
            if 'code=' in u and 'state=' in u:
                final_cb = u
                break
        if not final_cb:
            page.wait_for_timeout(1500)
    if not final_cb:
        raise RuntimeError('authorize callback not captured')
    done = gw_call('POST', '/v1/admin/oauth/complete', token=token,
                   proxy_server=proxy_server,
                   body={'flowId': started['flowId'],
                         'callbackUrl': final_cb})
    slot_id = None
    if isinstance(done.get('slot'), dict):
        slot_id = done['slot'].get('slotId')
    if not slot_id:
        slot_id = _find_slot_by_label(
            gw_call('GET', '/v1/admin/overview', token=token,
                    proxy_server=proxy_server), email_addr)
    if not slot_id:
        raise RuntimeError('completed but slot not found')
    exp = gw_call('POST', '/v1/admin/keys/export', token=token,
                  proxy_server=proxy_server, body={'slotIds': [slot_id]})
    path = os.path.join(KEYS_DIR, f'{email_addr.replace(chr(64), "_at_")}-gateway-key.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(exp, f, indent=1)
    gw_call('DELETE', f'/v1/admin/keys/{slot_id}', token=token,
            proxy_server=proxy_server)
    rec['gateway_key'] = path
    print(f'[export] {email_addr} -> {path} (pool slot removed)', flush=True)
    return path


def run_one(local, args):
    email_addr = os.environ.get('QUICKFARM_EMAIL')
    if not email_addr:
        if '@' in local:
            email_addr = local
        else:
            import outlook_mail as om
            email_addr = next(
                (a for a in om.load_store()
                 if a.split('@')[0].lower() == local.lower()), None)
    if not email_addr:
        raise RuntimeError(f'mailbox not found for {local}')
    name = args.name or f"{secrets.choice(FIRST)} {secrets.choice(LAST)}"
    password = gen_password()
    _prev = load_accounts().get(email_addr) or {}
    if _prev.get('password'):
        # Resume of a half-finished signup: the Builder ID may already
        # exist with this password; a fresh one would fail the sign-in.
        password = _prev['password']
        print('[start] reusing saved password (resume)', flush=True)
    rec = {'email': email_addr, 'name': name, 'password': password,
           'status': 'started', 'ts': time.strftime('%Y-%m-%d %H:%M:%S')}
    save_account(email_addr, rec)
    print(f'[start] {email_addr} name={name}', flush=True)

    raw_proxy = os.environ.get('QUICK_BOT_PROXY', '').strip()
    proxy = playwright_proxy(raw_proxy)
    if raw_proxy:
        from urllib.parse import urlparse
        _u = urlparse(raw_proxy)
        print(f'[proxy] {_u.scheme}://{_u.hostname}:{_u.port}', flush=True)
    exe = os.environ.get('QUICK_BOT_EXE') or os.path.expanduser(
        '~/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome')
    launch_kw = {}
    if os.path.exists(exe): launch_kw['executable_path'] = exe
    if proxy: launch_kw['proxy'] = proxy
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.headful, **launch_kw,
            ignore_default_args=['--enable-automation'],
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox',
                  '--ignore-certificate-errors'])
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 860}, locale='en-US')
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        # Block heavy resources: the host may be a 1 GB micro instance.
        def _block(route):
            if route.request.resource_type in ('image', 'media', 'font'):
                route.abort()
            else:
                route.continue_()
        ctx.route('**/*', _block)
        page = ctx.new_page()
        try:
            # Land on the marketing page first (it sets funnel cookies),
            # then JS-click the signup button and follow to its new tab.
            page.goto('https://aws.amazon.com/quick/', wait_until='domcontentloaded', timeout=90000)
            page.wait_for_timeout(4000)
            href = page.evaluate("""() => {
                const a = document.querySelector('[data-testid="create-account-button"]')
                    || [...document.querySelectorAll('a')].find(x => /Try Quick for free/i.test(x.textContent));
                if (a) { a.click(); return a.href; }
                return null;
            }""")
            if not href: raise RuntimeError('signup button not found')
            # The JS click is sometimes swallowed before the SPA hydrates
            # (browser stays on the landing page). Scan all tabs for the
            # email box; if none shows it, do a real mouse click and rescan.
            EMAIL_SELS = ['input[type="email"]', 'input[name="email"]',
                          'input[placeholder*="example.com" i]', '#email']
            email_in = None
            for round_ in range(3):
                deadline = time.time() + 60
                while time.time() < deadline and email_in is None:
                    for pg in ctx.pages:
                        cand = first_visible(pg, EMAIL_SELS, timeout=800)
                        if cand is not None:
                            page, email_in = pg, cand
                            break
                    if email_in is None:
                        page.wait_for_timeout(2000)
                if email_in is not None or round_ == 2:
                    break
                if any('signup.aws' in (pg.url or '') for pg in ctx.pages):
                    print('[retry] signup tab loading slowly, continuing wait',
                          flush=True)
                    continue
                print('[retry] signup click did not open the funnel; '
                      'real-clicking Try Quick for free', flush=True)
                try:
                    if 'aws.amazon.com/quick' not in (page.url or ''):
                        page.goto('https://aws.amazon.com/quick/',
                                  wait_until='domcontentloaded', timeout=90000)
                        page.wait_for_timeout(3000)
                    btn = page.locator('[data-testid="create-account-button"]').first
                    if not btn.is_visible(timeout=2000):
                        btn = page.get_by_role('button',
                                               name='Try Quick for free').first
                    btn.click(timeout=25000, no_wait_after=True)
                except Exception as e:
                    print(f'[retry] real click failed: {e}', flush=True)
            shot(page, f'{local}-1-signin')
            if not email_in: raise RuntimeError('email input not found')
            type_like_human(email_in, email_addr)
            if not click_continue(page): raise RuntimeError('continue after email failed')
            # Verify the submit actually advanced: the email box must go away.
            # If it comes back empty, the SPA bounced the submit — retry once
            # with Enter, then give up loudly instead of typing into wrong boxes.
            for attempt in range(2):
                gone = False
                for _ in range(20):
                    page.wait_for_timeout(2000)
                    try:
                        if not email_in.is_visible(): gone = True; break
                        val = email_in.input_value()
                        if val and val != email_addr: gone = True; break
                    except Exception:
                        gone = True; break
                if gone: break
                if attempt == 0:
                    print('[retry] email submit bounced, retrying with Enter', flush=True)
                    type_like_human(email_in, email_addr)
                    email_in.press('Enter')
                else:
                    raise RuntimeError('email submit bounced twice (possible bot check)')
            shot(page, f'{local}-2-name')

            # Post-email, the SPA orders the steps by account state:
            # a fresh email gets name -> OTP, but an email with a
            # pending signup (an earlier attempt died mid-flow) gets
            # OTP first ("Verify your identity") and may skip the name
            # entirely. Drive whichever page is showing; never type
            # the name into the OTP box (placeholder '6-digit').
            t0 = time.time() - 300  # OTP may already be in the inbox
            name_done = otp_done = False
            resubmits = 0
            phase_deadline = time.time() + 600
            while time.time() < phase_deadline:
                if first_visible(page, ['input[type="password"]'],
                                 timeout=500) is not None:
                    break  # reached the password page
                if name_done and otp_done:
                    break
                try:
                    low = page.inner_text('body').lower()
                except Exception:
                    low = ''
                if 'invalid email' in low:
                    raise RuntimeError(
                        f'email possibly rejected: {low[:200]}')
                if 'error processing your request' in low and resubmits < 3:
                    resubmits += 1
                    print(f'[retry] AWS error banner, resubmit '
                          f'{resubmits}/3', flush=True)
                    page.wait_for_timeout(2000)
                    click_continue(page)
                    page.wait_for_timeout(3000)
                    continue
                if not otp_done:
                    otp_in = first_visible(
                        page, ['input[placeholder*="6-digit" i]'],
                        timeout=500)
                    if otp_in is not None:
                        code = latest_otp(local, t0, email_addr=email_addr)
                        if not code:
                            raise RuntimeError('OTP not received in time')
                        print('[otp] code received', flush=True)
                        type_like_human(otp_in, code)
                        if not click_continue(page):
                            raise RuntimeError('continue after OTP failed')
                        otp_done = True
                        shot(page, f'{local}-3-otp')
                        page.wait_for_timeout(2500)
                        continue
                if not name_done:
                    name_in = None
                    try:
                        cand = page.get_by_label('Name', exact=True).first
                        if cand.is_visible(timeout=500):
                            name_in = cand
                    except Exception:
                        pass
                    if name_in is None and 'enter your name' in low:
                        cand = first_visible(page, ['input[type="text"]'],
                                             timeout=500)
                        if cand:
                            ph = (cand.get_attribute('placeholder')
                                  or '').lower()
                            if ('example.com' not in ph
                                    and 'email' not in ph
                                    and 'digit' not in ph):
                                name_in = cand
                    if name_in is not None:
                        type_like_human(name_in, name)
                        if not click_continue(page):
                            raise RuntimeError('continue after name failed')
                        name_done = True
                        shot(page, f'{local}-2-name')
                        page.wait_for_timeout(2500)
                        continue
                page.wait_for_timeout(2000)
            else:
                raise RuntimeError(
                    f'signup steps stalled (name={name_done}, '
                    f'otp={otp_done})')
            shot(page, f'{local}-4-password')

            pw_ready = wait_step(page, ['input[type="password"]'], text_needles=('create your password',))
            if not pw_ready: raise RuntimeError('password page never appeared')
            pw_fields = page.locator('input[type="password"]')
            n = pw_fields.count()
            if n == 0: raise RuntimeError('password field not found')
            type_like_human(pw_fields.nth(0), password)
            if n > 1: type_like_human(pw_fields.nth(1), password)
            if not click_continue(page): raise RuntimeError('continue after password failed')

            # wait for workspace; the flow may detour via the QuickSuite
            # bootstrap page ("Your account is ready") - click through it,
            # and count reaching it as registered even if the click fails.
            ok = False
            bootstrap_seen = False
            clicked_bootstrap = False
            for _ in range(60):
                page.wait_for_timeout(6000)
                url = page.url
                m2 = re.search(r'quick\.aws\.com/sn/account/(\d+)', url)
                if m2:
                    rec['account_id'] = m2.group(1); ok = True; break
                if 'quick.aws.com' in url and '/start' in url:
                    ok = True; break
                # Resumed accounts detour through a full sign-in:
                # password page, then an email OTP ("Verify your
                # identity") before the funnel continues.
                _otp_in = first_visible(
                    page, ['input[placeholder*="6-digit" i]'], timeout=500)
                if _otp_in is not None:
                    _code = latest_otp(local, t0, email_addr=email_addr)
                    if _code:
                        print('[otp] sign-in code received', flush=True)
                        type_like_human(_otp_in, _code)
                        click_continue(page)
                        page.wait_for_timeout(3000)
                        continue
                try:
                    txt = page.inner_text('body').lower()
                except Exception:
                    txt = ''
                if 'existing builder id' in txt:
                    # The Builder ID was created by an earlier attempt;
                    # the Quick funnel asks to confirm using it.
                    click_continue(page)
                if 'your account is ready' in txt:
                    bootstrap_seen = True
                    if not clicked_bootstrap:
                        clicked_bootstrap = True
                        for label in ('Get started', 'Continue', 'Start free trial',
                                      'Access Amazon Quick', 'Launch', 'Next'):
                            try:
                                btn = page.get_by_role('button', name=label, exact=False).first
                                if btn.is_visible(timeout=1500):
                                    btn.click(); break
                            except Exception:
                                continue
            shot(page, f'{local}-5-done')
            if not ok and bootstrap_seen:
                ok = True
                rec['bootstrap_pending'] = True
            if not ok:
                raise RuntimeError(f'did not reach workspace; url={page.url} text={page.inner_text("body")[:200]}')
            rec['status'] = 'registered'
            rec['workspace_url'] = page.url
            print(f'[done] {email_addr} account_id={rec.get("account_id")}', flush=True)
            if getattr(args, 'export_json', False):
                try:
                    export_gateway_key(ctx, page, local, email_addr,
                                       password, rec,
                                       proxy_server=raw_proxy or None)
                except Exception as e:
                    rec['gateway_key_error'] = str(e)[:300]
                    print(f'[export] FAILED {email_addr}: {e}', flush=True)
        except Exception as e:
            rec['status'] = 'failed'
            rec['error'] = str(e)[:400]
            shot(page, f'{local}-X-fail')
            print(f'[fail] {email_addr}: {e}', flush=True)
        finally:
            save_account(email_addr, rec)
            browser.close()
    return 0 if rec['status'] == 'registered' else 1

BATCH_INTERVAL_S = 300

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('locals', nargs='+',
                    help='one or more mailbox emails or locals; with several, '
                         'runs are paced 5 minutes apart (start to start)')
    ap.add_argument('--name', default=None)
    ap.add_argument('--headful', action='store_true')
    ap.add_argument('--export-json', action='store_true',
                    help='after registering, mint the gateway credential '
                         'JSON into keys/ via OAuth in the same browser, '
                         'then remove the pool slot')
    args = ap.parse_args()
    rc = 0
    for i, local in enumerate(args.locals):
        t0 = time.time()
        try:
            rc = run_one(local, args) or rc
        except SystemExit as e:
            print(f'[batch] {local}: aborted ({e})', flush=True)
            rc = 1
        if i < len(args.locals) - 1:
            wait = BATCH_INTERVAL_S - (time.time() - t0)
            if wait > 0:
                print(f'[batch] next email in {wait:.0f}s '
                      f'(5-minute pacing)', flush=True)
                time.sleep(wait)
    return rc

if __name__ == '__main__':
    sys.exit(main())
