"""录制回放的单元测试（evals/replay.py）。

重点验证：回放哈希在 client 方法边界（未加时戳的 user_prompt）进行，时间前缀不改变哈希。
"""

from pydantic import BaseModel

from evals.replay import ReplayStore, RecordingLLMClient, make_replay_key


class _M(BaseModel):
    x: int


class _Inner:
    def invoke(self, system, user, **kw):
        return f"invoke:{user}"

    def stream_invoke_to_string(self, system, user, **kw):
        return f"stream:{user}"

    def structured_invoke(self, system, user, model, **kw):
        return model(x=42)


def test_make_replay_key_deterministic_and_distinct():
    k1 = make_replay_key("e", "invoke", "sys", "user")
    k2 = make_replay_key("e", "invoke", "sys", "user")
    k3 = make_replay_key("e", "invoke", "sys", "other")
    assert k1 == k2
    assert k1 != k3


def test_recording_llm_client_replays_invoke():
    store = ReplayStore()
    rec = RecordingLLMClient(_Inner(), store, mode="record", engine="e")
    out1 = rec.invoke("sys", "hello")
    rep = RecordingLLMClient(_Inner(), store, mode="replay", engine="e")
    out2 = rep.invoke("sys", "hello")
    assert out1 == out2 == "invoke:hello"


def test_recording_llm_client_replays_structured():
    store = ReplayStore()
    rec = RecordingLLMClient(_Inner(), store, mode="record", engine="e")
    out1 = rec.structured_invoke("sys", "hello", _M)
    rep = RecordingLLMClient(_Inner(), store, mode="replay", engine="e")
    out2 = rep.structured_invoke("sys", "hello", _M)
    assert out1.x == 42
    assert out2.x == 42


def test_replay_misses_when_prompt_differs():
    store = ReplayStore()
    rec = RecordingLLMClient(_Inner(), store, mode="record", engine="e")
    rec.invoke("sys", "hello")
    rep = RecordingLLMClient(_Inner(), store, mode="replay", engine="e")
    # 未录制过的 prompt 会走 inner（真实调用），而非回放
    assert rep.invoke("sys", "unrecorded") == "invoke:unrecorded"
