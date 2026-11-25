// quick-farm backend API client.
// Connection (backend base URL + admin token) is kept in localStorage;
// the token lives in the backend's server/config.json (admin_token).

export type Conn = { base: string; token: string };

const KEY = "quickfarm-conn";

export function loadConn(): Conn | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Conn) : null;
  } catch {
    return null;
  }
}

export function saveConn(c: Conn) {
  localStorage.setItem(KEY, JSON.stringify(c));
}

export function clearConn() {
  localStorage.removeItem(KEY);
}

export type Mailbox = {
  local: string;
  provider: "outlook" | "url";
  email: string;
  registered: boolean;
  account_id: string | null;
  has_key: boolean;
  mail_protocol?: string | null;
};

export type OutlookImportItem = {
  email: string;
  ok: boolean;
  verified: boolean;
  protocol: string | null;
  message_count: number | null;
  error: string | null;
};

export type OutlookImportResult = {
  imported: OutlookImportItem[];
  errors: { line: number; error: string }[];
};

export type OutlookMessage = {
  id: string;
  from: string;
  subject: string;
  received_at: string;
  preview: string;
  code: string | null;
};

export type OutlookMessagesResp = {
  ok?: boolean;
  email: string;
  protocol: string;
  scope?: string;
  messages: OutlookMessage[];
};

export type AccountRec = {
  email: string;
  name?: string;
  status?: string;
  account_id?: string;
  gateway_key?: string;
  has_password?: boolean;
  ts?: string;
};

export type Task = {
  id: string;
  kind: string;
  params: Record<string, unknown>;
  status: "running" | "done" | "failed";
  started_at: number;
  finished_at: number | null;
  result: Record<string, unknown> | null;
  error: string | null;
  log?: string[];
};

export type FarmConfig = {
  admin_token: string;
  outlook?: { token_file: string };
  proxy: {
    url: string;
  };
  register: { batch_interval_s: number };
};

export type ProxyTestResult = {
  ok: boolean;
  proxy?: string;
  exit_ip?: string;
  latency_ms?: number;
  error?: string;
};

async function req<T>(
  conn: Conn,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(conn.base.replace(/\/$/, "") + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": conn.token,
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let msg = `${res.status}`;
    try {
      const body = await res.json();
      msg = body.detail || body.message || msg;
    } catch {}
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

export const api = {
  health: (base: string) =>
    fetch(base.replace(/\/$/, "") + "/api/health").then((r) => {
      if (!r.ok) throw new Error(String(r.status));
      return r.json() as Promise<{ ok: boolean }>;
    }),
  getConfig: (c: Conn) => req<FarmConfig>(c, "/api/config"),
  putConfig: (c: Conn, patch: Partial<FarmConfig>) =>
    req<FarmConfig>(c, "/api/config", {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  testProxy: (c: Conn, url: string) =>
    req<ProxyTestResult>(c, "/api/proxy/test", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),
  mailboxes: (c: Conn) =>
    req<{ mailboxes: Mailbox[] }>(c, "/api/mailboxes"),
  importOutlook: (c: Conn, raw: string, verify = true) =>
    req<OutlookImportResult>(c, "/api/mail/outlook/import", {
      method: "POST",
      body: JSON.stringify({ raw, verify }),
    }),
  deleteOutlook: (c: Conn, email: string) =>
    req<{ ok: boolean; email: string }>(c, "/api/mail/outlook/delete", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  testOutlook: (c: Conn, email: string, limit = 10) =>
    req<OutlookMessagesResp>(c, "/api/mail/outlook/test", {
      method: "POST",
      body: JSON.stringify({ email, limit }),
    }),
  outlookMessages: (c: Conn, email: string, limit = 10) =>
    req<OutlookMessagesResp>(
      c,
      `/api/mail/outlook/messages?email=${encodeURIComponent(email)}&limit=${limit}`,
    ),
  accounts: (c: Conn) =>
    req<{ accounts: Record<string, AccountRec> }>(c, "/api/accounts"),
  startRegister: (
    c: Conn,
    items: { local: string; provider: string; email?: string }[],
    useProxy: boolean,
    exportJson: boolean,
  ) =>
    req<Task>(c, "/api/tasks/register", {
      method: "POST",
      body: JSON.stringify({
        items,
        use_proxy: useProxy,
        export_json: exportJson,
      }),
    }),
  startExport: (c: Conn, email: string) =>
    req<Task>(c, "/api/tasks/export", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  tasks: (c: Conn) => req<{ tasks: Task[] }>(c, "/api/tasks"),
  task: (c: Conn, id: string, logTail = 300) =>
    req<Task>(c, `/api/tasks/${id}?log_tail=${logTail}`),
  downloadKey: async (c: Conn, email: string) => {
    const res = await fetch(
      `${c.base.replace(/\/$/, "")}/api/accounts/key?email=${encodeURIComponent(email)}`,
      { headers: { "X-Admin-Token": c.token } },
    );
    if (!res.ok) throw new Error(String(res.status));
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${email.replace("@", "_at_")}-gateway-key.json`;
    a.click();
    URL.revokeObjectURL(url);
  },
};
