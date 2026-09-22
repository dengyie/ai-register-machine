"""HTTP session for typesafe.ai console (ECDH prime256v1 + requests)."""

from __future__ import annotations

import ssl
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter, Retry

from .constants import COMMON_HEADERS, DEFAULT_TIMEOUT, TLS_ECDH_CURVE


class TlsAdapter(HTTPAdapter):
    """Pin an SSLContext so ECDH stays on prime256v1 (original script)."""

    def __init__(self, ctx: ssl.SSLContext, **kw: Any) -> None:
        self._ctx = ctx
        super().__init__(**kw)

    def init_poolmanager(self, *a: Any, **kw: Any) -> Any:
        kw["ssl_context"] = self._ctx
        return super().init_poolmanager(*a, **kw)

    def proxy_manager_for(self, proxy: str, **kw: Any) -> Any:
        kw["ssl_context"] = self._ctx
        return super().proxy_manager_for(proxy, **kw)


def create_session(proxy: str = "") -> requests.Session:
    """Build a requests Session with the original script's ECDH pin."""
    ctx = ssl.create_default_context()
    try:
        ctx.set_ecdh_curve(TLS_ECDH_CURVE)
    except Exception:
        pass
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1, status_forcelist=(502, 503, 504))
    session.mount("https://", TlsAdapter(ctx, max_retries=retry))
    session.headers.update(COMMON_HEADERS)
    proxy = (proxy or "").strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def request_with_retry(
    session: Any,
    method: str,
    url: str,
    *,
    retry_attempts: int = 3,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> tuple[Any | None, str]:
    last_error = ""
    for _ in range(max(1, retry_attempts)):
        try:
            return session.request(method.upper(), url, timeout=timeout, **kwargs), ""
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1)
    return None, last_error


def response_json(resp: Any) -> dict:
    if resp is None:
        return {}
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
