import types
import pytest
from astor.chat import agent, tools
from astor.chat.tools import ReferencedItem


def _text_block(t): return types.SimpleNamespace(type="text", text=t)
def _tool_block(id, name, inp): return types.SimpleNamespace(type="tool_use", id=id, name=name, input=inp)
def _resp(stop, content): return types.SimpleNamespace(stop_reason=stop, content=content)


class _FakeMessages:
    def __init__(self, scripted): self._scripted = list(scripted); self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted): self.messages = _FakeMessages(scripted)


def test_returns_text_when_no_tools(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    client = _FakeClient([_resp("end_turn", [_text_block("Hi, how can I help?")])])
    out = agent.run_chat(object(), [{"role": "user", "content": "hello"}], client=client)
    assert out.reply == "Hi, how can I help?"
    assert out.items == []
    assert client.messages.calls[0]["model"] == agent.settings.chat_model


def test_runs_tool_then_returns_text_and_items(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    monkeypatch.setattr(tools, "dispatch",
        lambda s, name, args: ({"protocols": [{"id": "x1", "title": "WB", "product_count": 5}]},
                               [ReferencedItem("protocol", "x1", "WB")]))
    client = _FakeClient([
        _resp("tool_use", [_tool_block("t1", "search_protocols", {"query": "western"})]),
        _resp("end_turn", [_text_block("I found the Western Blot protocol.")]),
    ])
    out = agent.run_chat(object(), [{"role": "user", "content": "western blot?"}], client=client)
    assert out.reply == "I found the Western Blot protocol."
    assert out.items == [ReferencedItem("protocol", "x1", "WB")]
    # second create call carried a tool_result back to the model
    second = client.messages.calls[1]["messages"]
    assert any(
        isinstance(m.get("content"), list) and m["content"] and m["content"][0].get("type") == "tool_result"
        for m in second
    )


def test_items_are_deduped(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    monkeypatch.setattr(tools, "dispatch",
        lambda s, name, args: ({}, [ReferencedItem("product", "p1", "A"),
                                    ReferencedItem("product", "p1", "A")]))
    client = _FakeClient([
        _resp("tool_use", [_tool_block("t1", "search_products", {"query": "a"})]),
        _resp("end_turn", [_text_block("done")]),
    ])
    out = agent.run_chat(object(), [{"role": "user", "content": "a"}], client=client)
    assert out.items == [ReferencedItem("product", "p1", "A")]


def test_iteration_cap_returns_gracefully(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    monkeypatch.setattr(tools, "dispatch", lambda s, name, args: ({}, []))
    # always asks for a tool -> never a text stop
    always_tool = _resp("tool_use", [_tool_block("t", "search_products", {"query": "x"})])

    class _Loop:
        def __init__(self): self.messages = self
        def create(self, **k): return always_tool
    out = agent.run_chat(object(), [{"role": "user", "content": "x"}], client=_Loop(), max_iters=3)
    assert isinstance(out.reply, str)  # best-effort, no crash


def test_thinking_is_disabled_so_no_thinking_blocks(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    client = _FakeClient([_resp("end_turn", [_text_block("hi")])])
    agent.run_chat(object(), [{"role": "user", "content": "hi"}], client=client)
    assert client.messages.calls[0]["thinking"] == {"type": "disabled"}


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        agent.run_chat(object(), [{"role": "user", "content": "hi"}])


def test_agent_loop_uses_protocols_by_material(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "k")
    calls = []
    def fake_dispatch(s, name, args):
        calls.append(name)
        if name == "protocols_by_material":
            return ({"total": 1, "protocols": [{"id": "x1", "title": "Cell passaging",
                     "product_count": 2, "matched_material": "Trypsin-EDTA"}]},
                    [ReferencedItem("protocol", "x1", "Cell passaging")])
        return ({}, [])
    monkeypatch.setattr(tools, "dispatch", fake_dispatch)

    client = _FakeClient([
        _resp("tool_use", [_tool_block("t1", "protocols_by_material", {"material": "Trypsin-EDTA"})]),
        _resp("end_turn", [_text_block("30 protocols use Trypsin-EDTA. Best match: Cell passaging.")]),
    ])
    out = agent.run_chat(object(), [{"role": "user", "content": "what protocols use trypsin-edta?"}],
                         client=client)
    assert "protocols_by_material" in calls
    assert out.items == [ReferencedItem("protocol", "x1", "Cell passaging")]
