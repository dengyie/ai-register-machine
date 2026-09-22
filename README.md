# AI 通用注册机（ai-register-machine）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](pyproject.toml)
[![LINUX DO](https://img.shields.io/badge/Community-LINUX%20DO-2563eb.svg?logo=linux&logoColor=white)](https://linux.do/)

---

## 🌟 特别致谢与社区认可：LINUX DO

> 🐧 **本项目深度认可并致谢 [LINUX DO (https://linux.do)](https://linux.do/) 社区，特此将 LINUX DO 列为首位致敬与致谢对象！**  
> 本项目在多模型 Provider 协议逆向、邮箱收信通道适配、出口 IP 轮换以及生产级工程化落地中的大量思路、灵感与技巧，均汲取自 **LINUX DO** 社区全体佬友们的无私分享与真诚交流。致敬开源精神与纯粹的技术探索！

---

面向大模型 API / Console 账号的现代化自动化注册机与凭据管理系统，集 **多线程并发、独立邮箱收信、有头/无头对抗、智能 IP 池轮换、增量真归因验收、CPA 批量导入与可视化 Web 控制面** 于一体。

---

## 🔥 核心项目特性

- ⚡ **多线程并发注册引擎**：内置统一 `Pipeline` 线程池调度系统，支持灵活自定义并发规模（`--threads`），提供智能软失败容错继续与硬性资源耗尽的 Fail-Fast 熔断机制。
- 📬 **内置邮箱管理系统与 API**：抽象统一 `EmailSource` 接口层，深度集成多渠道邮箱自动分配与邮件/验证码精准轮询（tinyhost API、Hotmail/Outlook OAuth2+REST/IMAP、DuckMail API、CloudMail 等），严格保证一号一箱。
- 🌐 **有头 / 无头双模浏览器驱动**：支持 Playwright 与 DrissionPage 双内核，高效应对 Cloudflare Turnstile 等人机验证；支持浏览器会话复用、崩溃自动清理与调试隔离。
- 🔄 **智能 IP 池与出口隔离轮换**：深度集成 Clash Verge 控制器，支持专用策略组与域名级精准分流（轮换时不干扰整机主代理）；同时支持自建 HTTP/SOCKS5 节点池（`proxy_list` / `nodes.json`）与实时健康度探针。
- 🧪 **自动化批量测试与状态归因**：坚守「本轮真实增量交付」原则，杜绝使用历史账本尾巴充数；具备内置连通性探测（smoke probe）、`CONTRACT_EXIT` 验收契约及私钥凭据 `0600` 强隔离。
- 🚀 **批量导入 CPA（CLIProxyAPI）**：支持从 SSO Cookie 自动进行 OIDC Device Flow 纯协议静默铸造，结合 Chat 模型探针进行 Healthy-Only 门禁筛选，并支持 SSH 远端自动热加载导入。
- 🖥️ **可视化 Web 控制台（Control Plane）**：开箱即用的 FastAPI + Vite/Preact 现代化 Web 控制面，一站式管理配置热更、凭据导入、批次启停监控与实时日志流。

---

## 支持的 Provider 矩阵

| Provider | 命令入口 | 核心协议 / 驱动栈 | 产物契约与交付 |
|----------|----------|-------------------|----------------|
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

### 2. 核心命令速查

```bash
# 统一帮助入口
./register.sh help

# typesafe.ai 注册（支持并发多线程与软失败继续）
./register.sh typesafe 1               # 单次注册测试
./register.sh typesafe 100 8           # 批量 100 个，8 线程并发
./register.sh smoke typesafe           # 控制台连通性探测

# Grok / xAI 注册
./register.sh grok 1 1                 # 注册 1 个号（推荐桌面有头模式）

# Xiaomi MiMo TTS Key 注册
./register.sh mimo

# 通用 register_core 分层编排
./register.sh core list
./register.sh core run -p typesafe -n 1
```

### 3. Web 控制台（Control Plane）

项目内置基于 FastAPI + Vite/Preact 的轻量级 Web 控制面，提供可视化配置、批次启停和实时日志：

```bash
export CONTROL_API_SESSION_SECRET="$(openssl rand -hex 32)"
./scripts/run_control_api.sh
# 浏览器访问 http://127.0.0.1:8787（首次登录默认 admin / admin123）
```

---

## 📊 最新实测（pxed 生产机真机实测）

所有实测严格遵守**「只计算本轮增量」**原则，不统计存量数据：

### typesafe.ai 实测（1000 批次 · 2026-09-22）

- **命令**：`./register.sh typesafe 1000`（8 线程，`--no-fail-fast`，超时 86400s）
- **环境**：pxed 生产机，Clash mixed-port `127.0.0.1:7897`（本批次 `rotate=off`），tinyhost 独立邮箱，收信直连
- **结果**：**976 成功 / 24 失败**，`CONTRACT_EXIT:0`，全部跑完无中途崩溃
- **交付物**：新增 976 个全新 `0600` `typesafe-*.json`，目录权限 `0700`，未向 `accounts.jsonl` 追加明文，未注入 CPA
- **失败归因**（全部为上游偶发软失败，不中断批次）：
  - `wait_magic_link` (17 次)：tinyhost 180s 内未收到邮件（`no_mail` 偶发丢信）
  - `send_magic_link` (5 次)：上游接口 HTTP 500
  - `auth_callback` (2 次)：上游接口 HTTP 401
- **同日验证**：首轮 100 次实测 97 成功 / 3 失败；修复 magic-link 优先级后次轮 100 次实测 98 成功 / 2 失败。

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

## 🐧 社区致谢与参考

- **[LINUX DO (https://linux.do)](https://linux.do/)**（🌟 **首位致谢对象**）：特别感谢 LINUX DO 社区全体佬友的智慧结晶与开源分享，为自动化工程与逆向探索提供了无可替代的技术灵感。
- **技术参考**：感谢社区开源项目 [ThinkerWen/ai-register](https://github.com/ThinkerWen/ai-register)、[Futureppo/typesafe_register](https://github.com/Futureppo/typesafe_register)、[daimon3332/OutlookRegister](https://github.com/daimon3332/OutlookRegister) 提供的架构与协议分析灵感。

---

## 📄 License

本项目基于 [MIT License](LICENSE) 开源。  
Copyright (c) 2026 dengyie
