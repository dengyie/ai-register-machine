# Project-owned egress nodes

Two layers — both live **inside this repo**, not Clash Verge UI:

| Layer | Path | What |
|-------|------|------|
| HTTP/SOCKS catalog | `nodes.json` | dialable URLs for curl_cffi |
| Protocol core | `.nodes/` + mihomo | VLESS/SS/VMess/Trojan… from your YAML → `http://127.0.0.1:17897` |

## 1) HTTP/SOCKS catalog

| File | Format |
|------|--------|
| `nodes.json` | `{ "version": 1, "nodes": [ { "url", "id", "label", "tags", "enabled" } ] }` |
| `nodes.txt` / `nodes.list` | one `http://user:pass@host:port` per line |

```bash
python -m register_core nodes list
python -m register_core nodes check
python -m register_core nodes add 'http://u:p@host:port' --label us1
```

## 2) Import / convert (mature light path)

**Conversion is opt-in CLI** — not loaded by the register path. It only parses,
validates, and writes artifacts the pipeline later consumes.

| Input | Auto format |
|-------|-------------|
| Clash / mihomo YAML (`proxies:`) | `clash_yaml` |
| V2Ray / Xray JSON (`outbounds`) | `v2ray_json` |
| Share URI lines (`ss://` `vmess://` `vless://` `trojan://` `socks5://` `http://`) | `uri_list` |

```bash
# legality only (no write)
python -m register_core nodes validate path/to/profile.yaml
python -m register_core nodes validate --format uri_list links.txt

# convert → nodes.json (HTTP/SOCKS) + .nodes/config/runtime.yaml (protocol)
python -m register_core nodes import path/to/profile.yaml --no-clash-home
python -m register_core nodes import links.txt --format uri_list --dry-run
# optional: also scan local Clash Verge profiles (mac default path)
python -m register_core nodes import
```

Split rules:

| Proxy type | Artifact | Needs mihomo? |
|------------|----------|---------------|
| http / socks* | `nodes.json` | **no** (`egress=list`) |
| vless / ss / vmess / trojan / … | `.nodes/config/runtime.yaml` | **yes** (`egress=core`) |

Invalid entries (missing `server`/`port`/`uuid`/…) are rejected with a report;
they never silently enter the pack. Mega free lists (>400 proxies/file) only
harvest dialable HTTP/SOCKS for the core pack.

Compat wrapper: `python scripts/import_clash_to_nodes.py` → same pipeline.

## 3) Embedded mihomo core (only if you have protocol nodes)

```bash
./scripts/bootstrap_nodes_core.sh                # once per machine
python -m register_core nodes core start
python -m register_core nodes core proxies
python -m register_core nodes core select 'node-name'
python -m register_core nodes core url            # http://127.0.0.1:17897
```

Core binary stays under `.nodes/bin/` (gitignored). Register code never embeds
protocol crypto — it only dials the local mixed-port URL.

## Egress switch (core vs Clash)

```bash
python -m register_core nodes egress show
python -m register_core nodes egress set core    # project mihomo only
python -m register_core nodes egress set clash   # external Clash :7897 only
python -m register_core nodes egress set list    # nodes.json / PROXY_LIST only
python -m register_core nodes egress set direct
python -m register_core nodes egress set auto    # list → core → clash
```

| Backend | Meaning |
|---------|---------|
| `core` | project `.nodes` mihomo `http://127.0.0.1:17897` |
| `clash` | external Clash Verge/mihomo `http://127.0.0.1:7897` |
| `list` | HTTP/SOCKS catalog only |
| `direct` | no proxy |
| `auto` | list → core → clash fixed URL |

Env/CLI: `REGISTER_EGRESS=core` or `./register.sh core run -p chatgpt --egress core`.
Persisted in `.nodes/config/egress.mode` (gitignored).

## Not in scope

- Driving **external** Clash Verge UI / system TUN as a required dependency
- Shipping subscription credentials in git (`.nodes/` and `nodes.json` are gitignored)
