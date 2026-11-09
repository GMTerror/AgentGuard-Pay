import pytest
from agentguard.agent import Agent
from agentguard.audit import AuditLog
from agentguard.gateway import Gateway
from agentguard.mandate import Mandate
from agentguard.policy import PolicyGate
from agentguard.risk import RiskEngine
from agentguard.semantic import IntentTracker, ToyEmbedder
from agentguard.stepup import StepUpManager
from demo.common import Script, say
from tests.test_autopay import FakeAutoPayPal
from tests.test_gateway import CATALOG, NOW


def tracker(*msgs, **kw):
    t = IntentTracker(ToyEmbedder(), **kw)
    for m in msgs:
        t.add(m)
    return t


def test_no_intent_or_empty_text_means_no_drift():
    assert tracker().drift("anything") == 0 and tracker("a mug").drift("   ") == 0


def test_match_and_mismatch():
    assert tracker("Buy me a mug").drift("Ceramic mug") == 0
    assert tracker("Buy me a mug").drift("Premium wellness bundle") == 1.0


def test_any_user_message_counts():
    assert tracker("a mug", "a notebook").drift("Hardcover notebook") == 0


def test_all_stopword_message_is_safe():
    assert 0 <= tracker("the a").drift("mug") <= 1


def test_partial_similarity_maps_linearly():
    vecs = {"ask": [1.0, 0.0], "item": [0.225, (1 - 0.225**2) ** 0.5]}
    emb = lambda texts: [vecs[t] for t in texts]
    t = IntentTracker(emb, floor=0.30)
    t.add("ask")
    assert t.drift("item") == pytest.approx(0.5)


def test_risk_accepts_semantic_signal():
    r = RiskEngine()
    r.update(semantic=1.0)
    assert r.level() == "warn"


def test_agent_passes_user_words_to_tracker():
    seen = []
    Agent(Script([say("hi")]), None, on_user=seen.append).run("buy a pen")
    assert seen == ["buy a pen"]


@pytest.fixture
def env(tmp_path):
    m = Mandate("u1", "s1", "n1", NOW, NOW + 600, "100.00", "USD", ("shop-a",))
    pp, stepup = FakeAutoPayPal(), StepUpManager()
    gw = Gateway(
        m,
        PolicyGate(m, CATALOG, clock=lambda: NOW),
        pp,
        AuditLog(str(tmp_path / "a.jsonl")),
        stepup,
        RiskEngine(),
        CATALOG,
        vault_token="TOK",
        intent=tracker("Buy me a pen"),
    )
    return gw, pp, stepup


def buy(gw, item):
    return gw.call_tool("create_order", {"items": [{"item_id": item, "quantity": 1}]})


def test_matching_item_pays_without_approval(env):
    gw, pp, _ = env
    assert buy(gw, "pen")["ok"] and len(pp.charges) == 1


def test_wrong_item_inside_limit_waits_for_user(env):
    gw, pp, stepup = env
    r = buy(gw, "desk")
    assert r["pending_step_up"] and pp.charges == []
    assert "does not match" in stepup.get(r["pending_step_up"])["reason"]
