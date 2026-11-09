import json
import pytest
from agentguard.agent import Agent, GroqLlm
from agentguard.audit import AuditLog
from agentguard.gateway import Gateway
from agentguard.mandate import Mandate
from agentguard.policy import PolicyGate
from agentguard.risk import RiskEngine
from agentguard.stepup import StepUpManager
from tests.test_gateway import CATALOG, NOW, FakePayPal


class Script:
    def __init__(self, outs):
        self.outs, self.seen = list(outs), []

    def __call__(self, system, messages, tools):
        self.seen.append(list(messages))
        return self.outs.pop(0)


def call(name, **args):
    return {"text": "", "tool_call": {"name": name, "args": args}}


def agent_with(llm, tmp_path, max_steps=8):
    m = Mandate("u1", "s1", "n1", NOW, NOW + 600, "100.00", "USD", ("shop-a",))
    pp = FakePayPal()
    gw = Gateway(
        m,
        PolicyGate(m, CATALOG, clock=lambda: NOW),
        pp,
        AuditLog(str(tmp_path / "a.jsonl")),
        StepUpManager(),
        RiskEngine(),
        CATALOG,
    )
    return Agent(llm, gw, max_steps=max_steps), pp


def test_happy_conversation(tmp_path):
    llm = Script(
        [
            call("search_catalog", query="desk"),
            call("create_order", items=[{"item_id": "desk", "quantity": 1}]),
            {"text": "Order created, please approve it.", "tool_call": None},
        ]
    )
    a, pp = agent_with(llm, tmp_path)
    assert a.run("buy a desk") == "Order created, please approve it."
    assert pp.n == 1


def test_obedient_model_hijacked_by_injection_is_stopped(tmp_path):
    # The scripted "model" blindly follows the injected text and tries to buy gold.
    llm = Script(
        [
            call("search_catalog", query="pen"),
            call("create_order", items=[{"item_id": "gold", "quantity": 50}]),
            {"text": "tried", "tool_call": None},
        ]
    )
    a, pp = agent_with(llm, tmp_path)
    a.run("find a pen")
    tool_results = [m["result"] for m in a.messages if m["role"] == "tool"]
    assert tool_results[-1]["ok"] is False and pp.n == 0


def test_step_limit(tmp_path):
    llm = Script([call("search_catalog", query="pen")] * 3)
    a, _ = agent_with(llm, tmp_path, max_steps=3)
    assert "step limit" in a.run("loop")


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._b, self.text = status, body, json.dumps(body)

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        return self.resps.pop(0) if len(self.resps) > 1 else self.resps[0]


def groq_ok(content="", tool_calls=None):
    return FakeResp(
        200, {"choices": [{"message": {"content": content, "tool_calls": tool_calls}}]}
    )


def tc(name, args):
    return {
        "id": "x",
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def test_groq_request_and_parse():
    s = FakeSession(groq_ok("ok", [tc("search_catalog", '{"query": "pen"}')]))
    msgs = [
        {"role": "user", "text": "hi"},
        {
            "role": "assistant",
            "text": "",
            "tool_call": {"name": "search_catalog", "args": {"query": "x"}},
        },
        {"role": "tool", "name": "search_catalog", "result": {"ok": True}},
    ]
    out = GroqLlm("KEY", session=s)(
        "sys", msgs, [{"name": "t", "description": "d", "parameters": {}}]
    )
    assert out == {
        "text": "ok",
        "tool_call": {"name": "search_catalog", "args": {"query": "pen"}},
    }
    url, kw = s.calls[0]
    assert url == GroqLlm.URL and kw["headers"] == {"Authorization": "Bearer KEY"}
    body = kw["json"]
    assert [m["role"] for m in body["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assistant, tool = body["messages"][2], body["messages"][3]
    assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "query": "x"
    }
    assert (
        body["tools"][0]["type"] == "function"
        and body["model"] == "llama-3.3-70b-versatile"
    )


def test_groq_second_tool_call_gets_its_own_id():
    msgs = [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "", "tool_call": {"name": "a", "args": {}}},
        {"role": "tool", "name": "a", "result": {}},
        {"role": "assistant", "text": "", "tool_call": {"name": "b", "args": {}}},
        {"role": "tool", "name": "b", "result": {}},
    ]
    m = GroqLlm._messages("s", msgs)
    assert [x.get("tool_call_id") for x in m if x["role"] == "tool"] == [
        "call_1",
        "call_2",
    ]


def test_groq_skips_empty_assistant_message():
    m = GroqLlm._messages(
        "s",
        [
            {"role": "user", "text": "hi"},
            {"role": "assistant", "text": "", "tool_call": None},
        ],
    )
    assert [x["role"] for x in m] == ["system", "user"]


def test_groq_bad_arguments_and_extra_calls():
    out = GroqLlm(
        "k",
        session=FakeSession(
            groq_ok("", [tc("create_order", "{not json"), tc("other", "{}")])
        ),
    )("s", [], [])
    assert out["tool_call"] == {"name": "create_order", "args": {}}


def test_groq_retries_malformed_tool_call_once():
    s = FakeSession(
        FakeResp(400, {"error": {"code": "tool_use_failed"}}), groq_ok("fine")
    )
    assert GroqLlm("k", session=s)("s", [], [])["text"] == "fine" and len(s.calls) == 2


@pytest.mark.parametrize("status", [400, 401, 500])
def test_groq_errors_raise_without_leaking_key(status):
    s = FakeSession(FakeResp(status, {"error": "nope"}))
    with pytest.raises(RuntimeError) as e:
        GroqLlm("SECRETKEY", session=s)("s", [], [])
    assert "SECRETKEY" not in str(e.value) and len(s.calls) == 1


def test_groq_empty_choices():
    assert GroqLlm("k", session=FakeSession(FakeResp(200, {})))("s", [], []) == {
        "text": "",
        "tool_call": None,
    }


class HdrResp(FakeResp):
    def __init__(self, status, body, headers):
        super().__init__(status, body)
        self.headers = headers


def test_groq_waits_out_short_rate_limit():
    s = FakeSession(
        HdrResp(429, {"error": "rl"}, {"retry-after": "2"}), groq_ok("fine")
    )
    slept, shown = [], []
    llm = GroqLlm("k", session=s, sleep=slept.append, on_wait=shown.append)
    assert llm("s", [], [])["text"] == "fine"
    assert slept == [2.0] and shown == [2.0] and len(s.calls) == 2


def test_groq_gives_up_after_repeated_rate_limits():
    s = FakeSession(HdrResp(429, {"error": "rl"}, {"retry-after": "1"}))
    with pytest.raises(RuntimeError, match="429"):
        GroqLlm("k", session=s, sleep=lambda _: None)("s", [], [])
    assert len(s.calls) == 3


def test_groq_does_not_wait_out_a_daily_cap():
    s = FakeSession(HdrResp(429, {"error": "daily"}, {"retry-after": "3600"}))
    slept = []
    with pytest.raises(RuntimeError, match="429"):
        GroqLlm("k", session=s, sleep=slept.append)("s", [], [])
    assert slept == [] and len(s.calls) == 1


def test_groq_rate_limit_without_header_uses_default_wait():
    s = FakeSession(FakeResp(429, {"error": "rl"}), groq_ok("ok"))
    slept = []
    GroqLlm("k", session=s, sleep=slept.append)("s", [], [])
    assert slept == [10.0]


def test_groq_compacts_older_search_results_but_keeps_latest():
    big = {
        "ok": True,
        "items": [
            {
                "item_id": "a@x.com",
                "name": "N",
                "description": "long text",
                "merchant": "x.com",
                "price": "5.00",
                "currency": "USD",
            }
        ],
    }
    msgs = [
        {"role": "user", "text": "s1"},
        {
            "role": "assistant",
            "text": "",
            "tool_call": {"name": "search_catalog", "args": {}},
        },
        {"role": "tool", "name": "search_catalog", "result": big},
        {
            "role": "assistant",
            "text": "",
            "tool_call": {"name": "search_catalog", "args": {}},
        },
        {"role": "tool", "name": "search_catalog", "result": big},
    ]
    tools = [
        json.loads(m["content"])
        for m in GroqLlm._messages("s", msgs)
        if m["role"] == "tool"
    ]
    assert (
        "description" not in tools[0]["items"][0]
        and tools[0]["items"][0]["item_id"] == "a@x.com"
    )
    assert tools[1]["items"][0]["description"] == "long text"
    assert "description" in big["items"][0]
