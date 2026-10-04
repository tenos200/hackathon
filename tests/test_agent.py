"""Research assistant service: tool loop, citation checks, budget and separation (offline-unit, fake model)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from atlas.agent.main import create_agent_app
from atlas.agent.service import BudgetRefused, DailyBudget
from atlas.api.settings import load_settings

from .conftest import real_store_for
from .synthetic_world import build_full_world

AGENT_ENV = {"ATLAS_AGENT_OPENAI_API_KEY": "sk-test", "ATLAS_AGENT_MODEL": "test-model",
             "ATLAS_AGENT_INPUT_USD_PER_1M": "1", "ATLAS_AGENT_OUTPUT_USD_PER_1M": "4",
             "ATLAS_AGENT_DAILY_BUDGET_USD": "1", "ATLAS_ALLOWED_ORIGINS": '["https://app.example"]'}


def _response(rid, output=(), text="", tokens=(1000, 200)):
    return SimpleNamespace(id=rid, output=list(output), output_text=text,
                           usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]))


def _call(name, args, call_id):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


class ScriptedModel:
    """Looks up an entity, then cites one assertion it was shown plus IDs it was never shown."""

    def __init__(self, calc_id):
        self.requests = []
        self.calc_id = calc_id

    def create(self, **kwargs):
        self.requests.append(kwargs)
        n = len(self.requests)
        if n == 1:
            return _response("r1", [_call("get_entity", {"entity_id": "HGNC:900001"}, "c1")])
        if n == 2:
            shown = json.loads(kwargs["input"][0]["output"])
            self.shown = shown["data"]["assertions"][0]["assertion_id"]
            return _response("r2", [_call("find_paths", {"from_id": "ctx:syn-a-loss", "to_id": "org:syn-beta",
                                                         "max_length": 4}, "c2")])
        answer = {"blocks": [
            {"kind": "sourced", "text": "Recorded statement.", "assertion_ids": [self.shown, "assert:invented"],
             "calculation_ids": [], "node_ids": ["HGNC:900001", "HGNC:not-real"]},
            {"kind": "sourced", "text": "Claim with only an invented citation.", "assertion_ids": ["assert:invented"],
             "calculation_ids": [], "node_ids": []},
            {"kind": "computed", "text": "Similarity never looked up.", "assertion_ids": [],
             "calculation_ids": [self.calc_id], "node_ids": []},
            {"kind": "unknown", "text": "No subgroup evidence is recorded.", "assertion_ids": [],
             "calculation_ids": [], "node_ids": []}],
            "follow_up_questions": ["Who studies this gene?"]}
        return _response("r3", [], json.dumps(answer))


@pytest.fixture(scope="module")
def world_store(tmp_path_factory):
    _, package, _ = build_full_world(tmp_path_factory.mktemp("agent") / "ws")
    return real_store_for(package)


def test_chat_runs_tools_and_keeps_only_citations_the_model_was_shown(world_store):
    calc_id = sorted(world_store.calculations)[0]
    model = ScriptedModel(calc_id)
    app = create_agent_app(AGENT_ENV, client=model, store=world_store)
    with TestClient(app) as client:
        assert client.get("/readyz").json()["status"] == "ready"
        r = client.post("/v1/assistant/chat", json={"messages": [{"role": "user", "content": "Tell me about GENEA"}]},
                        headers={"Origin": "https://app.example"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "https://app.example"
    body = r.json()
    assert body["warnings"][0]["code"] == "AI_GENERATED" and body["snapshot_id"] == world_store.snapshot_id
    blocks = body["data"]["blocks"]
    assert blocks[0]["kind"] == "sourced" and blocks[0]["assertion_ids"] == [model.shown]
    assert blocks[0]["node_ids"] == ["HGNC:900001"] and blocks[0]["removed_citations"] == 1
    assert blocks[1]["kind"] == "unsupported" and blocks[1]["assertion_ids"] == []
    assert blocks[2]["kind"] == "unsupported"  # a real calculation the model never saw is not a valid citation
    assert blocks[3]["kind"] == "unknown"
    assert [c["name"] for c in body["data"]["tools_used"]] == ["get_entity", "find_paths"]
    # the tool loop chains responses and sends tool outputs back by call id
    assert model.requests[1]["previous_response_id"] == "r1"
    assert model.requests[1]["input"][0]["type"] == "function_call_output"
    assert model.requests[0]["text"]["format"]["strict"] is True
    assert body["data"]["usage"]["input_tokens"] == 3000


def test_not_ready_without_key_budget_or_prices(world_store):
    for missing in ("ATLAS_AGENT_OPENAI_API_KEY", "ATLAS_AGENT_MODEL", "ATLAS_AGENT_DAILY_BUDGET_USD"):
        env = {k: v for k, v in AGENT_ENV.items() if k != missing}
        with TestClient(create_agent_app(env, client=ScriptedModel("x"), store=world_store)) as client:
            assert client.get("/readyz").status_code == 503
            r = client.post("/v1/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 503 and r.json()["error"]["code"] == "ASSISTANT_UNAVAILABLE"
    zero = {**AGENT_ENV, "ATLAS_AGENT_DAILY_BUDGET_USD": "0"}
    with TestClient(create_agent_app(zero, client=ScriptedModel("x"), store=world_store)) as client:
        assert client.get("/readyz").status_code == 503


def test_budget_reserves_worst_case_and_refuses_when_spent():
    budget = DailyBudget(cap_usd=0.01, input_usd_per_1m=1, output_usd_per_1m=4)
    budget.reserve(0.006)
    with pytest.raises(BudgetRefused):
        budget.reserve(0.005)
    budget.settle(0.006, 0.002)
    budget.reserve(0.005)
    assert budget.status()["remaining_usd"] == pytest.approx(0.003)


def test_request_validation_and_read_api_refuses_the_assistant_key(world_store):
    with TestClient(create_agent_app(AGENT_ENV, client=ScriptedModel("x"), store=world_store)) as client:
        assert client.post("/v1/assistant/chat", json={"messages": []}).status_code == 422
        last_assistant = {"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}
        assert client.post("/v1/assistant/chat", json=last_assistant).status_code == 422
    settings = load_settings({"ATLAS_DATA_MODE": "synthetic_fixture", "ATLAS_AGENT_OPENAI_API_KEY": "sk-test"})
    assert any("ATLAS_AGENT_OPENAI_API_KEY" in e for e in settings.config_errors)
