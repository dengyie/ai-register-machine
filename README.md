# AI 通用注册机（ai-register-machine）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](pyproject.toml)
[![LINUX DO](https://img.shields.io/badge/Community-LINUX%20DO-2563eb.svg?logo=linux&logoColor=white)](https://linux.do/)

---

## 🌟 特别致谢与社区认可：LINUX DO

> 🐧 **本项目深度认可并致谢 [LINUX DO (https://linux.do)](https://linux.do/) 社区，特此将 LINUX DO 列为首位致敬与致谢对象！**  
> 本项目在多模型 Provider 协议逆向、邮箱收信通道适配、出口 IP 轮换以及生产级工程化落地中的大量思路、灵感与技巧，均汲取自 **LINUX DO** 社区全体佬友们的无私分享与真诚交流。致敬开源精神与纯粹的技术探索！

---

在常见「多模型注册脚本合集」之上，做成 **可维护、可验收、可 Web 操作** 的工程化 monorepo：分层编排 + 多 Provider 生产链路 + **Web Control Plane**（配置 / 导入 / 批次启停与日志监控）。

**对标开源常规脚本合集，我们的核心优势：**
- **本轮真归因**：成功判定基于本轮实际产出（增量文件、时间戳与 offset 校验），杜绝拿历史账本尾巴或 exit 0 充数。
- **Fail-Fast 熔断**：资源耗尽（如无可用别名/网络硬封）立即停批，避免无意义空转与封禁扩散。
- **严格凭证与安全隔离**：产物 0600 权限、目录 0700、内置敏感数据脱敏与检测工具（`doctor_secrets.sh`），不上传任何私钥凭证。
- **统一入口与 Web 控制面**：`./register.sh` 统一 CLI + FastAPI/Preact 现代化 Web UI，开箱即用。

---

## 支持的 Provider

| Provider | 命令入口 | 核心协议 / 栈 | 产物契约与交付 |
|----------|----------|---------------|----------------|
| **typesafe.ai / jev** | `./register.sh typesafe [count] [threads]` | Python + requests + Stytch Magic-Link | `typesafe-*.json` (0600 API Key) |
| **Grok / xAI** | `./register.sh grok [count] [threads]` | Python + DrissionPage + OIDC Device Flow | `accounts_cli.txt` (SSO) + `cpa_auths/` (OIDC) |
| **Xiaomi MiMo** | `./register.sh mimo` | Node + Playwright | `mimo-*.json` (`sk-` API Key，OpenAI 兼容) |
| **Outlook / Hotmail** | `./register.sh outlook [count]` | Python + Playwright + slidex | `outlook_auths/` (四段凭证与 OAuth Token) |
| **通用分层编排** | `./register.sh core run -p <name>` | `register_core/` 通用引擎 | Pipeline 驱动，支持自定义邮箱与验证器 |

---

## ⚡ 快速开始（最短路径上手）

### 1. 环境准备与配置

需要 **Python 3.13**，推荐使用 [uv](https://docs.astral.sh/uv/)。运行快速脚本将基于 `config.simple.example.json` 自动初始化 `config.json`（支持 `duckmail` 或 Hotmail 模式）并检查运行环境：

```bash
git clone https://github.com/dengyie/ai-register-machine.git
cd ai-register-machine
bash scripts/setup_simple.sh
```

### 2. 核心命令

```bash
# 统一帮助入口
./register.sh help

# typesafe.ai 注册（支持并发与软失败继续）
./register.sh typesafe 1               # 单次注册
./register.sh typesafe 100 8           # 批量 100 个，8 线程并发
./register.sh smoke typesafe           # 控制台连通性探测

# Grok / xAI 注册
./register.sh grok 1 1                 # 注册 1 个号（推荐桌面有头模式）

# Xiaomi MiMo TTS Key 注册
./register.sh mimo

# 通用 register_core
./register.sh core list
./register.sh core run -p typesafe -n 1
```

### 3. Web 控制台（Control Plane）

项目内置基于 FastAPI + Vite/Preact 的 Web 控制面，支持可视化配置修改、凭证导入、批次启停和实时日志观察：

```bash
export CONTROL_API_SESSION_SECRET="$(openssl rand -hex 32)"
./scripts/run_control_api.sh
# 浏览器访问 http://127.0.0.1:8787（首次登录默认 admin / admin123）
```

---

## 📊 最新实测（pxed 生产机真机测试）

所有实测严格遵守**「只计算本轮增量」**原则，不统计存量数据：

### typesafe.ai 实测（1000 批次 · 2026-09-22）

- **命令**：`./register.sh typesafe 1000`（8 线程，`--no-fail-fast`，超时 86400s）
- **环境**：pxed 服务器，Clash mixed-port `127.0.0.1:7897`（本批次 `rotate=off`），tinyhost 独立邮箱，收信直连
- **结果**：**976 成功 / 24 失败**，`CONTRACT_EXIT:0`，全部跑完无中途崩溃
- **交付物**：新增 976 个全新 `0600` `typesafe-*.json`，目录权限 `0700`，未向 `accounts.jsonl` 追加明文，未注入 CPA
- **失败归因**（全部为上游偶发软失败，不中断批次）：
  - `wait_magic_link` (17 次)：tinyhost 180s 内未收到邮件（`no_mail` 偶发丢信）
  - `send_magic_link` (5 次)：上游接口 HTTP 500
  - `auth_callback` (2 次)：上游接口 HTTP 401
- **同日验证**：首轮 100 次实测 97 成功 / 3 失败；修复 magic-link 优先级后次轮 100 次实测 98 成功 / 2 失败。

---

## 🏗️ 架构分层

依据生产级设计，解耦各个阶段：

```text
               ┌───────────────────────────────┐
               │    Unified CLI / Web UI       │
               │   ./register.sh / Control API │
               └──────────────┬────────────────┘
                              │
               ┌──────────────▼────────────────┐
               │     register_core.pipeline    │
               │  (并发调度 / Fail-Fast / 重试) │
               └──────┬───────┬───────┬────────┘
                      │       │       │
       ┌──────────────┘       │       └──────────────┐
       ▼                      ▼                      ▼
┌──────────────┐      ┌──────────────┐       ┌──────────────┐
│ EmailSource  │      │   Provider   │       │ Verify / Sink│
│ (tinyhost /  │      │  (typesafe / │       │ (API Key校验/│
│  hotmail /   │ ───► │  grok / mimo/│ ────► │ 0600 隔离落盘│
│  duckmail)   │      │  outlook)    │       │ 绝不入账本)  │
└──────────────┘      └──────────────┘       └──────────────┘
```

1. **EmailSource（`register_core/email`）**：支持独立邮箱分配与收码（tinyhost、hotmail REST/IMAP、duckmail 等），禁止别名滥用。
2. **Provider 适配层（`register_core/providers`）**：协议逆向与会话流，严格输出结构化 `RegisterResult`。
3. **安全落盘与验证（`register_core/sink` & `verify`）**：产物按产品独立归档，私钥字段严格脱敏，不污染公开账本。
4. **出口策略（`proxy_rotate`）**：支持 Clash 域名隔离组轮换、自建 URL 代理池轮换及直连。

---

## 🛡️ 安全、合规与常见卡点

- **合规声明**：Education / personal automation only; not a free-quota farm. 请勿用于违反第三方服务条款的滥用行为。本项目不提供任何破除付费限制或生成未获授权配额的功能。
- **Grok 权限感知**：若遇到 `entitlement_denied`（或 chat 403），说明服务端未授予 free Build 权限，属于正常策略，**不要盲目 remint 空转**。
- **密钥零追踪**：切勿提交 `config.json`、`mail_credentials.txt` 或 `cpa_auths/*.json`。运行 `bash scripts/doctor_secrets.sh` 随时检查本地密钥安全；所有私钥产物均严格设置 `0600` 文件权限，目录设置 `0700`。

### 常见卡点速查

| 现象 | 处理建议 |
|------|----------|
| doctor: proxy port closed | 检查本地代理是否启动，确认 `config.json` 中代理端口 |
| doctor: duckmail / placeholder | 填写真实 DuckMail Key 或有效邮箱凭证，替换模板占位符 |
| Turnstile 验证卡住 | 推荐使用桌面有头模式（`--no-headless`）及干净代理 IP |
| `entitlement_denied` | 该账号未获 xAI free Build 资格，无需重复尝试 |

---

## 🐧 社区与致谢

- **[LINUX DO (linux.do)](https://linux.do/)**（🌟 **首位致谢对象**）：感谢 LINUX DO 社区全体佬友的智慧结晶与开源分享，为自动化工程与逆向探索提供了无可替代的灵感源泉。
- **[ThinkerWen/ai-register](https://github.com/ThinkerWen/ai-register)**：启发了多模型注册架构思考。
- **[Futureppo/typesafe_register](https://github.com/Futureppo/typesafe_register)**：typesafe 协议分析参考。
- **[daimon3332/OutlookRegister](https://github.com/daimon3332/OutlookRegister)**：Outlook 注册协议参考。

---

## 📄 License

本项目基于 [MIT License](LICENSE) 开源。  
Copyright (c) 2026 dengyie
