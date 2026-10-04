"""Grounded research assistant: an OpenAI tool loop over the published snapshot.

The model may only use the read-only tools in `atlas.agent.tools`. Its final
answer is structured (strict JSON schema) as blocks, each declaring which
published assertions, calculations and nodes it rests on. After the model
answers, every cited ID is checked: it must have appeared in a tool result in
this conversation and must resolve in the snapshot. Invalid citations are
removed, and a block that claims to be sourced or computed but is left with no
valid citation is relabelled `unsupported` so the interface can flag it. The
assistant never writes to the snapshot and its text is not a reviewed record.

Paid calls are refused unless a key, a model, per-token prices and a positive
daily budget are configured. Each call reserves its worst-case cost first and
settles with the reported usage afterwards.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from atlas.agent.tools import TOOL_SPECS, Toolbox

PROMPT_VERSION = "assistant_v1"
MAX_ROUNDS = 6
MAX_OUTPUT_TOKENS = 2500
CHARS_PER_TOKEN = 3  # conservative estimate for budget reservation only

INSTRUCTIONS = """You are the research assistant of the Rare Disease Atlas, a research-navigation tool for patient
organisations, families, researchers and biotech scouts. You answer ONLY from the atlas, through the tools provided.

Rules:
- Look things up with the tools before answering. Start with `search` when the user names a disease, gene, symptom,
  organisation or mechanism; use `get_context`, `get_connections`, `get_actions`, `get_network`, `find_paths`,
  `get_entity` and `get_assertion` to go deeper. Prefer real IDs from tool results; never invent IDs.
- Every factual statement must cite the assertion_ids (published statements) or calculation_ids (computed similarity)
  it rests on, exactly as returned by the tools. If you cannot cite it, do not state it as fact.
- Explain in plain language a family can follow; define technical terms briefly. Keep answers short (2-6 blocks).
- Distinguish: recorded source statements; computed similarity (exploratory, not a validated disease class);
  unknowns. A null mechanism means "not recorded", not "none". A missing link is a coverage gap, not evidence of absence.
- Similar symptoms do not imply the same mechanism; shared genes do not imply the same effect. Say so when relevant.
- People and organisations are leads to ask about, never endorsements or confirmed collaborations.
- Never diagnose, assess an individual's mechanism, eligibility or prognosis, or recommend treatment. If asked, say
  the atlas cannot do that and suggest discussing with their clinical team; you may still show what research exists.
- Accepted records are source-fidelity reviewed, not expert validated; mention this when stakes are high.
- End with what remains unknown and one concrete next question or step a patient-group leader could take, grounded
  in the records you found (for example who to ask, which resource to check, which evidence is missing).

Answer format (JSON): blocks[] each with kind:
  "sourced"    - a statement backed by assertion_ids,
  "computed"   - a similarity result backed by calculation_ids,
  "unknown"    - what the records do not show (no citation needed),
  "guidance"   - plain-language framing or a suggested next step (cite IDs when it relies on records).
node_ids lists entity or context IDs the block mentions, so the interface can link them.
follow_up_questions: up to 3 short questions the user could ask next."""

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["blocks", "follow_up_questions"],
    "properties": {
        "blocks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "text", "assertion_ids", "calculation_ids", "node_ids"],
            "properties": {
                "kind": {"type": "string", "enum": ["sourced", "computed", "unknown", "guidance"]},
                "text": {"type": "string"},
                "assertion_ids": {"type": "array", "items": {"type": "string"}},
                "calculation_ids": {"type": "array", "items": {"type": "string"}},
                "node_ids": {"type": "array", "items": {"type": "string"}},
            }}},
        "follow_up_questions": {"type": "array", "items": {"type": "string"}},
    },
}


class ModelClient(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class BudgetRefused(RuntimeError):
    pass


class AssistantFailed(RuntimeError):
    pass


@dataclass
class DailyBudget:
    """In-process daily spend cap (UTC day). Resets when the service restarts; set the cap accordingly."""

    cap_usd: float
    input_usd_per_1m: float
    output_usd_per_1m: float
    spent_usd: float = 0.0
    reserved_usd: float = 0.0
    day: str = field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens / 1e6 * self.input_usd_per_1m + output_tokens / 1e6 * self.output_usd_per_1m

    def _roll(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self.day:
            self.day, self.spent_usd, self.reserved_usd = today, 0.0, 0.0

    def reserve(self, amount: float) -> None:
        with self._lock:
            self._roll()
            if self.cap_usd <= 0 or self.spent_usd + self.reserved_usd + amount > self.cap_usd:
                raise BudgetRefused("The assistant's daily budget is used up; try again tomorrow.")
            self.reserved_usd += amount

    def settle(self, reserved: float, actual: float) -> None:
        with self._lock:
            self.reserved_usd = max(0.0, self.reserved_usd - reserved)
            self.spent_usd += actual

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._roll()
            return {"day": self.day, "cap_usd": self.cap_usd, "spent_usd": round(self.spent_usd, 4),
                    "remaining_usd": round(max(0.0, self.cap_usd - self.spent_usd - self.reserved_usd), 4)}


@dataclass
class Turn:
    role: str
    content: str


def _usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return (int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0))


def answer(client: ModelClient, model: str, budget: DailyBudget, store, turns: list[Turn],
           focus_id: str | None = None) -> dict[str, Any]:
    toolbox = Toolbox(store)
    conversation = [{"role": t.role, "content": t.content} for t in turns]
    if focus_id:
        conversation.insert(0, {"role": "developer", "content": f"The user is currently viewing node {focus_id}."})
    pending: list[dict[str, Any]] = conversation
    previous_id: str | None = None
    sent_chars = len(INSTRUCTIONS) + len(json.dumps(TOOL_SPECS)) + sum(len(t.content) for t in turns)
    totals = [0, 0, 0.0]
    final: Any = None
    for round_no in range(MAX_ROUNDS):
        reserve = budget.cost(sent_chars // CHARS_PER_TOKEN + 1, MAX_OUTPUT_TOKENS)
        budget.reserve(reserve)
        last_round = round_no == MAX_ROUNDS - 1
        try:
            response = client.create(
                model=model, instructions=INSTRUCTIONS, input=pending, previous_response_id=previous_id,
                tools=TOOL_SPECS, tool_choice="none" if last_round else "auto", max_output_tokens=MAX_OUTPUT_TOKENS,
                text={"format": {"type": "json_schema", "name": "atlas_answer", "strict": True,
                                 "schema": ANSWER_SCHEMA}},
                metadata={"prompt_version": PROMPT_VERSION})
        except Exception:
            budget.settle(reserve, reserve)  # an uncertain outcome stays charged
            totals[2] += reserve
            raise
        tokens_in, tokens_out = _usage(response)
        actual = budget.cost(tokens_in, tokens_out)
        budget.settle(reserve, actual)
        totals[0] += tokens_in
        totals[1] += tokens_out
        totals[2] += actual
        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if not calls:
            final = response
            break
        previous_id = response.id
        pending = []
        for call in calls:
            output = toolbox.run(call.name, call.arguments)
            sent_chars += len(output)
            pending.append({"type": "function_call_output", "call_id": call.call_id, "output": output})
    if final is None:
        raise AssistantFailed("The assistant did not finish within its step limit; ask a narrower question.")
    try:
        parsed = json.loads(final.output_text)
    except (ValueError, TypeError) as exc:
        raise AssistantFailed("The assistant returned an unreadable answer; please retry.") from exc
    return {"blocks": verify_blocks(parsed.get("blocks", []), toolbox),
            "follow_up_questions": [q for q in parsed.get("follow_up_questions", []) if isinstance(q, str)][:3],
            "tools_used": toolbox.calls,
            "usage": {"input_tokens": totals[0], "output_tokens": totals[1], "cost_usd": round(totals[2], 5)}}


def verify_blocks(blocks: list[dict[str, Any]], toolbox: Toolbox) -> list[dict[str, Any]]:
    out = []
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str) or not block["text"].strip():
            continue
        cited_a = [i for i in block.get("assertion_ids", []) if isinstance(i, str)]
        cited_c = [i for i in block.get("calculation_ids", []) if isinstance(i, str)]
        cited_n = [i for i in block.get("node_ids", []) if isinstance(i, str)]
        assertion_ids = list(dict.fromkeys(i for i in cited_a if toolbox.valid_assertion(i)))
        calculation_ids = list(dict.fromkeys(i for i in cited_c if toolbox.valid_calculation(i)))
        node_ids = list(dict.fromkeys(i for i in cited_n if toolbox.valid_node(i)))
        kind = block.get("kind")
        removed = (len(cited_a) - len(assertion_ids)) + (len(cited_c) - len(calculation_ids))
        grounded = bool(assertion_ids or calculation_ids)
        if kind == "sourced" and not assertion_ids:
            kind = "unsupported"
        elif kind == "computed" and not calculation_ids:
            kind = "unsupported"
        elif kind not in ("sourced", "computed", "unknown", "guidance"):
            kind = "unsupported"
        out.append({"kind": kind, "text": block["text"].strip(), "assertion_ids": assertion_ids,
                    "calculation_ids": calculation_ids, "node_ids": node_ids, "grounded": grounded,
                    "removed_citations": removed})
    return out
