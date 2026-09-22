# 🚀 AI 通用注册机（ai-register-machine）

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![LINUX DO](https://img.shields.io/badge/Community-LINUX%20DO-2563eb.svg?logo=linux&logoColor=white)](https://linux.do/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

---

## 🌟 特别致谢与社区认可：LINUX DO

> 🐧 **特别鸣谢与致敬：[LINUX DO (https://linux.do)](https://linux.do/) 社区**  
> 本项目深度认可并真诚致谢 **LINUX DO** 社区，特此将 **LINUX DO** 奉为本项目首要致敬与致谢对象！  
> 
> 在多大模型 Provider 协议逆向、多渠道邮箱精准收信、动态出口 IP 池分流与工程化容错落地过程中，项目中凝聚的大量关键思路、对抗技巧与优化灵感，皆源自 **LINUX DO** 社区各位佬友无私、高质量的经验分享与深度切磋。致敬自由、纯粹的技术探索与开源极客精神！🤝

---

面向大模型 API 与开发者账号打造的**现代化、工程级自动化注册机与凭据生命周期管理平台**。  
告别脆弱零散的单点脚本，原生集成了 **⚡ 多线程高并发调度 · 📬 独立邮箱收发中枢 · 🎭 有头/无头双模抗风控 · 🧭 智能 IP 出口隔离 · 🧪 严格增量真归因 · 🚀 CPA 批量无缝注入 · 🎛️ 现代化 Web 控制台**。

---

## 💎 核心项目特性

### ⚡ 1. 多线程高并发注册引擎
* **弹性线程池调度**：基于统一 `Pipeline` 线程池架构，支持根据出口代理与系统负载灵活调控并发规模（`--threads`，内置过载保护与动态排队）。
* **智能分级容错**：针对上游丢信或偶发网络抖动实施**软失败平滑跳过**；一旦触发账号耗尽或硬性风控拦截，立即激活 **Fail-Fast 熔断机制**，严禁无效空转。

### 📬 2. 内置邮箱管理系统与 API 集成
* **标准化收信中枢**：抽象通用 `EmailSource` 接口层，深度集成主流临时邮箱与自建邮箱通道（tinyhost API、Hotmail/Outlook OAuth2+REST/IMAP、DuckMail API、CloudMail 等）。
* **严格单号单箱**：杜绝易被批量封禁的别名农场（Alias Farm），严守「一号一独立邮箱」规范，精准解析轮询 OTP 验证码与 Magic-Link 登录认证链接。

### 🎭 3. 有头 / 无头双模智能浏览器驱动
* **双渲染内核协同**：无缝支持 Playwright 与 DrissionPage 双驱动栈，专为穿透 Cloudflare Turnstile 等复杂人机风控场景深度优化。
* **高鲁棒性进程守护**：提供轻量化会话上下文复用、多标签隔离及孤儿/僵尸进程自动清理回收，保障 7×24 小时长时间自动化作业不泄露内存。

### 🧭 4. 智能 IP 池与出口精准隔离轮换
* **域名级外部精准分流**：深度联动 Clash / Mihomo 外部控制器，自动注入专用策略组与 `DOMAIN-SUFFIX` 规则，**仅将目标注册流量定向走代理轮换，零侵入、不干扰全局系统网络及主代理链路**。
* **弹性多源代理池**：原生支持自建 HTTP/SOCKS5 代理列表（`proxy_list` / `nodes.json`），内置连接存活预检、RTT 延迟评分与失效自动熔断降级。

### 🧪 5. 自动化批量测试与状态真归因
* **坚持增量真归因**：严格遵守「只对本轮真实产出负责」交付原则，绝不拿历史账本追加或模糊的 `exit 0` 滥竽充数。
* **严谨契约与私钥强隔离**：内置端点连通性探针（`smoke probe`）与标准化 `CONTRACT_EXIT` 验收契约；落盘私钥凭据一律施加 `0600` / 目录 `0700` 权限强力封存。

### 🚀 6. 批量导入 CPA（CLIProxyAPI）热加载
* **纯协议静默铸造**：支持从 SSO Cookie 自动发起 OIDC Device Flow 协议握手，全程静默铸造凭证，无需人工手动弹窗介入。
* **严格可用性门禁**：联动 Chat 模型探针执行 **Healthy-Only** 强力筛查，剔除失效无配额账号；通过 SSH 隧道直通远端 CPA 节点，免重启实现毫秒级热加载。

### 🎛️ 7. 可视化 Web 控制台（Control Plane）
* **现代化图形控制面**：基于 FastAPI + Vite/Preact 构建的轻量美观 Web 控制台（默认运行于 `http://127.0.0.1:8787`）。
* **全景式一站式运维**：支持运行时配置在线热更、代理节点与账号凭证一键导入、批次任务秒级启停监控与 WebSocket/SSE 级毫秒级实时日志大盘。

---

## 🧩 支持的 Provider 矩阵

| Provider | 命令入口 | 核心协议 / 驱动栈 | 产物契约与交付标准 | 状态 |
|:---|:---|:---|:---|:---:|
| **typesafe.ai / jev** | `./register.sh typesafe [count] [threads]` | Python + requests + Stytch Magic-Link | `typesafe-*.json` (0600 API Key) | 🟢 生产稳定 (97.6%) |
| **Grok / xAI** | `./register.sh grok [count] [threads]` | Python + DrissionPage + OIDC Device Flow | `accounts_cli.txt` (SSO) + `cpa_auths/` (OIDC) | 🟢 生产稳定 |
| **Xiaomi MiMo** | `./register.sh mimo` | Node + Playwright | `mimo-*.json` (`sk-` API Key，OpenAI 兼容) | 🟢 自动化就绪 |
| **Outlook / Hotmail** | `./register.sh outlook [count]` | Python + Playwright + slidex | `outlook_auths/` (四段凭证与 OAuth Token) | 🟡 实验支持 |
| **通用分层编排** | `./register.sh core run -p <name>` | `register_core/` 通用编排引擎 | Pipeline 驱动，支持自由装配邮箱源与校验器 | 🟢 核心底座 |

---

## ⚡ 快速开始（最短路径上手）

### 1. 环境准备与配置初始化

项目基于 **Python 3.13** 构建，推荐使用 [uv](https://docs.astral.sh/uv/) 工具链。执行快速初始化脚本将基于 `config.simple.example.json` 自动配置 `config.json`（支持 `duckmail` 或 Hotmail 模式）并进行自检诊断：

```bash
# 1. 克隆代码仓库
git clone https://github.com/dengyie/ai-register-machine.git
cd ai-register-machine

# 2. 一键安装依赖并自检初始化
bash scripts/setup_simple.sh
```

### 2. 核心命令速查

```bash
# 💡 查看统一管理脚手架帮助
./register.sh help

# ⚡ typesafe.ai 批量并发注册（支持多线程与断点平滑继续）
./register.sh typesafe 1               # 单次注册自检
./register.sh typesafe 100 8           # 批量 100 个，8 线程高并发
./register.sh smoke typesafe           # 控制台连通性前置探测

# 🤖 Grok / xAI 注册
./register.sh grok 1 1                 # 注册 1 个号（推荐桌面有头模式）

# 🎙️ Xiaomi MiMo TTS Key 注册
./register.sh mimo

# 🧩 通用 register_core 模块化运行
./register.sh core list
./register.sh core run -p typesafe -n 1
```

### 3. Web 控制台（Control Plane）

项目内置基于 FastAPI + Vite/Preact 的现代化 Web 控制面，一键启动图形化操作：

```bash
# 🎛️ 启动 Web 控制台
export CONTROL_API_SESSION_SECRET="$(openssl rand -hex 32)"
./scripts/run_control_api.sh
# 浏览器访问 http://127.0.0.1:8787（默认初始化凭证：admin / admin123）
```

---

## 📊 生产实测验证（真机全量运行）

所有测试严格遵守**「只计本轮真实增量」**的交付铁律，绝不使用历史账本冒充：

### 🎯 typesafe.ai 生产实测（1000 批次 · 2026-09-22）

* **执行命令**：`./register.sh typesafe 1000`（8 线程，`--no-fail-fast`，超时阈值 86400s）
* **网络与环境**：pxed 生产宿主机，Clash mixed-port `127.0.0.1:7897`（本批次 `rotate=off`），tinyhost 独立邮箱，收信直连
* **实测战报**：**976 成功 / 24 失败（成功率 97.6%）**，`CONTRACT_EXIT:0`，全流程无任何进程崩溃或挂死
* **产物交付**：真实产出 976 个全新 `0600` `typesafe-*.json` 密钥文件，目录严格隔离为 `0700`，未向公共账本追加明文，未滥用注入 CPA
* **失败细粒度归因**（均为偶发性上游软失败，批次自动平滑容错跳过）：
  * `wait_magic_link`（17 次）：tinyhost 在 180s 窗口内未收到邮件（`no_mail` 偶发网络丢信）
  * `send_magic_link`（5 次）：上游接口偶发 HTTP 500
  * `auth_callback`（2 次）：上游鉴权接口偶发 HTTP 401
* **前置对照验证**：同日首轮 100 次实测 97 成功 / 3 失败；优化 Magic-Link 匹配优先级后，次轮 100 次实测提升至 98 成功 / 2 失败。

---

## 🛡️ 安全、合规与常见卡点

* **合规声明**：Education / personal automation only; not a free-quota farm. 请严格在学术研究、个人自动化及合法授权范围内使用，严禁用于任何破坏服务商条款的黑产或恶意滥用行为。
* **Grok 权限感知**：如遇 `entitlement_denied`（或 Chat 探针返回 403），代表该账号未被服务端赋予 free Build 权限，属于常规风控策略，**切勿无休止盲目 remint 浪费配额**。
* **密钥零泄漏追踪**：严禁将 `config.json`、`mail_credentials.txt` 或 `cpa_auths/*.json` 纳入版本追踪。随时执行 `bash scripts/doctor_secrets.sh` 扫描本地凭据安全状态。

### 常见卡点速查

| 现象 | 原因分析 | 推荐处理策略 |
|:---|:---|:---|
| `doctor: proxy port closed` | 代理客户端未启动或端口被占用 | 检查本地 Clash/代理运行状态，校对 `config.json` 中 `proxy` 端口 |
| `doctor: duckmail / placeholder` | 仍在使用示例占位字符 | 在 `config.json` 中配置有效的 DuckMail API Key 或真实邮箱凭证 |
| `Turnstile 验证长时间卡住` | 无头浏览器特征被识别或节点信誉不佳 | 切换至桌面有头模式（`--no-headless`）并轮换纯净代理 IP |
| `entitlement_denied` | xAI 服务端未下发体验配额 | 正常策略拦截，直接记录状态并跳过，无需重复重试 |

---

## 🐧 社区致谢与技术参考

* **[LINUX DO (https://linux.do)](https://linux.do/)**（🌟 **首位致谢对象**）：由衷感谢 LINUX DO 社区全体佬友的高质量技术分享与开源互助，为本项目协议逆向分析与生产级工程化落地提供了不可或缺的灵感源泉。
* **技术参考**：感谢社区开源项目 [ThinkerWen/ai-register](https://github.com/ThinkerWen/ai-register)、[Futureppo/typesafe_register](https://github.com/Futureppo/typesafe_register)、[daimon3332/OutlookRegister](https://github.com/daimon3332/OutlookRegister) 在多架构与协议探索方面带来的有益启发。

---

## 📄 License

本项目基于 [MIT License](LICENSE) 许可协议开源发布。  
Copyright (c) 2026 dengyie
