# typesafe.ai / jev console provider

In-process protocol register for **typesafe.ai console** accounts (Stytch
magic-link + `/api/auth/callback` + `/api/api-keys`). Produces a this-run
`api_key`. Ported from [Futureppo/typesafe_register](https://github.com/Futureppo/typesafe_register)
(Apache-2.0) — **not** a drop-in of that script's 512-account / 256-worker farm.

## Stack

| Layer | Choice |
|-------|--------|
| Style | **In-process** (consumes `EmailSource`) |
| HTTP | `requests` + ECDH `prime256v1` TLS pin |
| Mail | `tinyhost` default (full body required for magic-link) |
| Auth | Stytch magic-link, no browser / Turnstile |
| CPA | **none** — never auto-inject production |

## Hub

```bash
./register.sh typesafe [count]
./register.sh core run -p typesafe -n 1 --email-source tinyhost
# aliases: jev | typesafe-ai
```

`TYPESAFE_LEGACY=1` falls back to `providers/typesafe/run-register.sh`.

```bash
./register.sh smoke typesafe              # tinyhost + console probe, no mailbox
TYPESAFE_SMOKE_LIVE=1 ./register.sh smoke typesafe   # n=1 this-run api_key
```

## Env

| Var | Default | Meaning |
|-----|---------|---------|
| `TYPESAFE_EMAIL_SOURCE` | `tinyhost` | Must return full mail body (not OTP-only) |
| `TYPESAFE_EMAIL_DOMAIN` | empty | Optional tinyhost domain pin |
| `TYPESAFE_OTP_TIMEOUT` | `180` | Magic-link poll window (seconds) |
| `TYPESAFE_API_KEY_NAME` | `register-core` | Console key label |
| `TYPESAFE_PROXY` | empty | Fixed register egress |
| `TYPESAFE_SKIP_CONSOLE_PROBE` | empty | Skip login GET+parse before mailbox allocate |
| `TYPESAFE_SMOKE_LIVE` | `0` | `1` = n=1 live register from `./register.sh smoke typesafe` |
| `REGISTER_EGRESS` | `auto` | `list` \| `core` \| `clash` \| `direct` \| `auto` |

Do **not** set `ACCOUNT_COUNT=512` / `CONCURRENCY=256`. Pipeline `count` /
threads own scale. Default is `1`.

## Artifacts

- `providers/typesafe/output/` (0700)
- `providers/typesafe/output/typesafe-<email>-<ts>.json` (0600) — this-run deliverable
- Pipeline sink: `providers/typesafe/output/pipeline.jsonl`

Success = this-run `secret` / `secret_kind=api_key` plus a new 0600 json.
Do **not** append full keys to `accounts.jsonl`; historical tails do not count.

## Mail

Cloudflare Worker / Gmail IMAP wrappers in this repo only extract 6-digit
OTPs. typesafe login mail carries a Stytch URL, so the default source is
tinyhost (subject + body + html). `sender_hint=typesafe`.

## Latest case (pxed, 2026-09-22)

`./register.sh typesafe 1000`, 8 workers, `--no-fail-fast`, egress
`rotate=off` (fixed Clash mixed-port, this run did **not** rotate IP).

`CONTRACT_EXIT:0`, ok=976, fail=24 (`mail_miss`×17 no-mail timeouts,
`session`×5 HTTP 500 on send, `oauth_callback`×2 HTTP 401). 976 new `0600`
json files, dir `0700`, no `accounts.jsonl` append, no CPA inject.

Same day, smaller runs: n=100 → 97/3 `mail_miss`; after the magic-link parse
fix, n=100 → 98/2 HTTP 500.

## Compliance

Education / personal automation only. May violate third-party ToS. No
mass-farm defaults, no plus-alias, no CPA inject. This project recognizes
the [LINUX DO](https://linux.do/) community.
