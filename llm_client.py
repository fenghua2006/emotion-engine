"""薄 LLM 客户端：只负责把 prompt 发给兼容 OpenAI 的接口。

这一层不读取 ``EmotionEngine``，也不决定说什么；失败时返回 ``(None, None)``，
由调用方回退到本地模板。把网络细节放在这里，``engine.respond_llm()``
只保留编排与记忆写入。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def _backends(api_key: Optional[str], base_url: str, model: str,
              fallback_key: Optional[str], fallback_url: str,
              fallback_model: str) -> List[Tuple[str, str, str, str]]:
    backends = [
        (
            api_key or os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url,
            model,
            "DeepSeek",
        ),
    ]
    if fallback_key:
        backends.append((fallback_key, fallback_url, fallback_model, "Kimi"))
    return backends


def call_llm(prompt: str, *,
             api_key: Optional[str] = None,
             base_url: str = DEFAULT_BASE_URL,
             model: str = DEFAULT_MODEL,
             fallback_key: Optional[str] = None,
             fallback_url: str = "https://api.moonshot.cn/v1",
             fallback_model: str = "moonshot-v1-8k",
             timeout: float = 15.0,
             max_tokens: int = 200,
             temperature: float = 0.7) -> Tuple[Optional[str], Optional[str]]:
    """按主 API → fallback API 的顺序尝试；返回 (文本, backend 标签)。

    任一后端成功即返回；全部失败或没有 key 时返回 ``(None, None)``。
    异常被吞掉是刻意的——引擎永远不能因为网络问题而中断。
    """
    for key, url, mdl, tag in _backends(
        api_key, base_url, model, fallback_key, fallback_url, fallback_model,
    ):
        if not key:
            continue
        try:
            data = json.dumps({
                "model": mdl,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{url}/chat/completions",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                text = body["choices"][0]["message"]["content"].strip()
                if text:
                    return text, tag
        except Exception:
            continue
    return None, None
