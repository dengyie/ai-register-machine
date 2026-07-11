# Contributing

Thanks for considering a contribution.

## Before you start

1. Read [DISCLAIMER.md](DISCLAIMER.md) and [SECURITY.md](SECURITY.md).
2. Do **not** commit secrets or local runtime files.
3. Prefer small, focused pull requests.

## Development setup

```bash
git clone https://github.com/dengyie/grok-register.git
cd grok-register
uv sync --extra dev
cp config.example.json config.json
# optional: mail_credentials for live mail tests only
```

Python **3.13** is required (`requires-python` in `pyproject.toml`).

## Tests

Offline (default, used by CI):

```bash
uv run python -m pytest -q
# or without pytest:
uv run python test_account_backup.py
uv run python test_cpa_remote_inject.py
uv run python test_fail_policy.py
uv run python test_hotmail_rest_code.py
uv run python test_sso_normalize.py
```

Live Hotmail REST (needs real `mail_credentials.txt`, **not** for CI):

```bash
GROK_REGISTER_LIVE=1 uv run python test_hotmail_rest_code.py
```

Syntax check (CI also compiles `grok_register_ttk.py`):

```bash
uv run python -m py_compile register_cli.py grok_register_ttk.py cpa_export.py account_backup.py cpa_xai/*.py
```

## Coding guidelines

- Match existing style; avoid drive-by refactors in `grok_register_ttk.py`
- Keep changes scoped to the bug/feature
- Add or extend offline tests when fixing logic
- Never log raw passwords, refresh tokens, or access tokens
- Document user-facing config keys in `config.example.json` comment keys
- **SSO handling:** only use `cpa_xai.accounts.normalize_sso_cookie` / `format_account_line`. Do not invent a second strip rule. Normalize must stay at mint core + ledger write (CLI/GUI).
- **CPA export path:** production register and default backfill go through `cpa_export.export_cpa_xai_for_account` so remote inject / backup hooks stay consistent. Do not reintroduce “mint only” as the default backfill path.
- **Config booleans:** use `_config_bool` (or equivalent) so string `"false"` is false.

## Pull requests

- Describe **what** and **why**
- Note how you tested (offline / live)
- Confirm no secrets are included (`git status`, diff review)
- Link related issues when applicable

## Issue reports

Include OS, Python version, proxy yes/no, headed/headless, and redacted logs.
Do not paste SSO cookies, OIDC tokens, or mailbox refresh tokens.
