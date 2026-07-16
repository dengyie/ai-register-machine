# ChatGPT / OpenAI platform provider

In-process protocol register for **new OpenAI platform accounts** (auth.openai.com
PKCE + email OTP + oauth/token). Produces `refresh_token` / `access_token` for
auth-file pools (CLIProxyAPI / codex-lb consumers), **not** a chat2api gateway.

## Stack

| Layer | Choice |
|-------|--------|
| Style | **In-process** (consumes `EmailSource`) |
| HTTP | `curl_cffi` impersonate chrome (TLS fingerprint) |
| Mail | `cloudflare` Worker default; override `gmail_imap` / `tinyhost` / `duckmail` |
| Captcha | OpenAI Sentinel PoW (no browser Turnstile farm) |
| CPA | **none** — never auto-inject production |

## Hub

```bash
# pick egress: core=project mihomo, clash=external Clash, list, direct, auto
python -m register_core nodes egress set core
REGISTER_EGRESS=core ./register.sh chatgpt [count]
# or per-run:
./register.sh core run -p chatgpt -n 1 --egress clash --email-source cloudflare
./register.sh chatgpt [count]
# or layered:
./register.sh core run -p chatgpt -n 1 --email-source cloudflare

# Project-owned nodes (no external VPN / Clash required):
cp nodes.example.json nodes.json   # edit real HTTP proxy URLs
python -m register_core nodes check
./providers/chatgpt/run-register.sh 1

# Or explicit pool:
CHATGPT_PROXY_LIST='http://u:p@1.2.3.4:8080,http://u:p@5.6.7.8:8080' \
  ./providers/chatgpt/run-register.sh 3
# equivalent:
./register.sh core run -p chatgpt -n 3 --email-source cloudflare \
  --proxy-list 'http://u:p@1.2.3.4:8080,http://u:p@5.6.7.8:8080'
```

## Egress switch (core vs Clash)

| Backend | Meaning |
|---------|---------|
| `core` | project mihomo `.nodes` → `http://127.0.0.1:17897` |
| `clash` | external Clash Verge/mihomo → `http://127.0.0.1:7897` |
| `list` | only `nodes.json` / `PROXY_LIST` HTTP-SOCKS |
| `direct` | no proxy |
| `auto` | list → core → clash (default) |

```bash
python -m register_core nodes egress show
python -m register_core nodes egress set core   # primary: list|core|direct
REGISTER_EGRESS=core ./providers/chatgpt/run-register.sh 1
```

```bash
python -m register_core nodes import profile.yaml   # merge HTTP/SOCKS + pack protocol
python -m register_core nodes list|check|add 'http://u:p@host:port'
python -m register_core nodes core start|select
```

## Env

| Var | Default | Meaning |
|-----|---------|---------|
| `REGISTER_EGRESS` / `CHATGPT_EGRESS` | `auto` | Backend: `core`\|`clash`\|`list`\|`direct`\|`auto` |
| `REGISTER_NODES_FILE` / `NODES_FILE` | `./nodes.json` | HTTP/SOCKS catalog |
| `REGISTER_NODES` | `1` | Set `0` to ignore catalog |
| `REGISTER_NODES_PREFLIGHT` | `1` | Probe catalog before register (list/auto) |
| `REGISTER_NODES_MAX_FAIL` | `3` | Hard quarantine after consecutive proxy fails |
| `REGISTER_NODES_SKIP_FAILED` | `1` | Skip quarantined nodes in rotation |
| `REGISTER_NODES_COOLDOWN_RISK` | `600` | Soft cooldown (s) after `registration_disallowed` — **not** quarantine |
| `REGISTER_NODES_COOLDOWN_NETWORK` | `120` | Soft cooldown (s) after network/proxy fail (still counts fail_count) |
| `REGISTER_NODES_COOLDOWN_PER_USE` | `0` | Optional post-success cool; default **off** (small pools) |
| `CLASH_PROXY` | `http://127.0.0.1:7897` | External Clash mixed port (`egress=clash`) |
| `CHATGPT_PROXY` | empty | Fixed **register** egress URL override |
| `CHATGPT_PROXY_LIST` / `PROXY_LIST` | empty | Self-controlled HTTP pool (skips catalog preflight) |
| `CHATGPT_PROXY_ROTATE_MODE` / `PROXY_ROTATE_MODE` | auto | `off` \| `list` \| `nodes` \| `clash` |
| `CHATGPT_PROXY_ROTATE_EVERY` | `1` | Rotate every N attempts |
| `CHATGPT_MAIL_PROXY` / `EMAIL_PROXY` / `MAIL_PROXY` | empty | **Mail HTTP only** (tinyhost/Worker API). Default direct; never inherits register proxy |
| `CHATGPT_EMAIL_SOURCE` | `cloudflare` | Pluggable: `cloudflare` \| `gmail_imap` \| `tinyhost` \| `duckmail` \| `auto` |
| `CHATGPT_EMAIL_DOMAIN` | `publicvm.com` | Force tinyhost domain when source is tinyhost |
| `CLOUDFLARE_API_BASE` | from `config.json` | Worker base (pxed: `https://temp-mail.mangoqwq.com`) |
| `CLOUDFLARE_API_KEY` / `CLOUDFLARE_AUTH_MODE` | optional | Worker auth; mode `none`\|`bearer`\|`header` |
| `CHATGPT_OTP_TIMEOUT` | `180` | OTP poll seconds (split across first + resend polls) |
| `CHATGPT_SINK` | `providers/chatgpt/output/pipeline.jsonl` | private JSONL |
| `CHATGPT_TIMEOUT` | `900` | pipeline timeout hint |

### Mail vs register egress

| Path | Proxy |
|------|--------|
| OpenAI register (`curl_cffi`) | `CHATGPT_PROXY` / `PROXY_LIST` / `nodes.json` / egress backend |
| Email allocate/poll (HTTP Worker / tinyhost) | **direct** unless `CHATGPT_MAIL_PROXY` / `EMAIL_PROXY` / `MAIL_PROXY` |
| Gmail IMAP (override) | local TLS to `imap.gmail.com` — not register pool |

Artifacts include redacted `register_proxy`, `mail_proxy`, and on `mail_miss` optional `otp_wait` (`OtpWaitDiagnostics`: poll_count, failure_class, …).

## Artifacts (gitignored)

```text
providers/chatgpt/output/
  accounts.jsonl          # append-only this-run records (0600)
  chatgpt-<email>-*.json  # full token dump per success (0600)
  pipeline.jsonl          # sink from hub
```

Success attribution: `RegisterResult` returned from this process only — never
read historical `accounts.jsonl` tail as this-run success.

## Secret shape

- `secret` = `refresh_token`
- `secret_kind` = `refresh_token`
- access/id tokens stored in artifacts / auth file, redacted in public dict

## Fail-fast / error taxonomy

- Missing email source / empty allocate → `FailFastError`
- OTP timeout → `MailMissError` / `error_kind=mail_miss` (pipeline may stop under fail_fast). **Does not** cool or quarantine register nodes.
- `create_account` risk → `error_kind=registration_disallowed` → soft cooldown (`REGISTER_NODES_COOLDOWN_RISK`) only
- Network/proxy fail → fail_count + optional `REGISTER_NODES_COOLDOWN_NETWORK`; hard quarantine after `MAX_FAIL`
- Sentinel soft-fail continues once; hard HTTP 4xx/5xx on register/OTP → fail attempt

`failure_class` (under `artifacts.otp_wait`, not a separate error_kind): `no_mail` | `parse_fail` | `stale_code` | `imap_error` | `aborted`.

## Manual-required

- **OTP inbox**: default `CHATGPT_EMAIL_SOURCE=cloudflare` against project Worker (`cloudflare_api_base` / `CLOUDFLARE_API_BASE`, e.g. `https://temp-mail.mangoqwq.com`). Override `gmail_imap` (catch-all `@mangoqwq.com`) or `tinyhost`/`duckmail` when needed. OpenAI OTP deliverability to Worker domains is asset-dependent; Gmail HTML parser still strips CSS poison when using IMAP.
- **`create_account` → `registration_disallowed`**: protocol + OTP validated live; OpenAI risk engine still rejects final account create for some IP/domain/device combos. Needs cleaner residential egress and/or better-reputation mailbox domain — not fixed by payload shape alone. Code soft-cools the node; does not empty-retry spin.
- Live OpenAI API usage probe (cost / policy) — verifier default is offline shape only
- Phone challenge accounts (not handled; fail closed)
- Live smoke on **pxed** (config.json already pins Worker base); Local Mac needs `cloudflare_api_base` or env overlay, not empty local config.
- Live `ok=1` out号 is asset-dependent (OTP delivery + residential reputation). Code gate can ship with honest smoke diagnosis when assets block.

## Offline verify (code gate)

```bash
.venv/bin/python -m unittest \
  test_otp_diagnostics test_chatgpt_error_kinds test_mail_proxy_separation \
  test_register_core_nodes test_register_core_proxy -v
```

## Do not

- Hotmail plus-alias farm
- Silent tebi/CPA production inject
- Soft-inject / remint spin on entitlement or risk
- Browser Selenium farms as primary path
- Treat mail_miss as proxy death (no quarantine)
