"""LLM / 搜索调用的录制与回放（DESIGN 8.4）。

关键点：``LLMClient.invoke/stream_invoke/structured_invoke`` 内部会给 ``user_prompt``
注入「今天的实际时间是…」前缀（engines/common/llm_client.py），因此**必须在 client
方法边界**对「未加时戳的 user_prompt」做哈希，不能对账本里的 prompt_text 哈希。

fixture 默认回放，真实调用由 ``--live`` 显式开启并录制。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional


def make_replay_key(engine: str, method: str, system_prompt: str, user_prompt: str) -> str:
    payload = json.dumps([engine, method, system_prompt, user_prompt], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ReplayStore:
    """内存 dict + 可选落盘 JSON。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self._data: dict = {}
        if path and path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))

    def record(self, key: str, record: dict):
        self._data[key] = record

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def save(self):
        if self.path:
            self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")


class RecordingLLMClient:
    """包装 LLMClient，在方法边界录制/回放。其余属性透传给 inner。"""

    def __init__(self, inner, store: ReplayStore, mode: str = "record", engine: str = ""):
        self._inner = inner
        self._store = store
        self._mode = mode          # record | replay
        self._engine = engine

    def _key(self, method: str, system_prompt: str, user_prompt: str) -> str:
        return make_replay_key(self._engine, method, system_prompt, user_prompt)

    def invoke(self, system_prompt: str, user_prompt: str, **kw) -> str:
        key = self._key("invoke", system_prompt, user_prompt)
        hit = self._store.get(key)
        if self._mode == "replay" and hit is not None:
            return hit["completion"]
        out = self._inner.invoke(system_prompt, user_prompt, **kw)
        if self._mode == "record":
            self._store.record(key, {"completion": out})
        return out

    def stream_invoke_to_string(self, system_prompt: str, user_prompt: str, **kw) -> str:
        key = self._key("stream", system_prompt, user_prompt)
        hit = self._store.get(key)
        if self._mode == "replay" and hit is not None:
            return hit["completion"]
        out = self._inner.stream_invoke_to_string(system_prompt, user_prompt, **kw)
        if self._mode == "record":
            self._store.record(key, {"completion": out})
        return out

    def structured_invoke(self, system_prompt: str, user_prompt: str, output_model, **kw):
        key = self._key("structured", system_prompt, user_prompt)
        hit = self._store.get(key)
        if self._mode == "replay" and hit is not None:
            return output_model.model_validate(hit["completion"])
        out = self._inner.structured_invoke(system_prompt, user_prompt, output_model, **kw)
        if self._mode == "record":
            self._store.record(key, {"completion": out.model_dump()})
        return out

    def __getattr__(self, name: str):
        return getattr(self._inner, name)
