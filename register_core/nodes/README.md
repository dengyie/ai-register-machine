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

## 2) Embedded mihomo core (YAML protocol nodes)

```bash
./scripts/bootstrap_nodes_core.sh
python scripts/import_clash_to_nodes.py          # import Clash Verge YAMLs into .nodes/
python -m register_core nodes core start
python -m register_core nodes core proxies
python -m register_core nodes core select '🇺🇸【北美洲】美国04原生丨直连【2x】'
python -m register_core nodes core url            # http://127.0.0.1:17897
```

Import packs medium profiles into `.nodes/config/runtime.yaml` (gitignored).
Mega free lists (>400 proxies/file) are skipped for core; their HTTP/SOCKS still
go into `nodes.json`.

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
