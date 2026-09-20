"""Governance Agent 客户端（S3 提供）。**未就绪时按设计降级**，不抛错、不阻塞门户。"""

from __future__ import annotations

import urllib.error
import urllib.request

import config


def health() -> tuple[bool, str]:
    """返回 (是否就绪, 说明)。任何异常都按"未就绪"处理（fail-safe）。"""
    url = config.GOVERNANCE_API_URL.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=config.GOVERNANCE_API_TIMEOUT) as resp:
            if resp.status == 200:
                return True, resp.read().decode("utf-8", "replace")[:200]
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"


def ask(question: str) -> tuple[bool, str]:
    """向 Agent 提问；未就绪时返回降级提示。"""
    url = config.GOVERNANCE_API_URL.rstrip("/") + "/chat"
    body = ('{"question": "%s"}' % question.replace('"', '\\"')).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return True, resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"
