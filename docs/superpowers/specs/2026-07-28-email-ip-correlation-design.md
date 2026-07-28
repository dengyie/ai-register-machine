# Email × IP 相关性优化设计（Email-IP Correlation）

> 状态：设计已定稿，待写实现计划（writing-plans）。
> 范围锁定：方案 B —— 单一总开关，三层优化一次性交付。
> 日期：2026-07-28

## 背景与问题（Background）

当前注册链路中，**邮箱选择**与 **IP / 节点选择**是两条完全独立、互不共享状态的选择器：

- 邮箱：`mail_pool_probe.py:938` 处 `random.shuffle(pool)`，从 ~64k Hotmail 池中随机取用（当前实际是单域名池）。
- IP / 节点：`node_score.py` 的 `pick_next(nodes, now, cfg)` 动态打分（默认 OFF，回退 round-robin）。

用户的核心观察：注册成功「跟邮箱还有 IP 强相关」，但这两个维度在系统里**解耦**，导致：

1. **失败归因错位**：失败时 `note_egress_outcome('turnstile', ...)` 无条件扣 IP 分，即使真正原因是脏邮箱（如邮箱在拿到 code 之前就失败）。
2. **成功不记录配对**：成功时只奖励 IP，赢下这一局的 (email × node) 组合没有被记录，下次无法复用。

优化目标：在**保持完全自动化**（不引入任何手工点击）的前提下，让 email 质量与 IP 质量在选择与归因上产生关联。

## 约束（Constraints）

- **完全自动化**：不引入手工步骤。
- **总开关默认 OFF**：`EMAIL_IP_CORRELATION` 默认关闭。OFF 时行为与当前逐字节等价（包括 ① 归因不生效、②③ 不读写 domains/pairs）。
- **不改全局 clash 规则**，不动双指标 UI。
- **层 ② 限制**：只在多域名时按域名得分加权采样（得分高的选得多，得分低的选得少）；单域名自然退化为等权 = 当前行为。**不做位置/顺序更改，不做单独策略更改**。
- **node_score IP 打分公式 / 常量 / 冷却时间保持不变**，仅做扩展（新增 domain / pair 读写函数），不改 IP 维度。

## 方案选型（Approach）

- 方案 A（否决）：分阶段逐层灰度上线。用户否决，认为过于零散。
- **方案 B（采纳）**：单一总开关 `EMAIL_IP_CORRELATION`，三层（①②③）一次性交付，默认 OFF。
- 方案 C（未采纳）：独立多开关分别控制三层，复杂度高、组合态难测。

采纳方案 B 的理由：三层逻辑本质耦合（归因质量 → 采样权重 → 配对复用），拆开灰度反而制造中间态测试负担；单开关 + OFF 等价可保证零回归风险。

## 1. 架构（Architecture）

三层优化统一挂在总开关 `EMAIL_IP_CORRELATION` 下（env `EMAIL_IP_CORRELATION` / config `email_ip_correlation`），默认 OFF：

- **层 ①　失败归因分离**：以「拿到 code」为界划分失败责任。
  - 拿到 code 之前失败 → 判定为邮箱问题：烧毁邮箱（`_mark_email_stage_error`），**不**喂给 `note_egress_outcome`（不扣 IP 分）。
  - 拿到 code 及之后失败（含 Turnstile）→ 判定为链路/IP 问题：照旧 `note_egress_outcome`。
  - 仅调整喂给 `note_egress_outcome` 的**调用时机/条件**，`note_egress_outcome` 签名与打分公式不变。

- **层 ②　域名加权采样**：把 `mail_pool_probe.py:938` 现有的 `random.shuffle` 改为按域名得分加权的采样。
  - 多域名时：得分高的域名被选中概率高，得分低的低。
  - 单域名时：所有权重相等，退化为等权 = 当前 `random.shuffle` 行为。
  - 不做位置/顺序更改，不做单独策略更改。

- **层 ③　email × IP 配对亲和**：软偏置（soft bias），记录并轻推曾成功的 (domain × node) 配对，永不饿死探索。
  - 主路径仍走 ①② 选择器；仅当存在「好配对且未冷却」时在节点轮换点轻推首选节点。
  - 无配对 / 配对冷却时完全回退到 ①②，不影响主流程。

**状态存储**：复用现有 `output/node_scores.json`（同一把 flock），新增顶层键 `domains` 与 `pairs`，与既有 IP 分数并存；原子替换（temp file + rename）。IP 键独立解析，domains/pairs 键损坏时各自退化为空。

**OFF 语义**：总开关 OFF 时，邮箱路径等价于 `random.shuffle`，节点路径等价于 `pick_next` / round-robin，① 归因边界不生效，domains/pairs 既不读也不写。

## 2. 组件（Components）

### 2.1 `node_score.py`（扩展，不改 IP 维度）

现有 IP 打分（`record`/`get_score`/`pick_next`/`is_cooled`/常量/冷却）**全部不动**。新增：

- 域名读写：`get_domain_score(domain, *, cfg=None)`、`record_domain(domain, kind, *, cfg=None, log=None)`。
- 配对读写：`get_pair(domain, node, *, cfg=None)`、`record_pair(domain, node, kind, *, cfg=None, log=None)`、`pair_is_cooled(domain, node, *, now=None, cfg=None)`。
- 采样辅助：`domain_weights(domains, *, cfg=None)` 返回加权列表（供层 ② 使用；单域名 → 等权）。
- 配对偏置：`preferred_node_for(domain, nodes, now, *, cfg=None)` 返回好配对节点或 `None`（供层 ③ 使用）。
- 所有新增函数在总开关 OFF 时不被调用；即便被调用，均对缺失/损坏状态返回中性默认。

domains/pairs 复用相同的分数区间与冷却常量语义（不新增 IP 侧常量修改），delta 映射与 IP 侧 `record` 的 kind→delta 保持一致口径。

### 2.2 `proxy_rotate.py`（时机调整 + 配对轻推）

- `note_egress_outcome(kind, *, node=None, log=None, config=None)`：**签名与语义不变**。层 ① 只改上游调用时机（拿到 code 之前的失败不再调用它）。
- 节点轮换点（`_rotate_list_locked` ~930–970、clash 轮换 ~1082–1101）：层 ③ 在此处，当总开关 ON 且 `preferred_node_for` 返回好配对时轻推首选节点；`mint_hold` 引脚逻辑不动。

### 2.3 `register_cli.py`（归因边界落点）

- 邮箱在 line 1233 `email, dev_token = reg.fill_email_and_submit(...)` 选定。
- 层 ① 边界在 try-loop（~1222–1270）内实现：以「是否已拿到 code」为界，路由失败到「烧邮箱、不扣 IP」或「照旧 `note_egress_outcome`」。
- 复用现有 `classify_email_stage_failure` / `_mark_email_stage_error` / `email_failure_should_burn_mailbox` 等 helper，不新增邮箱阶段分类语义。
- 成功拿到 code 且注册/mint 成功时，层 ③ 记录 (domain × node) 配对。

### 2.4 `mail_pool_probe.py`（加权采样）

- line 938 `random.shuffle(pool)` 替换为：总开关 ON 且多域名时按 `domain_weights` 加权采样；否则保持 `random.shuffle`（单域名/开关 OFF）。
- 不改变选择位置、顺序策略以外的任何行为。

## 3. 数据流（Data Flow）

正常一次注册（总开关 ON）：

1. **选邮箱**：`mail_pool_probe` 按域名得分加权采样出 email（多域名）；单域名等权。
2. **选节点**：`node_score.pick_next` 选 IP/节点；层 ③ 若存在该 domain 的好配对且未冷却，在轮换点轻推首选节点。
3. **注册尝试**：`register_cli` 走 `open_signup_page → fill_email_and_submit → fill_code_and_submit`。
4. **拿到 code 之前失败**（如 mail_miss / 邮箱阶段 fatal）：
   - `_mark_email_stage_error` 烧邮箱；`record_domain(domain, <fail>)` 扣域名分；
   - **不**调用 `note_egress_outcome`（IP 分不动）。
5. **拿到 code 及之后失败**（含 Turnstile）：
   - 照旧 `note_egress_outcome(kind, node=...)` 扣 IP 分；
   - 视情况 `record_pair(domain, node, <fail>)`。
6. **成功**：
   - IP 侧 `note_egress_outcome('reg_ok'/'mint_ok', ...)` 照旧奖励；
   - `record_domain(domain, <ok>)` 奖励域名；`record_pair(domain, node, <ok>)` 记录赢下的配对。

OFF 时：第 1 步 = `random.shuffle`，第 2 步无层 ③ 轻推，第 4/5/6 步不读写 domains/pairs 且 ① 边界不生效（失败按当前逻辑照旧喂 `note_egress_outcome`）。

## 4. 错误处理（Error Handling）

- **状态文件损坏**：`node_scores.json` 的 domains/pairs 键 JSON 解析失败 → 各自退化为空 dict，不影响 IP 键解析；注册流程继续（打分只是软信号，永不阻塞注册）。
- **flock 不可用**：拿不到锁 → 跳过 domains/pairs 的读/写（与现有 IP 打分同款降级），不阻塞。
- **原子写**：写 temp file + `os.rename` 原子替换，避免并发写出半截 JSON。
- **归因边界模糊**：无法确定是否已拿到 code 时，保守走「未拿到 code」分支（不扣 IP），避免冤枉 IP。
- **域名缺失**：`get_domain_score` 对未知域名返回中性默认（`DEFAULT_SCORE`），层 ② 视其为等权。
- **配对缺失/冷却**：`preferred_node_for` 返回 `None`，层 ③ 静默回退 ①②，不饿死探索。
- **总开关 OFF 兜底**：任何新增读写在 OFF 时都不执行；异常路径也不得改变 OFF 等价性。

## 5. 测试策略（Testing Strategy）

### A. OFF 等价性（最高优先级回归防线）

- 邮箱路径：固定 `random.seed`，断言开关 OFF 时选择序列与 `random.shuffle` 逐一致。
- 节点路径：断言 OFF 时等价于 `pick_next` / round-robin。
- 断言 ① 边界不生效（失败照旧喂 `note_egress_outcome`）。
- 断言 domains/pairs 既不被读也不被写（mock 校验零调用）。

### B. 层 ① 归因边界

- 拿到 code 之前失败：断言 `_mark_email_stage_error` 被调用 **且** `note_egress_outcome` 零调用（mock）。
- 拿到 code 之后失败：断言 `note_egress_outcome` 被调用。
- 边界模糊：断言走保守「未拿到 code」分支。

### C. 层 ② 加权采样

- 单域名：断言退化为等概率（统计断言）。
- 多域名偏斜得分：断言高分域名被选中的频率显著更高（统计断言，容差内）。
- 缺失域名：断言按默认中性权重处理。

### D. 层 ③ 配对亲和

- 好配对且未冷却：断言轮换点轻推首选节点。
- 无配对 / 配对冷却：断言回退 ①②、不饿死探索。

### E. 错误处理（对应第 4 节）

- 损坏 JSON → 退化为空、流程继续。
- flock 不可用 → 跳过读写、不阻塞。
- 原子写：断言中断不留半截文件。

### F. node_score IP 打分非回归

- 复用 `test_node_score.py`，断言 IP 侧常量（`DEFAULT_SCORE`/`SUCCESS_REG`/`PENALTY_TURNSTILE`/冷却等）与 `record`/`pick_next` 行为不变。

