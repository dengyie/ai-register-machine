# Changelog

All notable changes to this project are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
project versioning follows [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-07-12

### Added

- Public project packaging for open-source release
- `LICENSE` (MIT), `DISCLAIMER.md`, `SECURITY.md`, `CONTRIBUTING.md`
- GitHub Actions CI (syntax + offline tests)
- Local account backup helpers (`account_backup.py`, `scripts/backup_registered_accounts.py`)
- Optional remote CPA auth inject after successful OIDC mint
- Hotmail/Outlook Office REST code fetch with IMAP fallback
- Protocol-first CPA OIDC mint (`cpa_xai/protocol_mint.py`) with browser fallback

### Changed

- README rebranded as **Grok 注册机** with full setup / ops docs
- Live Hotmail REST test gated behind `GROK_REGISTER_LIVE=1`
- Project package name aligned to `grok-register`

### Security

- Runtime secrets remain gitignored (`config.json`, accounts, CPA auths, backups, logs)

## [1.0.0] - 2026-07-11

### Added

- Chromium + DrissionPage registration core
- Hotmail four-field credential pool and plus-alias registration
- CPA export hook and `cpa_xai` OIDC tooling
- CLI (`register_cli.py`) and GUI (`grok_register_ttk.py`) entrypoints
