"""HTTP session helpers for ChatGPT protocol register."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from .constants import DEFAULT_IMPERSONATE, DEFAULT_TIMEOUT


def create_session(
    proxy: str = "",
    *,
    impersonate: str = DEFAULT_IMPERSONATE,
) -> Any:
    """Prefer curl_cffi (TLS fingerprint); fall back to requests.

    Default impersonate is aligned with constants.USER_AGENT / sec-ch-ua
    (Mac Chrome 145). Passing a mismatched platform is a risk-engine signal.
    """
    proxy = (proxy or "").strip()
    try:
        from curl_cffi import requests as curl_requests

        kwargs: dict[str, Any] = {"impersonate": impersonate, "verify": False}
        if proxy:
            kwargs["proxy"] = proxy
        return curl_requests.Session(**kwargs)
    except Exception:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        session = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.verify = False
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


def quote_param(value: str) -> str:
    return quote(str(value), safe="")


def response_json(resp: Any) -> dict:
    if resp is None:
        return {}
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def cookie_get(session: Any, name: str) -> str:
    jar = getattr(session, "cookies", None)
    if jar is None:
        return ""
    try:
        val = jar.get(name)
        if val:
            return str(val)
    except Exception:
        pass
    try:
        for cookie in jar:
            if getattr(cookie, "name", "") == name:
                return str(getattr(cookie, "value", "") or "")
    except Exception:
        pass
    return ""
