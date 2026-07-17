# Design: ChatGPT 代码层面对齐开源最佳实践

**Date:** 2026-07-16  
**Status:** approved (user: Scope A + Layer C + success B; §1–§2 OK; proceed implement)  
**Repo:** local `grok-register` / remote register-machine monorepo  
**Approach:** Core-first 契约扩展（Approach 1）

## 1. Context

pxed ChatGPT smoke（2026-07-16）协议路径到 `send_otp` 200，随后 Gmail catch-all **未收到** OpenAI OTP → fail-fast `mail_miss`。对照开源（zhuce6 / open-reg-auto / gpt-free-register）：成功关键不是另一套 client_id，而是：

1. 邮箱投递质量（本轮 **不接** 商业邮箱 API 实现）
2. 注册 egress 与邮件路径分离
3. OTP 等待可观测 diagnostics
4. 节点时间冷却（soft）与业务失败隔离
5. 细错误分类（尤其 `registration_disallowed`）

本设计只做 **代码层能力对齐**；OTP 投递/住宅声誉仍为 Manual-required。

## 2. Goals / Non-goals

### Goals

| ID | 目标 |
|---|---|
| G1 | OTP diagnostics：poll 次数、扫信数、first_seen/matched 延迟、failure_class |
| G2 | 细 `error_kind`：`mail_miss` / `registration_disallowed` / `captcha` / `proxy`/`network` / `provider` / `verify` / `fatal` / `other` |
| G3 | 邮件路径默认不继承注册 proxy；可选独立 `mail_proxy` / `EMAIL_PROXY` |
| G4 | `NodeManager` 时间冷却 API + 接线（risk/network soft-cool；mail_miss 永不 cool/quarantine） |
| G5 | 配置面文档化（`.env.example` / provider README / config 注释） |
| G6 | 单测全绿 + pxed COUNT=1 smoke 可解释失败类；**禁止** 空转与误 quarantine |

### Non-goals

- 新商业邮箱（MaliAPI / cfmail 实现）
- ChatGPT 自动 CPA inject
- 保证现有 gmail+clash 必出号
- Grok free Build chat 403 / grokbuild-proxy 中转
- Approach 3 大重构（EgressPolicy 全家桶）

### Success standard B（用户选定）

拆成两道门禁：

1. **代码门禁（必须）：** 契约+实现+单测绿+smoke 诊断诚实  
2. **出号门禁（尽量）：** `ok=1`；若资产不足 → 交付「代码 ✅ / 出号 ⚠️ Manual-required」，不伪造成功

## 3. Architecture

```
register_core/
  contracts.py          # OtpWaitDiagnostics; error_kind 注释/常量
  errors.py             # MailMissError.diagnostics 可选属性
  email/base.py         # 文档约定 last_wait_diagnostics（Protocol 签名不炸）
  email/sources/*       # gmail_imap / tinyhost 写 diagnostics
  email/registry.py     # get_email_source 支持 mail_proxy 与 register proxy 分离
  nodes/models.py       # cooldown_until / cooldown_reason
  nodes/manager.py      # is_cooling / cooldown() / pick 跳过冷却
  util/proxy.py         # report_attempt_proxy_result → soft cool on risk/network
  providers/chatgpt_adapter.py  # 解耦 mail proxy；放宽 kind allowlist；artifacts.otp_wait
providers/chatgpt/protocol/flow.py  # registration_disallowed kind；otp_sent_at
```

**原则：** 通用化在 core；ChatGPT 只接线。Grok/MiMo 不受破坏性变更影响（additive）。

## 4. Error taxonomy

### 4.1 `error_kind` 权威表

| kind | 含义 | hard quarantine | soft cooldown | fail-fast |
|---|---|---|---|---|
| `mail_miss` | OTP 未达/未匹配 | 否 | 否 | 单次停（现状） |
| `registration_disallowed` | OpenAI risk code | 否 | **是** | 单次 fail，不整批 fatal |
| `captcha` | PoW/人机 | 否 | 可选 | 否 |
| `proxy` / `network` | 连接/TLS/隧道 | 计入 fail_count | 是 | 否 |
| `provider` | 其它产品流 | 否 | 否 | 否 |
| `verify` | 后验能力失败 | 否 | 否 | 否 |
| `fatal` | 配置/源耗尽 | 否 | 否 | **整批** |
| `other` | 未归类 | 否 | 否 | 否 |

### 4.2 必改现状

1. `flow.py` create_account：识别 risk 后 `kind="registration_disallowed"`（现误为 `provider`）  
2. `chatgpt_adapter` allowlist 纳入 `registration_disallowed` / `proxy` / `network` / `fatal`  
3. `is_proxy_network_failure` 已排除 risk — **保持**；另增 soft-cooldown 路径  
4. `RegisterResult.error_kind` 注释同步

### 4.3 `failure_class`（mail 细分，不进 error_kind 爆炸）

| failure_class | 何时 |
|---|---|
| `no_mail` | 窗口内 0 封新信 |
| `parse_fail` | 有信无 OTP |
| `stale_code` | 码过期/已用 |
| `imap_error` | IMAP 层错误（auth 可升 fatal） |
| `aborted` | 外部中止 |

`error_kind` 仍多为 `mail_miss`；细分在 `artifacts["otp_wait"]`。

## 5. OTP diagnostics 数据模型

```python
@dataclass(slots=True)
class OtpWaitDiagnostics:
    poll_count: int = 0
    message_scan_count: int = 0
    empty_rounds: int = 0
    elapsed_seconds: float = 0.0
    timeout_s: float = 0.0
    first_message_seen_at: float | None = None
    matched_at: float | None = None
    first_seen_after_seconds: float | None = None
    matched_after_seconds: float | None = None
    abort_reason: str = ""
    failure_class: str = ""  # no_mail | parse_fail | stale_code | imap_error | aborted | ""
    provider: str = ""
    sender_hint: str = ""
    notes: str = ""
```

**挂载：**

- `MailMissError.diagnostics: OtpWaitDiagnostics | None`（`str(exc)` 行为不变）
- Source 实例可选 `last_wait_diagnostics`
- Adapter：`artifacts["otp_wait"] = asdict(diag)`；public 路径无 raw MIME/token
- ChatGPT flow 记 `otp_sent_at` 供延迟字段

**EmailSource Protocol：** 不强制改 `poll_otp` 签名；约定 `last_wait_diagnostics` 属性。

## 6. 邮件 / 代理分离

### 6.1 现状问题

`chatgpt_adapter` 把 **注册 proxy** 传入 `get_email_source(..., proxy=proxy)` → `TinyhostSource` 经 ProxyHandler 走同一出口；邮件 API 与 OpenAI 风险出口耦合。zhuce 对邮箱路径 `del proxy` 直连。

### 6.2 规则

| 路径 | 默认 proxy |
|---|---|
| OpenAI register（curl_cffi session） | 注册 egress（PROXY_LIST / nodes / CHATGPT_PROXY） |
| EmailSource allocate/poll（HTTP API） | **None（直连）**，除非显式 mail proxy |
| Gmail IMAP | 本机 IMAP；不走注册 proxy（现状基本已是） |

### 6.3 配置

优先级（mail）：

1. `extra["mail_proxy"]`
2. `CHATGPT_MAIL_PROXY` / `EMAIL_PROXY` / `MAIL_PROXY`
3. 默认 `""`（直连）

**禁止** 再把 register proxy 静默塞进 email source kwargs（breaking intentional）。

Adapter：

```python
mail_proxy = resolve_mail_proxy(extra)  # never falls back to register proxy
source = get_email_source(name, proxy=mail_proxy or None, ...)
```

`artifacts` 增加：`mail_proxy`（redact）、`register_proxy`（已有 proxy 字段）。

## 7. 节点时间冷却

### 7.1 与 quarantine 区别

| 机制 | 触发 | 效果 | 解除 |
|---|---|---|---|
| hard quarantine | `fail_count >= MAX` 且 last_ok False | 永久跳过直到 probe ok | `check_all` / 手动 |
| soft cooldown | risk / 用后 / network 软失败 | `now < cooldown_until` 时 skip pick | 时间到期 |

### 7.2 Node 字段（runtime + 可持久化）

```python
cooldown_until: float | None = None  # epoch
cooldown_reason: str = ""            # registration_disallowed | per_use | network | ...
```

### 7.3 API

```python
NodeManager.cooldown(url, seconds: float, reason: str = "", *, persist=True) -> Node | None
NodeManager.is_cooling(n: Node) -> bool
# enabled_nodes / pick: skip is_cooling
```

默认秒数（env）：

- `REGISTER_NODES_COOLDOWN_PER_USE` 默认 `0`（关闭「每次用完都 cool」，避免小池饿死；zhuce 120s 作可选）
- `REGISTER_NODES_COOLDOWN_RISK` 默认 `600`（registration_disallowed）
- `REGISTER_NODES_COOLDOWN_NETWORK` 默认 `120`（network fail 在 quarantine 前的 soft 层；仍保留 fail_count）

### 7.4 `report_attempt_proxy_result` 接线

- `ok` → clear fail_count；可选 per_use cool（仅 env>0）
- `registration_disallowed` → **不** quarantine；`cooldown(RISK)`
- `mail_miss` / `captcha` / `verify` / `fatal` → 不 cool、不 quarantine
- network → 现有 mark fail + 可选 NETWORK cool

## 8. ChatGPT 接线清单

| 文件 | 变更 |
|---|---|
| `register_core/contracts.py` | `OtpWaitDiagnostics`；error_kind 注释；可选 `ALLOWED_ERROR_KINDS` |
| `register_core/errors.py` | `MailMissError.__init__(msg, *, diagnostics=None)` |
| `register_core/email/sources/gmail_imap.py` | poll 写 last_wait_diagnostics；auth 分类 |
| `register_core/email/sources/tinyhost.py` | poll 循环写 diagnostics；默认可不接 register proxy |
| `register_core/nodes/models.py` | cooldown 字段序列化 |
| `register_core/nodes/manager.py` | cooldown API；pick 跳过 |
| `register_core/util/proxy.py` | risk → soft cool |
| `register_core/providers/chatgpt_adapter.py` | mail_proxy 分离；kind allowlist；otp_wait artifacts |
| `providers/chatgpt/protocol/flow.py` | registration_disallowed；otp_sent_at 日志 |
| `providers/chatgpt/README.md` + `.env.example` | 配置说明 |
| 新单测 | taxonomy / diagnostics / mail_proxy / cooldown |

**不变：** 无 CPA inject；fail-fast 不空转；secrets 不进 git。

## 9. 配置面

| 变量 | 默认 | 含义 |
|---|---|---|
| `CHATGPT_EMAIL_SOURCE` | `gmail_imap` | 邮箱源 |
| `CHATGPT_OTP_TIMEOUT` | `180` | 总 OTP 预算（秒） |
| `CHATGPT_PROXY` / 节点池 | — | **仅**注册 egress |
| `CHATGPT_MAIL_PROXY` / `EMAIL_PROXY` | 空=直连 | 邮件 HTTP API |
| `REGISTER_NODES_MAX_FAIL` | `3` | hard quarantine |
| `REGISTER_NODES_COOLDOWN_RISK` | `600` | risk soft cool |
| `REGISTER_NODES_COOLDOWN_NETWORK` | `120` | network soft cool |
| `REGISTER_NODES_COOLDOWN_PER_USE` | `0` | 用后 cool（默认关） |

## 10. 测试与验收

### 离线（必须）

- `test_chatgpt_error_kinds.py`（或扩现有）：disallowed 不被抹成 provider  
- cooldown：RISK cool 后 pick 跳过，到期恢复  
- mail_proxy：adapter 构造 source 时 kwargs.proxy 不等于 register proxy（除非显式 mail）  
- diagnostics：mail_miss artifacts 含 `otp_wait.failure_class`  
- 既有 `test_fail_policy` / `test_cpa_remote_inject` 不回归（ChatGPT 无关也勿破坏）

### pxed smoke（出号门禁）

```bash
# 解析 GMAIL_* 勿 source 整份 .env
COUNT=1 hub register.sh chatgpt 1   # 或项目现行入口
```

期望：

- 若 `ok=1`：tokens 落盘；无 CPA inject  
- 若仍失败：SUMMARY/`error_kind` + `otp_wait` 可区分 `no_mail` vs `registration_disallowed` vs `proxy`  
- 节点不会因 mail_miss 被 quarantine

## 11. Milestone 执行契约

```text
Milestone：ChatGPT 代码层 OSS 对齐（core-first）
目标：G1–G6；尽量实测 ok=1
P0/P1：taxonomy + diagnostics + mail/proxy 分离 + soft cooldown + 单测 + smoke
不做的 P2/P3：商业邮箱源、CPA inject、大重构、Grok chat 403
Manual-required：OpenAI OTP 投递与住宅出口声誉
阶段上限：3
阶段拆分：
  1) core 契约 + errors + diagnostics 类型 + 单测骨架
  2) nodes cooldown + proxy 反馈 + email sources/adapter/flow 接线
  3) 文档 + 全量相关单测 + pxed smoke 记录
验收标准：单测绿；smoke 诊断诚实；出号尽量
停止条件：P0/P1 完成或 3 阶段耗尽或外部资产阻塞出号（代码仍可交付）
```

## 12. Risks

| 风险 | 缓解 |
|---|---|
| 小节点池 + per_use cool 饿死 | 默认 PER_USE=0 |
| diagnostics 泄露 MIME | 只存计数/epoch/failure_class；public redact |
| gmail_imap 依赖 grok_register_ttk 黑盒 | 外层包 diagnostics；auth 错误字符串分类 |
| 出号仍失败 | 诚实 Manual-required；不放宽 inject |

## 13. Implementation order

1. contracts + errors + 常量  
2. Node cooldown + tests  
3. proxy report 接线  
4. gmail_imap / tinyhost diagnostics  
5. chatgpt adapter mail_proxy + allowlist + artifacts  
6. flow.py kind + otp_sent_at  
7. docs/env  
8. pxed smoke  

Commit 风格：`feat(core): …` / `fix(chatgpt): …` / `test: …` / `docs: …`

## 14. References

- pxed smoke memory：`project_pxed_smoke_20260716`  
- zhuce6：`last_wait_diagnostics`、`del proxy` on mail、proxy_pool cooldown 120/600  
- 本仓库：`register_core/providers/chatgpt_adapter.py`、`providers/chatgpt/protocol/flow.py`、`register_core/util/proxy.py`
