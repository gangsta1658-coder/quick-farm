#!/usr/bin/env python3
"""Outlook/Hotmail mailbox OTP polling via Microsoft Graph or IMAP XOAUTH2.

Supports both:
  1. Mailboxes authorized via outlook_auth.py's device-code flow (Graph).
  2. Imported credentials in `email----password----client_id----refresh_token`
     format (automatically detecting Graph Mail.Read vs IMAP XOAUTH2).

Store: JSON (mode 600) mapping email -> {client_id, refresh_token, scope,
...}. Path from env OUTLOOK_TOKEN_FILE, default next to this file.
Microsoft rotates refresh tokens on use; the newest one is written
back after every refresh. Tokens and passwords are never printed.
"""
import base64
import email as em
from email.header import decode_header
from email.utils import parsedate_to_datetime
import imaplib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
TOKEN_URL_CONSUMERS = (
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
)
TOKEN_URL_COMMON = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/token"
)
TOKEN_URL = TOKEN_URL_CONSUMERS
GRAPH = "https://graph.microsoft.com/v1.0"
IMAP_HOSTS = ("outlook.office365.com", "outlook.live.com")
CODE_RE = re.compile(r"(?<!#)\b\d{6}\b")
SENDERS = ("signin.aws", "login.awsapps.com")
GRAPH_SCOPE = "https://graph.microsoft.com/Mail.Read offline_access"
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
DEFAULT_SCOPE = GRAPH_SCOPE

_STORE_LOCK = threading.RLock()
_CONSUMER_DOMAINS = ("hotmail.", "outlook.", "live.", "msn.")


def token_file():
    return os.path.expanduser(
        os.environ.get("OUTLOOK_TOKEN_FILE",
                       os.path.join(BASE, "outlook-tokens.json")))


def load_store():
    with _STORE_LOCK:
        path = token_file()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {}


def save_store(store):
    with _STORE_LOCK:
        path = token_file()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=1, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)


def has_account(address):
    if not address:
        return False
    return address.lower() in {k.lower() for k in load_store()}


def _entry(address):
    store = load_store()
    for k, v in store.items():
        if k.lower() == address.lower():
            return k, v
    raise KeyError(f"no outlook token stored for {address}")


def parse_import_line(line):
    """Parse one credential line.

    Supported formats:
      - email----password----client_id----refresh_token
      - email----client_id----refresh_token
      - email----https://...  (URL-based OTP polling)
      - email----password----https://...
    Returns (email_addr, entry_dict). Raises ValueError on bad input.
    """
    raw = line.strip()
    if not raw:
        raise ValueError("空行")
    parts = [p.strip() for p in raw.split("----")]
    email_addr = parts[0] if parts else ""
    if "@" not in email_addr or "." not in email_addr.split("@", 1)[1]:
        raise ValueError(f"邮箱格式无效: {email_addr or '(空)'}")

    if len(parts) >= 2 and parts[1].lower().startswith(("http://", "https://")):
        otp_url = "----".join(parts[1:]).strip()
        return email_addr, {
            "protocol": "url_otp",
            "otp_url": otp_url,
        }
    if len(parts) >= 3 and parts[2].lower().startswith(("http://", "https://")):
        password = parts[1]
        otp_url = "----".join(parts[2:]).strip()
        entry = {
            "protocol": "url_otp",
            "otp_url": otp_url,
        }
        if password:
            entry["password"] = password
        return email_addr, entry

    if len(parts) >= 4:
        password, client_id, refresh_token = parts[1], parts[2], parts[3]
    elif len(parts) == 3:
        password, client_id, refresh_token = "", parts[1], parts[2]
    else:
        raise ValueError(
            "格式不正确，支持 邮箱----密码----client_id----refresh_token 或 邮箱----接码URL"
        )
    if not client_id:
        raise ValueError("缺少 client_id")
    if not refresh_token:
        raise ValueError("缺少 refresh_token")
    entry = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "scope": "",
    }
    if password:
        entry["password"] = password
    return email_addr, entry


def import_credentials(raw_text):
    """Parse one or more lines and save them into the Outlook token store.

    Returns {"imported": [email, ...], "errors": [{"line": n, "error": ...}]}.
    """
    imported = []
    errors = []
    with _STORE_LOCK:
        store = load_store()
        for idx, line in enumerate((raw_text or "").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                email_addr, entry = parse_import_line(stripped)
            except ValueError as e:
                errors.append({"line": idx, "error": str(e)})
                continue
            existing_key = next(
                (k for k in store if k.lower() == email_addr.lower()), None
            )
            if existing_key and existing_key != email_addr:
                store.pop(existing_key, None)
            store[email_addr] = entry
            imported.append(email_addr)
        if imported:
            save_store(store)
    return {"imported": imported, "errors": errors}


def delete_account(address):
    with _STORE_LOCK:
        store = load_store()
        key = next((k for k in store if k.lower() == address.lower()), None)
        if not key:
            return False
        store.pop(key)
        save_store(store)
        return True


def _token_url_for(address):
    domain = address.split("@", 1)[1].lower() if "@" in address else ""
    if any(domain.startswith(p) for p in _CONSUMER_DOMAINS):
        return TOKEN_URL_CONSUMERS
    return TOKEN_URL_COMMON


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "quick-farm/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
            err = json.loads(body)
            desc = err.get("error_description") or err.get("error") or body
        except Exception:
            desc = body or str(e)
        raise RuntimeError(f"OAuth token HTTP {e.code}: {desc[:240]}") from e


def _jwt_claims(token):
    if not token or token.count(".") != 2:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _detect_protocol(scope_str, access_token):
    low = (scope_str or "").lower()
    if "mail.read" in low or "graph.microsoft.com" in low:
        return "graph"
    if "imap.accessasuser.all" in low or "outlook.office.com" in low \
            or "outlook.office365.com" in low:
        return "imap_xoauth2"
    claims = _jwt_claims(access_token)
    aud = str(claims.get("aud", "")).lower()
    scp = str(claims.get("scp", "")).lower()
    if "graph.microsoft.com" in aud or "00000003-0000-0000-c000-000000000000" in aud:
        return "graph"
    if "outlook.office" in aud or "00000002-0000-0ff1-ce00-000000000000" in aud:
        return "imap_xoauth2"
    if "mail.read" in scp:
        return "graph"
    if "imap.accessasuser.all" in scp:
        return "imap_xoauth2"
    return None


def _refresh_once(url, client_id, refresh_token, scope=None):
    fields = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if scope:
        fields["scope"] = scope
    return _post_form(url, fields)


def _refresh_token_info(address, force_scope=None):
    """Refresh OAuth access token, persisting rotated refresh tokens.

    Returns (access_token, protocol, granted_scope).
    """
    key, entry = _entry(address)
    stored_scope = force_scope if force_scope is not None else entry.get("scope", "")
    urls = [_token_url_for(key)]
    if TOKEN_URL_COMMON not in urls:
        urls.append(TOKEN_URL_COMMON)

    out = None
    last_err = None
    scope_candidates = [stored_scope] if stored_scope else [None, IMAP_SCOPE, GRAPH_SCOPE]

    for url in urls:
        for sc in scope_candidates:
            try:
                out = _refresh_once(
                    url, entry["client_id"], entry["refresh_token"], scope=sc
                )
                break
            except RuntimeError as e:
                last_err = e
                msg = str(e).lower()
                # If the refresh token itself is revoked/invalid, stop retrying scopes
                if "invalid_grant" in msg and "scope" not in msg:
                    break
        if out is not None:
            break
        if last_err and "invalid_grant" in str(last_err).lower() \
                and "scope" not in str(last_err).lower():
            break

    if out is None:
        raise last_err or RuntimeError("OAuth token refresh failed")

    granted_scope = out.get("scope") or stored_scope or ""
    protocol = _detect_protocol(granted_scope, out.get("access_token", ""))
    if not protocol and entry.get("protocol"):
        protocol = entry["protocol"]

    with _STORE_LOCK:
        store = load_store()
        cur = store.get(key, entry)
        updated = dict(cur)
        changed = False
        if out.get("refresh_token") and out["refresh_token"] != cur.get("refresh_token"):
            updated["refresh_token"] = out["refresh_token"]
            changed = True
        if granted_scope and not cur.get("scope"):
            updated["scope"] = granted_scope
            changed = True
        if protocol and cur.get("protocol") != protocol:
            updated["protocol"] = protocol
            changed = True
        if changed:
            store[key] = updated
            save_store(store)

    return out["access_token"], protocol or "graph", granted_scope


def _access_token(address):
    token, _, _ = _refresh_token_info(address)
    return token


def _get_json(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "quick-farm/1.0",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _strip_html(html):
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_ts(value):
    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _decode_mime_header(value):
    if not value:
        return ""
    out = []
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", "replace"))
        else:
            out.append(part)
    return "".join(out)


def _extract_code(text):
    if not text:
        return None
    m = CODE_RE.search(text)
    return m.group(0) if m else None


def _extract_email_body(msg):
    plain_parts = []
    html_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            ctype = (part.get_content_type() or "").lower()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, "replace")
            if ctype == "text/plain":
                plain_parts.append(decoded)
            elif ctype == "text/html":
                html_parts.append(decoded)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, "replace")
            if (msg.get_content_type() or "").lower() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)
    plain = "\n".join(plain_parts).strip()
    if plain:
        return plain
    return _strip_html("\n".join(html_parts))


def _fetch_messages_graph(token, limit=20):
    q = urllib.parse.urlencode({
        "$top": limit,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,body,bodyPreview",
    })
    data = _get_json(f"{GRAPH}/me/messages?{q}", token)
    out = []
    for msg in data.get("value", []):
        sender = ((msg.get("from") or {})
                  .get("emailAddress") or {}).get("address", "")
        subj = msg.get("subject", "") or ""
        received_at = msg.get("receivedDateTime", "") or ""
        raw_body = (msg.get("body") or {}).get("content", "")
        text = _strip_html(raw_body) if "<" in raw_body else re.sub(r"\s+", " ", raw_body).strip()
        preview = (msg.get("bodyPreview") or text or "")[:200]
        out.append({
            "id": msg.get("id") or f"{received_at}:{subj}",
            "from": sender,
            "subject": subj,
            "received_at": received_at,
            "timestamp": _parse_ts(received_at),
            "text": text,
            "preview": preview,
            "code": _extract_code(subj + " " + text),
        })
    return out


def _imap_connect_xoauth2(address, token):
    auth_bytes = f"user={address}\x01auth=Bearer {token}\x01\x01".encode("utf-8")
    last_err = None
    for host in IMAP_HOSTS:
        try:
            m = imaplib.IMAP4_SSL(host, 993, timeout=30)
            sent = False

            def _cb(_resp):
                nonlocal sent
                if not sent:
                    sent = True
                    return auth_bytes
                return b""

            m.authenticate("XOAUTH2", _cb)
            return m
        except Exception as e:
            last_err = e
    raise RuntimeError(f"IMAP XOAUTH2 login failed: {last_err}")


def _fetch_messages_imap(address, token, limit=20):
    m = _imap_connect_xoauth2(address, token)
    out = []
    try:
        for folder in ("INBOX", "Junk"):
            try:
                typ, _ = m.select(folder, readonly=True)
                if typ != "OK":
                    continue
                typ, data = m.search(None, "ALL")
                if typ != "OK" or not data or not data[0]:
                    continue
                nums = data[0].split()[-limit:]
                for mid in reversed(nums):
                    typ2, fetched = m.fetch(mid, "(RFC822)")
                    if typ2 != "OK" or not fetched:
                        continue
                    raw_bytes = None
                    for item in fetched:
                        if isinstance(item, tuple) and len(item) >= 2:
                            raw_bytes = item[1]
                            break
                    if not raw_bytes:
                        continue
                    msg = em.message_from_bytes(raw_bytes)
                    sender = _decode_mime_header(msg.get("From", ""))
                    subj = _decode_mime_header(msg.get("Subject", ""))
                    date_hdr = msg.get("Date", "")
                    ts = None
                    received_at = date_hdr
                    try:
                        dt = parsedate_to_datetime(date_hdr)
                        if dt is not None:
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            ts = dt.timestamp()
                            received_at = dt.astimezone(timezone.utc).isoformat()
                    except Exception:
                        pass
                    text = _extract_email_body(msg)
                    msg_id = msg.get("Message-ID") or f"{folder}:{mid.decode(errors='replace')}"
                    out.append({
                        "id": msg_id,
                        "from": sender,
                        "subject": subj,
                        "received_at": received_at,
                        "timestamp": ts,
                        "text": text,
                        "preview": text[:200],
                        "code": _extract_code(subj + " " + text),
                    })
            except Exception:
                continue
    finally:
        try:
            m.logout()
        except Exception:
            pass
    out.sort(key=lambda x: x.get("timestamp") or 0, reverse=True)
    return out[:limit]


def _parse_any_ts(value):
    if value is None or value == "":
        return None, ""
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return ts, iso
    s = str(value).strip()
    if s.isdigit():
        ts = float(s)
        if ts > 1e12:
            ts /= 1000.0
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return ts, iso
    ts = _parse_ts(s)
    if ts is not None:
        return ts, s
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp(), dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    return None, s


def _fetch_messages_url(otp_url, limit=20):
    req = urllib.request.Request(otp_url, headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw_text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise RuntimeError(f"URL 接码 HTTP {e.code}: {body[:200] or e}") from e

    items = []
    parsed = None
    try:
        parsed = json.loads(raw_text)
    except Exception:
        parsed = None

    if parsed is None:
        stripped = raw_text.strip()
        if stripped:
            items = [stripped]
    elif isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        has_empty_code = (
            parsed.get("success") is False
            and not parsed.get("error")
            and "code" in parsed
            and not str(parsed.get("code") or "").strip()
            and not str(parsed.get("subject") or "").strip()
        )
        if has_empty_code:
            items = []
        elif parsed.get("success") is False or parsed.get("error"):
            err_msg = parsed.get("error") or parsed.get("message") or parsed.get("msg") or raw_text[:200]
            raise RuntimeError(f"URL 接码返回错误: {err_msg}")
        else:
            for k in ("data", "messages", "emails", "mails", "items", "list", "results", "records"):
                val = parsed.get(k)
                if isinstance(val, list):
                    items = val
                    break
            else:
                if isinstance(parsed.get("data"), dict):
                    items = [parsed["data"]]
                elif any(k in parsed for k in (
                    "code", "otp", "verification_code", "pin",
                    "subject", "body", "text", "html", "content", "message", "data",
                )):
                    if isinstance(parsed.get("data"), str):
                        items = [parsed["data"]]
                    else:
                        items = [parsed]
    else:
        items = [str(parsed)]

    out = []
    for idx, item in enumerate(items):
        if isinstance(item, dict):
            sender = str(
                item.get("from")
                or item.get("sender")
                or item.get("from_address")
                or item.get("fromEmail")
                or ""
            ).strip()
            subj = str(item.get("subject") or item.get("title") or "").strip()
            raw_html = str(
                item.get("html")
                or item.get("body_html")
                or item.get("content_html")
                or ""
            )
            raw_body = str(
                item.get("text")
                or item.get("body")
                or item.get("content")
                or item.get("body_text")
                or item.get("message")
                or item.get("preview")
                or ""
            )
            text = raw_body.strip() or _strip_html(raw_html)
            if "<" in text and ">" in text:
                text = _strip_html(text)
            explicit_code = (
                item.get("code")
                or item.get("otp")
                or item.get("verification_code")
                or item.get("pin")
            )
            code = (
                _extract_code(str(explicit_code))
                if explicit_code is not None else None
            ) or _extract_code(subj + " " + text)
            ts, received_at = None, ""
            for tk in (
                "timestamp", "ts", "received_at", "receivedAt",
                "created_at", "createdAt", "date", "time",
                "updated_at", "updatedAt",
            ):
                if item.get(tk) not in (None, ""):
                    ts, received_at = _parse_any_ts(item.get(tk))
                    break
            if not subj and not text and not code:
                continue
            msg_id = str(
                item.get("id")
                or item.get("msg_id")
                or item.get("uid")
                or f"url:{idx}:{received_at}:{subj}:{code or text[:40]}"
            )
            out.append({
                "id": msg_id,
                "from": sender,
                "subject": subj,
                "received_at": received_at,
                "timestamp": ts,
                "text": text,
                "preview": (text or code or "")[:200],
                "code": code,
            })
        else:
            s = str(item).strip()
            if not s:
                continue
            code = _extract_code(s)
            out.append({
                "id": f"url:{idx}:{s[:40]}",
                "from": "",
                "subject": "",
                "received_at": "",
                "timestamp": None,
                "text": s,
                "preview": s[:200],
                "code": code,
            })

    if any(m.get("timestamp") is not None for m in out):
        out.sort(key=lambda x: x.get("timestamp") or 0, reverse=True)
    return out[:limit]


def list_messages(address, limit=10):
    """Fetch recent messages for an Outlook/Hotmail or URL-OTP mailbox."""
    key, entry = _entry(address)
    if entry.get("protocol") == "url_otp" or entry.get("otp_url"):
        msgs = _fetch_messages_url(entry["otp_url"], limit=limit)
        return {
            "email": key,
            "protocol": "url_otp",
            "scope": "",
            "messages": [
                {
                    "id": m["id"],
                    "from": m["from"],
                    "subject": m["subject"],
                    "received_at": m["received_at"],
                    "preview": m["preview"],
                    "code": m["code"],
                }
                for m in msgs
            ],
        }

    token, protocol, granted_scope = _refresh_token_info(address)
    try:
        if protocol == "imap_xoauth2":
            msgs = _fetch_messages_imap(address, token, limit=limit)
        else:
            msgs = _fetch_messages_graph(token, limit=limit)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and protocol == "graph":
            # Token may actually be scoped for Outlook IMAP rather than Graph
            token, protocol, granted_scope = _refresh_token_info(
                address, force_scope=IMAP_SCOPE
            )
            protocol = "imap_xoauth2"
            msgs = _fetch_messages_imap(address, token, limit=limit)
        else:
            raise
    return {
        "email": key,
        "protocol": protocol,
        "scope": granted_scope,
        "messages": [
            {
                "id": m["id"],
                "from": m["from"],
                "subject": m["subject"],
                "received_at": m["received_at"],
                "preview": m["preview"],
                "code": m["code"],
            }
            for m in msgs
        ],
    }


def latest_otp(address, after_ts, timeout=180):
    """Poll the mailbox for the newest AWS verification code."""
    deadline = time.time() + timeout
    seen = set()
    token = None
    protocol = None
    last_err = None
    _, entry = _entry(address)
    is_url = entry.get("protocol") == "url_otp" or bool(entry.get("otp_url"))

    while time.time() < deadline:
        try:
            if is_url:
                msgs = _fetch_messages_url(entry["otp_url"], limit=20)
                for msg in msgs:
                    mid = msg.get("id")
                    if not mid or mid in seen:
                        continue
                    seen.add(mid)
                    sender = (msg.get("from") or "").lower()
                    if sender and not any(
                        s in sender for s in (*SENDERS, "aws", "amazon")
                    ):
                        continue
                    ts = msg.get("timestamp")
                    if ts is not None and ts < after_ts:
                        continue
                    subj = msg.get("subject", "")
                    if subj and (
                        "verif" not in subj.lower()
                        and "code" not in subj.lower()
                        and "otp" not in subj.lower()
                        and "amazon" not in subj.lower()
                        and "aws" not in subj.lower()
                        and "验证" not in subj
                    ):
                        continue
                    code = msg.get("code") or _extract_code(msg.get("text", ""))
                    if code:
                        return code
                time.sleep(5)
                continue

            if token is None:
                token, protocol, _ = _refresh_token_info(address)
            if protocol == "imap_xoauth2":
                msgs = _fetch_messages_imap(address, token, limit=20)
            else:
                msgs = _fetch_messages_graph(token, limit=20)
            for msg in msgs:
                mid = msg.get("id")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                sender = (msg.get("from") or "").lower()
                if not any(s in sender for s in SENDERS):
                    continue
                ts = msg.get("timestamp")
                if ts is not None and ts < after_ts:
                    continue
                subj = msg.get("subject", "")
                if "verif" not in subj.lower() and "code" not in subj.lower():
                    continue
                code = _extract_code(msg.get("text", ""))
                if code:
                    return code
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and protocol == "graph":
                try:
                    token, protocol, _ = _refresh_token_info(
                        address, force_scope=IMAP_SCOPE
                    )
                    protocol = "imap_xoauth2"
                    continue
                except Exception as e2:
                    err_s = str(e2)
                    if err_s != last_err:
                        print(f"[outlook] {address}: {err_s}", flush=True)
                        last_err = err_s
                    token = None
            else:
                token = None
        except Exception as e:
            err_s = str(e)
            if err_s != last_err:
                print(f"[outlook] {address}: {err_s}", flush=True)
                last_err = err_s
            token = None
        time.sleep(5)
    return None
