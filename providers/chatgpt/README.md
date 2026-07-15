# ChatGPT / OpenAI platform provider

In-process protocol register for **new OpenAI platform accounts** (auth.openai.com
PKCE + email OTP + oauth/token). Produces `refresh_token` / `access_token` for
auth-file pools (CLIProxyAPI / codex-lb consumers), **not** a chat2api gateway.

## Stack

| Layer | Choice |
|-------|--------|
| Style | **In-process** (consumes `EmailSource`) |
| HTTP | `curl_cffi` impersonate chrome (TLS fingerprint) |
| Mail | `gmail_imap` default (catch-all); `tinyhost` / `duckmail` OK |
| Captcha | OpenAI Sentinel PoW (no browser Turnstile farm) |
| CPA | **none** — never auto-inject production |

## Hub

```bash
./register.sh chatgpt [count]
# or layered:
./register.sh core run -p chatgpt -n 1 --email-source gmail_imap
CHATGPT_PROXY=http://127.0.0.1:7897 ./providers/chatgpt/run-register.sh 1

# Self-controlled nodes (no Clash selector dependency):
CHATGPT_PROXY_LIST='http://u:p@1.2.3.4:8080,http://u:p@5.6.7.8:8080' \
  ./providers/chatgpt/run-register.sh 3
# equivalent:
./register.sh core run -p chatgpt -n 3 --email-source gmail_imap \
  --proxy-list 'http://u:p@1.2.3.4:8080,http://u:p@5.6.7.8:8080'
```

## Egress (self-controlled)

Register machine owns the node pool. **Preferred:** `list` mode with explicit
proxy URLs — each attempt binds `curl_cffi` to a concrete upstream from
`CHATGPT_PROXY_LIST` / `--proxy-list`. This does **not** call Clash API to
pick a GLOBAL/PROXY node.

| Mode | How | Clash dependency |
|------|-----|------------------|
| fixed `CHATGPT_PROXY` | single URL (often local mixed port) | optional local forwarder only |
| `list` + `CHATGPT_PROXY_LIST` | rotate explicit URLs per attempt | **none** |
| `clash` | dedicated group only (legacy Grok path) | controller API; never main selector |

Empty list + no mode → fixed proxy (default `http://127.0.0.1:7897`).
Setting a non-empty proxy list auto-enables `list` mode.

## Env

| Var | Default | Meaning |
|-----|---------|---------|
| `CHATGPT_PROXY` | `http://127.0.0.1:7897` | Fixed outbound proxy when not rotating |
| `CHATGPT_PROXY_LIST` / `PROXY_LIST` | empty | Self-controlled node pool (comma/newline/`.txt`) |
| `CHATGPT_PROXY_ROTATE_MODE` / `PROXY_ROTATE_MODE` | auto | `off` \| `list` \| `clash` (list auto if pool set) |
| `CHATGPT_PROXY_ROTATE_EVERY` | `1` | Rotate every N attempts |
| `CHATGPT_EMAIL_DOMAIN` | `publicvm.com` | Force tinyhost domain (higher OTP deliverability) |
| `CHATGPT_OTP_TIMEOUT` | `180` | OTP poll seconds |
| `CHATGPT_SINK` | `providers/chatgpt/output/pipeline.jsonl` | private JSONL |
| `CHATGPT_TIMEOUT` | `900` | pipeline timeout hint |

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

## Fail-fast

- Missing email source / empty allocate → `FailFastError`
- OTP timeout → `MailMissError` (pipeline may stop under fail_fast)
- Sentinel soft-fail continues once; hard HTTP 4xx/5xx on register/OTP → fail attempt

## Manual-required

- **OTP inbox**: prefer `CHATGPT_EMAIL_SOURCE=gmail_imap` with catch-all (`@mangoqwq.com` → Gmail). Pure tinyhost domains often never deliver OpenAI OTP. Gmail OTP parser must strip HTML (OpenAI body is HTML-only; CSS numbers used to poison extractors).
- **`create_account` → `registration_disallowed`**: protocol + OTP validated live; OpenAI risk engine still rejects final account create for some IP/domain/device combos. Needs cleaner residential egress and/or better-reputation mailbox domain — not fixed by payload shape alone.
- Live OpenAI API usage probe (cost / policy) — verifier default is offline shape only
- Phone challenge accounts (not handled; fail closed)
- Local Mac: Gmail IMAP TLS to `imap.gmail.com:993` may EOF (network path); use **pxed** (HTTP CONNECT via Clash) for live smoke

## Do not

- Hotmail plus-alias farm
- Silent tebi/CPA production inject
- Browser Selenium farms as primary path
