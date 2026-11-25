#!/usr/bin/env python3
"""Authorize the owner's own Outlook/Hotmail mailbox for Graph OTP
polling, via Microsoft's device-code flow:

  1. The owner registers an app in their own Microsoft account and
     gives us the (public) client_id.
  2. `outlook_auth.py auth <client_id>` prints a short code; the
     owner opens https://www.microsoft.com/link, signs in to their
     own mailbox and consents to Mail.Read.
  3. The resulting refresh token is stored in outlook-tokens.json
     (mode 600). Tokens are never printed.

Usage:
  outlook_auth.py auth <client_id>
  outlook_auth.py import <cred_line_or_file>
  outlook_auth.py list
  outlook_auth.py check <email>
"""
import base64, json, sys, time
import urllib.error, urllib.parse, urllib.request

import outlook_mail as om

DEVICE_URL = ("https://login.microsoftonline.com/consumers"
              "/oauth2/v2.0/devicecode")
SCOPE = "offline_access openid https://graph.microsoft.com/Mail.Read"


def _post(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "quick-farm/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        try:
            return None, json.load(e)
        except Exception:
            return None, {"error": f"http {e.code}"}


def cmd_auth(client_id, expect_email=None):
    out, err = _post(DEVICE_URL, {"client_id": client_id, "scope": SCOPE})
    if err or not out:
        print(f"[auth] device code request failed: "
              f"{(err or {}).get('error_description') or err}")
        return 1
    print("[auth] 让邮箱本人在浏览器里完成这一步：")
    print(f"[auth] 打开 {out.get('verification_uri', 'https://www.microsoft.com/link')}")
    print(f"[auth] 输入代码: {out['user_code']}")
    print("[auth] 用要授权的那个 Outlook 邮箱登录并同意 Mail.Read")
    interval = int(out.get("interval", 5))
    deadline = time.time() + int(out.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        tok, err = _post(om.TOKEN_URL, {
            "client_id": client_id,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": out["device_code"],
        })
        if tok:
            break
        code = (err or {}).get("error")
        if code == "authorization_pending":
            continue
        if code == "slow_down":
            interval += 5
            continue
        print(f"[auth] failed: {(err or {}).get('error_description') or err}")
        return 1
    else:
        print("[auth] code expired before authorization completed")
        return 1
    # Identify the account from the id_token (the /me profile endpoint
    # needs User.Read, which we deliberately do not request). Save the
    # token FIRST, verify mail access afterwards: a crash between
    # consent and save would lose the one-time token.
    email_addr = None
    if tok.get("id_token"):
        try:
            payload = tok["id_token"].split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            print(f"[auth] id_token claims: {sorted(claims)}")
            email_addr = (claims.get("preferred_username")
                          or claims.get("email"))
        except Exception:
            pass
    else:
        print(f"[auth] token fields: {sorted(tok)} (no id_token)")
    if not email_addr:
        try:
            me = om._get_json(
                f"{om.GRAPH}/me?$select=mail,userPrincipalName",
                tok["access_token"])
            email_addr = me.get("mail") or me.get("userPrincipalName")
        except Exception:
            pass
    if expect_email and email_addr and \
            email_addr.lower() != expect_email.lower():
        print(f"[auth] note: consented account is {email_addr}, "
              f"not {expect_email}")
    if not email_addr:
        email_addr = expect_email
    if not email_addr or not tok.get("refresh_token"):
        print("[auth] token received but account email unknown; "
              "not stored")
        return 1
    store = om.load_store()
    store[email_addr] = {
        "client_id": client_id,
        "refresh_token": tok["refresh_token"],
        "scope": SCOPE,
    }
    om.save_store(store)
    print(f"[auth] authorized and stored: {email_addr}")
    try:
        msgs = om._get_json(
            f"{om.GRAPH}/me/messages?$top=1&$select=subject",
            tok["access_token"])
        print(f"[auth] mail access verified "
              f"({len(msgs.get('value', []))} latest message readable)")
    except Exception as e:
        print(f"[auth] stored, but mail read check failed: {e}")
    return 0


def cmd_import(arg):
    import os
    raw = open(arg, encoding="utf-8").read() if os.path.isfile(arg) else arg
    res = om.import_credentials(raw)
    for addr in res["imported"]:
        print(f"[import] stored: {addr}")
    for err in res["errors"]:
        print(f"[import] line {err['line']} error: {err['error']}")
    return 0 if res["imported"] and not res["errors"] else 1


def cmd_list():
    for email_addr in om.load_store():
        print(email_addr)
    return 0


def cmd_check(email_addr):
    res = om.list_messages(email_addr, limit=3)
    print(f"[check] {res['email']} ({res['protocol']}): inbox readable, "
          f"latest {len(res['messages'])} messages:")
    for m in res["messages"]:
        code_tag = f" [OTP: {m['code']}]" if m.get("code") else ""
        print(f"  {m.get('received_at', '')}  {m.get('subject', '')[:60]}{code_tag}")
    return 0


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "auth":
        return cmd_auth(sys.argv[2],
                        sys.argv[3] if len(sys.argv) >= 4 else None)
    if len(sys.argv) >= 3 and sys.argv[1] == "import":
        return cmd_import(sys.argv[2])
    if len(sys.argv) >= 2 and sys.argv[1] == "list":
        return cmd_list()
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        return cmd_check(sys.argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
