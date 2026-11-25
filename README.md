# Quick Farm

AWS Quick（Amazon Quick）账号自动注册与本地 OAuth 凭证导出控制台（FastAPI 后端 + Next.js 前端 + Playwright）。

## 这是什么 / 产物

这是一个批量注册、批量养 AWS Quick 账号的“养号农场”（account farm）。你给它一批邮箱（Outlook 邮箱或接码 URL）和可选的代理，它自动把每个邮箱注册成一个 Quick 账号。

跑完之后得到的产物是：

- **一批注册好的 Quick 账号**：每个账号的邮箱和密码记在本地 `accounts.json` 里，控制台总览页能看到谁注册了、谁还没注册。
- **每个账号一份 OAuth 凭证 JSON**：含登录 Quick 所需的凭证（`client_id` / `refresh_token` 等），点「导出 JSON」即可下载，格式可直接导入 quick2api 这类网关当号池用。
- **账号复验**：`login_check.py` 能拿 `accounts.json` 里的账号重新登录一次，确认号还能正常进工作区。

注意它本身不是 API 网关，不提供 Quick 对话服务，只负责把号注册出来、把凭证导出来；号怎么用（进哪个网关、跑什么模型）由网关那边决定。

## 功能特性

- **全自动注册**：Playwright 驱动完成落地页 → 邮箱 → 姓名 → 自动收验证码 → 设密码 → 工作区引导。
- **多方式邮箱导入与接码**：
  - `邮箱----密码----client_id----refresh_token`（或 `邮箱----client_id----refresh_token`）：自动识别 Microsoft Graph（`Mail.Read`）与 IMAP XOAUTH2（`outlook.office365.com` / `outlook.live.com`），支持刷新令牌自动轮换落盘。
  - `邮箱----接码URL`（或 `邮箱----密码----接码URL`）：兼容 JSON 数组、包裹对象、单条验证码 JSON 与纯文本/HTML 响应。
  - 控制台内置「接验证码」收件箱面板，支持直接查看最近邮件与一键复制 6 位验证码。
- **代理管理与校验**：
  - 设置页配置代理 URL（支持 `http://user:pass@host:port`、`http://host:port`、`socks5://...`，留空直连）。
  - 支持一键「测试连通性」（探测出口 IP 与延迟）。
  - 注册页「走代理」开关开启时，启动前自动校验是否已保存有效代理 URL。
- **本地 OAuth 导出**：内置 PKCE OAuth 客户端（`server/oauth_local.py`），在本地后端直接完成登录与授权回调换取凭证 JSON，无需依赖外部网关。

## 目录结构

```text
quick-farm/
├── register.py          # Playwright 注册主脚本
├── outlook_mail.py      # Outlook (Graph / IMAP XOAUTH2) 与 URL 接码核心模块
├── outlook_auth.py      # Outlook 设备码授权 / 凭据导入 CLI
├── export_only.py       # 已有账号命令行导出工具
├── login_check.py       # 账号登录复验工具
├── requirements.txt     # Python 依赖
├── server/
│   ├── app.py           # FastAPI 控制 API
│   ├── config_store.py  # 配置管理（首次启动自动生成 server/config.json）
│   ├── config.example.json
│   ├── export_service.py
│   ├── mail_adapters.py
│   ├── oauth_local.py
│   └── run.sh
└── web/                 # Next.js 控制台
```

## 快速开始

### 1. 启动后端

需要 Python 3.11+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 安装全量 Chromium（推荐完整 Chromium 而非仅 headless shell）
python3 -m playwright install chromium
# Linux 服务器如需补全系统依赖：
# python3 -m playwright install-deps chromium

sh server/run.sh
```

首次启动会自动生成 `server/config.json`（权限 `600`）并随机生成 `admin_token`，打开文件查看令牌用于登录控制台。

### 2. 启动前端

需要 Node.js 20+。

```bash
cd web
npm ci
npm run build
npm run start
```

开发模式可用 `npm run dev`。默认通过 `next.config.ts` 将 `/backend/*` 转发到 `http://127.0.0.1:8790`（可通过环境变量 `BACKEND_URL` 修改）；生产环境也可使用 Nginx 反代 `/` → `127.0.0.1:3000`、`/backend/` → `127.0.0.1:8790/`。

### 3. 浏览器访问

打开 `http://127.0.0.1:3000`，输入 `server/config.json` 中的 `admin_token` 即可进入控制台：

1. 在 **「总览 → 导入邮箱」** 或 **「设置」** 粘贴导入邮箱（支持批量每行一条）。
2. 如需代理，在 **「设置 → 代理与节奏」** 填入代理 URL，点「测试连通性」验证通过后点「保存设置」。
3. 在 **「注册」** 勾选邮箱并开始任务；完成后可在 **「总览」** 点击「导出 JSON」下载凭证。

## 安全提醒

- `accounts.json`、`outlook-tokens.json`、`server/config.json`、`keys/` 均包含敏感凭据，已在 `.gitignore` 中排除，切勿提交到公开仓库。
- 导出的 OAuth refresh token 为一次性轮换令牌，请勿在多个实例中同时刷新同一份凭证。
