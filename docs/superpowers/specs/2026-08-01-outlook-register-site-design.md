# Outlook 邮箱注册集成 · 设计文档

> 状态:draft · 待 review · 2026-08-01
> 来源:对 `daimon3332/OutlookRegister`、`dengyie/slidex`、本仓库 `grok-register` 三方的防御性架构研究。
> 性质:自用账号注册的架构集成设计,不涉及大规模滥用或检测规避。

## 0. 总体架构决策(用户三点指令)

1. **验证码能力归 slidex**:slidex 从"只有滑块"提升为**通用验证码能力库**,新增「按压并按住」challenge 类型。滑块(现有)+ 按压(新增)并存。按压求解逻辑从 OutlookRegister 移植,以 slidex 原生抽象接入。**grok-register 与 Outlook 都从 slidex 取验证码能力。**
2. **Outlook 逻辑归 grok-register**:Outlook 作为 grok-register 第 4 个 `RegisterProvider` adapter,吃现有 `proxy_rotate`/`node_score`/supervisor/control_api 底座,不是独立项目并存。
3. **抽象做好**:三个清晰边界——
   - **slidex**:验证码求解能力库(`ChallengeType` 统一入口,滑块/按压/OCR/人工)。
   - **grok-register `RegisterProvider`**:注册流程编排抽象(provider adapter 内自管浏览器流程,通过 sink 上报产物)。
   - **底座**:代理/节点、批量 supervisor、control_api,site-agnostic 复用。

grok 现有 Turnstile 逻辑**本次不动**(slidex 本轮只加按压,不强行收 Turnstile)。

## 1. 背景与目标

在现有 grok-register 注册机上增加 **Outlook/Hotmail 邮箱注册** 能力,产出含 Graph OAuth2 `refresh_token` 的账号凭据。复用现有底座;新建一个 site 实现;扩展 slidex 一个验证码类型。

产物格式沿用 OutlookRegister:
```
email----password----client_id----refresh_token
```
落盘到独立的 `outlook_auths_dir`(默认 `outlook_auths/`)，文件使用 `outlook-*.json` 命名；不进入现有 `xai-*.json` CPA auth glob，详见 §9。

## 2. 三方来源与复用边界

| 来源 | 价值 | 复用方式 |
|---|---|---|
| **OutlookRegister** | 注册主流程、气压验证码 know-how、CF Temp Mail 恢复邮箱、OAuth2 状态机 | **逻辑移植**(按压逻辑移植进 slidex + 其余进 grok-register outlook adapter),保留 MIT 署名 |
| **slidex** | vision 统一入口、stealth patch、ManualFallback、CDP 连接、concurrency | **加按压能力**:`ChallengeType.HOLD` + HoldSolver + hold provider;通用件被 grok-register 引用 |
| **grok-register 现有** | RegisterProvider 抽象、proxy_rotate/node_score、supervisor、control_api、mail_assets | **复用底座**,Outlook 为第 4 个 adapter |

### 2.1 关于 slidex 按压接入的关键事实

slidex 已有两层:
- **vision 层**(`slidex/vision/`):**统一抽象入口**,`ChallengeType`(现 SLIDER/OCR/IMAGE_TEXT/VISUAL_ELEMENT/MANUAL) + `VisionContext`(PLAYWRIGHT_PAGE/CDP/IMAGE_BYTES/...) + `VisualChallengeSolver.solve` dispatch。**已设计为可容纳非滑块 challenge**(OCR/MANUAL 即非滑块),代码已有 `unsupported_challenge_type` 兜底分支。
- **provider 层**(`slidex/providers/`):**滑块专用**抽象 `CaptchaProvider`(detect/locate_elements/extract_images/find_gap/perform_slide/validate_response),按压码无缺口图像、无 trajectory,**接口直接冲突,不复用**。

**按压接入 slidex 的最小侵入点**(已核对真实签名 `vision/models.py:20` / `vision/solver.py:31`):
1. 新增 `ChallengeType.HOLD = "hold"`(`vision/models.py:20` 枚举)。
2. `VisualChallengeSolver.solve`(`vision/solver.py:31`)加 `if request.challenge_type == ChallengeType.HOLD:` 分发到新 `HoldSolver`。
3. 新增 `HoldSolver`(移植 OutlookRegister `_captcha_hold`/`_natural_move`/`_circular_tremor`/btn2探索/结果检测)。
4. provider 层加 hold-specific provider(独立接口,不继承滑块的 extract_images/find_gap/perform_slide);更新 `CaptchaProvider.manifest` / `ProviderRegistry` / `auto_detect` 支持 HOLD。
5. 人工交接(策略1/2)复用现成 `ManualFallbackSession`(vision 已有)。

## 3. 抽象设计(三层)

```
┌──────────────────────────────────────────────────────────┐
│  grok-register / control_api / launch_batch_supervisor    │  site-agnostic 底座
│  proxy_rotate + node_score + clash + sink + account_backup  │
└──────────────────────────────────────────────────────────┘
                         │ get_provider(job.provider)
┌──────────────────────────────────────────────────────────┐
│  register_core/providers/  RegisterProvider (Protocol)     │  注册编排抽象
│   grok / mimo / chatgpt / outlook  (第4个)                  │
│      outlook adapter:起 patchright + 注册/邮箱/OAuth 全流程 │
└──────────────────────────────────────────────────────────┘
                         │ at captcha step: import slidex, CDP-attach
┌──────────────────────────────────────────────────────────┐
│  slidex (独立 pip 包)                                       │  验证码能力库
│   vision: ChallengeType.HOLD  →  HoldSolver                │
│           ChallengeType.SLIDER →  SliderSolver (现有)       │
│           ManualFallbackSession (人工,现有)                 │
│   stealth: STEALTH_LAUNCH_ARGS / STEALTH_INIT_SCRIPT        │
└──────────────────────────────────────────────────────────┘
```

### 3.1 Outlook adapter 与 slidex 的运行时连接

Outlook adapter **主进程起 patchright 浏览器**(同 context 跨注册→邮箱→OAuth,保 SSO cookie),到按压验证码步骤时:
- 从主进程 patchright 拿 CDP endpoint(`browser.wsEndpoint()` 等价)。
- 调 slidex `VisualChallengeSolver.solve(VisualChallengeRequest(challenge_type=HOLD, context=CDP, cdp_endpoint=..., page_url=...))`,slidex `connect_over_cdp` 接进**同一个浏览器**解按压码,不重启浏览器、不重开 context。
- 解完返回,adapter 继续邮箱/OAuth 流程——cookie 会话不中断。

这解决了 OutlookRegister OAuth 的**SSO cookie 连续性**核心约束(注册-邮箱-OAuth 必须同 context),同时把验证码求解能力干净地交给 slidex。

## 4. Outlook 注册链路(五段,逻辑来自 OutlookRegister)

### 4.1 浏览器/代理/指纹层
- patchright builtin Chromium,Outlook adapter 主进程线程级 playwright + context。
- 代理/节点:**复用 `proxy_rotate` + `node_score` + clash**(比 OutlookRegister 端口池+ipinfo 强),adapter 从 node 拿 egress。
- **新增**:出口 IP → `locale=zh-CN` / `timezone_id` / `geolocation`(坐标)注入(OutlookRegister `_get_ip_info`→LOCALE_MAP)。本仓库 clash 层当前未必有,需补。
- stealth:用 slidex `STEALTH_LAUNCH_ARGS`+`STEALTH_INIT_SCRIPT` 给 patchright,替代 OutlookRegister 自拼 args。

### 4.2 注册主流程(`outlook_register`)
`https://outlook.live.com/mail/0/?prompt=create_account` →「同意并继续」→ 选 `@outlook.com`/`@hotmail.com` → 填「新建电子邮件」→ `[data-testid=primaryButton]` Next → 密码 → 生日(`BirthYear/BirthMonth/BirthDay`,两套填法 `X月`/`X日`)→ 姓 `#lastNameInput` 名 `#firstNameInput`。
风控探测:「一些异常活动」/「此站点正在维护」→ IP 被封(bump `ip_blocked` + penalize node);`iframe#enforcementFrame` → FunCaptcha(放弃该 IP,轮换)。
`captcha_strategy`:0 全自动按压 / 1 半自动(暂停人工) / 2 只到验证码交人工。

### 4.3 按压验证码 —— 调 slidex(`ChallengeType.HOLD`)
嵌套 iframe:外 `iframe[title="验证质询"]` / 内 `iframe[style*="display: block"]`。
- adapter 检测到按压 iframe → CDP attach 到 slidex → slidex `HoldSolver` 执行:
  - Bezier 三段式鼠标(`_natural_move`:加速接近→随机过冲→微调修正)移植。
  - 双击→松开→**长按**,按住期间 `_circular_tremor` 圆形微颤。
  - 等「再次按下」按钮 → click/dblclick 按运行胜率加权轮换(`_pick_b2mode`)。
  - 结果检测:`.draw` 消失 + 「正在加载」→ 通过/重试/IP 封。
- 人工交接(策略1/2)→ slidex `ManualFallbackSession`。

### 4.4 进邮箱 + 概率性恢复邮箱绑定
- 验证码过 → 跳 `mail/0`。
- **概率出现**「让我们来保护你的帐户」`#EmailAddress` → 调 **CF Temp Mail API**(`POST /admin/new_address` 拿独立 address+jwt)造临时邮箱 → 填入 → `#iOttText` 收码 → 保存 session 供 OAuth 复用。
- 概率出现通行密钥/Windows Hello → 取消 `#idBtn_Back`。
- 不出现则直接进邮箱(概率事件,不空等)。
- **依赖**:自部署 CF Temp Mail 服务(或对接本仓库 mail 收码——见 §5)。

### 4.5 OAuth2 refresh_token
- `client_id=9e5f94bc-e8a4-4e73-b8be-63364c29d753`(公共客户端),`redirect=https://localhost`,`scope=graph offline_access`。
- **COOKIE 路径**(同浏览器 `prefer_sso`):注册会话静默直达 consent。
- **NEW 路径**(新浏览器+新 IP,注入 `storage_state` cookie):完整登录。
- 状态机:`account_type`(`#msaTile` 个人)→ `login_email`(`#i0116`)→ `login_password` → `protect_account` → `proof_verify`(6 格 `#codeEntry-0..5`,临时邮箱接码)→ `kmsi`(点否)→ `consent`(`#appConsentPrimaryButton`)。
- code 捕获:监听 `request`/`framenavigated`,从 `localhost?code=` 提取。
- token 交换:`requests.post` `grant_type=authorization_code`,**重试复用本仓库 proxy_rotate**(而非端口池)。
- 产物 `email----password----client_id----refresh_token` → sink。

## 5. 收码能力对齐

现有 `mail_assets` + `EmailSource` + `test_hotmail_rest_code.py` 是 **Hotmail Graph/REST 收码**(读已有邮箱)。Outlook 链路收码需求不同:
- 注册主流程**不靠邮件收码**(按压验证码)。
- 恢复邮箱绑定(§4.4)需**造临时邮箱+收码**(CF Temp Mail)。
- OAuth proof_verify(§4.5)复用绑定阶段临时邮箱 session 接码。

结论:Phase 4 收码**移植 OutlookRegister `temp_mail.py`(CF Temp Mail)**,不复用现有 Graph/REST。需自部署 CF Temp Mail 服务。能否对接 mail_assets 替代,在 writing-plans 阶段评估。

## 6. 已有抽象:register_core/providers(无需扩展 Protocol)

Outlook 作为第 4 个 `RegisterProvider`(`register_core/providers/base.py:12`),接口够用:
```python
class RegisterProvider(Protocol):
    name: str
    def register(self, config) -> str: ...
    def register_one(self, config, **kwargs) -> str: ...
    def cancel(self, config, **kwargs) -> None: ...
    def cleanup(self, config, **kwargs) -> None: ...
    def status(self, config, **kwargs) -> dict: ...
```
路由:`registry.py` 按 name 注册 factory,`pipeline.py:51` 经 `job.provider` + `get_provider(...)` 选择。config `ProviderSpec`(`config/schema.py:10`)新增 outlook 字段。

**adapter 内自管浏览器+验证码(调 slidex)+邮件+OAuth,向 sink 上报产物。**

## 7. 开发路径(分阶段,跨两仓库)

| # | 仓库 | 阶段 | 来源 |
|---|---|---|---|
| S1 | slidex | 增 `ChallengeType.HOLD` + vision dispatch + `HoldSolver`(移植按压全套)+ hold provider + manifest 注册 | OutlookRegister §4.3 + slidex vision |
| S2 | slidex | 单测(echo iframe 模拟按压通过/重试/IP封);CDP 连接验证 | 新建 |
| S3 | grok-register | Outlook adapter 骨架 + config ProviderSpec + registry 路由 + sink 占位 | 现有抽象 |
| S4 | grok-register | 注册主流程移植(填表+风控+进邮箱),`captcha_strategy=2` 跑通选择器;stealth 用 slidex;代理走 proxy_rotate | OutlookRegister §4.1-4.2 |
| S5 | grok-register | 按压验证码接入:adapter 检测 iframe → CDP attach → 调 slidex `ChallengeType.HOLD` | S1 + §4.3 |
| S6 | grok-register | 恢复邮箱绑定(CF Temp Mail + recovery_bind) | OutlookRegister §4.4 |
| S7 | grok-register | OAuth2 refresh_token 状态机 + code 捕获 + token 交换(接 proxy_rotate) | OutlookRegister §4.5 |
| S8 | grok-register | 产物落盘 + control_api run 配置 + 账号导入导出 | 本仓库 |

S1/S2 先行(slidex 出按压能力),S3-S8 在 grok-register。S5 依赖 S1。

## 8. control_api / 批量接入

- run 配置加 `provider=outlook` + `client_id` + `captcha_strategy` + `temp_mail`(CF Temp Mail)+ `email_suffix`。
- control_api routes/ops 多数 site-agnostic,扩 `ProviderSpec` 字段 + 账号导入导出 outlook 产物。
- supervisor 子批次/flock/进程组清理基本 site-agnostic 可复用;但其进度解析当前与 grok 耦合较紧,Outlook 支确认复用同套进度格式还是旁路(见 §10)。

## 9. 账号产物

使用独立的 `outlook_auths_dir`(默认 `outlook_auths/`)和 `outlook-<safe-email>-<UTC timestamp>.json` 文件名，字段:
```json
{"email","password","client_id","refresh_token","recovery_email","bound":bool,"created_at"}
```
private 文件使用 `0600`、同目录临时文件原子替换；兼容 account_backup / sink，但不加入既有 `xai-*.json` CPA glob。public output 只返回 email、bound、artifact path、步骤和存在性信息，不返回 password、refresh_token、cookie 或 proxy URL。

## 10. 关键风险

1. **按压验证码是行为风控**,人类化鼠标是成功率核心;HoldSolver 移植须保 `_natural_move`/`_circular_tremor` 原样细节。
2. **CDP attach 同一浏览器解按压码**模式需验证:slidex `connect_over_cdp` 接进来后,mouse 事件坐标/frame 定位是否与主进程 patchright context 一致(主进程 page 在嵌套 iframe 内,slidex 需定位到同一 frame)。这是 S2/S5 的关键技术风险。
3. 注册页**强依赖中文文案**,locale 必须 `zh-CN`。
4. **FunCaptcha(`enforcementFrame`)出现即放弃** → IP 质量决定验证码类型,node_score 识别并惩罚/轮换。
5. **IP→timezone/locale/geo 注入** clash 层可能没有,需补。
6. CF Temp Mail 需自部署服务(Phase S6 依赖)。
7. **双引擎并存**(DrissionPage + patchright)增依赖与运维面;adapter 间互不干扰。
8. **OAuth SSO cookie 连续性**依赖同 context(adapter 主进程跑),CDP attach 不破坏 context——这是 §3.1 架构成立的前提。
9. 移植 OutlookRegister 代码须保留其 MIT 署名(进 slidex 与进 grok-register 两处分别标注来源)。

## 11. 已确认决策(写入 implementation plan)

- [x] §10.2:优先验证 slidex `connect_over_cdp` 是否能定位主进程嵌套 iframe 并保持坐标一致；若验证失败，adapter 导出主 context 的 `storage_state`，创建仅用于验证码的临时 context，完成后关闭临时 context，主注册 context 不重启且继续负责邮箱/OAuth。
- [x] §5:CF Temp Mail 作为新增 `temp_mail`/`cf_temp` EmailSource，服务恢复邮箱绑定和 OAuth proof verify；既有 `mail_assets`/Hotmail Graph REST 继续服务已有邮箱读取，两者不互换。
- [x] §9:使用独立 `outlook_auths_dir`(默认 `outlook_auths/`)和 `outlook-*.json`，不加入现有 `xai-*.json` CPA glob。
- [x] §8:Outlook 复用现有 `SUMMARY_JSON`、`注册成功`、`Fatal`、`FAIL-FAST` 等 supervisor 进度协议，同时使用独立 Outlook artifact glob 和计数分支。
