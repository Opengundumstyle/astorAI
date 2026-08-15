import types
import pytest
from astor.chat import agent, tools
from astor.chat.tools import ReferencedItem


def _delta(t):
    return types.SimpleNamespace(type="content_block_delta",
                                 delta=types.SimpleNamespace(type="text_delta", text=t))
def _text_block(t): return types.SimpleNamespace(type="text", text=t)
def _tool_block(id, name, inp): return types.SimpleNamespace(type="tool_use", id=id, name=name, input=inp)
def _final(stop, content): return types.SimpleNamespace(stop_reason=stop, content=content)


class _FakeStream:
    def __init__(self, events, final): self._events = events; self._final = final
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(self._events)
    def get_final_message(self): return self._final


class _FakeMessages:
    def __init__(self, scripted): self._scripted = list(scripted); self.calls = []
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted): self.messages = _FakeMessages(scripted)


def _run(client, monkeypatch, dispatch=None):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    if dispatch is not None:
        monkeypatch.setattr(tools, "dispatch", dispatch)
    return list(agent.run_chat_stream(object(), [{"role": "user", "content": "hi"}], client=client))


def test_immediate_text_streams_delta_then_items_then_done(monkeypatch):
    client = _FakeClient([_FakeStream([_delta("Hel"), _delta("lo")],
                                      _final("end_turn", [_text_block("Hello")]))])
    events = _run(client, monkeypatch)
    assert events == [
        {"type": "delta", "text": "Hel"},
        {"type": "delta", "text": "lo"},
        {"type": "items", "items": []},
        {"type": "done"},
    ]
    assert client.messages.calls[0]["thinking"] == {"type": "disabled"}


def test_tool_round_then_text_emits_status_and_items(monkeypatch):
    client = _FakeClient([
        _FakeStream([], _final("tool_use", [_tool_block("t1", "search_protocols", {"query": "wb"})])),
        _FakeStream([_delta("Found it.")], _final("end_turn", [_text_block("Found it.")])),
    ])
    dispatch = lambda s, name, args, request_context=None: (
        {"protocols": [{"id": "x1", "title": "WB", "product_count": 5}]},
        [ReferencedItem("protocol", "x1", "WB")])
    events = _run(client, monkeypatch, dispatch)
    assert events[0] == {"type": "status", "text": "Searching protocols…"}
    assert {"type": "delta", "text": "Found it."} in events
    assert {"type": "items", "items": [{"type": "protocol", "id": "x1", "name": "WB"}]} in events
    assert events[-1] == {"type": "done"}
    # second stream() call carried a tool_result back
    second = client.messages.calls[1]["messages"]
    assert any(isinstance(m.get("content"), list) and m["content"]
               and m["content"][0].get("type") == "tool_result" for m in second)


def test_items_deduped(monkeypatch):
    client = _FakeClient([
        _FakeStream([], _final("tool_use", [_tool_block("t1", "search_products", {"query": "a"})])),
        _FakeStream([_delta("ok")], _final("end_turn", [_text_block("ok")])),
    ])
    dispatch = lambda s, name, args, request_context=None: (
        {}, [ReferencedItem("product", "p1", "A"), ReferencedItem("product", "p1", "A")])
    events = _run(client, monkeypatch, dispatch)
    items_ev = next(e for e in events if e["type"] == "items")
    assert items_ev["items"] == [{"type": "product", "id": "p1", "name": "A"}]


def test_missing_key_yields_error(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", None)
    events = list(agent.run_chat_stream(object(), [{"role": "user", "content": "hi"}]))
    assert events and events[0]["type"] == "error"


def test_status_helper():
    assert agent._status_for(["search_protocols"]) == "Searching protocols…"
    assert agent._status_for(["search_products"]) == "Searching the catalog…"
    assert agent._status_for(["protocol_products"]) == "Pulling the details…"
    assert agent._status_for(["mystery"]) == "Looking that up…"
