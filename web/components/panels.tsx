"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type AccountRec,
  type Conn,
  type FarmConfig,
  type Mailbox,
  type OutlookImportResult,
  type OutlookMessagesResp,
  type ProxyTestResult,
  type Task,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import TextureCard from "@/components/ui/texture-card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Check,
  Copy,
  Download,
  KeyRound,
  Mail,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";

function fmtTime(ts: number | null | undefined) {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleTimeString("zh-CN", {
    hour12: false,
  });
}

function StatusBadge({ status }: { status: string }) {
  if (status === "running")
    return <Badge className="bg-amber-500 text-white">运行中</Badge>;
  if (status === "done")
    return <Badge className="bg-emerald-600 text-white">完成</Badge>;
  if (status === "failed") return <Badge variant="destructive">失败</Badge>;
  return <Badge variant="secondary">{status}</Badge>;
}

function protocolLabel(p: string | null | undefined) {
  if (p === "imap_xoauth2") return "IMAP OAuth";
  if (p === "graph") return "Graph";
  if (p === "url_otp") return "URL API";
  return p || "";
}

function providerLabel(p: Mailbox["provider"]) {
  if (p === "outlook") return "Outlook";
  if (p === "url") return "URL接码";
  return p;
}

function isTokenOrUrlProvider(p: Mailbox["provider"]) {
  return p === "outlook" || p === "url";
}

function OutlookImportBox({
  conn,
  onImported,
}: {
  conn: Conn;
  onImported?: () => void;
}) {
  const [raw, setRaw] = useState("");
  const [verify, setVerify] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [res, setRes] = useState<OutlookImportResult | null>(null);

  const submit = async () => {
    if (!raw.trim()) {
      setErr("先粘贴邮箱凭据（邮箱----密码----client_id----refresh_token 或 邮箱----接码URL）");
      return;
    }
    setBusy(true);
    setErr("");
    setRes(null);
    try {
      const r = await api.importOutlook(conn, raw, verify);
      setRes(r);
      if (r.imported.length > 0) {
        if (r.errors.length === 0) setRaw("");
        onImported?.();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded-2xl border bg-muted/30 p-4">
      <div className="space-y-1.5">
        <Label>导入邮箱（支持单条或每行一条批量）</Label>
        <Textarea
          rows={3}
          className="font-mono text-xs"
          placeholder={"邮箱----密码----client_id----refresh_token\n或 邮箱----https://接码URL"}
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2">
          <Switch checked={verify} onCheckedChange={setVerify} />
          <span className="text-sm">导入后立即测试接码连接</span>
        </label>
        <Button
          size="sm"
          className="rounded-full"
          disabled={busy || !raw.trim()}
          onClick={submit}
        >
          <Plus className="mr-1 h-4 w-4" />
          {busy ? "导入中..." : "导入邮箱"}
        </Button>
      </div>
      {err && <p className="text-sm text-destructive">{err}</p>}
      {res && (
        <div className="space-y-1.5 text-sm">
          {res.imported.map((it) => (
            <div
              key={it.email}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl border bg-background px-3 py-2"
            >
              <span className="font-medium">{it.email}</span>
              {it.verified ? (
                <Badge className="bg-emerald-600 text-white">
                  已导入 · 连接正常
                  {it.protocol ? ` (${protocolLabel(it.protocol)})` : ""}
                </Badge>
              ) : it.error ? (
                <span className="text-xs text-amber-600">
                  已保存（测连接失败：{it.error}）
                </span>
              ) : (
                <Badge variant="secondary">已导入</Badge>
              )}
            </div>
          ))}
          {res.errors.map((er) => (
            <p key={er.line} className="text-xs text-destructive">
              第 {er.line} 行：{er.error}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- overview */

export function OverviewPanel({
  conn,
  goTasks,
}: {
  conn: Conn;
  goTasks: () => void;
}) {
  const [boxes, setBoxes] = useState<Mailbox[]>([]);
  const [accounts, setAccounts] = useState<Record<string, AccountRec>>({});
  const [tasks, setTasks] = useState<Task[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [inbox, setInbox] = useState<OutlookMessagesResp | null>(null);
  const [inboxBusy, setInboxBusy] = useState("");
  const [copiedId, setCopiedId] = useState("");

  const load = useCallback(async () => {
    try {
      const [m, a, t] = await Promise.all([
        api.mailboxes(conn),
        api.accounts(conn),
        api.tasks(conn),
      ]);
      setBoxes(m.mailboxes);
      setAccounts(a.accounts);
      setTasks(t.tasks);
      setErr("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [conn]);

  useEffect(() => {
    load();
  }, [load]);

  const doExport = async (email: string) => {
    setBusy(email);
    try {
      await api.startExport(conn, email);
      goTasks();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  };

  const loadInbox = async (email: string) => {
    setInboxBusy(email);
    setErr("");
    try {
      const r = await api.outlookMessages(conn, email, 10);
      setInbox(r);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setInboxBusy("");
    }
  };

  const doDeleteOutlook = async (email: string) => {
    setBusy(`del:${email}`);
    setErr("");
    try {
      await api.deleteOutlook(conn, email);
      if (inbox?.email === email) setInbox(null);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  };

  const copyCode = async (id: string, code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopiedId(id);
      setTimeout(() => setCopiedId(""), 1500);
    } catch {}
  };

  const registered = boxes.filter((b) => b.registered).length;
  const withKey = boxes.filter((b) => b.has_key).length;
  const running = tasks.filter((t) => t.status === "running").length;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ["邮箱总数", boxes.length],
          ["已注册", registered],
          ["已有 JSON", withKey],
          ["运行中任务", running],
        ].map(([label, v]) => (
          <TextureCard key={label as string}>
            <div className="p-5">
              <div className="text-3xl font-medium tracking-tight text-foreground">
                {v}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                {label}
              </div>
            </div>
          </TextureCard>
        ))}
      </div>

      <Card className="rounded-3xl shadow-sm">
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle>邮箱与账号</CardTitle>
            <CardDescription>
              支持导入 Outlook/Hotmail 或 邮箱----接码URL 自动收验证码，导出在本机后端本地完成。
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant={showImport ? "secondary" : "default"}
              size="sm"
              className="rounded-full"
              onClick={() => setShowImport((v) => !v)}
            >
              <Plus className="mr-1 h-4 w-4" /> 导入邮箱
            </Button>
            <Button variant="outline" size="sm" onClick={load}>
              <RefreshCw className="mr-1 h-4 w-4" /> 刷新
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {showImport && <OutlookImportBox conn={conn} onImported={load} />}
          {err && <p className="text-sm text-destructive">{err}</p>}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>邮箱</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>账号 ID</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {boxes.map((b) => (
                <TableRow key={b.provider + b.email}>
                  <TableCell className="font-medium">{b.email}</TableCell>
                  <TableCell className="space-x-1">
                    <Badge variant="secondary">
                      {providerLabel(b.provider)}
                    </Badge>
                    {b.mail_protocol && (
                      <Badge variant="outline" className="text-xs">
                        {protocolLabel(b.mail_protocol)}
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    {b.registered ? (
                      <span className="text-emerald-600">
                        已注册
                        {accounts[b.email]?.name
                          ? ` · ${accounts[b.email]?.name}`
                          : ""}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">未注册</span>
                    )}
                    {b.has_key && (
                      <Badge className="ml-2 bg-neutral-800 text-white">
                        JSON
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {b.account_id || "-"}
                  </TableCell>
                  <TableCell className="space-x-2 text-right">
                    {isTokenOrUrlProvider(b.provider) && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={inboxBusy === b.email}
                        onClick={() => loadInbox(b.email)}
                      >
                        <Mail className="mr-1 h-4 w-4" />
                        {inboxBusy === b.email ? "收信中" : "接验证码"}
                      </Button>
                    )}
                    {b.registered && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy === b.email}
                        onClick={() => doExport(b.email)}
                      >
                        <KeyRound className="mr-1 h-4 w-4" />
                        {busy === b.email ? "启动中" : "导出 JSON"}
                      </Button>
                    )}
                    {b.has_key && (
                      <Button
                        size="sm"
                        onClick={() => api.downloadKey(conn, b.email)}
                      >
                        <Download className="mr-1 h-4 w-4" /> 下载
                      </Button>
                    )}
                    {isTokenOrUrlProvider(b.provider) && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy === `del:${b.email}`}
                        onClick={() => doDeleteOutlook(b.email)}
                        title="移除此邮箱"
                      >
                        <Trash2 className="h-4 w-4 text-muted-foreground" />
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {boxes.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={5}
                    className="text-center text-muted-foreground"
                  >
                    没有邮箱，点右上角「导入邮箱」或去设置里添加。
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {inbox && (
        <Card className="rounded-3xl shadow-sm">
          <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
            <div>
              <CardTitle className="text-base">
                收件箱 · {inbox.email}
              </CardTitle>
              <CardDescription>
                通道：{protocolLabel(inbox.protocol)} · 最新 {inbox.messages.length} 封邮件
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={inboxBusy === inbox.email}
                onClick={() => loadInbox(inbox.email)}
              >
                <RefreshCw className="mr-1 h-4 w-4" />
                {inboxBusy === inbox.email ? "刷新中" : "刷新邮件"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setInbox(null)}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {inbox.messages.length === 0 && (
              <p className="text-sm text-muted-foreground">收件箱暂无邮件。</p>
            )}
            {inbox.messages.map((m) => (
              <div
                key={m.id}
                className="flex flex-col gap-1.5 rounded-2xl border p-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0 space-y-0.5">
                  <div className="truncate font-medium">
                    {m.subject || "（无主题）"}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {m.from} · {m.received_at}
                  </div>
                  {m.preview && (
                    <div className="line-clamp-2 text-xs text-muted-foreground">
                      {m.preview}
                    </div>
                  )}
                </div>
                {m.code && (
                  <Button
                    size="sm"
                    variant="secondary"
                    className="shrink-0 rounded-full font-mono"
                    onClick={() => copyCode(m.id, m.code!)}
                  >
                    {copiedId === m.id ? (
                      <>
                        <Check className="mr-1 h-3.5 w-3.5 text-emerald-600" />
                        已复制 {m.code}
                      </>
                    ) : (
                      <>
                        <Copy className="mr-1 h-3.5 w-3.5" />
                        验证码 {m.code}
                      </>
                    )}
                  </Button>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- register */

export function RegisterPanel({
  conn,
  goTasks,
}: {
  conn: Conn;
  goTasks: () => void;
}) {
  const [boxes, setBoxes] = useState<Mailbox[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [useProxy, setUseProxy] = useState(true);
  const [exportJson, setExportJson] = useState(false);
  const [err, setErr] = useState("");
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    api
      .mailboxes(conn)
      .then((m) => {
        setBoxes(m.mailboxes);
        setPicked(
          new Set(
            m.mailboxes
              .filter((b) => !b.registered)
              .map((b) => b.provider + ":" + b.email),
          ),
        );
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [conn]);

  const toggle = (key: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const start = async () => {
    const items = boxes
      .filter((b) => picked.has(b.provider + ":" + b.email))
      .map((b) => ({ local: b.local, provider: b.provider, email: b.email }));
    if (!items.length) {
      setErr("先勾选至少一个邮箱");
      return;
    }
    setErr("");
    setStarting(true);
    try {
      if (useProxy) {
        const cfg = await api.getConfig(conn);
        if (!cfg.proxy?.url?.trim()) {
          setErr("已开启走代理，但尚未保存代理 URL，请先到「设置」填入并保存代理");
          return;
        }
      }
      await api.startRegister(conn, items, useProxy, exportJson);
      goTasks();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  return (
    <Card className="rounded-3xl shadow-sm">
      <CardHeader>
        <CardTitle>注册新账号</CardTitle>
        <CardDescription>
          勾选邮箱后开始。多个邮箱会按后端配置的间隔逐个跑（默认 5
          分钟一个），按设置里的代理 URL 出口运行。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {err && <p className="text-sm text-destructive">{err}</p>}
        <div className="space-y-2">
          {boxes.map((b) => {
            const key = b.provider + ":" + b.email;
            return (
              <label
                key={key}
                className="flex cursor-pointer items-center gap-3 rounded-2xl border px-4 py-3"
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-neutral-900"
                  checked={picked.has(key)}
                  onChange={() => toggle(key)}
                />
                <span className="font-medium">{b.email}</span>
                <Badge variant="secondary">
                  {providerLabel(b.provider)}
                </Badge>
                {b.registered && (
                  <span className="text-sm text-emerald-600">已注册</span>
                )}
              </label>
            );
          })}
          {boxes.length === 0 && (
            <p className="text-sm text-muted-foreground">
              没有邮箱，去设置里加。
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-6">
          <label className="flex items-center gap-2">
            <Switch checked={useProxy} onCheckedChange={setUseProxy} />
            <span className="text-sm">走代理</span>
          </label>
          <label className="flex items-center gap-2">
            <Switch checked={exportJson} onCheckedChange={setExportJson} />
            <span className="text-sm">注册完直接导出 JSON</span>
          </label>
        </div>
        <Button
          className="rounded-full"
          onClick={start}
          disabled={starting}
        >
          <Play className="mr-1 h-4 w-4" />
          {starting ? "启动中" : `开始注册（${picked.size} 个）`}
        </Button>
      </CardContent>
    </Card>
  );
}

/* ---------------------------------------------------------------- tasks */

export function TasksPanel({ conn }: { conn: Conn }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [detail, setDetail] = useState<Task | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  const loadList = useCallback(async () => {
    try {
      const t = await api.tasks(conn);
      const sorted = [...t.tasks].sort(
        (a, b) => b.started_at - a.started_at,
      );
      setTasks(sorted);
      if (!selected && sorted.length) setSelected(sorted[0].id);
    } catch {}
  }, [conn, selected]);

  useEffect(() => {
    loadList();
    const iv = setInterval(loadList, 2500);
    return () => clearInterval(iv);
  }, [loadList]);

  useEffect(() => {
    if (!selected) return;
    let alive = true;
    const fetchDetail = async () => {
      try {
        const d = await api.task(conn, selected);
        if (alive) setDetail(d);
      } catch {}
    };
    fetchDetail();
    const iv = setInterval(fetchDetail, 2500);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [conn, selected]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [detail?.log]);

  const taskTitle = (t: Task) => {
    const p = t.params as Record<string, unknown>;
    if (t.kind === "export") return `导出 · ${p.email ?? p.local ?? ""}`;
    if (t.kind === "register") {
      const items = (p.items as { local: string; email?: string }[]) || [];
      return `注册 · ${items.map((i) => i.email || i.local).join(", ")}`;
    }
    return t.kind;
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <Card className="rounded-3xl shadow-sm">
        <CardHeader>
          <CardTitle>任务</CardTitle>
          <CardDescription>点一条看日志。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {tasks.map((t) => (
            <button
              key={t.id}
              onClick={() => setSelected(t.id)}
              className={`w-full rounded-2xl border px-4 py-3 text-left transition-shadow hover:shadow-md ${
                selected === t.id ? "border-neutral-900" : ""
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{taskTitle(t)}</span>
                <StatusBadge status={t.status} />
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {fmtTime(t.started_at)}
                {t.finished_at ? ` → ${fmtTime(t.finished_at)}` : ""}
              </div>
            </button>
          ))}
          {tasks.length === 0 && (
            <p className="text-sm text-muted-foreground">还没有任务。</p>
          )}
        </CardContent>
      </Card>

      <Card className="rounded-3xl shadow-sm">
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle>日志</CardTitle>
            <CardDescription>
              {detail ? taskTitle(detail) : "未选择任务"}
            </CardDescription>
          </div>
          {detail?.kind === "export" && detail.status === "done" && (
            <Button
              size="sm"
              className="rounded-full"
              onClick={() =>
                api.downloadKey(
                  conn,
                  String(
                    (detail.params as { email?: string; local?: string })
                      .email ?? "",
                  ),
                )
              }
            >
              <Download className="mr-1 h-4 w-4" /> 下载 JSON
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {detail?.error && (
            <p className="mb-3 rounded-xl bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {detail.error}
            </p>
          )}
          <pre
            ref={logRef}
            className="h-[420px] overflow-auto rounded-2xl bg-neutral-950 p-4 font-mono text-xs leading-relaxed text-neutral-100"
          >
            {(detail?.log || []).join("\n") || "（暂无日志）"}
          </pre>
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------- settings */

export function SettingsPanel({ conn }: { conn: Conn }) {
  const [cfg, setCfg] = useState<FarmConfig | null>(null);
  const [msg, setMsg] = useState("");
  const [testingProxy, setTestingProxy] = useState(false);
  const [proxyTest, setProxyTest] = useState<ProxyTestResult | null>(null);

  useEffect(() => {
    api
      .getConfig(conn)
      .then((c) => setCfg(c))
      .catch((e) => setMsg(e instanceof Error ? e.message : String(e)));
  }, [conn]);

  if (!cfg) return <p className="text-sm text-muted-foreground">{msg || "加载中"}</p>;

  const set = <K extends keyof FarmConfig>(
    section: K,
    patch: Partial<NonNullable<FarmConfig[K]>>,
  ) =>
    setCfg((prev) =>
      prev
        ? {
            ...prev,
            [section]: {
              ...((prev[section] as object | undefined) ?? {}),
              ...patch,
            },
          }
        : prev,
    );

  const runProxyTest = async () => {
    const raw = (cfg.proxy.url ?? "").trim();
    if (!raw) {
      setProxyTest({ ok: false, error: "请先填入代理 URL" });
      return;
    }
    setTestingProxy(true);
    setProxyTest(null);
    try {
      const r = await api.testProxy(conn, raw);
      setProxyTest(r);
    } catch (e) {
      setProxyTest({
        ok: false,
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setTestingProxy(false);
    }
  };

  const save = async () => {
    try {
      const saved = await api.putConfig(conn, {
        ...(cfg.outlook ? { outlook: cfg.outlook } : {}),
        proxy: cfg.proxy,
        register: cfg.register,
      });
      setCfg(saved);
      setMsg("已保存");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const field = (
    label: string,
    value: string,
    onChange: (v: string) => void,
    type = "text",
  ) => (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <Input type={type} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );

  return (
    <div className="space-y-4">
      <Card className="rounded-3xl shadow-sm">
        <CardHeader>
          <CardTitle>Outlook / URL 接码邮箱（导入）</CardTitle>
          <CardDescription>
            支持 邮箱----密码----client_id----refresh_token 与 邮箱----接码URL，导入后即可自动接验证码。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {field(
            "令牌存储文件",
            cfg.outlook?.token_file ?? "../outlook-tokens.json",
            (v) => set("outlook", { token_file: v }),
          )}
          <OutlookImportBox conn={conn} />
        </CardContent>
      </Card>

      <Card className="rounded-3xl shadow-sm">
        <CardHeader>
          <CardTitle>代理与节奏</CardTitle>
          <CardDescription>
            填入代理 URL 后可先测试连通性，再点击保存生效；支持 {"{rand}"} 占位符自动为每个账号生成独立随机会话，留空则直连。
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>代理 URL</Label>
            <div className="flex gap-2">
              <Input
                type="text"
                placeholder="http://user:pass@host:port 或 http://host:port（留空直连）"
                value={cfg.proxy.url ?? ""}
                onChange={(e) => {
                  set("proxy", { url: e.target.value });
                  setProxyTest(null);
                }}
              />
              <Button
                type="button"
                variant="outline"
                disabled={testingProxy || !(cfg.proxy.url ?? "").trim()}
                onClick={runProxyTest}
              >
                {testingProxy ? "测试中..." : "测试连通性"}
              </Button>
            </div>
            {proxyTest && (
              <p
                className={`text-xs ${
                  proxyTest.ok ? "text-emerald-600" : "text-destructive"
                }`}
              >
                {proxyTest.ok
                  ? `连通正常 · 出口 IP: ${proxyTest.exit_ip}${
                      proxyTest.latency_ms != null
                        ? ` · 延迟 ${proxyTest.latency_ms} ms`
                        : ""
                    }`
                  : `连通失败：${proxyTest.error || "未知错误"}`}
              </p>
            )}
          </div>
          {field(
            "批量注册间隔（秒）",
            String(cfg.register.batch_interval_s),
            (v) => set("register", { batch_interval_s: Number(v) || 300 }),
            "number",
          )}
        </CardContent>
      </Card>

      <div className="flex items-center gap-3">
        <Button className="rounded-full" onClick={save}>
          保存设置
        </Button>
        {msg && <span className="text-sm text-muted-foreground">{msg}</span>}
      </div>
    </div>
  );
}
