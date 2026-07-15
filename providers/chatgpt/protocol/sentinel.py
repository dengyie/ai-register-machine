"""OpenAI Sentinel proof-of-work token builder."""

from __future__ import annotations

import base64
import json
import random
import time
import uuid
from typing import Any

from .constants import SENTINEL_BASE, SENTINEL_SDK, USER_AGENT


class SentinelTokenGenerator:
    MAX_ATTEMPTS = 500_000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id: str, ua: str = USER_AGENT) -> None:
        self.device_id = device_id
        self.user_agent = ua
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        return format(h & 0xFFFFFFFF, "08x")

    def _get_config(self) -> list[Any]:
        perf_now = random.uniform(1000, 50000)
        return [
            "1920x1080",
            time.strftime(
                "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)",
                time.gmtime(),
            ),
            4294705152,
            random.random(),
            self.user_agent,
            SENTINEL_SDK,
            None,
            None,
            "en-US",
            random.random(),
            random.choice(
                [
                    "vendorSub-undefined",
                    "plugins-undefined",
                    "mimeTypes-undefined",
                    "hardwareConcurrency-undefined",
                ]
            ),
            random.choice(
                ["location", "implementation", "URL", "documentURI", "compatMode"]
            ),
            random.choice(
                ["Object", "Function", "Array", "Number", "parseFloat", "undefined"]
            ),
            perf_now,
            self.sid,
            "",
            random.choice([4, 8, 12, 16]),
            time.time() * 1000 - perf_now,
        ]

    @staticmethod
    def _b64(data: Any) -> str:
        raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    def generate_requirements_token(self) -> str:
        data = self._get_config()
        data[3] = 1
        data[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._b64(data)

    def generate_token(self, seed: str, difficulty: str) -> str:
        start = time.time()
        data = self._get_config()
        difficulty = str(difficulty or "0")
        for i in range(self.MAX_ATTEMPTS):
            data[3] = i
            data[9] = round((time.time() - start) * 1000)
            payload = self._b64(data)
            if self._fnv1a_32(seed + payload)[: len(difficulty)] <= difficulty:
                return "gAAAAAB" + payload + "~S"
        return "gAAAAAB" + self.ERROR_PREFIX + self._b64(str(None))


def build_sentinel_token(session: Any, device_id: str, flow: str) -> str:
    """POST sentinel/req and return JSON string for openai-sentinel-token header."""
    generator = SentinelTokenGenerator(device_id, USER_AGENT)
    resp = session.post(
        f"{SENTINEL_BASE}/backend-api/sentinel/req",
        data=json.dumps(
            {
                "p": generator.generate_requirements_token(),
                "id": device_id,
                "flow": flow,
            }
        ),
        headers={
            "Content-Type": "text/plain;charset=UTF-8",
            "Referer": f"{SENTINEL_BASE}/backend-api/sentinel/frame.html",
            "Origin": SENTINEL_BASE,
            "User-Agent": USER_AGENT,
            "sec-ch-ua": '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
        timeout=20,
    )
    try:
        data = resp.json() if hasattr(resp, "json") else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    token = str(data.get("token") or "").strip()
    status = int(getattr(resp, "status_code", 0) or 0)
    if status != 200 or not token:
        raise RuntimeError(f"sentinel_req_failed_{status}")
    pow_data = data.get("proofofwork") or {}
    if isinstance(pow_data, dict) and pow_data.get("required") and pow_data.get("seed"):
        p_value = generator.generate_token(
            str(pow_data.get("seed") or ""),
            str(pow_data.get("difficulty") or "0"),
        )
    else:
        p_value = generator.generate_requirements_token()
    return json.dumps(
        {"p": p_value, "t": "", "c": token, "id": device_id, "flow": flow},
        separators=(",", ":"),
    )
