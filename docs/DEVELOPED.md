# 已落地功能一览（ai-register-machine）

本索引汇总**已在 main 落地**的功能开发，便于一眼看清「开发了什么」。每条附代表 commit；对应的设计文档（design spec / plan）已归档至 [`docs/archive/`](archive/)。

> 生产现状：Grok 仍跑根目录 legacy（`grok_register_ttk.py` + `register_cli.py` + `cpa_xai/`），`register_core/` 为分层目标架构。下表「归档 spec/plan」指设计稿，不代表代码落点。

| 功能 | 交付内容 | 代表 commit | 归档 spec/plan |
|---|---|---|---|
| 多 provider hub + 目标架构 | register_machine 多产品中心、register_core 分层库（contracts/pipeline/providers/nodes/verify/sink）、MiMo provider、embedded mihomo | `4c0da27`、`96b641c`（改名为 ai-register-machine） | — |
| ChatGPT code-align | protocol/OTP 诊断、soft cool、mail-proxy 拆分、PKCE 最佳努力残留、mint_method 戳、产品退出诚实计数、软/硬回收 + 致命停止不空转 | `4229542`、`97064a3`、`c02283f`、`cd46a2a`、`0eb97d8`、`691ce9f` | [chatgpt-code-align](archive/specs/2026-07-16-chatgpt-code-align-design.md) |
| CPA mid-tier probe | 直连 vs CPA 双通道 chat gate 策略、hybrid mid-tier probe 接入 mint/export/backfill | `dc4002d`、`51689b4` | [cpa-mid-tier-probe](archive/specs/2026-07-16-cpa-mid-tier-probe-design.md) |
| nodes target preflight | target-aware L1(ipify) + L2(provider 域名) 节点预检、import/batch 前只发 healthy、smart_order、urllib status 探针 | `9866e34`、`d2d1ef6`、`af3ceba` | [nodes-target-preflight](archive/specs/2026-07-17-nodes-target-preflight-design.md) |
| register_profile_config（M1–M4） | profile 驱动 mailbox∥decode∥strategy；M2 burn+cool 策略；M3/M4 MiMo/Grok FIXED_EMAIL 注入；M2–M4 review 修复 | `ee668c5`、`57d620d`、`b520035` | [register-profile-config](archive/specs/2026-07-17-register-profile-config-design.md) |
| Clash egress pinning | 权威 Clash egress 探针、pin ChatGPT 组/优选、真实 backend 标签、bare-curl 假 CN 判别 | `f3a65ab`、`8023f95`、`9525177` | [nodes-target-preflight](archive/specs/2026-07-17-nodes-target-preflight-design.md) |
| ChatGPT human-pace | protocol API 步骤间 ~10s±1s 拟人节拍 | `f421837` | [chatgpt-code-align](archive/specs/2026-07-16-chatgpt-code-align-design.md) |
| SPA-stuck browser_boot 回收 | pre-email「您正在登录」粘滞 → `AccountRetryNeeded browser_boot` slot-retry；legacy wording 兼容归类 | `773404c`、`e635334`、`530f770` | — |
| Hotmail plus-alias 关停 | 别名农场 kill-switch（mode=off / allow=false），别名耗尽立即致命停止不空转 | `78fd894` | [chatgpt-code-align](archive/specs/2026-07-16-chatgpt-code-align-design.md) |

## 待开发（backlog，未启动，本轮不做）

- **register_core 迁移收尾**：生产仍跑 legacy `grok_register_ttk.py`，未把 Grok/ChatGPT 切到 `register_core/` 管线。
- **CF `token长度=0` 日志噪声**：P3，非阻塞。
- 节点 catalog / Clash 产物进一步瘦身（`nodes.json` 2.2MB；可选）。

## Manual-required（需外部介入，本轮不解）

- **免费 Build chat 403 / console.x.ai entitlement**：账号侧权限，不 remint、不 soft inject；需 console 解锁或 grokbuild-proxy 服务端中转。
- **OpenAI `registration_disallowed`**：IP/风控侧，与 chat 403 同属 external。

验证入口：mint 后本地 probe `/v1/responses` 观察 403 body；ChatGPT 注册观察 create_account 是否仍 `registration_disallowed`。
