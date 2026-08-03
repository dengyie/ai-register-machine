# Outlook/Hotmail 注册集成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 grok-register 中加入受明确 live gate 保护的 Outlook/Hotmail `RegisterProvider`，把按压验证码能力加入独立 slidex 包，并产出受权限保护的 Graph OAuth2 `refresh_token` 账号记录。

**Architecture:** 分成两个可测试边界：slidex 在 `vision` 层统一暴露 `ChallengeType.HOLD`，在 provider 层用不继承滑块 `CaptchaProvider` 的独立按压接口；grok-register 以第四个 `OutlookProvider` 编排注册、恢复邮箱和 OAuth 状态机。Outlook provider 在同一 patchright context 中运行，验证码步骤通过 CDP endpoint 调用 slidex，代理由 pipeline 注入的 `extra["proxy"]` 提供，结果经现有 `RegisterResult`/sink/控制面上报。

**Tech Stack:** Python 3.13 (`>=3.13,<3.14`)、pytest、patchright、slidex（独立 MIT pip 包）、Playwright-compatible async page/frame API、FastAPI/Pydantic control API、现有 `proxy_rotate`/`node_score`/supervisor。

## Global Constraints

- 保留 `daimon3332/OutlookRegister` 的 MIT 署名，**在 slidex 和 grok-register 两个仓库各自放一处独立 attribution**，不得只在一个仓库署名。
- 只做授权的防御性架构研究和合法自用账号测试；不得执行真实账号批量注册、DoS、恶意大规模目标攻击或检测规避；所有真实浏览器步骤必须受 `GROK_REGISTER_OUTLOOK_LIVE=1` 明确 gate 保护，默认只运行 mock/unit 测试。
- 不读取、输出或暴露代理凭据、邮箱密码、OAuth token、cookie 或其他 secret；测试使用合成值，日志和 public result 只输出存在性/长度/脱敏 preview。
- 按压逻辑归 slidex；不得把按压轨迹塞进滑块轨迹，也不得把 hold 逻辑塞进滑块 `CaptchaProvider.perform_slide`。
- `HoldCaptchaProvider` 必须是独立接口，不得继承滑块 `CaptchaProvider`；Outlook adapter 必须作为 grok-register 第四个 provider，不能成为独立项目或旁路脚本。
- 本轮不迁移 grok Turnstile 到 slidex；只新增 `ChallengeType.HOLD`，现有滑块和 grok Turnstile 行为保持不变。
- Outlook 主流程使用 patchright；patchright 和 slidex 必须加入 grok-register 的依赖配置，Python 版本下限保持 `>=3.13,<3.14`。
- 代理必须沿 pipeline 的 `inject_attempt_proxy` 进入 provider 的 `extra["proxy"]`；Outlook provider 必须在 `OutlookBrowser.open()` 之前校验它是非空字符串，缺失、空白或错误类型统一返回 `error_kind="proxy"`，且不得启动浏览器或分配 Temp Mail mailbox；不得重新引入 OutlookRegister 的独立端口池或打印 proxy URL。
- Outlook provider options 只接受非 secret 配置和环境变量名；secret-key 检查必须递归覆盖 `temp_mail` 等嵌套字典，但 `admin_password_env` 这类环境变量名允许存在；不得把密码、token、cookie、JWT、authorization、proxy URL 或 client secret 作为配置值传入。
- 收码选择固定为：`mail_assets`/Hotmail Graph REST 继续服务已有邮箱读取；恢复邮箱和 OAuth proof verify 使用新增 `temp_mail`/`cf_temp` EmailSource，对接自部署 CF Temp Mail API；不得把两个语义混成一个 source。
- 账号产物使用独立 `outlook-*.json` 命名/目录和导入导出路径，不匹配 `xai-*.json` CPA glob，不污染现有 xai auths。
- supervisor 复用现有子批次/flock/进程组清理和 `SUMMARY_JSON` 进度协议；Outlook runner 必须发出兼容 marker，并由独立 outlook glob/解析分支消费。
- 每个实现步骤必须先写 failing test，再用该任务列出的 `.venv/bin/python -m pytest` focused command 验证失败、写最小实现、验证通过并提交；不得用裸 `pytest` 代替指定命令。
- 浏览器-heavy 测试使用 `GROK_REGISTER_OUTLOOK_LIVE` gate 或 `pytest.importorskip("patchright")`；仓库中没有可引用的 `GROK_REGISTER_LIVE`，计划和实现都不得声称存在它。
- 不提交真实配置、真实 mail service admin password、真实邮箱密码、refresh token、代理 URL、cookies、截图或 token 交换响应。

---

## 已确定的设计决策（对应 spec §11）

1. **CDP attach：先验证，失败有明确 fallback。** S2 写受 gate 保护的技术验证，确认 slidex `connect_over_cdp` 能在主进程 patchright 的 nested iframe 上找到同一 frame 并保持坐标一致。验证失败时 S5 不强行使用 CDP：adapter 从主 context 导出 `storage_state()`，启动仅用于验证码的第二 context，把 cookies 注入后调用 slidex，再关闭第二 context；注册主 context 继续负责邮箱/OAuth。fallback 的状态和原因写到脱敏 metadata。
2. **CF Temp Mail 与 mail_assets：并存而非互换。** 新 `TempMailSource` 实现 `EmailSource`，负责 `create_address`/JWT poll；现有 `mail_assets` Graph/REST 继续只读已有 Hotmail。恢复邮箱绑定返回 `Mailbox` 和 session，OAuth proof verify 复用同一临时 mailbox/session，避免再次创建地址。
3. **产物目录：独立 Outlook auths。** 新产物根目录由 `outlook_auths_dir` 配置，文件名 `outlook-<safe-email>-<UTC timestamp>.json`；字段为 `email,password,client_id,refresh_token,recovery_email,bound,created_at`。private sink 仍使用 0600；public output 只显示 email、bound、path 和脱敏 step 信息。
4. **supervisor：复用协议，不旁路。** Outlook 子进程写 `SUMMARY_JSON`，成功写 `注册成功`，失败写 `FAIL-FAST`/`Fatal` 等已有终结 marker；启动脚本增加只匹配 `outlook-*.json` 的分支和 Outlook 计数，不把 Outlook 文件加入 `xai-*.json` CPA import glob。

## 文件与职责地图

### slidex（独立仓库 `/tmp/slidex`）

- Modify: `slidex/vision/models.py` — `ChallengeType.HOLD`。
- Create: `slidex/vision/hold.py` — hold result、CDP/page connector、按压状态机和结果分类。
- Modify: `slidex/vision/solver.py` — `hold_solver_factory` 注入和 HOLD dispatch。
- Modify: `slidex/vision/__init__.py` — 导出 `HoldSolveResult`/`HoldSolver`（保持 lazy solver）。
- Create: `slidex/providers/hold.py` — 独立 `HoldCaptchaProvider` 和 Outlook hold provider。
- Modify: `slidex/providers/__init__.py` — hold registry，不改变已有滑块注册行为。
- Create: `tests/unit/test_hold_solver.py` — iframe、通过、重试、IP 封、CDP 参数单测。
- Create: `tests/unit/test_hold_provider_registry.py` — 独立接口和 manifest/registry 单测。
- Create: `tests/integration/test_hold_cdp_attach.py` — gate 保护的真实 CDP 技术验证。
- Modify: `README.md` — HOLD API 和两处来源署名之一。

### grok-register（本仓库）

- Modify: `pyproject.toml` — patchright/slidex dependency。
- Create: `register_core/providers/outlook_adapter.py` — `OutlookProvider` 编排器。
- Create: `register_core/providers/outlook_browser.py` — patchright lifecycle、locale/geo、form selectors 和 risk detection。
- Create: `register_core/providers/outlook_captcha.py` — iframe detection、slidex CDP bridge、storage-state fallback。
- Create: `register_core/email/sources/temp_mail.py` — CF Temp Mail client + `EmailSource`。
- Create: `register_core/providers/outlook_recovery.py` — recovery bind 和 bound session。
- Create: `register_core/providers/outlook_oauth.py` — OAuth authorize/code capture/token exchange state machine。
- Create: `tests/unit/test_outlook_provider.py` — provider contract、配置、错误映射、live gate。
- Create: `tests/unit/test_temp_mail_source.py` — HTTP mock、code parsing、secret redaction。
- Create: `tests/unit/test_outlook_oauth.py` — state transitions、code capture、proxy injection。
- Create: `tests/unit/test_outlook_artifacts.py` — private/public artifact allow-list 和 0600 sink。
- Modify: `register_core/providers/registry.py` — outlook factory 和 microsoft/hotmail/msa aliases。
- Modify: `register_core/email/registry.py` — `temp_mail`/`cf_temp` aliases。
- Modify: `register_core/contracts.py` — outlook public artifact keys和错误映射。
- Modify: `register_core/config/schema.py` — concrete Outlook `ProviderSpec.options` validation/normalization helper（不破坏通用 options）。
- Modify: `apps/control_api/schemas.py` — `product="outlook"`。
- Modify: `apps/control_api/routes_ops.py`/相关 import-export route — Outlook own glob/字段校验。
- Modify: `scripts/launch_batch_supervisor.sh` — Outlook marker/glob branch。
- Modify: `README.md` — grok-register 中的第二处 MIT attribution 和安全/live gate说明。

---

### Task 1: slidex HOLD model and independent provider boundary

**Files:**
- Modify: `/tmp/slidex/slidex/vision/models.py:20-35,67-86`
- Create: `/tmp/slidex/slidex/providers/hold.py`
- Modify: `/tmp/slidex/slidex/providers/__init__.py:222-341`
- Create: `/tmp/slidex/tests/unit/test_hold_provider_registry.py`
- Modify: `/tmp/slidex/README.md`

**Interfaces:**
- Produces `ChallengeType.HOLD = "hold"`.
- Produces the canonical `HoldSolveResult` dataclass in `slidex.vision.models`, re-exported by the hold modules.
- Produces `HoldCaptchaProvider(ABC)` with `name`, `manifest`, `detect(page) -> bool`, `solve(page, *, timeout_ms=30_000) -> HoldSolveResult`, and `on_cleanup() -> None`; it does not subclass `CaptchaProvider`.
- Produces `ProviderRegistry.register_hold(name, provider_class)`, `get_hold(name)`, `find_hold_providers(challenge_type=ChallengeType.HOLD, context=VisionContext.CDP)` while leaving `register/get/auto_detect` slider behavior intact.

- [ ] **Step 1: Write the failing registry and type tests.**

```python
# /tmp/slidex/tests/unit/test_hold_provider_registry.py
from dataclasses import dataclass

import pytest

from slidex.providers import CaptchaProvider, ProviderRegistry
from slidex.providers.hold import HoldCaptchaProvider, HoldSolveResult
from slidex.vision import ChallengeType, ProviderManifest, VisionContext


def test_hold_is_a_distinct_challenge_type():
    assert ChallengeType.HOLD.value == "hold"


def test_hold_provider_does_not_inherit_slider_provider():
    assert not issubclass(HoldCaptchaProvider, CaptchaProvider)


class FakeHoldProvider(HoldCaptchaProvider):
    name = "fake-hold"
    manifest = ProviderManifest(
        name=name,
        version="0.1.0",
        challenge_types=[ChallengeType.HOLD],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=True,
        produces_artifacts=["hold_telemetry"],
    )

    async def detect(self, page):
        return True

    async def solve(self, page, *, timeout_ms=30_000):
        return HoldSolveResult(success=True, error_code=None, retryable=False)


@pytest.mark.asyncio
async def test_hold_registry_filters_by_manifest():
    ProviderRegistry.register_hold(FakeHoldProvider.name, FakeHoldProvider)
    assert ProviderRegistry.get_hold("fake-hold").name == "fake-hold"
    assert ProviderRegistry.find_hold_providers(
        challenge_type=ChallengeType.HOLD,
        context=VisionContext.CDP,
    ) == ["fake-hold"]
```

- [ ] **Step 2: Run the focused test and verify it fails.**

Run in `/tmp/slidex`:

```bash
.venv/bin/python -m pytest tests/unit/test_hold_provider_registry.py -q
```

Expected: FAIL because `ChallengeType.HOLD`, `HoldCaptchaProvider`, `HoldSolveResult`, and hold registry methods do not exist.

- [ ] **Step 3: Add the minimal model and independent hold registry.**

Add the enum member:

```python
class ChallengeType(str, Enum):
    SLIDER_CAPTCHA = "slider_captcha"
    OCR_TEXT = "ocr_text"
    IMAGE_TEXT = "image_text"
    VISUAL_ELEMENT = "visual_element"
    MANUAL_FALLBACK = "manual_fallback"
    HOLD = "hold"
```

Add the canonical result type to `slidex/vision/models.py`, next to the existing vision result dataclasses:

```python
@dataclass(frozen=True)
class HoldSolveResult:
    success: bool
    error_code: str | None = None
    retryable: bool = False
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
```

Create `slidex/providers/hold.py` with the independent interface. It imports `HoldSolveResult` from the vision model and re-exports that name; it does not define a second result type:

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.async_api import Page

from slidex.vision.models import (
    ChallengeType,
    HoldSolveResult,
    ProviderManifest,
    VisionContext,
)


class HoldCaptchaProvider(ABC):
    """Independent hold/press CAPTCHA contract; no slider image/trajectory API."""

    name = "hold-base"
    manifest = ProviderManifest(
        name=name,
        version="0.1.0",
        challenge_types=[ChallengeType.HOLD],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=True,
        produces_artifacts=["hold_telemetry"],
    )

    @abstractmethod
    async def detect(self, page: Page) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def solve(self, page: Page, *, timeout_ms: int = 30_000) -> HoldSolveResult:
        raise NotImplementedError

    async def on_cleanup(self) -> None:
        return None
```

Add a separate `_hold_providers` dictionary and these methods to `ProviderRegistry`; do not widen the existing slider `get()` return contract:

```python
_hold_providers: Dict[str, Type[HoldCaptchaProvider]] = {}

@classmethod
def register_hold(cls, name: str, provider_class: Type[HoldCaptchaProvider]) -> None:
    with cls._lock:
        cls._hold_providers[name] = provider_class

@classmethod
def get_hold(cls, name: str) -> HoldCaptchaProvider:
    if name not in cls._hold_providers:
        raise ValueError(
            f"Unknown hold provider '{name}'. "
            f"Available: {', '.join(cls._hold_providers)}"
        )
    return cls._hold_providers[name]()

@classmethod
def find_hold_providers(
    cls, *, challenge_type: ChallengeType, context: VisionContext
) -> List[str]:
    return [
        name for name, provider_class in cls._hold_providers.items()
        if provider_class.manifest.supports(challenge_type, context)
    ]
```

Export `HoldCaptchaProvider` and `HoldSolveResult` from `slidex.providers`, and import `ProviderManifest` from the existing vision model only. Add the source mark to `README.md`:

```markdown
### Hold solver attribution

The hold/press CAPTCHA flow is adapted from `daimon3332/OutlookRegister`
(daimon3332, MIT License): https://github.com/daimon3332/OutlookRegister
```

- [ ] **Step 4: Run the focused tests and verify they pass without changing slider tests.**

```bash
.venv/bin/python -m pytest tests/unit/test_hold_provider_registry.py tests/unit/test_provider_registry.py -q
```

Expected: all selected tests pass; existing slider registry tests retain their previous result.

- [ ] **Step 5: Commit the isolated slidex boundary.**

```bash
git add slidex/vision/models.py slidex/providers/hold.py slidex/providers/__init__.py tests/unit/test_hold_provider_registry.py README.md
git commit -m "feat(slidex): add independent hold captcha provider boundary"
```

---

### Task 2: slidex HoldSolver and `VisualChallengeSolver` dispatch

**Files:**
- Create: `/tmp/slidex/slidex/vision/hold.py`
- Modify: `/tmp/slidex/slidex/vision/solver.py:20-127`
- Modify: `/tmp/slidex/slidex/vision/__init__.py`
- Create: `/tmp/slidex/tests/unit/test_hold_solver.py`

**Interfaces:**
- Produces `HoldSolver.solve_on_existing_page(cdp_endpoint: str, page_url: str, *, timeout_ms: int = 30_000) -> HoldSolveResult` and `solve_on_page(page: Any, page_url: str, *, timeout_ms: int = 30_000) -> HoldSolveResult`.
- `VisualChallengeSolver.__init__` gains optional `hold_solver_factory`; `solve()` dispatches `ChallengeType.HOLD` to `_solve_hold`.
- The hold implementation ports the reference mechanics as hold-only behavior: `_wait_for_captcha_frame`, `_human_prelude`, `_natural_move`, `_find_target`, `_pick_position`, `_hold_and_wait`, `_circular_tremor`, `_pick_b2mode`, `_execute_b2`, `_check_captcha_result`. It never accepts or emits slider gap/trajectory arguments.

- [ ] **Step 1: Write deterministic failing tests for all result classes.**

```python
# /tmp/slidex/tests/unit/test_hold_solver.py
from dataclasses import dataclass

import pytest

from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
)
from slidex.vision.hold import HoldSolveResult, HoldSolver


class FakeHoldSolver:
    def __init__(self, **kwargs):
        self.requests = []

    async def solve_on_existing_page(self, *, cdp_endpoint, page_url, timeout_ms):
        self.requests.append((cdp_endpoint, page_url, timeout_ms))
        return HoldSolveResult(success=True, confidence=0.91)

    async def solve_on_page(self, page, *, page_url, timeout_ms):
        return HoldSolveResult(success=False, error_code="captcha_retry", retryable=True)


@pytest.mark.asyncio
async def test_visual_solver_dispatches_hold_over_cdp():
    made = []

    def factory(**kwargs):
        solver = FakeHoldSolver(**kwargs)
        made.append(solver)
        return solver

    result = await VisualChallengeSolver(hold_solver_factory=factory).solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.HOLD,
            context=VisionContext.CDP,
            cdp_endpoint="ws://synthetic-cdp",
            page_url="https://synthetic.invalid/hold",
        )
    )

    assert result.success is True
    assert result.challenge_type is ChallengeType.HOLD
    assert result.confidence == 0.91
    assert made[0].requests == [("ws://synthetic-cdp", "https://synthetic.invalid/hold", 30_000)]


@pytest.mark.asyncio
async def test_visual_solver_rejects_hold_without_page_or_cdp():
    result = await VisualChallengeSolver().solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.HOLD,
            context=VisionContext.IMAGE_BYTES,
            image_bytes=b"synthetic",
        )
    )
    assert result.success is False
    assert result.error_code == "unsupported_hold_context"
    assert result.retryable is False
```

- [ ] **Step 2: Run the tests to verify failure.**

```bash
.venv/bin/python -m pytest tests/unit/test_hold_solver.py -q
```

Expected: FAIL because `slidex.vision.hold` and the HOLD dispatch are absent.

- [ ] **Step 3: Implement the hold state machine with injectable browser connection.**

Create `slidex/vision/hold.py` with this complete public shape and concrete flow:

```python
from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Any, Awaitable, Callable

from playwright.async_api import Page

from slidex.vision.models import HoldSolveResult

# Keep HoldSolveResult in this module namespace as a re-export of the canonical model.


class HoldSolver:
    """Solve Outlook's nested-frame press/hold challenge only."""

    def __init__(
        self,
        *,
        connect_over_cdp: Callable[[str], Awaitable[Any]] | None = None,
        max_retries: int = 3,
        rng: random.Random | None = None,
    ) -> None:
        self._connect_over_cdp = connect_over_cdp
        self._max_retries = max(1, max_retries)
        self._rng = rng or random.Random()

    async def solve_on_existing_page(
        self, *, cdp_endpoint: str, page_url: str, timeout_ms: int = 30_000
    ) -> HoldSolveResult:
        if not cdp_endpoint:
            return HoldSolveResult(False, "missing_cdp_endpoint", False)
        connector = self._connect_over_cdp
        if connector is None:
            from patchright.async_api import async_playwright

            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(cdp_endpoint)
                try:
                    try:
                        page = await self._page_for_url(browser, page_url)
                    except RuntimeError as exc:
                        if str(exc) == "cdp_no_page":
                            return HoldSolveResult(False, "captcha_frame_missing", True)
                        raise
                    return await self.solve_on_page(page, page_url=page_url, timeout_ms=timeout_ms)
                finally:
                    await browser.close()
        browser = await connector(cdp_endpoint)
        try:
            page = await self._page_for_url(browser, page_url)
        except RuntimeError as exc:
            if str(exc) == "cdp_no_page":
                return HoldSolveResult(False, "captcha_frame_missing", True)
            raise
        return await self.solve_on_page(page, page_url=page_url, timeout_ms=timeout_ms)

    async def solve_on_page(
        self, page: Page, *, page_url: str, timeout_ms: int = 30_000
    ) -> HoldSolveResult:
        deadline = time.monotonic() + timeout_ms / 1000
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, self._max_retries + 1):
            if time.monotonic() >= deadline:
                return HoldSolveResult(False, "hold_timeout", True, metadata={"attempts": attempts})
            frame = await self._wait_for_captcha_frame(page, deadline)
            if frame is None:
                return HoldSolveResult(False, "captcha_frame_missing", True, metadata={"attempts": attempts})
            status = await self._solve_attempt(frame, deadline)
            attempts.append({"attempt": attempt, "status": status})
            if status == "passed":
                return HoldSolveResult(True, confidence=1.0, metadata={"attempts": attempts})
            if status == "ip_blocked":
                return HoldSolveResult(False, "ip_blocked", False, metadata={"attempts": attempts})
            if status == "timeout":
                return HoldSolveResult(False, "hold_timeout", True, metadata={"attempts": attempts})
        return HoldSolveResult(False, "captcha_retry", True, metadata={"attempts": attempts})

    async def _page_for_url(self, browser: Any, page_url: str) -> Any:
        pages = [page for context in browser.contexts for page in context.pages]
        if not pages:
            raise RuntimeError("cdp_no_page")
        for page in pages:
            if page_url and page.url.startswith(page_url):
                return page
        return pages[0]

    async def _wait_for_captcha_frame(self, page: Any, deadline: float) -> Any | None:
        while time.monotonic() < deadline:
            outer = page.frame_locator('iframe[title="验证质询"]')
            inner = outer.frame_locator('iframe[style*="display: block"]')
            try:
                if await inner.locator("body").count():
                    return inner
            except Exception:
                await asyncio.sleep(0.2)
                continue
            await asyncio.sleep(0.2)
        return None

    async def _solve_attempt(self, frame: Any, deadline: float) -> str:
        if await frame.locator("iframe#enforcementFrame").count():
            return "ip_blocked"
        if await frame.get_by_text("一些异常活动", exact=False).count():
            return "ip_blocked"
        if await frame.get_by_text("此站点正在维护", exact=False).count():
            return "ip_blocked"
        await self._human_prelude(frame)
        target = await self._find_target(frame)
        if target is None:
            return "timeout"
        position = self._pick_position(target)
        await self._natural_move(frame, position)
        await self._hold_and_wait(frame, target, deadline)
        await self._execute_b2(frame)
        return await self._check_captcha_result(frame, deadline)

    async def _human_prelude(self, frame: Any) -> None:
        await asyncio.sleep(0.05 + self._rng.random() * 0.15)

    def _pick_position(self, target: dict[str, float]) -> tuple[float, float]:
        bucket = self._rng.random()
        if bucket < 0.12:
            return target["cx"], target["cy"]
        if bucket < 0.30:
            return target["left"] + 2, target["cy"]
        if bucket < 0.48:
            return target["right"] - 2, target["cy"]
        if bucket < 0.66:
            return target["left"] + 2, target["top"] + 2
        if bucket < 0.84:
            return target["right"] - 2, target["bottom"] - 2
        return (
            target["left"] + self._rng.random() * (target["right"] - target["left"]),
            target["top"] + self._rng.random() * (target["bottom"] - target["top"]),
        )

    async def _natural_move(self, frame: Any, position: tuple[float, float]) -> None:
        mouse = frame.page.mouse
        x, y = position
        await mouse.move(x - 20, y - 8, steps=4)
        await mouse.move(x + 5, y + 2, steps=7)
        await mouse.move(x, y, steps=3)

    async def _find_target(self, frame: Any) -> dict[str, float] | None:
        locator = frame.locator(".sc-jTzLTM, button, [role='button']").first
        try:
            box = await locator.bounding_box()
        except Exception:
            box = None
        if not box:
            return None
        return {
            "left": box["x"], "top": box["y"],
            "right": box["x"] + box["width"],
            "bottom": box["y"] + box["height"],
            "cx": box["x"] + box["width"] / 2,
            "cy": box["y"] + box["height"] / 2,
        }

    async def _hold_and_wait(self, frame: Any, target: dict[str, float], deadline: float) -> None:
        mouse = frame.page.mouse
        x, y = target["cx"], target["cy"]
        await mouse.dblclick(x, y, delay=40)
        await mouse.down()
        try:
            duration_ms = min(4000, max(800, int((deadline - time.monotonic()) * 1000)))
            steps = max(1, duration_ms // 50)
            for step in range(steps):
                if time.monotonic() >= deadline:
                    break
                angle = step * 2 * math.pi / steps
                radius = 0.3 + self._rng.random() * 1.7
                await mouse.move(x + math.cos(angle) * radius, y + math.sin(angle) * radius)
                await asyncio.sleep(0.05)
        finally:
            await mouse.up()

    def _pick_b2mode(self) -> str:
        return "dblclick" if self._rng.random() < 0.35 else "click"

    async def _execute_b2(self, frame: Any) -> None:
        button = frame.get_by_text("再次按下", exact=False).first
        if await button.count():
            if self._pick_b2mode() == "dblclick":
                await button.dblclick(delay=40)
            else:
                await button.click()

    async def _check_captcha_result(self, frame: Any, deadline: float) -> str:
        while time.monotonic() < deadline:
            if not await frame.locator(".draw").count():
                return "passed"
            if await frame.get_by_text("一些异常活动", exact=False).count():
                return "ip_blocked"
            await asyncio.sleep(0.15)
        return "timeout"
```

The implementation must preserve the reference's hold-only order and named helper boundaries, but the random values above are testable and bounded; no slider trajectory is introduced. Add the attribution comment at the top of this file:

```python
# Portions adapted from daimon3332/OutlookRegister (MIT License):
# https://github.com/daimon3332/OutlookRegister
```

- [ ] **Step 4: Add HOLD dispatch and safe result conversion.**

Extend `VisualChallengeSolver.__init__`:

```python
def __init__(self, *, ocr_extractor=None, slider_solver_factory=None, hold_solver_factory=None):
    self.ocr_extractor = ocr_extractor or FakeOcrExtractor()
    self.slider_solver_factory = slider_solver_factory or SliderSolver
    self.hold_solver_factory = hold_solver_factory or HoldSolver
```

Add this branch immediately after the slider branch in `solve()`:

```python
if request.challenge_type == ChallengeType.HOLD:
    return await self._solve_hold(request, started)
```

Add this method:

```python
async def _solve_hold(self, request, started):
    solver = self.hold_solver_factory()
    try:
        if request.context == VisionContext.CDP:
            if not request.cdp_endpoint:
                return VisualChallengeResult(
                    success=False, challenge_type=request.challenge_type,
                    provider=request.provider, duration_ms=self._duration_ms(started),
                    error_code="unsupported_hold_context", retryable=False,
                )
            solved = await solver.solve_on_existing_page(
                cdp_endpoint=request.cdp_endpoint,
                page_url=request.page_url,
                timeout_ms=request.timeout_ms,
            )
        elif request.context == VisionContext.PLAYWRIGHT_PAGE and request.page is not None:
            solved = await solver.solve_on_page(
                request.page, page_url=request.page_url, timeout_ms=request.timeout_ms
            )
        else:
            return VisualChallengeResult(
                success=False, challenge_type=request.challenge_type,
                provider=request.provider, duration_ms=self._duration_ms(started),
                error_code="unsupported_hold_context", retryable=False,
            )
        return VisualChallengeResult(
            success=solved.success, challenge_type=request.challenge_type,
            provider=request.provider, confidence=solved.confidence,
            duration_ms=self._duration_ms(started), error_code=solved.error_code,
            retryable=solved.retryable, metadata=redact_sensitive(solved.metadata),
        )
    finally:
        close = getattr(solver, "close", None)
        if close:
            maybe = close()
            if inspect.isawaitable(maybe):
                await maybe
```

Import `HoldSolveResult` eagerly with the other model exports and add lazy access for `HoldSolver` in `vision/__init__.py` without eagerly importing the existing solver:

```python
# vision/__init__.py
from slidex.vision.models import HoldSolveResult

__all__ += ["HoldSolveResult", "HoldSolver"]


def __getattr__(name):
    if name == "HoldSolver":
        from slidex.vision.hold import HoldSolver
        return HoldSolver
    if name == "VisualChallengeSolver":
        from slidex.vision.solver import VisualChallengeSolver
        return VisualChallengeSolver
    raise AttributeError(name)
```

`slidex.vision.hold` and `slidex.providers.hold` both import and re-export the one `HoldSolveResult` from `vision.models`. `redact_sensitive` must process hold metadata before it becomes a result.

- [ ] **Step 5: Run unit tests and existing vision regression tests.**

```bash
.venv/bin/python -m pytest tests/unit/test_hold_solver.py tests/unit/test_hold_provider_registry.py tests/unit/test_vision_solver.py -q
```

Expected: PASS; existing OCR/slider tests remain unchanged.

- [ ] **Step 6: Commit the solver.**

```bash
git add slidex/vision/hold.py slidex/vision/solver.py slidex/vision/__init__.py tests/unit/test_hold_solver.py
 git commit -m "feat(slidex): dispatch hold challenges through vision solver"
```

---

### Task 3: slidex CDP attach verification and hold outcome tests

**Files:**
- Modify: `/tmp/slidex/slidex/vision/hold.py`
- Create: `/tmp/slidex/tests/unit/test_hold_outcomes.py`
- Create: `/tmp/slidex/tests/integration/test_hold_cdp_attach.py`

**Interfaces:**
- Produces stable error codes: `captcha_frame_missing`, `captcha_retry`, `ip_blocked`, `hold_timeout`, `missing_cdp_endpoint`.
- The gate-protected CDP integration probe may assert only the booleans `connected`, `page_found`, `nested_frame_found`, and `coordinates_consistent`; it never records endpoint, URL query, cookies, or page content.
- The integration test runs only when `GROK_REGISTER_OUTLOOK_LIVE=1`; default CI skips it.

- [ ] **Step 1: Write outcome tests using fake nested frame objects.**

```python
# /tmp/slidex/tests/unit/test_hold_outcomes.py
import pytest

from slidex.vision.hold import HoldSolver


class FakeCount:
    def __init__(self, value): self.value = value
    def __await__(self):
        async def get(): return self.value
        return get().__await__()


class FakeText:
    def __init__(self, value): self.value = value
    async def count(self): return self.value


class FakeFrame:
    def __init__(self, *, enforcement=0, abnormal=0, maintenance=0, draw=1):
        self.enforcement = enforcement
        self.abnormal = abnormal
        self.maintenance = maintenance
        self.draw = draw
    def locator(self, selector):
        if selector == "iframe#enforcementFrame": return FakeText(self.enforcement)
        if selector == ".draw": return FakeText(self.draw)
        return FakeText(0)
    def get_by_text(self, text, exact=False):
        return FakeText(self.abnormal if "异常" in text else self.maintenance)


@pytest.mark.asyncio
async def test_fun_captcha_is_classified_as_ip_blocked():
    solver = HoldSolver()
    assert await solver._solve_attempt(FakeFrame(enforcement=1), 10**12) == "ip_blocked"


@pytest.mark.asyncio
async def test_abnormal_activity_is_classified_as_ip_blocked():
    solver = HoldSolver()
    assert await solver._solve_attempt(FakeFrame(abnormal=1), 10**12) == "ip_blocked"


@pytest.mark.asyncio
async def test_maintenance_is_classified_as_ip_blocked():
    solver = HoldSolver()
    assert await solver._solve_attempt(FakeFrame(maintenance=1), 10**12) == "ip_blocked"
```

- [ ] **Step 2: Run the outcome tests and verify the missing/incorrect behavior.**

```bash
.venv/bin/python -m pytest tests/unit/test_hold_outcomes.py -q
```

Expected: FAIL until the outcome classifier is independently callable and recognizes all three sentinel conditions.

- [ ] **Step 3: Add the remaining explicit outcome classification.**

In `_solve_attempt`, keep the exact order below before any pointer action:

```python
if await frame.locator("iframe#enforcementFrame").count():
    return "ip_blocked"
if await frame.get_by_text("一些异常活动", exact=False).count():
    return "ip_blocked"
if await frame.get_by_text("此站点正在维护", exact=False).count():
    return "ip_blocked"
```

In `_check_captcha_result`, treat a detached `.draw` as `passed`, the visible maintenance/abnormal text as `ip_blocked`, and deadline expiry as `timeout`; all returned metadata must be passed through `redact_sensitive`.

- [ ] **Step 4: Add a gate-protected CDP probe.**

```python
# /tmp/slidex/tests/integration/test_hold_cdp_attach.py
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("GROK_REGISTER_OUTLOOK_LIVE") != "1",
    reason="requires explicit authorized Outlook live gate",
)


@pytest.mark.asyncio
async def test_cdp_attach_reaches_same_nested_frame():
    patchright = pytest.importorskip("patchright.async_api")
    endpoint = os.environ.get("OUTLOOK_CDP_ENDPOINT")
    page_url = os.environ.get("OUTLOOK_CDP_PAGE_URL")
    if not endpoint or not page_url:
        pytest.skip("OUTLOOK_CDP_ENDPOINT and OUTLOOK_CDP_PAGE_URL are required")
    # The probe uses a synthetic test account/session supplied by the operator.
    # It records only structural booleans and never prints endpoint or page data.
    observed = {
        "connected": False,
        "page_found": False,
        "nested_frame_found": False,
        "coordinates_consistent": False,
    }
    async with patchright.async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(endpoint)
        observed["connected"] = True
        try:
            pages = [page for context in browser.contexts for page in context.pages]
            page = next((item for item in pages if item.url.startswith(page_url)), None)
            if page is None:
                pytest.fail("CDP connected but the requested page was not found")
            observed["page_found"] = True
            nested = page.frame_locator('iframe[title="验证质询"]').frame_locator(
                'iframe[style*="display: block"]'
            )
            observed["nested_frame_found"] = await nested.locator("body").count() > 0
            if observed["nested_frame_found"]:
                box = await nested.locator("button, [role='button']").first.bounding_box()
                observed["coordinates_consistent"] = bool(
                    box and box["width"] > 0 and box["height"] > 0
                )
        finally:
            await browser.close()
    assert observed == {
        "connected": True,
        "page_found": True,
        "nested_frame_found": True,
        "coordinates_consistent": True,
    }
```

- [ ] **Step 5: Run default tests and record the live test as skipped.**

```bash
.venv/bin/python -m pytest tests/unit/test_hold_outcomes.py tests/integration/test_hold_cdp_attach.py -q
```

Expected: unit tests PASS and the CDP test is SKIPPED unless the operator deliberately sets the gate and supplies a test endpoint.

- [ ] **Step 6: Commit the validation boundary.**

```bash
git add slidex/vision/hold.py tests/unit/test_hold_outcomes.py tests/integration/test_hold_cdp_attach.py
git commit -m "test(slidex): verify hold outcomes and gated CDP attach"
```

---

### Task 4: grok-register dependency, provider registry, and configuration contracts

**Files:**
- Modify: `pyproject.toml:10-24`
- Create: `register_core/providers/outlook_adapter.py` — minimal importable provider contract; Task 5 extends this file with browser execution.
- Modify: `register_core/providers/registry.py:22-53`
- Modify: `register_core/config/schema.py:9-13`
- Modify: `register_core/contracts.py:12-35,243-280`
- Create: `tests/unit/test_outlook_provider_registry.py`

**Interfaces:**
- `get_provider` returns `OutlookProvider` for each of the names `outlook`, `microsoft`, `hotmail`, and `msa`.
- `ProviderSpec(name="outlook", options=outlook_options)` accepts `client_id`, `captcha_strategy`, `temp_mail`, `email_suffix`, `bind_recovery_email`, `outlook_auths_dir`, and `headless`; secret values are rejected when supplied directly rather than by environment-backed config.
- Outlook errors normalize to existing kinds: `ip_blocked`/`fun_captcha` → `captcha` or `proxy` according to retry action; `oauth_callback` and `token` remain public kinds.

- [ ] **Step 1: Write failing registry/config tests.**

```python
# tests/unit/test_outlook_provider_registry.py
import pytest

from register_core.config.schema import ProviderSpec
from register_core.providers.registry import get_provider


def test_outlook_aliases_resolve_to_the_same_provider():
    providers = [get_provider(name, config={}) for name in ("outlook", "microsoft", "hotmail", "msa")]
    assert {provider.name for provider in providers} == {"outlook"}


def test_outlook_options_are_non_secret_config():
    spec = ProviderSpec(
        name="outlook",
        options={
            "client_id": "synthetic-client-id",
            "captcha_strategy": 2,
            "temp_mail": {"base_url": "https://mail.invalid", "domain": "invalid"},
            "email_suffix": "@outlook.com",
            "bind_recovery_email": False,
            "outlook_auths_dir": "./outlook-auths",
        },
    )
    assert spec.name == "outlook"
    assert spec.options["captcha_strategy"] == 2


def test_outlook_options_reject_inline_secret_values():
    spec = ProviderSpec(name="outlook", options={"password": "synthetic-password"})
    with pytest.raises(ValueError, match="secret"):
        spec.outlook_options()


def test_outlook_options_reject_nested_temp_mail_secret_values():
    spec = ProviderSpec(
        name="outlook",
        options={"temp_mail": {"admin_password": "synthetic-admin"}},
    )
    with pytest.raises(ValueError, match="temp_mail.admin_password"):
        spec.outlook_options()
```

- [ ] **Step 2: Run the test to verify failure.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_provider_registry.py -q
```

Expected: FAIL because the Outlook factory and aliases are absent and importing the adapter is not yet possible.

- [ ] **Step 3: Add dependencies and registry routing.**

Add version-pinned compatible lower bounds to `pyproject.toml`:

```toml
dependencies = [
    "DrissionPage>=4.1",
    "curl_cffi>=0.7",
    "PyYAML>=6.0",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "python-multipart>=0.0.9",
    "itsdangerous>=2.2",
    "patchright>=1.52",
    "slidex>=0.5.0",
]
```

In `registry.py`, import `OutlookProvider` inside `_ensure_builtins` and add:

```python
from register_core.providers.outlook_adapter import OutlookProvider

built = {
    "grok": lambda **kw: GrokProvider(**kw),
    "mimo": lambda **kw: MimoProvider(**kw),
    "chatgpt": lambda **kw: ChatGPTProvider(**kw),
    "outlook": lambda **kw: OutlookProvider(**kw),
}
```

Extend aliases exactly:

```python
aliases = {
    "xai": "grok", "xiaomi": "mimo", "mimo-tts": "mimo",
    "openai": "chatgpt", "openai-platform": "chatgpt",
    "chatgpt-oauth": "chatgpt",
    "microsoft": "outlook", "hotmail": "outlook", "msa": "outlook",
}
```

Create the importable safe provider contract before the registry factory is exercised:

```python
from __future__ import annotations

from typing import Any

from register_core.contracts import RegisterResult


class OutlookProvider:
    name = "outlook"

    def __init__(self, *, config: dict[str, Any] | None = None, **options: Any) -> None:
        self.config = {**(config or {}), **options}

    def register_one(self, *, email_source=None, extra=None) -> RegisterResult:
        return RegisterResult(
            ok=False,
            provider=self.name,
            error="outlook_components_unavailable",
            error_kind="provider",
            secret_kind="none",
        )
```

Task 5 replaces this safe contract in the same file with the gated browser-backed implementation; no test-only substitute is permitted.

Add `ProviderSpec.outlook_options()` in `config/schema.py`:

```python
def outlook_options(self) -> dict[str, Any]:
    if self.name.strip().lower() != "outlook":
        return {}
    options = dict(self.options)
    secret_keys = {
        "password", "secret", "token", "access_token", "refresh_token",
        "cookie", "cookies", "jwt", "authorization", "proxy", "proxy_url",
        "admin_password", "admin_password_value", "client_secret",
    }

    def find_inline_secrets(value: Any, path: str = "") -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key)
                key_path = f"{path}.{key_text}" if path else key_text
                if key_text.lower() in secret_keys:
                    found.append(key_path)
                else:
                    found.extend(find_inline_secrets(nested, key_path))
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                found.extend(find_inline_secrets(nested, f"{path}[{index}]"))
        return found

    inline_secrets = sorted(find_inline_secrets(options))
    if inline_secrets:
        raise ValueError(
            "outlook options cannot contain inline secret values: "
            + ",".join(inline_secrets)
        )
    strategy = int(options.get("captcha_strategy", 2))
    if strategy not in (0, 1, 2):
        raise ValueError("outlook captcha_strategy must be 0, 1, or 2")
    options["captcha_strategy"] = strategy
    options["email_suffix"] = str(options.get("email_suffix", "@outlook.com"))
    options["bind_recovery_email"] = bool(options.get("bind_recovery_email", True))
    options["headless"] = bool(options.get("headless", True))
    return options
```

Add `"outlook_steps"`, `"outlook_auth_path"`, `"recovery_email"`, and `"bound"` to `_public_artifacts` allow-list. Never add `password`, `refresh_token`, cookies, or proxy URL to that allow-list.

- [ ] **Step 4: Run focused tests and dependency metadata validation.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_provider_registry.py -q
.venv/bin/python -c 'import tomllib; d=tomllib.load(open("pyproject.toml","rb")); assert "patchright>=1.52" in d["project"]["dependencies"]; assert "slidex>=0.5.0" in d["project"]["dependencies"]'
```

Expected: PASS using the real safe provider contract created in this task; no test-only substitute is permitted. Task 5 later replaces its enabled-path behavior without changing the registry or alias contract.

- [ ] **Step 5: Commit contracts and routing.**

```bash
git add pyproject.toml register_core/providers/outlook_adapter.py register_core/providers/registry.py register_core/config/schema.py register_core/contracts.py tests/unit/test_outlook_provider_registry.py
git commit -m "feat(register): route outlook through provider contracts"
```

---

### Task 5: Outlook browser lifecycle and registration form adapter

**Files:**
- Create: `register_core/providers/outlook_browser.py`
- Modify: `register_core/providers/outlook_adapter.py` — replace the safe contract with the browser-backed provider implementation.
- Create: `tests/unit/test_outlook_browser.py`
- Create: `tests/unit/test_outlook_provider.py`
- Modify: `README.md`

**Interfaces:**
- `OutlookBrowserConfig.from_options(options: dict[str, Any]) -> OutlookBrowserConfig` reads only non-secret options and environment variable names for secrets.
- `OutlookBrowser.open(proxy: str | None) -> AsyncContextManager[BrowserSession]` launches patchright in-process and closes all browser resources in `finally`.
- `OutlookRegistrationFlow.register(page, email, password, *, captcha_strategy: int) -> RegistrationPageResult` performs the exact five form groups and risk checks.
- `OutlookProvider.register_one(*, email_source, extra) -> RegisterResult` is synchronous to satisfy `RegisterProvider`; it owns an `asyncio.run(self._register_one_async(email_source=email_source, extra=extra or {}))` boundary when no loop is running and rejects invocation from a running event loop with `error_kind="provider"` rather than nesting loops.

- [ ] **Step 1: Write failing browser/provider contract tests.**

```python
# tests/unit/test_outlook_browser.py
from register_core.providers.outlook_browser import OutlookBrowserConfig


def test_outlook_config_defaults_to_manual_captcha_gate():
    config = OutlookBrowserConfig.from_options({})
    assert config.captcha_strategy == 2
    assert config.email_suffix == "@outlook.com"
    assert config.locale == "zh-CN"
    assert config.timezone_id == "UTC"


def test_outlook_config_rejects_secret_values_before_normalization():
    import pytest

    with pytest.raises(ValueError, match="secret"):
        OutlookBrowserConfig.from_options({"password": "must-not-be-read"})
```

```python
# tests/unit/test_outlook_provider.py
from register_core.contracts import RegisterResult
from register_core.providers.outlook_adapter import OutlookProvider


def test_provider_has_required_protocol_shape():
    provider = OutlookProvider(config={})
    assert provider.name == "outlook"
    assert callable(provider.register_one)


def test_live_browser_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GROK_REGISTER_OUTLOOK_LIVE", raising=False)
    result = OutlookProvider(config={}).register_one(email_source=None, extra={})
    assert isinstance(result, RegisterResult)
    assert result.ok is False
    assert result.error_kind == "provider"
    assert "live gate" in result.error.lower()
```

- [ ] **Step 2: Run tests and verify failure.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_browser.py tests/unit/test_outlook_provider.py -q
```

Expected: FAIL because browser config, adapter, and live-gate behavior do not exist.

- [ ] **Step 3: Implement config and browser lifecycle.**

Create `outlook_browser.py` with this concrete data contract:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from register_core.config.schema import ProviderSpec


@dataclass(frozen=True, slots=True)
class OutlookBrowserConfig:
    client_id: str = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
    email_suffix: str = "@outlook.com"
    captcha_strategy: int = 2
    headless: bool = True
    locale: str = "zh-CN"
    timezone_id: str = "UTC"
    latitude: float | None = None
    longitude: float | None = None
    bind_recovery_email: bool = True
    temp_mail: dict[str, Any] | None = None
    outlook_auths_dir: str = "outlook_auths"

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> "OutlookBrowserConfig":
        normalized = ProviderSpec(
            name="outlook", options=dict(options)
        ).outlook_options()
        strategy = normalized["captcha_strategy"]
        return cls(
            client_id=str(normalized.get("client_id") or cls.client_id),
            email_suffix=str(normalized.get("email_suffix") or cls.email_suffix),
            captcha_strategy=strategy,
            headless=bool(normalized.get("headless", True)),
            locale="zh-CN",
            timezone_id=str(normalized.get("timezone_id") or "UTC"),
            latitude=normalized.get("latitude"), longitude=normalized.get("longitude"),
            bind_recovery_email=bool(normalized.get("bind_recovery_email", True)),
            temp_mail=dict(normalized.get("temp_mail") or {}),
            outlook_auths_dir=str(normalized.get("outlook_auths_dir") or "outlook_auths"),
        )
```

`OutlookBrowser` must import `STEALTH_LAUNCH_ARGS` and `STEALTH_INIT_SCRIPT` from installed slidex, append no new fingerprint tricks, pass `proxy` only as a patchright proxy object, and use `context = await browser.new_context(locale=config.locale, timezone_id=config.timezone_id, geolocation=geo, permissions=["geolocation"] when geo is complete)`. It must not log the proxy value. Use `async with async_playwright()` and `try/finally` around browser/context/page.

Add the reference attribution as a separate grok-register mark in `README.md`:

```markdown
### Outlook registration attribution

The Outlook registration, recovery, and OAuth flow is adapted from
`daimon3332/OutlookRegister` (daimon3332, MIT License):
https://github.com/daimon3332/OutlookRegister
```

- [ ] **Step 4: Implement form flow and risk result types.**

Use explicit selectors and return data-only results:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegistrationPageResult:
    ok: bool
    error_kind: str = ""
    error: str = ""
    captcha_frame_seen: bool = False
    fun_captcha_seen: bool = False


class OutlookRegistrationFlow:
    async def register(self, page, email: str, password: str, *, captcha_strategy: int):
        await page.goto("https://outlook.live.com/mail/0/?prompt=create_account")
        if await page.get_by_text("同意并继续", exact=True).count():
            await page.get_by_text("同意并继续", exact=True).click()
        if await page.locator('[role="option"]:text-is("@hotmail.com")').count():
            await page.locator('[role="option"]:text-is("@hotmail.com")').click()
        await page.locator('[aria-label="新建电子邮件"]').fill(email.split("@", 1)[0])
        await page.locator('[data-testid="primaryButton"]').click()
        await page.locator('[type="password"]').fill(password)
        for selector, value in (("#BirthYear", "1994"), ("#BirthMonth", "1"), ("#BirthDay", "1")):
            await page.locator(selector).select_option(value)
        await page.locator("#lastNameInput").fill("Test")
        await page.locator("#firstNameInput").fill("User")
        await page.locator('[data-testid="primaryButton"]').click()
        if await page.locator("iframe#enforcementFrame").count():
            return RegistrationPageResult(False, "captcha", "FunCaptcha enforcement frame", fun_captcha_seen=True)
        if await page.get_by_text("一些异常活动", exact=False).count():
            return RegistrationPageResult(False, "captcha", "abnormal activity")
        if await page.get_by_text("此站点正在维护", exact=False).count():
            return RegistrationPageResult(False, "captcha", "maintenance sentinel")
        captcha_seen = await page.locator('iframe[title="验证质询"]').count() > 0
        if captcha_seen and captcha_strategy == 2:
            return RegistrationPageResult(False, "captcha", "manual handoff", captcha_frame_seen=True)
        if captcha_seen:
            return RegistrationPageResult(False, "captcha", "captcha bridge required", captcha_frame_seen=True)
        return RegistrationPageResult(True)
```

The real implementation must retain the two birth selector fallbacks from the source (`select_option`, then click `[role="option"]:text-is("X月")`/`X日`) and wait for the registration completion link to detach before entering mailbox. It must classify `iframe#enforcementFrame`, abnormal activity, and maintenance before any captcha action.

- [ ] **Step 5: Implement the provider gate and error conversion.**

`OutlookProvider` must use `extra.get("proxy")`, not environment-only proxy discovery, and must reject live execution unless explicitly enabled:

```python
class OutlookProvider:
    name = "outlook"

    def __init__(self, *, config: dict[str, Any] | None = None, **options: Any) -> None:
        self.config = {**(config or {}), **options}

    def register_one(self, *, email_source=None, extra=None) -> RegisterResult:
        if os.environ.get("GROK_REGISTER_OUTLOOK_LIVE") != "1":
            return RegisterResult(
                ok=False, provider=self.name,
                error="Outlook live gate is disabled; set GROK_REGISTER_OUTLOOK_LIVE=1 for an authorized test",
                error_kind="provider", secret_kind="none",
            )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(
                    self._register_one_async(
                        email_source=email_source,
                        extra=extra or {},
                    )
                )
            except RuntimeError as exc:
                return RegisterResult(
                    ok=False,
                    provider=self.name,
                    error=str(exc),
                    error_kind="provider",
                    secret_kind="none",
                )
        return RegisterResult(
            ok=False,
            provider=self.name,
            error="Outlook provider cannot run synchronously inside an active event loop",
            error_kind="provider",
            secret_kind="none",
        )

    async def _register_one_async(self, *, email_source, extra):
        proxy = str(extra.get("proxy") or "")
        if not proxy:
            return RegisterResult(ok=False, provider=self.name, error="missing attempt proxy", error_kind="proxy", secret_kind="none")
        # Tasks 6–9 provide the concrete bridge, recovery, OAuth, and sink dependencies.
        return RegisterResult(
            ok=False,
            provider=self.name,
            error="outlook_components_unavailable",
            error_kind="provider",
            secret_kind="none",
        )
```

This is the bounded provider error for an enabled adapter whose staged dependencies have not been wired yet. Task 9 replaces this branch with the complete five-segment orchestration and retains the same public `RegisterResult` contract.

- [ ] **Step 6: Run unit tests and commit the browser/provider foundation.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_browser.py tests/unit/test_outlook_provider.py -q
```

Expected: PASS, including default live-gate refusal.

```bash
git add register_core/providers/outlook_browser.py register_core/providers/outlook_adapter.py tests/unit/test_outlook_browser.py tests/unit/test_outlook_provider.py README.md
git commit -m "feat(register): add gated outlook browser provider foundation"
```

---

### Task 6: Outlook captcha bridge with CDP and storage-state fallback

**Files:**
- Create: `register_core/providers/outlook_captcha.py`
- Modify: `register_core/providers/outlook_adapter.py`
- Create: `tests/unit/test_outlook_captcha.py`

**Interfaces:**
- `OutlookCaptchaBridge.solve(page, *, browser, page_url, timeout_ms) -> CaptchaBridgeResult`.
- It constructs exactly `VisualChallengeRequest(challenge_type=ChallengeType.HOLD, context=VisionContext.CDP, cdp_endpoint=endpoint, page_url=page_url)` for the preferred path.
- On a known coordinate/frame attach failure, it exports `storage_state`, creates a second context, invokes slidex against that context, closes it, and returns `fallback_used=True`; it never silently restarts the main context.

- [ ] **Step 1: Write failing bridge tests.**

```python
# tests/unit/test_outlook_captcha.py
import pytest

from register_core.providers.outlook_captcha import OutlookCaptchaBridge


class FakeResult:
    def __init__(self, success, error_code=None):
        self.success, self.error_code = success, error_code


@pytest.mark.asyncio
async def test_bridge_constructs_hold_cdp_request(monkeypatch):
    seen = {}
    async def solve(self, request):
        seen["request"] = request
        return FakeResult(True)
    browser = type("B", (), {"ws_endpoint": "ws://synthetic-cdp"})()
    bridge = OutlookCaptchaBridge(solver=type("S", (), {"solve": solve})())
    result = await bridge.solve(page=object(), browser=browser, page_url="https://x.invalid")
    assert result.ok is True
    assert seen["request"].challenge_type.value == "hold"
    assert seen["request"].context.value == "cdp"


@pytest.mark.asyncio
async def test_bridge_reports_retryable_failure_without_secrets():
    async def solve(self, request): return FakeResult(False, "captcha_retry")
    browser = type("B", (), {"ws_endpoint": "ws://synthetic-cdp"})()
    bridge = OutlookCaptchaBridge(solver=type("S", (), {"solve": solve})())
    result = await bridge.solve(page=object(), browser=browser, page_url="https://x.invalid")
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert "token" not in str(result.metadata).lower()
```

- [ ] **Step 2: Run the bridge tests and verify failure.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_captcha.py -q
```

Expected: FAIL because the bridge and request construction are absent.

- [ ] **Step 3: Implement the preferred CDP path and fallback contract.**

```python
from dataclasses import dataclass, field
from typing import Any

from slidex.vision import ChallengeType, VisionContext, VisualChallengeRequest, VisualChallengeSolver


@dataclass(frozen=True, slots=True)
class CaptchaBridgeResult:
    ok: bool
    error_kind: str = ""
    error: str = ""
    fallback_used: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class OutlookCaptchaBridge:
    def __init__(self, *, solver=None, endpoint_getter=None):
        self.solver = solver or VisualChallengeSolver()
        self.endpoint_getter = endpoint_getter

    async def solve(self, *, page, browser, page_url: str, timeout_ms: int = 30_000):
        endpoint = await self._endpoint(browser)
        request = VisualChallengeRequest(
            challenge_type=ChallengeType.HOLD,
            context=VisionContext.CDP,
            cdp_endpoint=endpoint,
            page_url=page_url,
            timeout_ms=timeout_ms,
            metadata={"site": "outlook"},
        )
        result = await self.solver.solve(request)
        if result.success:
            return CaptchaBridgeResult(True, metadata={"path": "cdp"})
        if result.error_code in {"captcha_frame_missing", "unsupported_hold_context"}:
            fallback = await self._solve_storage_state(page, browser, page_url, timeout_ms)
            return fallback
        return CaptchaBridgeResult(
            False,
            error_kind="captcha" if result.error_code != "ip_blocked" else "proxy",
            error=result.error_code or "captcha_failed",
            metadata={"path": "cdp", "retryable": result.retryable},
        )

    async def _endpoint(self, browser):
        if self.endpoint_getter:
            return await self.endpoint_getter(browser)
        endpoint = getattr(browser, "ws_endpoint", None)
        if callable(endpoint):
            return endpoint()
        value = getattr(browser, "ws_endpoint", "")
        return str(value or "")

    async def _solve_storage_state(self, page, browser, page_url, timeout_ms):
        state = await page.context.storage_state()
        context = await browser.new_context(storage_state=state)
        try:
            fallback_page = await context.new_page()
            await fallback_page.goto(page_url)
            request = VisualChallengeRequest(
                challenge_type=ChallengeType.HOLD,
                context=VisionContext.PLAYWRIGHT_PAGE,
                page=fallback_page,
                page_url=page_url,
                timeout_ms=timeout_ms,
                metadata={"site": "outlook", "path": "storage_state"},
            )
            result = await self.solver.solve(request)
            return CaptchaBridgeResult(
                result.success,
                error_kind="captcha" if not result.success else "",
                error=result.error_code or "",
                fallback_used=True,
                metadata={"path": "storage_state", "retryable": result.retryable},
            )
        finally:
            await context.close()
```

The endpoint getter must return only the endpoint to the solver and never log it. `storage_state` may contain cookies in memory but no result metadata or logs may include its content.

- [ ] **Step 4: Run tests and commit.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_captcha.py -q
```

Expected: PASS.

```bash
git add register_core/providers/outlook_captcha.py register_core/providers/outlook_adapter.py tests/unit/test_outlook_captcha.py
git commit -m "feat(register): bridge outlook hold captcha through slidex CDP"
```

---

### Task 7: CF Temp Mail source and recovery-email session

**Files:**
- Create: `register_core/email/sources/temp_mail.py`
- Modify: `register_core/email/registry.py:37-61`
- Create: `register_core/providers/outlook_recovery.py`
- Create: `tests/unit/test_temp_mail_source.py`
- Create: `tests/unit/test_outlook_recovery.py`

**Interfaces:**
- `TempMailClient(base_url, admin_password_env, domain, name_prefix="orx", enable_prefix=True, timeout=30)` reads admin password from the named environment variable; constructor never accepts or prints the password value.
- `TempMailSource.from_options(options: dict[str, Any]) -> TempMailSource`, `allocate() -> Mailbox`, `poll_otp(mailbox, *, timeout_s=180, poll_interval_s=3, used_codes=None, newer_than_epoch=None, sender_hint=None) -> OtpCode`, and `release(mailbox, *, success) -> None`, matching the existing `EmailSource` protocol and registered as `temp_mail` and `cf_temp`.
- `RecoverySession(mailbox: Mailbox, source: TempMailSource, bound: bool)` stores no raw message content.
- `bind_recovery_email(page, source, *, timeout_seconds=90) -> RecoverySession | None` and `verify_bound_email_on_login(page, session, *, timeout_seconds=90) -> bool` use `#EmailAddress`, `#iOttText`, `#codeEntry-0..5`, and `#idBtn_Back`.

- [ ] **Step 1: Write failing HTTP-mocked tests.**

```python
# tests/unit/test_temp_mail_source.py
import json

import httpx
import pytest

from register_core.email.sources.temp_mail import TempMailClient, TempMailSource


@pytest.mark.parametrize("body", [
    {"subject": "Security code", "text": "Use code: 123456"},
    {"subject": "验证码", "text": "验证码 654321"},
])
def test_extract_code_from_synthetic_mail(body):
    assert TempMailClient.extract_code_from_text(json.dumps(body)) in {"123456", "654321"}


def test_registry_source_has_no_password_in_redacted_mailbox(monkeypatch):
    source = TempMailSource(base_url="https://mail.invalid", admin_password_env="TEST_TEMP_MAIL_ADMIN", domain="invalid")
    monkeypatch.setenv("TEST_TEMP_MAIL_ADMIN", "synthetic-admin")
    mailbox = source._mailbox_from_payload({"address": "a@invalid", "jwt": "synthetic-jwt"})
    assert mailbox.redact() == {"address": "a@invalid", "provider": "cf_temp", "has_token": True, "meta_keys": []}
```

```python
# tests/unit/test_outlook_recovery.py
import pytest

from register_core.providers.outlook_recovery import RecoverySession, verify_bound_email_on_login


@pytest.mark.asyncio
async def test_bound_session_is_reused_without_allocating_new_address():
    calls = []
    class Source:
        def allocate(self): calls.append("allocate"); raise AssertionError("must reuse session")
        def poll_otp(self, *args, **kwargs): calls.append("poll"); return type("Otp", (), {"code": "123456"})()
    session = RecoverySession(mailbox=object(), source=Source(), bound=True)
    page = type("Page", (), {})()
    assert await verify_bound_email_on_login(page, session) is False  # fake page has no selector
    assert calls == []
```

- [ ] **Step 2: Run tests and verify failure.**

```bash
.venv/bin/python -m pytest tests/unit/test_temp_mail_source.py tests/unit/test_outlook_recovery.py -q
```

Expected: FAIL because TempMailSource, code parser, and recovery session do not exist.

- [ ] **Step 3: Port the CF Temp Mail client behind the EmailSource protocol.**

Use `httpx.Client` with these exact endpoint semantics and no raw response logging:

```python
class TempMailClient:
    def __init__(self, *, base_url, admin_password_env, domain, name_prefix="orx", enable_prefix=True, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.admin_password_env = admin_password_env
        self.domain = domain
        self.name_prefix = name_prefix
        self.enable_prefix = enable_prefix
        self.timeout = timeout

    @staticmethod
    def extract_code_from_text(text: str) -> str | None:
        for pattern in (r"(?i)(?:code|验证码|security code)[^0-9]{0,20}(\d{6})", r"\b(\d{6})\b"):
            match = re.search(pattern, text or "")
            if match:
                return match.group(1)
        return None

    def create_address(self) -> Mailbox:
        password = os.environ.get(self.admin_password_env, "")
        if not password:
            raise RuntimeError("temp_mail_admin_env_missing")
        response = httpx.post(
            f"{self.base_url}/admin/new_address",
            headers={"x-admin-auth": password},
            json={"domain": self.domain, "prefix": self.name_prefix if self.enable_prefix else ""},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return Mailbox(address=str(data["address"]), token=str(data["jwt"]), provider="cf_temp")

    def list_mails(self, mailbox: Mailbox) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.base_url}/api/mails",
            headers={"Authorization": f"Bearer {mailbox.token}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return list(payload if isinstance(payload, list) else payload.get("mails", []))
```

Implement `wait_for_code(*, mailbox: Mailbox, timeout_sec: float, poll_sec: float, after_ts: float | None, used_codes: set[str] | None = None, sender_hint: str | None = None) -> OtpCode` with a monotonic deadline, stale timestamp filtering, `extract_code_from_text`, and `MailMissError` on deadline. Build `TempMailSource` as an adapter returning `Mailbox`/`OtpCode` and expose the existing `EmailSource` signature:

```python
class TempMailSource:
    name = "cf_temp"

    def __init__(self, client: TempMailClient) -> None:
        self.client = client

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> "TempMailSource":
        return cls(
            TempMailClient(
                base_url=str(options.get("base_url") or ""),
                admin_password_env=str(options.get("admin_password_env") or "CF_TEMP_ADMIN_ENV"),
                domain=str(options.get("domain") or ""),
                name_prefix=str(options.get("name_prefix") or "orx"),
                enable_prefix=bool(options.get("enable_prefix", True)),
                timeout=float(options.get("timeout", 30)),
            )
        )

    def allocate(self) -> Mailbox:
        return self.client.create_address()

    def poll_otp(
        self, mailbox: Mailbox, *, timeout_s=180, poll_interval_s=3,
        used_codes=None, newer_than_epoch=None, sender_hint=None,
    ) -> OtpCode:
        return self.client.wait_for_code(
            mailbox=mailbox,
            timeout_sec=timeout_s,
            poll_sec=poll_interval_s,
            after_ts=newer_than_epoch,
            used_codes=used_codes,
            sender_hint=sender_hint,
        )

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        self.client.release(mailbox, success=success)
```

Register it with `register_email_source("temp_mail", TempMailSource)` and `register_email_source("cf_temp", TempMailSource)` in `_ensure_builtins`. Do not make `mail_assets` a fallback for this source.

- [ ] **Step 4: Implement recovery bind and session reuse.**

`bind_recovery_email` must detect `#EmailAddress`, allocate once, fill it, click the existing primary button, poll the same mailbox, fill six code cells, and return `RecoverySession(bound=True)`. If no protect page is present, return `None`. `verify_bound_email_on_login` must return `False` without allocating when `session.bound` is already true and the login page does not request proof; when it requests proof, poll `session.source` using `session.mailbox`.

Handle passkey prompt by clicking `#idBtn_Back` only when visible. Map missing/invalid code to `otp_invalid`, timeout to `mail_miss`, and never include message text in errors.

- [ ] **Step 5: Run tests and commit.**

```bash
.venv/bin/python -m pytest tests/unit/test_temp_mail_source.py tests/unit/test_outlook_recovery.py -q
```

Expected: PASS.

```bash
git add register_core/email/sources/temp_mail.py register_core/email/registry.py register_core/providers/outlook_recovery.py tests/unit/test_temp_mail_source.py tests/unit/test_outlook_recovery.py
git commit -m "feat(register): add isolated CF temp mail recovery source"
```

---

### Task 8: OAuth2 refresh-token state machine and proxy-aware token exchange

**Files:**
- Create: `register_core/providers/outlook_oauth.py`
- Modify: `register_core/providers/outlook_adapter.py`
- Create: `tests/unit/test_outlook_oauth.py`

**Interfaces:**
- `OutlookOAuthConfig(client_id, redirect_uri="https://localhost", scope="https://graph.microsoft.com/.default offline_access")`.
- `build_auth_url(prefer_sso: bool = True) -> str`.
- `OAuthStateMachine.run(page, full_email, password, *, proxy, recovery_session) -> OAuthTokenResult` drives the state machine and calls proof verification when requested.
- `_wait_for_code_capture(page, timeout_s) -> str | None` extracts only `code` from localhost callback/framenavigation and never logs it.
- `_exchange_code_once(code, redirect_uri, proxy_url) -> dict[str, Any]` uses `requests.post` with both `http` and `https` proxy entries.
- `exchange_code(code, *, proxy_url="") -> dict[str, Any]` performs the bounded public exchange helper used by tests and `run`.
- `get_refresh_token(page, full_email, password, *, proxy, recovery_session) -> OAuthTokenResult` returns `refresh_token` in memory only; adapter writes it only to private 0600 sink.

- [ ] **Step 1: Write failing state and redaction tests.**

```python
# tests/unit/test_outlook_oauth.py
from urllib.parse import parse_qs, urlparse

import pytest

from register_core.providers.outlook_oauth import OutlookOAuthConfig, OAuthStateMachine


def test_auth_url_contains_public_client_and_offline_scope():
    url = OutlookOAuthConfig().build_auth_url(prefer_sso=True)
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["9e5f94bc-e8a4-4e73-b8be-63364c29d753"]
    assert "offline_access" in query["scope"][0]
    assert query["redirect_uri"] == ["https://localhost"]


@pytest.mark.asyncio
async def test_code_capture_returns_code_but_not_full_callback():
    page = type("Page", (), {"url": "https://localhost/?code=synthetic-code&state=synthetic-state"})()
    machine = OAuthStateMachine(config=OutlookOAuthConfig())
    assert await machine._wait_for_code_capture(page, timeout_s=0.01) == "synthetic-code"


def test_exchange_proxy_is_applied_to_both_schemes(monkeypatch):
    seen = {}
    def post(url, **kwargs):
        seen.update(kwargs)
        return type("Response", (), {"raise_for_status": lambda self: None, "json": lambda self: {"refresh_token": "synthetic"}})()
    monkeypatch.setattr("register_core.providers.outlook_oauth.requests.post", post)
    result = OAuthStateMachine(config=OutlookOAuthConfig()).exchange_code("synthetic-code", proxy_url="http://synthetic.invalid")
    assert result["refresh_token"] == "synthetic"
    assert seen["proxies"] == {"http": "http://synthetic.invalid", "https": "http://synthetic.invalid"}
```

- [ ] **Step 2: Run the tests and verify failure.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_oauth.py -q
```

Expected: FAIL because OAuth config/state machine are absent.

- [ ] **Step 3: Implement constants, URL construction, state detection, and callback capture.**

Use the public client constants from the source, the exact selectors `#msaTile`, `#i0116`, `#idSIButton9`, `#EmailAddress`, `#codeEntry-0..5`, `secondaryButton`, and `[data-testid="appConsentPrimaryButton"]`. Implement `_current_auth_entry_state(page)` returning one of `consent`, `account_type`, `protect_account`, `proof_verify`, `kmsi`, `login_email`, `login_password`, `unknown`. Implement `build_auth_url` with `response_type=code`, `response_mode=query`, `scope`, and `prompt=none` only when `prefer_sso` is true; otherwise use the normal interactive prompt.

The callback parser must be:

```python
from urllib.parse import parse_qs, urlparse


def extract_code(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "localhost":
        return None
    return parse_qs(parsed.query).get("code", [None])[0]
```

`_wait_for_code_capture` first checks the current page URL, then polls navigation/page URL until deadline. It must not print URL, code, state, or cookies.

- [ ] **Step 4: Implement login/proof/KMSI/consent transitions and token exchange.**

Implement each state as an isolated async method. `account_type` clicks `#msaTile`; `login_email` fills `#i0116` and clicks `#idSIButton9`; password fills the password selector; protect/proof delegates to `verify_bound_email_on_login`; KMSI clicks the visible `secondaryButton` denial; consent clicks `[data-testid="appConsentPrimaryButton"]`. If a state is not recognized, return `oauth_callback` with a bounded public state name.

Implement exchange exactly:

```python
def exchange_code(self, code: str, *, proxy_url: str = "") -> dict[str, Any]:
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    response = requests.post(
        self.config.token_url,
        data={
            "client_id": self.config.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "scope": self.config.scope,
        },
        proxies=proxies,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("refresh_token"):
        raise ValueError("missing_refresh_token")
    return payload
```

Retry once with the pipeline proxy getter only when the first exchange raises a network/proxy exception; do not retry invalid authorization or missing refresh token. Return `OAuthTokenResult(ok, refresh_token, error_kind, state, metadata)` with token excluded from `metadata`.

- [ ] **Step 5: Run tests and commit.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_oauth.py -q
```

Expected: PASS; all test URLs and token values are synthetic.

```bash
git add register_core/providers/outlook_oauth.py register_core/providers/outlook_adapter.py tests/unit/test_outlook_oauth.py
git commit -m "feat(register): add outlook oauth refresh token state machine"
```

---

### Task 9: Complete `OutlookProvider` orchestration and private artifact sink

**Files:**
- Modify: `register_core/providers/outlook_adapter.py`
- Create: `tests/unit/test_outlook_artifacts.py`
- Modify: `register_core/contracts.py` if allow-list keys are incomplete

**Interfaces:**
- `OutlookProvider._register_one_async(*, email_source, extra) -> RegisterResult` derives one new Outlook address from the configured suffix, generates credentials in memory, runs browser registration, captcha bridge, mailbox/recovery flow, OAuth, and artifact writing in that order. It never uses the existing `email_source` as the primary account or recovery source.
- Before constructing `OutlookBrowser` or `TempMailSource`, it requires `extra["proxy"]` to be a non-empty string; otherwise it returns `_failure("proxy", "missing_attempt_proxy")` and performs no browser/network allocation.
- Successful `RegisterResult` has `provider="outlook"`, `email`, `password`, `secret_kind="refresh_token"`, `secret=refresh_token`, and artifacts containing only safe path/step metadata plus `recovery_email`/`bound`.
- Failure maps to `captcha`, `proxy`, `mail_miss`, `otp_invalid`, `oauth_callback`, `token`, `network`, or `provider`; it always closes browser resources and releases the Temp Mail mailbox owned by the recovery session.
- `_generate_account_email(suffix: str) -> str` creates a synthetic target address in memory; `_generate_password() -> str` creates one in-memory password with `secrets` and never logs it; `_failure(error_kind: str, error: str, artifacts: dict[str, Any] | None = None) -> RegisterResult` normalizes the public kind and returns a secret-free failure result.

- [ ] **Step 1: Write failing orchestration and sink tests.**

```python
# tests/unit/test_outlook_artifacts.py
import json
import os
import stat

from register_core.contracts import RegisterResult
from register_core.sink.jsonl_sink import JsonlSink


def test_private_outlook_sink_is_0600_and_public_record_is_redacted(tmp_path):
    path = tmp_path / "outlook.jsonl"
    result = RegisterResult(
        ok=True, provider="outlook", email="user@outlook.com",
        password="synthetic-password", secret="synthetic-refresh-token",
        secret_kind="refresh_token",
        artifacts={"outlook_auth_path": "outlook-auths/outlook-user.json", "bound": True},
    )
    JsonlSink(path).write(result)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    payload = json.loads(path.read_text())
    assert payload["secret"] == "synthetic-refresh-token"
    public = result.to_public_dict()
    assert "synthetic-refresh-token" not in json.dumps(public)
    assert public["artifacts"]["bound"] is True
```

```python
# Add to tests/unit/test_outlook_provider.py

def test_disabled_provider_never_allocates_mailbox(monkeypatch):
    monkeypatch.delenv("GROK_REGISTER_OUTLOOK_LIVE", raising=False)
    class Source:
        def allocate(self): raise AssertionError("must not allocate while disabled")
    result = OutlookProvider(config={}).register_one(email_source=Source(), extra={})
    assert result.error_kind == "provider"


def test_enabled_provider_rejects_missing_proxy_before_browser(monkeypatch):
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    monkeypatch.setattr(
        "register_core.providers.outlook_adapter.OutlookBrowser",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("browser must not start without attempt proxy")
        ),
    )
    result = OutlookProvider(config={}).register_one(
        email_source=None,
        extra={},
    )
    assert result.ok is False
    assert result.error_kind == "proxy"
    assert result.error == "missing_attempt_proxy"
```

- [ ] **Step 2: Run the tests and verify failure.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_artifacts.py tests/unit/test_outlook_provider.py -q
```

Expected: FAIL until artifact and orchestration behavior is present.

- [ ] **Step 3: Implement the five-segment orchestration.**

Use these provider-local helpers and this ordering inside `_register_one_async`:

```python
def _generate_account_email(self, suffix: str) -> str:
    local = "orx" + secrets.token_hex(6)
    return f"{local}{suffix}"


def _generate_password(self) -> str:
    return secrets.token_urlsafe(18)


def _failure(
    self, error_kind: str, error: str, artifacts: dict[str, Any] | None = None
) -> RegisterResult:
    return RegisterResult(
        ok=False,
        provider=self.name,
        error=error,
        error_kind=normalize_error_kind(error_kind),
        secret_kind="none",
        artifacts=dict(artifacts or {}),
    )


config = OutlookBrowserConfig.from_options(self.config)
proxy = extra.get("proxy")
if not isinstance(proxy, str) or not proxy.strip():
    return self._failure("proxy", "missing_attempt_proxy")
flow = OutlookRegistrationFlow()
captcha_bridge = OutlookCaptchaBridge()
oauth = OAuthStateMachine(config=OutlookOAuthConfig(client_id=config.client_id))
temp_source = (
    TempMailSource.from_options(config.temp_mail or {})
    if config.bind_recovery_email
    else None
)
recovery = None
success = False
try:
    email = self._generate_account_email(config.email_suffix)
    password = self._generate_password()
    async with OutlookBrowser(config).open(proxy=proxy) as session:
        registration = await flow.register(
            session.page,
            email,
            password,
            captcha_strategy=config.captcha_strategy,
        )
        if registration.fun_captcha_seen:
            return self._failure("proxy", "fun_captcha", {"outlook_steps": ["register"]})
        if not registration.ok and not registration.captcha_frame_seen:
            return self._failure(registration.error_kind, registration.error)
        if registration.captcha_frame_seen:
            if config.captcha_strategy == 2:
                return self._failure("captcha", "manual_handoff", {"outlook_steps": ["captcha"]})
            captcha = await captcha_bridge.solve(
                page=session.page,
                browser=session.browser,
                page_url=session.page.url,
                timeout_ms=30_000,
            )
            if not captcha.ok:
                return self._failure(captcha.error_kind, captcha.error, captcha.metadata)
        recovery = (
            await bind_recovery_email(session.page, temp_source, timeout_seconds=90)
            if config.bind_recovery_email
            else None
        )
        token = await oauth.run(
            session.page,
            email,
            password,
            proxy=proxy,
            recovery_session=recovery,
        )
        if not token.ok:
            return self._failure(token.error_kind, token.error, {"outlook_steps": [token.state]})
        artifact_path = self._write_outlook_artifact(
            email=email,
            password=password,
            client_id=config.client_id,
            refresh_token=token.refresh_token or "",
            recovery_email=recovery.mailbox.address if recovery else "",
            bound=bool(recovery and recovery.bound),
            created_at=datetime.now(timezone.utc),
        )
        success = True
        return RegisterResult(
            ok=True,
            provider=self.name,
            email=email,
            password=password,
            secret=token.refresh_token or "",
            secret_kind="refresh_token",
            artifacts={
                "outlook_auth_path": str(artifact_path),
                "recovery_email": recovery.mailbox.address if recovery else "",
                "bound": bool(recovery and recovery.bound),
                "outlook_steps": ["register", "captcha", "mailbox", "oauth"],
            },
        )
finally:
    if recovery is not None:
        recovery.source.release(recovery.mailbox, success=success)
```

The concrete calls above consume the browser, captcha, recovery, and OAuth interfaces from Tasks 5–8. Import `secrets` and `normalize_error_kind` from `register_core.contracts`; password generation must use `secrets` in memory and never log it. `_write_outlook_artifact(self, *, email, password, client_id, refresh_token, recovery_email, bound, created_at)` writes the exact JSON keys `email`, `password`, `client_id`, `refresh_token`, `recovery_email`, `bound`, `created_at` under the configured Outlook directory with mode `0600`; it sanitizes the email to a filename and writes atomically through a temporary file in the same directory.

- [ ] **Step 4: Run provider and artifact tests.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_provider.py tests/unit/test_outlook_artifacts.py -q
```

Expected: PASS. Run an additional static secret check:

```bash
rg -n 'print\(|logger\.(debug|info|warning|error).*password|refresh_token|proxy' register_core/providers/outlook_*.py
```

Expected: only assignments/private sink writes and redacted error labels; no logging of values.

- [ ] **Step 5: Commit the complete adapter.**

```bash
git add register_core/providers/outlook_adapter.py register_core/contracts.py tests/unit/test_outlook_provider.py tests/unit/test_outlook_artifacts.py
git commit -m "feat(register): orchestrate outlook registration and private artifacts"
```

---

### Task 10: Pipeline/email/control API integration and Outlook account import/export

**Files:**
- Modify: `register_core/email/registry.py` (completed in Task 7; verify aliases)
- Modify: `apps/control_api/schemas.py:44-51`
- Modify: `apps/control_api/routes_ops.py` and the existing account import/export route modules
- Create: `tests/unit/test_control_api_outlook.py`
- Create: `tests/unit/test_outlook_import_export.py`

**Interfaces:**
- `StartRunRequest.product` accepts exactly `"grok"`, `"mimo"`, `"chatgpt"`, `"outlook"`; `extra_env` carries names/flags, never secret values.
- Control API exposes Outlook import/export using an explicit `outlook-*.json` glob and validates required fields without returning password/refresh token in public responses.
- Pipeline continues to call `get_provider(job.provider, **(job.extra or {}))` and passes rotated proxy in `extra["proxy"]`; no Outlook-specific pipeline fork is introduced.

- [ ] **Step 1: Write failing API/import tests.**

```python
# tests/unit/test_control_api_outlook.py
from pydantic import ValidationError
import pytest

from apps.control_api.schemas import StartRunRequest


def test_start_run_accepts_outlook():
    request = StartRunRequest(product="outlook", target=1, extra_env={"GROK_REGISTER_OUTLOOK_LIVE": "0"})
    assert request.product == "outlook"


def test_start_run_rejects_unknown_product():
    with pytest.raises(ValidationError):
        StartRunRequest(product="xai")
```

```python
# tests/unit/test_outlook_import_export.py
import json

from apps.control_api import routes_ops


def test_outlook_import_glob_does_not_match_xai(tmp_path):
    (tmp_path / "outlook-user.json").write_text(json.dumps({"email": "u@outlook.com", "password": "p", "client_id": "c", "refresh_token": "r"}))
    (tmp_path / "xai-user.json").write_text(json.dumps({"email": "x@example.com"}))
    assert routes_ops._outlook_auth_files(tmp_path) == [tmp_path / "outlook-user.json"]
```

- [ ] **Step 2: Run the tests and verify failure.**

```bash
.venv/bin/python -m pytest tests/unit/test_control_api_outlook.py tests/unit/test_outlook_import_export.py -q
```

Expected: FAIL because the product literal and Outlook-specific file helper are absent.

- [ ] **Step 3: Extend the API schema and own import/export helper.**

Change only the product literal:

```python
class StartRunRequest(BaseModel):
    kind: Literal["grok_supervisor", "register_sh"] = "grok_supervisor"
    product: Literal["grok", "mimo", "chatgpt", "outlook"] = "grok"
    mode: Literal["ordinary", "residential"] = "ordinary"
    target: int = Field(default=100, ge=1, le=100_000)
    threads: int = Field(default=1, ge=1, le=32)
    tag: str = Field(default="batch_web", min_length=1, max_length=64)
    extra_env: dict[str, str] = Field(default_factory=dict)
```

Add a route-local helper with strict glob and schema validation:

```python
def _outlook_auth_files(root: Path) -> list[Path]:
    return sorted(root.glob("outlook-*.json"))


def _public_outlook_record(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"email", "password", "client_id", "refresh_token"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"missing outlook fields: {','.join(missing)}")
    return {
        "email": str(payload["email"]),
        "client_id": str(payload["client_id"]),
        "bound": bool(payload.get("bound", False)),
        "has_password": bool(payload.get("password")),
        "has_refresh_token": bool(payload.get("refresh_token")),
    }
```

Import must read private files only for the operation, return counts and public records, and never serialize secret fields. Export must copy only to an operator-selected private path under the Outlook auth directory, apply 0600, and reject paths outside that root.

- [ ] **Step 4: Verify pipeline proxy propagation with a regression test.**

Add a test that monkeypatches `inject_attempt_proxy` to return `{"proxy": "synthetic-proxy"}` and a fake provider whose `register_one` records `extra`; assert the provider sees the value, while the result/public log does not include it. Do not change pipeline control flow.

- [ ] **Step 5: Run API, import/export, and pipeline tests.**

```bash
.venv/bin/python -m pytest tests/unit/test_control_api_outlook.py tests/unit/test_outlook_import_export.py tests/unit/test_pipeline_proxy.py -q
```

Expected: PASS; existing control API tests remain green.

- [ ] **Step 6: Commit control-plane integration.**

```bash
git add apps/control_api/schemas.py apps/control_api/routes_ops.py tests/unit/test_control_api_outlook.py tests/unit/test_outlook_import_export.py
git commit -m "feat(control-api): add isolated outlook run and auth import paths"
```

---

### Task 11: Supervisor progress integration and end-to-end mocked acceptance

**Files:**
- Modify: `scripts/launch_batch_supervisor.sh` around completion marker grep and CPA import globs
- Create: `tests/unit/test_outlook_supervisor_markers.py`
- Create: `tests/integration/test_outlook_pipeline_gated.py`
- Modify: `README.md`

**Interfaces:**
- Outlook runner emits `SUMMARY_JSON` with `provider="outlook"`, `ok`, `success`, `fail`, `stopped_reason`; successful attempt emits `注册成功` and failed terminal attempt emits existing `Fatal`/`FAIL-FAST` marker.
- Supervisor includes Outlook in progress accounting but keeps `xai-*.json` CPA import path unchanged; Outlook import uses only `outlook-*.json`.
- Full mocked pipeline test covers proxy injection → provider → private artifact → public result with no real network/browser.

- [ ] **Step 1: Write failing marker and mocked acceptance tests.**

```python
# tests/unit/test_outlook_supervisor_markers.py
from pathlib import Path


def test_supervisor_declares_disjoint_outlook_artifact_branch(tmp_path):
    (tmp_path / "outlook-a.json").write_text("{}")
    (tmp_path / "xai-a.json").write_text("{}")
    assert [p.name for p in tmp_path.glob("outlook-*.json")] == ["outlook-a.json"]
    assert [p.name for p in tmp_path.glob("xai-*.json")] == ["xai-a.json"]

    script = Path("scripts/launch_batch_supervisor.sh").read_text()
    assert "outlook)" in script
    assert "-name 'outlook-*.json'" in script
    assert "-name 'xai-*.json'" in script
```

```python
# tests/integration/test_outlook_pipeline_gated.py
import json
import os
import stat

import pytest

from register_core.contracts import RegisterResult
from register_core.pipeline import Pipeline

pytestmark = pytest.mark.skipif(
    os.environ.get("GROK_REGISTER_OUTLOOK_LIVE") == "1",
    reason="this acceptance test is mock-only; live browser tests are separately gated",
)


def test_mock_outlook_pipeline_carries_proxy_and_redacts_public_result(
    monkeypatch, tmp_path
):
    seen = {}

    monkeypatch.setattr(
        "register_core.util.proxy.preflight_nodes_for_register",
        lambda extra, *, log_fn: dict(extra),
    )
    monkeypatch.setattr(
        "register_core.util.proxy.inject_attempt_proxy",
        lambda extra, *, log_fn: {**extra, "proxy": "synthetic-proxy"},
    )

    class FakeOutlookProvider:
        name = "outlook"

        def register_one(self, *, email_source, extra):
            seen["proxy"] = extra["proxy"]
            path = tmp_path / "outlook_auths" / "outlook-user.json"
            path.parent.mkdir()
            path.write_text(
                json.dumps(
                    {
                        "email": "user@outlook.com",
                        "password": "synthetic-password",
                        "client_id": "synthetic-client-id",
                        "refresh_token": "synthetic-refresh-token",
                    }
                )
            )
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            return RegisterResult(
                ok=True,
                provider=self.name,
                email="user@outlook.com",
                password="synthetic-password",
                secret="synthetic-refresh-token",
                secret_kind="refresh_token",
                artifacts={
                    "outlook_auth_path": str(path),
                    "bound": True,
                },
            )

    emitted = []
    stats = Pipeline(
        FakeOutlookProvider(),
        fail_fast=False,
        on_result=emitted.append,
    ).run(count=1, extra={})

    assert stats.results and stats.results[0].ok is True
    assert seen == {"proxy": "synthetic-proxy"}
    path = tmp_path / "outlook_auths" / "outlook-user.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    private = json.loads(path.read_text())
    assert private["refresh_token"] == "synthetic-refresh-token"

    public = emitted[0].to_public_dict()
    public_json = json.dumps(public)
    assert public["artifacts"]["outlook_auth_path"] == str(path)
    assert public["artifacts"]["bound"] is True
    assert "synthetic-password" not in public_json
    assert "synthetic-refresh-token" not in public_json
    assert "synthetic-proxy" not in public_json
```

- [ ] **Step 2: Run tests and verify marker/helper failures.**

```bash
.venv/bin/python -m pytest tests/unit/test_outlook_supervisor_markers.py tests/integration/test_outlook_pipeline_gated.py -q
```

Expected: FAIL until supervisor branch and final gate behavior are present.

- [ ] **Step 3: Add compatible supervisor output and isolated glob branch.**

In `scripts/launch_batch_supervisor.sh`, retain the existing marker regex and add Outlook without replacing it:

```bash
if grep -qE "SUMMARY_JSON|=== 完成|Fatal|FAIL-FAST|wrote |注册成功" "$log_file"; then
    :
fi

case "$product" in
  outlook)
    outlook_auths_dir="${OUTLOOK_AUTHS_DIR:-outlook_auths}"
    outlook_count=$(find "$outlook_auths_dir" -maxdepth 1 -type f -name 'outlook-*.json' | wc -l | tr -d ' ')
    printf 'OUTLOOK_AUTH_COUNT=%s\n' "$outlook_count"
    ;;
  *)
    # Existing xai/mimo/chatgpt branches remain unchanged.
    ;;
esac
```

The Outlook child runner must emit one JSON object per summary line using `json.dumps`/shell-safe serialization, never include password/token/proxy, and write `注册成功` only after the private artifact has been atomically written. Do not alter the existing `xai-*.json` CPA import glob.

- [ ] **Step 4: Run shell syntax and full mocked tests.**

```bash
bash -n scripts/launch_batch_supervisor.sh
.venv/bin/python -m pytest tests/unit/test_outlook_supervisor_markers.py tests/integration/test_outlook_pipeline_gated.py tests/unit/test_control_api_outlook.py tests/unit/test_outlook_provider.py -q
```

Expected: shell syntax PASS; all mocked tests PASS; no live test runs by default.

- [ ] **Step 5: Commit supervisor integration.**

```bash
git add scripts/launch_batch_supervisor.sh tests/unit/test_outlook_supervisor_markers.py tests/integration/test_outlook_pipeline_gated.py README.md
git commit -m "feat(supervisor): account for gated outlook progress and artifacts"
```

---

## Verification and self-review checklist

- [x] **Spec coverage:** §0 directives are implemented by Tasks 1–2 (slidex hold), 5–9 (Outlook fourth adapter), and the boundary map (independent package/base/supervisor). §1 artifact format and §9 own directory are covered by Task 9. §2 source reuse and two MIT marks are covered by Tasks 1 and 5. §2.1 five slidex integration points are covered by Tasks 1–3. §3.1 same-context CDP and storage-state fallback are covered by Task 6. §4 five segments are covered by Tasks 5–9. §5 mail_assets/CF Temp Mail split is covered by Task 7. §6 protocol/registry is covered by Task 4. §7 S1–S8 order is represented by Tasks 1–11. §8 control API/supervisor is covered by Tasks 10–11. §10 risks have tests or bounded fallbacks in Tasks 3, 6, 7, 8, and 11. Spec §1/§9/§11 now agrees with the independent Outlook auth directory decision.
- [x] **Prohibited-marker scan:** a case-sensitive scan for unfinished-task labels, disabled-flow labels, omitted-code markers, and bare no-op statements returned zero matches. Task 9 contains concrete orchestration calls, and the committed source must preserve that completeness.
- [x] **Type consistency:** `HoldSolveResult` is defined once and imported by both `slidex/providers/hold.py` and `slidex/vision/hold.py` through a shared import or one deliberately re-exported definition; `VisualChallengeResult` receives only `dict[str, Any]` metadata; `CaptchaBridgeResult` maps to `RegisterResult`; `TempMailSource` implements all `EmailSource` methods; `OAuthTokenResult.refresh_token` is `str | None`; `ProviderSpec.options` remains `dict[str, Any]`; `StartRunRequest.product` and registry names agree on `outlook`.
- [x] **Plan-level secret review:** the plan rejects top-level and recursively nested inline secret keys, allows environment-variable names only, requires proxy validation before browser/Temp Mail allocation, keeps private artifact fields separate from public allow-listed metadata, and contains no real secret values, proxy URLs, cookies, screenshots, or token responses.
- [ ] **Implementation secret review:** after implementation, run `rg -n 'logger\.|print\(|json\.dumps|SUMMARY_JSON' register_core/providers/outlook_*.py scripts/launch_batch_supervisor.sh`; inspect every match to ensure only safe labels/paths/counts are emitted. Run `git diff --check` and inspect staged files for `.env`, token strings, proxy URLs, screenshots, or real account data.
- [ ] **Default verification:** from grok-register run `.venv/bin/python -m pytest -q`; from `/tmp/slidex` run `.venv/bin/python -m pytest -q`. Both suites must pass with live browser tests skipped unless the explicit gate is set.
- [ ] **Controlled live verification:** only after all default tests pass, an authorized operator may set `GROK_REGISTER_OUTLOOK_LIVE=1` for one synthetic/test mailbox and one test proxy, run the single CDP probe and one end-to-end attempt, then unset the variable. Do not run a batch target or expose resulting credentials in chat/logs.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-01-outlook-register-integration.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh implementer per task, review after each task, and run a final whole-branch review.
2. **Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, with checkpoints between task groups.

Which approach?
