#!/usr/bin/env python3
"""quick-farm backend: registration bot control API.

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8790
Auth: every /api/* call (except /api/health) needs the header
      X-Admin-Token: <admin_token from server/config.json>
"""
import json, os, subprocess, sys, threading, time, uuid
from collections import deque

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import config_store
import mail_adapters
import export_service
import outlook_mail as om

CFG = config_store.load_config()

_tok = CFG.get("outlook", {}).get("token_file")
if _tok:
    if not os.path.isabs(_tok):
        _tok = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), _tok))
    os.environ.setdefault("OUTLOOK_TOKEN_FILE", _tok)
PY = os.environ.get("QUICKFARM_PYTHON", sys.executable)

app = FastAPI(title="quick-farm backend", version="1.0.0")

# The web UI is served from another origin (Next dev/prod server);
# access is still gated by the X-Admin-Token header on every route.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_tasks = {}
_tasks_lock = threading.Lock()


def _auth(x_admin_token: str = Header(default="")):
    if x_admin_token != CFG.get("admin_token"):
        raise HTTPException(401, "bad admin token")


def _new_task(kind, params):
    tid = uuid.uuid4().hex[:12]
    task = {"id": tid, "kind": kind, "params": params, "status": "running",
            "started_at": time.time(), "finished_at": None,
            "result": None, "error": None, "log": deque(maxlen=2000)}
    with _tasks_lock:
        _tasks[tid] = task
    return task


def _task_view(task, log_tail=0):
    out = {k: task[k] for k in ("id", "kind", "params", "status",
                                "started_at", "finished_at", "result",
                                "error")}
    if log_tail:
        out["log"] = list(task["log"])[-log_tail:]
    return out


def _accounts():
    path = os.path.join(BASE, "accounts.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _outlook_token_path():
    tok = CFG.get("outlook", {}).get("token_file")
    if not tok:
        return None
    if os.path.isabs(tok):
        return tok
    return os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), tok))


def _sync_outlook_env():
    tok = _outlook_token_path()
    if tok:
        os.environ["OUTLOOK_TOKEN_FILE"] = tok


def _outlook_store():
    _sync_outlook_env()
    try:
        return om.load_store()
    except Exception:
        return {}


def _proxy_url():
    return str((CFG.get("proxy") or {}).get("url") or "").strip()


def _resolve_proxy_url(raw):
    import secrets
    raw = str(raw or "").strip()
    if not raw:
        return ""
    if "{rand}" in raw or "{ssid}" in raw:
        sid = "".join(secrets.choice("0123456789") for _ in range(8))
        raw = raw.replace("{rand}", sid).replace("{ssid}", sid)
    return raw


def _safe_proxy_label(raw):
    from urllib.parse import urlparse
    u = urlparse(raw)
    if not u.scheme or not u.hostname:
        return None
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{u.hostname}{port}"


def _subprocess_env(provider, local=None, email_addr=None, use_proxy=True,
                    proxy_url=None):
    env = dict(os.environ)
    if proxy_url is not None:
        env["QUICK_BOT_PROXY"] = proxy_url
    else:
        env["QUICK_BOT_PROXY"] = _resolve_proxy_url(_proxy_url()) if use_proxy else ""
    if provider in ("outlook", "url"):
        tok = _outlook_token_path()
        if tok:
            env["OUTLOOK_TOKEN_FILE"] = tok
        if email_addr:
            env["QUICKFARM_EMAIL"] = email_addr
    return env


def _run_register_task(task, items, use_proxy, do_export):
    log = task["log"].append
    failed, done_ok = [], []
    try:
        proxy_template = _proxy_url() if use_proxy else ""
        if use_proxy:
            label = _safe_proxy_label(proxy_template)
            if not label:
                raise RuntimeError("已开启走代理，但未保存有效代理 URL")
            log(f"[task] proxy verified: {label}")
        else:
            log("[task] proxy disabled (direct)")
        interval = int(CFG.get("register", {}).get("batch_interval_s", 300))
        first = True
        for item in items:
            if not first and interval:
                log(f"[task] batch interval {interval}s")
                time.sleep(interval)
            first = False
            local, provider = item["local"], item["provider"]
            email_addr = item.get("email")
            if not email_addr and provider in ("outlook", "url"):
                email_addr = next(
                    (a for a in _outlook_store()
                     if a.split("@")[0].lower() == local.lower()), None)
            if not email_addr:
                log(f"[task] register {local}: mailbox not found, skipped")
                failed.append(local)
                continue
            item_proxy = _resolve_proxy_url(proxy_template) if use_proxy else ""
            cmd = [PY, os.path.join(BASE, "register.py"), local]
            log(f"[task] register {email_addr}: {' '.join(cmd[1:])}")
            proc = subprocess.Popen(
                cmd, cwd=BASE, env=_subprocess_env(provider, local,
                                                   email_addr,
                                                   use_proxy=use_proxy,
                                                   proxy_url=item_proxy),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            for line in proc.stdout:
                log(line.rstrip())
            rc = proc.wait()
            if rc != 0:
                log(f"[task] register {email_addr} exited {rc}")
                failed.append(email_addr)
                continue
            done_ok.append(email_addr)
            if do_export:
                log(f"[task] export {email_addr} (local OAuth)")
                res = export_service.export_account(
                    email_addr, proxy_url=item_proxy,
                    log=lambda m: log(str(m)))
                log(f"[task] exported: {res}")
        if failed:
            task["status"] = "failed"
            task["error"] = "注册失败: " + ", ".join(failed)
        else:
            task["status"] = "done"
        task["result"] = {"accounts": list(_accounts().keys()),
                          "registered": done_ok, "failed": failed}
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)[:500]
    finally:
        task["finished_at"] = time.time()


def _run_export_task(task, email_key, use_proxy):
    log = task["log"].append
    try:
        proxy_url = _resolve_proxy_url(_proxy_url()) if use_proxy else ""
        res = export_service.export_account(
            email_key, proxy_url=proxy_url, log=lambda m: log(str(m)))
        task["status"] = "done"
        task["result"] = res
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)[:500]
    finally:
        task["finished_at"] = time.time()


# --------------------------------------------------------------- routes

@app.get("/api/health")
def health():
    return {"ok": True, "service": "quick-farm", "version": "1.0.0"}


@app.get("/api/config")
def get_config(x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    return config_store.public_view(CFG)


@app.put("/api/config")
async def put_config(req: Request,
                     x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    global CFG
    patch = await req.json()
    CFG = config_store.apply_update(CFG, patch)
    _sync_outlook_env()
    return config_store.public_view(CFG)


def _probe_proxy(raw_url):
    import shutil
    from urllib.parse import urlparse
    u = urlparse(raw_url)
    scheme = (u.scheme or "").lower()
    if scheme not in ("http", "https", "socks5", "socks5h", "socks4") \
            or not u.hostname:
        return {"ok": False, "error": "代理 URL 格式无效（支持 http/https/socks5）"}
    label = _safe_proxy_label(raw_url)
    curl_bin = shutil.which("curl") or shutil.which("curl.exe")
    if not curl_bin:
        return {"ok": False, "proxy": label, "error": "服务器未找到 curl"}
    t0 = time.time()
    proc = subprocess.run(
        [
            curl_bin, "-sS", "-x", raw_url,
            "--connect-timeout", "10", "--max-time", "15",
            "-w", "\n%{http_code} %{time_total}",
            "https://checkip.amazonaws.com",
        ],
        capture_output=True, text=True, timeout=20,
    )
    elapsed_ms = int((time.time() - t0) * 1000)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"curl exit {proc.returncode}").strip()
        return {"ok": False, "proxy": label, "latency_ms": elapsed_ms,
                "error": err[:240]}
    lines = proc.stdout.strip().splitlines()
    meta = lines[-1].split() if lines else []
    code = meta[0] if meta else ""
    try:
        latency_ms = int(float(meta[1]) * 1000)
    except Exception:
        latency_ms = elapsed_ms
    body = "\n".join(lines[:-1]).strip()
    if code != "200" or not body:
        return {"ok": False, "proxy": label, "latency_ms": latency_ms,
                "error": f"HTTP {code or '?'} {body[:120]}".strip()}
    return {"ok": True, "proxy": label, "exit_ip": body,
            "latency_ms": latency_ms}


@app.post("/api/proxy/test")
async def test_proxy(req: Request,
                     x_admin_token: str = Header(default="")):
    import asyncio
    _auth(x_admin_token)
    body = await req.json()
    raw = str(body.get("url") if "url" in body else _proxy_url()).strip()
    if not raw:
        raise HTTPException(400, "请先填入代理 URL")
    return await asyncio.to_thread(_probe_proxy, _resolve_proxy_url(raw))


@app.get("/api/mailboxes")
def mailboxes(x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    accounts = _accounts()
    out = []
    outlook_store = _outlook_store()
    for addr in sorted(outlook_store):
        rec = accounts.get(addr, {})
        info = outlook_store.get(addr) or {}
        is_url = info.get("protocol") == "url_otp" or bool(info.get("otp_url"))
        out.append({
            "local": addr.split("@")[0],
            "provider": "url" if is_url else "outlook",
            "email": addr,
            "registered": rec.get("status") == "registered",
            "account_id": rec.get("account_id"),
            "has_key": bool(rec.get("gateway_key")),
            "mail_protocol": info.get("protocol") or None,
        })
    return {"mailboxes": out}


@app.post("/api/mail/outlook/import")
async def import_outlook_mailboxes(req: Request,
                                   x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    _sync_outlook_env()
    body = await req.json()
    raw = str(body.get("raw") or "")
    verify = bool(body.get("verify", True))
    if not raw.strip():
        raise HTTPException(400, "raw credentials required")
    res = om.import_credentials(raw)
    results = []
    for addr in res["imported"]:
        item = {"email": addr, "ok": True, "verified": False,
                "protocol": None, "message_count": None, "error": None}
        if verify:
            try:
                info = om.list_messages(addr, limit=5)
                item["verified"] = True
                item["protocol"] = info.get("protocol")
                item["message_count"] = len(info.get("messages", []))
            except Exception as e:
                item["error"] = str(e)[:300]
        results.append(item)
    return {"imported": results, "errors": res["errors"]}


@app.post("/api/mail/outlook/delete")
async def delete_outlook_mailbox(req: Request,
                                 x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    _sync_outlook_env()
    body = await req.json()
    email_addr = str(body.get("email") or "").strip()
    if not email_addr:
        raise HTTPException(400, "email required")
    if not om.delete_account(email_addr):
        raise HTTPException(404, "unknown outlook mailbox")
    return {"ok": True, "email": email_addr}


@app.post("/api/mail/outlook/test")
async def test_outlook_mailbox(req: Request,
                               x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    _sync_outlook_env()
    body = await req.json()
    email_addr = str(body.get("email") or "").strip()
    limit = max(1, min(int(body.get("limit", 10)), 25))
    if not email_addr:
        raise HTTPException(400, "email required")
    if not om.has_account(email_addr):
        raise HTTPException(404, "unknown outlook mailbox")
    try:
        info = om.list_messages(email_addr, limit=limit)
        return {"ok": True, **info}
    except Exception as e:
        raise HTTPException(502, str(e)[:300])


@app.get("/api/mail/outlook/messages")
def outlook_messages(email: str, limit: int = 10,
                     x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    _sync_outlook_env()
    if not om.has_account(email):
        raise HTTPException(404, "unknown outlook mailbox")
    limit = max(1, min(limit, 25))
    try:
        return om.list_messages(email, limit=limit)
    except Exception as e:
        raise HTTPException(502, str(e)[:300])


@app.get("/api/accounts")
def accounts(x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    out = {}
    for key, rec in _accounts().items():
        out[key] = {k: v for k, v in rec.items() if k != "password"}
        out[key]["has_password"] = bool(rec.get("password"))
    return {"accounts": out}


@app.get("/api/accounts/key")
def download_key(email: str, x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    rec = _accounts().get(email)
    rel = (rec or {}).get("gateway_key")
    if not rel:
        raise HTTPException(404, "no exported key for this account")
    path = rel if os.path.isabs(rel) else os.path.join(BASE, rel)
    if not os.path.exists(path):
        raise HTTPException(404, "key file missing")
    return FileResponse(path, media_type="application/json",
                        filename=os.path.basename(path))


@app.post("/api/tasks/register")
async def start_register(req: Request,
                         x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    body = await req.json()
    items = body.get("items") or []
    if not items:
        raise HTTPException(400, "items required: [{local, provider}]")
    for it in items:
        if "local" not in it or "provider" not in it:
            raise HTTPException(400, "each item needs local + provider")
    use_proxy = bool(body.get("use_proxy", True))
    if use_proxy:
        raw = _proxy_url()
        if not raw:
            raise HTTPException(400, "已开启走代理，但尚未保存代理 URL，请先到「设置」保存代理")
        if not _safe_proxy_label(raw):
            raise HTTPException(400, "已保存的代理 URL 格式无效（示例: http://user:pass@host:port）")
    task = _new_task("register", body)
    t = threading.Thread(
        target=_run_register_task,
        args=(task, items, use_proxy, bool(body.get("export_json", False))),
        daemon=True)
    t.start()
    return _task_view(task)


@app.post("/api/tasks/export")
async def start_export(req: Request,
                       x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    body = await req.json()
    email_key = body.get("email")
    if not email_key and body.get("local"):
        email_key = next(
            (k for k, r in _accounts().items()
             if r.get("email", "").split("@")[0] == body["local"]), None)
    if not email_key:
        raise HTTPException(400, "email required")
    if email_key not in _accounts():
        raise HTTPException(404, "unknown account")
    use_proxy = bool(body.get("use_proxy", bool(_proxy_url())))
    if body.get("use_proxy") is True and not _proxy_url():
        raise HTTPException(400, "已开启走代理，但尚未保存代理 URL，请先到「设置」保存代理")
    body["email"] = email_key
    task = _new_task("export", body)
    t = threading.Thread(target=_run_export_task,
                         args=(task, email_key, use_proxy),
                         daemon=True)
    t.start()
    return _task_view(task)


@app.get("/api/tasks")
def list_tasks(x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    with _tasks_lock:
        return {"tasks": [_task_view(t) for t in _tasks.values()]}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, log_tail: int = 200,
             x_admin_token: str = Header(default="")):
    _auth(x_admin_token)
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    return _task_view(task, log_tail=log_tail)
