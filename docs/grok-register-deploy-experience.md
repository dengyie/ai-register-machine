# Grok Register Deployment Experience (2026-07-29)

## Overview
Deployment of the grok-register project follows a **manual scp** model rather than full CI/CD for the Python batch layer. The `deploy.yml` CI pipeline only builds and ships the **console10 static SPA** to `/data/grok-register/apps/web` on pxed. The 4 core Python files (`node_score.py`, `proxy_rotate.py`, `mail_pool_probe.py`, `register_cli.py`) are **manually scp'd** to `pxed:/data/grok-register/` after each merge (or after relevant changes). The pxed tree is a **live directory** — not a git checkout — so there is no `git pull`; files are overwritten in place.

## Deployment Steps
1. After a feature branch (e.g. `feat/email-ip-correlation`) is merged to main via PR, the changed Python files are scp'd:
   ```bash
   scp node_score.py proxy_rotate.py mail_pool_probe.py register_cli.py pxed:/data/grok-register/
   ```
   (or `scp register_cli <host>` pattern used historically)

2. The SPA is auto-deployed by CI (`deploy.yml` only affects `/apps/web`).

3. No git operations on pxed — atomic `.next`→rename on SPA side; Python files overwritten directly.

## Current pxed State (post-merge)
- Files overwritten to merged-main sizes (e.g. node_score.py:590, proxy_rotate.py:1408, etc.)
- `py_compile` succeeds
- `correlation_enabled()` = `False` (OFF by default — `EMAIL_IP_CORRELATION` unset in `.env`/`config.json`)
- `_registration_domain` = ''; no `register_cli` running
- Batch remains inert until operator sets `EMAIL_IP_CORRELATION=1` + restarts the batch process.

## Activation
To enable the email×IP correlation feature:
```bash
# On host or via console
set EMAIL_IP_CORRELATION=1 in .env or config.json
# Restart batch supervisor / relevant process
```

## Related
- See [[project_email_ip_correlation_merge_deploy_20260729.md]] for full merge+deploy notes
- SPA auto-deploy documented in [[project_console10_ci_deploy_20260725.md]]
- Manual scp pattern confirmed across prior deploys (e.g. [[project_full_pool_mail_probe_deploy_20260727.md]])

**Note to self:** Always verify `correlation_enabled()` after scp and before next batch run. Feature ships OFF-by-default per spec.
