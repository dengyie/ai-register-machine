# Register Profile Config Abstraction Design

**Date:** 2026-07-17  
**Status:** Approved (approach 1; user: continue without pause)  
**Goal:** Drive the whole register machine from one profile: mailbox, decode, provider, strategy (runtime + risk), verify, sink, secrets.

## Context

Today layers exist (`email/*`, `providers/*`, `verify/*`, `pipeline`) but wiring is scattered across CLI flags, env vars, and `RegisterJob.extra`. ChatGPT is in-process; Grok/MiMo are black-box and ignore `EmailSource`. Operators want:

1. **Decode** — CF Worker / Gmail / … as config  
2. **Register** — target site as config  
3. **Strategy** — runtime + risk/asset policy as config  
4. **Mailbox** — address allocation separate from decode  

Decisions locked in brainstorming:

| Topic | Choice |
|-------|--------|
| Strategy | **D** — runtime + risk/burn in `strategy`; product params on provider |
| Config form | **A** — one YAML/JSON profile is authority |
| Product scope | **B** — all three products truly share mailbox+decode (phased) |
| Mailbox vs decode | **B** — split two layers |
| Secrets | **C** — dev may plaintext; prod forces `*_env` refs |
| Architecture | **Approach 1** — Profile → RuntimeBundle four-layer runtime |

## Architecture (end state)

```text
profiles/<name>.yaml          # single run authority
        │
 ProfileLoader (schema + secrets resolve + CLI overrides)
        │
 RuntimeBundle
   ├─ MailboxProvider   allocate / release only
   ├─ OtpDecoder        wait_otp(mailbox) only
   ├─ RegisterProvider  site protocol; consumes mailbox+decoder
   ├─ StrategyEngine    fail-fast, egress, burn/cool, mail≠register proxy
   └─ Verifier + Sink
        │
 Pipeline.run(count)
```

### Hard boundaries (production discipline)

1. Mail proxy never inherits register egress.  
2. Fail-fast on configured `error_kind`s; no empty retry same IP+domain class.  
3. Success = this-run attribution only.  
4. No soft-inject without verify pass.  
5. Prod profiles: no plaintext secrets.  
6. Three products share the same Pipeline contracts eventually.

## Profile schema (v1)

```yaml
# profiles/chatgpt-cf-clash.example.yaml
apiVersion: register.v1
kind: RegisterProfile
metadata:
  name: chatgpt-cf-clash
  description: ChatGPT via CF Worker mailbox+decode, Clash egress

spec:
  provider:
    name: chatgpt                 # chatgpt | grok | mimo
    # product-local knobs only
    options:
      otp_timeout_s: 180
      headless: true              # ignored by chatgpt; used by grok/mimo later

  count: 1

  mailbox:
    type: cloudflare              # cloudflare | tinyhost | gmail_imap | duckmail | local_part
    # type-specific:
    domain: mangoqwq.com          # optional pin for tinyhost/local_part
    # local: random | fixed:<name>

  decode:
    type: cloudflare              # cloudflare | gmail_imap | tinyhost | duckmail
    options:
      timeout_s: 180
      poll_interval_s: 2.5
      sender_hint: ""             # optional

  strategy:
    fail_fast: true
    fail_fast_kinds:
      - registration_disallowed
      - unsupported_email
      - fatal
      - verify
    egress:
      mode: clash                 # auto | core | clash | list | direct
      proxy: "http://127.0.0.1:7897"   # optional fixed
      proxy_list: ""              # path or comma URLs
      rotate_every: 1
      rotate_required: false
    mail_proxy: direct            # direct | url | env:MAIL_PROXY
    burn:
      enabled: true
      track: [ip, domain]
      on_kinds: [registration_disallowed, unsupported_email]
      state_path: ".register/burn_state.json"   # optional; empty = memory only
    cool:
      soft_seconds: 0             # 0 = off in M1

  verify:
    enabled: true
    name: auto                    # auto = provider default; or noop

  sink:
    path: "providers/chatgpt/output/pipeline.jsonl"  # empty = no sink

  secrets:
    mode: prod                    # dev | prod
    # prod: only env refs allowed for secret fields
    # map used by mailbox/decode constructors:
    # cloudflare_api_base_env: CLOUDFLARE_API_BASE
    # cloudflare_api_key_env: CLOUDFLARE_API_KEY
```

### Compatibility aliases

- `mailbox.type` / `decode.type`: `cf` → `cloudflare`, `gmail` → `gmail_imap`  
- If only legacy `email_source: cloudflare` is present (CLI path), loader builds matching mailbox+decode pair of the same type (paired mode).

### Paired vs split

| Mode | Meaning |
|------|---------|
| **paired** | mailbox.type == decode.type and both talk to same backend (CF allocate + CF poll). Default for M1 adapters. |
| **split** | Different types (e.g. mailbox local_part@mangoqwq.com + decode gmail_imap). Allowed when both layers can cooperate on the same address. |

M1 implements paired fully; split is validated only when both types support `Mailbox.token` / address contract (documented per pair). Unsupported splits fail-fast at load.

## Interfaces

### MailboxProvider

```python
class MailboxProvider(Protocol):
    name: str
    def allocate(self) -> Mailbox: ...
    def release(self, mailbox: Mailbox, *, success: bool) -> None: ...
```

### OtpDecoder

```python
class OtpDecoder(Protocol):
    name: str
    def wait_otp(
        self,
        mailbox: Mailbox,
        *,
        timeout_s: float = 180,
        poll_interval_s: float = 3,
        used_codes: set[str] | None = None,
        newer_than_epoch: float | None = None,
        sender_hint: str | None = None,
    ) -> OtpCode: ...
    # optional: last_wait_diagnostics
```

### Composite EmailSource (compat bridge)

```python
class CompositeEmailSource:
    """EmailSource façade over MailboxProvider + OtpDecoder for existing adapters."""
    name: str  # f"{mailbox.name}+{decode.name}" or single if paired
    def allocate(...): return mailbox.allocate()
    def poll_otp(...): return decoder.wait_otp(...)
    def release(...): return mailbox.release(...)
```

Existing `ChatGPTProvider` keeps accepting `EmailSource`; Pipeline injects `CompositeEmailSource` from profile. No adapter rewrite required for M1.

### StrategyEngine (M2 full; M1 subset)

M1: read fail_fast + egress + mail_proxy from profile into `RegisterJob` / `extra` (same keys pipeline already understands).  
M2: burn state file + feedback hooks centralized (today partially in proxy util + ops scripts).

### RegisterProvider (unchanged signature M1)

```python
def register_one(*, email_source: EmailSource | None = None, extra: dict | None = None) -> RegisterResult
```

M3/M4: Grok/MiMo must consume injected EmailSource (composite); black-box internal mail becomes fallback only when profile says `mailbox.type: provider` / `decode.type: provider`.

## Loader rules

1. Accept `.yaml` / `.yml` / `.json`.  
2. `apiVersion` must be `register.v1`.  
3. Resolve secrets:  
   - `secrets.mode=prod`: any field ending in secret-like names must be env ref or empty; plaintext non-empty secrets → load error.  
   - `dev`: plaintext allowed.  
4. CLI overrides (non-secret): `--count`, `--no-verify`, `--egress`, `--proxy`, `--sink`, `--no-fail-fast`.  
5. Env still fills constructor secrets when profile uses `*_env` keys or legacy env (CLOUDFLARE_API_BASE, GMAIL_IMAP_*).  
6. Output: `RegisterJob` + resolved RuntimeBundle metadata in `job.extra["_profile"]` (name, path, mailbox, decode types — no secrets).

## CLI

```bash
# New (preferred)
python -m register_core run --profile profiles/chatgpt-cf-clash.yaml

# Legacy flags still work (build ephemeral profile in memory)
python -m register_core run -p chatgpt --email-source cloudflare -n 1 --egress clash
```

When both `--profile` and `-p` are set: profile is base; CLI provider must match or fail-fast.

## Data flow (one attempt)

1. Strategy preflight egress (existing nodes preflight).  
2. Strategy inject attempt proxy into `extra`.  
3. Provider `register_one(email_source=composite, extra=...)`.  
4. composite.allocate → site protocol → composite.poll_otp → token.  
5. Verifier; Sink; Strategy feedback (burn on configured kinds).

## Migration milestones

| ID | Deliverable | Done when |
|----|-------------|-----------|
| **M1** | Schema + Loader + Mailbox/Decode protocols + Composite + ChatGPT via `--profile` | unit tests; offline load; ChatGPT path equivalent to flags |
| **M2** | StrategyEngine burn/cool state | burn file updates on registration_disallowed without residual shell |
| **M3** | MiMo consumes EmailSource | profile can switch CF/Gmail for MiMo |
| **M4** | Grok consumes EmailSource | same |

## Non-goals (this design)

- Soft inject / remint spin  
- Hotmail plus-alias farm  
- Unifying Grok Drission DOM into curl_cffi  
- Live chat entitlement unlock  
- Plugin marketplace / DAG workflow language  

## Testing (M1)

- Load example profiles (dev + prod secret rejection)  
- Composite allocate/poll delegates  
- Pipeline.from_job still works with legacy email_source  
- Pipeline.from_profile builds job with mail_proxy + egress  
- ChatGPT verifier still accepts rt.1  

## Example profiles shipped

- `profiles/chatgpt-cf.example.yaml`  
- `profiles/chatgpt-tinyhost.example.yaml`  
- `profiles/chatgpt-gmail.example.yaml`  
