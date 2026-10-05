from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from agora.api.contracts import CreateCardRequest
from agora.api.storage import IdempotencyStore
from agora.board import BoardState
from agora.broker.client import BrokerTimeout
from agora.broker.contracts import BrokerPolicy, System1Judgment, validate_capabilities
from agora.broker.executor import BrokerExecutor
from agora.broker.main import _parser
from agora.broker.review_gate import ReviewGate, ReviewGateConfig
from agora.cards import Card
from agora.remote.client import AgoraApiError
from conftest import create_card, write_profile
from test_ai_runner import _runner
from test_broker import FakeBroker, _broker_client, _work


class System1Broker(FakeBroker):
    def __init__(self) -> None:
        super().__init__(contract_version="2.11")
        self.judgments: list[dict[str, Any]] = []
        self.judge_timeouts: list[float] = []
        self.system1_enabled = True
        self.judge_failure: str | None = None
        self.judgment: dict[str, Any] = {
            "use_case": "agora_review_gate",
            "accepted": True,
            "decision": True,
            "confidence": 0.99,
            "confidence_is_calibrated": False,
            "alternatives": [{"value": False, "confidence": 0.01}],
            # The Broker's real order is Ollama/Nimble first, then Laya (Client_API 15.4).
            "provider": "ollama_system1",
            "model": "test-judge",
            "latency_ms": 2.0,
            "fallback_used": False,
            "reason_code": None,
            "attempts": [
                {
                    "provider": "ollama_system1",
                    "model": "test-judge",
                    "latency_ms": 2.0,
                    "reason_code": None,
                    "tokens_input": 25,
                    "tokens_output": 1,
                }
            ],
        }

    @property
    def capabilities_payload(self) -> dict[str, object]:
        return {**super().capabilities_payload, "system1_judgments": self.system1_enabled}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/system1/judge":
            assert request.headers["x-admin-token"] == self.token
            self.judgments.append(json.loads(request.content))
            self.judge_timeouts.append(request.extensions["timeout"]["read"])
            if self.judge_failure == "timeout":
                raise httpx.ReadTimeout("test timeout", request=request)
            if self.judge_failure == "offline":
                raise httpx.ConnectError("test offline", request=request)
            if self.judge_failure == "http":
                return httpx.Response(503, json={"detail": "test failure"})
            if self.judge_failure == "json":
                return httpx.Response(200, text="not JSON")
            return httpx.Response(200, json=self.judgment)
        return super().handle(request)


def context() -> dict[str, Any]:
    return {
        "goal": "Write a verified report",
        "task_type": "report",
        "acceptance_criteria": ["Explain the result and cite the verification"],
        "agent_output": "The result is correct; verification: report-structure passed.",
        "verifications": [{"name": "report-structure", "status": "passed", "required": True}],
        "required_evidence": ["verification"],
        "evidence": {"verification": "check completed"},
    }


def gate_config(**overrides: Any) -> ReviewGateConfig:
    return ReviewGateConfig.model_validate(
        {
            "enabled": True,
            "shadow_mode": False,
            "reviewer_profiles": ["summarizer"],
            "task_types": ["report", "code"],
            "uncalibrated_policy": "allow",
            **overrides,
        }
    )


def execute(
    tmp_path: Path,
    fake: System1Broker,
    *,
    data: dict[str, Any] | None = None,
    config: ReviewGateConfig | None = None,
    policy: BrokerPolicy | None = None,
) -> tuple[Any, ReviewGate, list[tuple[str, str]]]:
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    card = Card.create(function="transform", request="summarize", origin="human")
    card.metadata["review_context"] = context() if data is None else data
    gate = ReviewGate(config or gate_config())
    executor = BrokerExecutor(
        _broker_client(fake), tmp_path / "AGENTS", policy or BrokerPolicy(), review_gate=gate
    )
    checkpoints: list[tuple[str, str]] = []
    result = executor.execute(
        _work(card), (), checkpoint=lambda task, key: checkpoints.append((task, key))
    )
    return result, gate, checkpoints


def test_verified_report_autoapproval_uses_only_synchronous_broker_judgment(tmp_path: Path) -> None:
    fake = System1Broker()
    result, gate, checkpoints = execute(tmp_path, fake)
    audit = result.audit["review_gate"]
    assert audit["reviewer_skipped"] and audit["confidence"] == 0.99
    assert audit["reason"] == "AUTOAPPROVED" and audit["threshold"] == 0.97
    assert result.task_id is None and result.model is None and checkpoints == []
    assert fake.submissions == []
    approval = json.loads(result.artifacts["review-approval.json"])
    assert approval["approved"] is True
    assert result.audit["total_cost_usd"] is None
    assert audit["reviewer_tokens_saved"] is None and audit["reviewer_time_saved_ms"] is None
    assert gate.metrics.counts["reviewers_skipped"] == 1
    assert audit["tokens_input"] == 25 and audit["tokens_output"] == 1
    payload = fake.judgments[0]
    assert set(payload) == {"use_case", "input", "decision_type", "instructions", "cloud_allowed"}
    assert payload["decision_type"] == "binary" and payload["cloud_allowed"] is False
    assert "Record" not in json.dumps(payload) and "PROFILE" not in json.dumps(payload)
    assert fake.judge_timeouts == [75.0]


def test_incomplete_code_keeps_reviewer(tmp_path: Path) -> None:
    fake = System1Broker()
    fake.judgment.update(decision=False, alternatives=[{"value": True, "confidence": 0.01}])
    data = context()
    data.update(task_type="code", agent_output="TODO: implement function")
    result, gate, _ = execute(tmp_path, fake, data=data)
    assert result.audit["review_gate"]["reason"] == "NOT_SATISFIED"
    assert len(fake.submissions) == gate.metrics.counts["reviewers_executed"] == 1


@pytest.mark.parametrize("status", ["failed", "missing"])
def test_required_tests_prevent_judgment_even_with_mock_099(tmp_path: Path, status: str) -> None:
    fake = System1Broker()
    data = context()
    data["verifications"][0]["status"] = status
    result, _, _ = execute(tmp_path, fake, data=data)
    assert result.audit["review_gate"]["reason"] == "REQUIRED_VERIFICATION_NOT_PASSED"
    assert fake.judgments == [] and len(fake.submissions) == 1


@pytest.mark.parametrize(
    "flag",
    [
        "mandatory_review",
        "explicit_review_requested",
        "sensitive",
        "destructive",
        "publication_requires_review",
        "deployment_requires_review",
    ],
)
def test_mandatory_review_cannot_be_overridden(tmp_path: Path, flag: str) -> None:
    fake = System1Broker()
    data = {**context(), flag: True}
    result, _, _ = execute(tmp_path, fake, data=data)
    assert result.audit["review_gate"]["reason"] == "MANDATORY_REVIEW"
    assert fake.judgments == [] and len(fake.submissions) == 1


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"evidence": {}}, "MISSING_REQUIRED_EVIDENCE"),
        ({"verifications": []}, "MISSING_REQUIRED_VERIFICATION"),
        ({"acceptance_criteria": [" "]}, "MISSING_ACCEPTANCE_CRITERIA"),
        ({"agent_output": " "}, "MISSING_OUTPUT_OR_GOAL"),
        ({"task_type": "unconfigured"}, "TASK_TYPE_DISABLED"),
        ({"mandatory_review": "false"}, "INVALID_REVIEW_CONTEXT"),
    ],
)
def test_missing_or_invalid_inputs_keep_reviewer(
    tmp_path: Path,
    change: dict[str, Any],
    reason: str,
) -> None:
    fake = System1Broker()
    result, _, _ = execute(tmp_path, fake, data={**context(), **change})
    assert result.audit["review_gate"]["reason"] == reason
    assert fake.judgments == [] and len(fake.submissions) == 1


def test_confidence_096_keeps_reviewer(tmp_path: Path) -> None:
    fake = System1Broker()
    fake.judgment["confidence"] = 0.96
    result, gate, _ = execute(tmp_path, fake)
    audit = result.audit["review_gate"]
    assert audit["reason"] == "LOW_CONFIDENCE" and audit["fallback"]
    assert not audit["reviewer_skipped"] and len(fake.submissions) == 1
    assert gate.metrics.counts["fallbacks"] == 1


def test_confidence_097_meets_inclusive_default_threshold(tmp_path: Path) -> None:
    fake = System1Broker()
    fake.judgment["confidence"] = 0.97
    result, _, _ = execute(tmp_path, fake)
    assert result.audit["review_gate"]["reviewer_skipped"]
    assert fake.submissions == []


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("timeout", "TIMEOUT"),
        ("offline", "BROKER_ERROR"),
        ("http", "BROKER_ERROR"),
        ("json", "INVALID_OUTPUT"),
    ],
)
def test_judge_failure_falls_back_once(tmp_path: Path, failure: str, reason: str) -> None:
    fake = System1Broker()
    fake.judge_failure = failure
    result, _, _ = execute(tmp_path, fake)
    assert result.audit["review_gate"]["reason"] == reason
    assert len(fake.judgments) == 1 and len(fake.submissions) == 1


def test_ollama_failure_and_laya_fallback_can_still_approve(tmp_path: Path) -> None:
    fake = System1Broker()
    fake.judgment.update(provider="laya_mcp", fallback_used=True, reason_code="TIMEOUT")
    fake.judgment["attempts"].insert(
        0,
        {"provider": "ollama_system1", "model": None, "latency_ms": 1.0, "reason_code": "TIMEOUT"},
    )
    fake.judgment["attempts"][1]["provider"] = "laya_mcp"
    result, gate, _ = execute(tmp_path, fake)
    audit = result.audit["review_gate"]
    assert audit["provider"] == "laya_mcp" and audit["provider_fallback_used"]
    assert audit["reviewer_skipped"] and not audit["fallback"]
    assert audit["broker_reason_code"] == "TIMEOUT"
    assert audit["tokens_input"] is None  # The failed provider did not report tokens.
    assert gate.metrics.counts["provider_fallbacks"] == 1


def test_feature_off_preserves_submission_and_deliverable(tmp_path: Path) -> None:
    fake = System1Broker()
    result, _, _ = execute(tmp_path, fake, config=gate_config(enabled=False))
    baseline = FakeBroker(contract_version="2.11")
    card = Card.create(function="transform", request="summarize", origin="human")
    card.metadata["review_context"] = context()
    executor = BrokerExecutor(_broker_client(baseline), tmp_path / "AGENTS", BrokerPolicy())
    old = executor.execute(_work(card), (), checkpoint=lambda _task, _key: None)
    assert result.artifacts == old.artifacts
    # CARD timestamps are independent; compare the substantive task settings.
    for key in ("generation", "execution", "model_requirements", "output", "risk"):
        assert fake.submissions[0][key] == baseline.submissions[0][key]
    assert fake.judgments == [] and len(fake.submissions) == 1


def test_feature_off_sends_no_gate_progress_nor_audit(tmp_path: Path) -> None:
    """An older board rejects `review_gate_audit`: feature off must not send it."""
    fake = System1Broker()
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    card = Card.create(function="transform", request="summarize", origin="human")
    card.metadata["review_context"] = context()
    gate = ReviewGate(gate_config(enabled=False))
    executor = BrokerExecutor(
        _broker_client(fake), tmp_path / "AGENTS", BrokerPolicy(), review_gate=gate
    )
    progress: list[dict[str, Any]] = []
    result = executor.execute(
        _work(card), (), checkpoint=lambda _task, _key: None,
        review_checkpoint=progress.append,
    )
    assert progress == []
    assert "review_gate" not in result.audit
    assert not any("Review gate" in milestone for milestone in result.milestones)
    assert gate.metrics.counts["reviewable_outputs"] == 0


def test_shadow_mode_keeps_reviewer_and_audits_proposal(tmp_path: Path) -> None:
    fake = System1Broker()
    result, gate, _ = execute(tmp_path, fake, config=gate_config(shadow_mode=True))
    audit = result.audit["review_gate"]
    assert audit["would_skip"] and not audit["reviewer_skipped"]
    assert audit["reason"] == "SHADOW_APPROVAL" and audit["reviewer_executed"]
    assert audit["shadow_discrepancy"] is None
    assert len(fake.submissions) == 1 and gate.metrics.counts["reviewers_skipped"] == 0


def test_shadow_compares_only_explicit_reviewer_verdict(tmp_path: Path) -> None:
    fake = System1Broker()
    body = b'{"approved": false}'
    fake.artifact_bodies["art-final"] = body
    fake.artifacts[0]["sha256"] = hashlib.sha256(body).hexdigest()
    result, gate, _ = execute(
        tmp_path,
        fake,
        config=gate_config(shadow_mode=True, reviewer_verdict_field="approved"),
    )
    assert result.audit["review_gate"]["shadow_discrepancy"] is True
    assert gate.metrics.counts["shadow_comparisons"] == 1
    assert gate.metrics.counts["shadow_discrepancies"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"accepted": "true"},
        {"decision": 1},
        {"decision": "true"},
        {"confidence": "0.99"},
        {"confidence": 1.01},
        {"confidence": None},
        {"confidence_is_calibrated": "false"},
        {"use_case": "other"},
        {"provider": None},
        {"alternatives": []},
        {"attempts": "bad"},
        {"attempts": []},
        {"alternatives": [{"value": False, "confidence": 1.0}]},
    ],
)
def test_invalid_judgment_never_skips_reviewer(tmp_path: Path, change: dict[str, Any]) -> None:
    fake = System1Broker()
    fake.judgment.update(change)
    result, _, _ = execute(tmp_path, fake)
    assert result.audit["review_gate"]["reason"] == "INVALID_OUTPUT"
    assert len(fake.submissions) == 1


def test_broker_rejection_is_not_inferred_from_provider_or_http200(tmp_path: Path) -> None:
    fake = System1Broker()
    fake.judgment.update(
        accepted=False,
        decision=None,
        confidence=None,
        fallback_used=True,
        reason_code="LOW_CONFIDENCE",
    )
    result, gate, _ = execute(tmp_path, fake)
    audit = result.audit["review_gate"]
    assert audit["reason"] == "BROKER_REJECTED"
    assert len(fake.submissions) == 1
    # Observed on the real 2.11 broker: one attempt, `fallback_used: true` because
    # System-1 was exhausted. No provider switch happened, so none is counted.
    assert audit["broker_fallback_used"] and not audit["provider_fallback_used"]
    assert gate.metrics.counts["provider_fallbacks"] == 0


def test_uncalibrated_scores_require_explicit_operator_optin(tmp_path: Path) -> None:
    fake = System1Broker()
    result, _, _ = execute(tmp_path, fake, config=gate_config(uncalibrated_policy="review"))
    assert result.audit["review_gate"]["reason"] == "UNCALIBRATED_SCORE"
    assert len(fake.submissions) == 1


def test_task_type_threshold_is_applied(tmp_path: Path) -> None:
    fake = System1Broker()
    result, _, _ = execute(
        tmp_path, fake, config=gate_config(thresholds_by_task_type={"report": 0.995})
    )
    assert result.audit["review_gate"]["threshold"] == 0.995
    assert result.audit["review_gate"]["reason"] == "LOW_CONFIDENCE"


def test_missing_capability_defaults_to_old_flow(tmp_path: Path) -> None:
    fake = System1Broker()
    fake.system1_enabled = False
    result, _, _ = execute(tmp_path, fake)
    assert result.audit["review_gate"]["reason"] == "SYSTEM1_UNAVAILABLE"
    assert fake.judgments == [] and len(fake.submissions) == 1
    capabilities = validate_capabilities(FakeBroker().capabilities_payload)
    assert not capabilities.system1_judgments and not capabilities.system1_semantic_routing


def test_ordinary_cards_do_not_invoke_system1(tmp_path: Path) -> None:
    fake = System1Broker()
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    card = Card.create(function="transform", request="summarize", origin="human")
    executor = BrokerExecutor(
        _broker_client(fake),
        tmp_path / "AGENTS",
        BrokerPolicy(),
        review_gate=ReviewGate(gate_config()),
    )
    result = executor.execute(_work(card), (), checkpoint=lambda _task, _key: None)
    assert "review_gate" not in result.audit and fake.judgments == []
    assert len(fake.submissions) == 1


def test_exclusive_content_never_reaches_system1(tmp_path: Path) -> None:
    fake = System1Broker()
    result, _, _ = execute(tmp_path, fake, policy=BrokerPolicy(auxiliary_invocations=False))
    assert result.audit["review_gate"]["reason"] == "CONTENT_EXCLUSIVITY"
    assert fake.judgments == [] and len(fake.submissions) == 1


def test_structured_reviewer_document_is_never_replaced_by_binary_approval(tmp_path: Path) -> None:
    fake = System1Broker()
    body = b'{"approved": true, "review_notes": "Reviewed result"}'
    fake.artifact_bodies["art-final"] = body
    fake.artifacts[0]["sha256"] = hashlib.sha256(body).hexdigest()
    result, _, _ = execute(
        tmp_path,
        fake,
        policy=BrokerPolicy(
            output_schema={
                "type": "object",
                "required": ["approved", "review_notes"],
                "properties": {"approved": {"type": "boolean"}, "review_notes": {"type": "string"}},
            }
        ),
    )
    assert result.audit["review_gate"]["reason"] == "REVIEWER_OUTPUT_CONTRACT"
    assert fake.judgments == [] and len(fake.submissions) == 1


def test_approval_only_contract_can_use_gate_without_inventing_review_content(
    tmp_path: Path,
) -> None:
    fake = System1Broker()
    policy = BrokerPolicy(
        output_schema={
            "type": "object",
            "required": ["approved"],
            "additionalProperties": False,
            "properties": {"approved": {"type": "boolean"}},
        }
    )
    result, _, _ = execute(tmp_path, fake, policy=policy)
    assert fake.submissions == []
    assert result.audit["review_gate"]["reviewer_skipped"]
    assert json.loads(result.artifacts["review-approval.json"]) == {"approved": True}


def test_api_runner_closes_autoapproved_review_and_persists_audit(tmp_path: Path) -> None:
    fake = System1Broker()
    application, runner = _runner(tmp_path, fake)
    runner.executor.review_gate = ReviewGate(gate_config())
    client = runner.agora.client  # type: ignore[attr-defined]
    response = client.post(
        "/api/v1/cards",
        headers={"Idempotency-Key": "create-review"},
        json={
            "filename": "review.md",
            "function": "transform",
            "request": "summarize result",
            "review_context": context(),
        },
    )
    assert response.status_code == 201, response.text
    outcome = runner.run_once()
    assert outcome.status == "completed"
    done = Card.load(application.board.directory(BoardState.DONE) / "review.md")
    assert done.metadata["review_gate"]["reviewer_skipped"]
    assert done.metadata["execution"]["review_gate"]["reviewer_skipped"]
    assert "AUTOAPPROVED" in done.body and "remote" not in done.metadata
    assert json.loads(Path(done.metadata["paths"][0]).read_bytes())["approved"] is True
    assert fake.submissions == []


def test_gate_audit_survives_reviewer_timeout_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = System1Broker()
    application, runner = _runner(tmp_path, fake)
    runner.executor.review_gate = ReviewGate(gate_config(shadow_mode=True))
    card_path = create_card(application.board)
    card = Card.load(card_path)
    card.metadata["review_context"] = context()
    card.save()
    with monkeypatch.context() as patch:

        def wait(*_args: Any, **_kwargs: Any) -> Any:
            raise BrokerTimeout("still running")

        patch.setattr(type(runner.broker), "wait_task", wait)
        assert runner.run_once().status == "broker_running"
    stored = Card.load(application.board.directory(BoardState.IN_PROGRESS) / "task.md")
    assert stored.metadata["review_gate"]["reason"] == "SHADOW_APPROVAL"
    assert stored.metadata["review_gate"]["reviewer_submitted"]
    assert stored.metadata["review_gate"]["reviewer_executed"] is None
    assert (
        runner.executor.review_gate.metrics.snapshot()["counts"]["reviewer_execution_unobserved"]
        == 1
    )
    assert runner.run_once().status == "completed"
    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert done.metadata["execution"]["review_gate"]["reason"] == "SHADOW_APPROVAL"
    assert len(fake.judgments) == len(fake.submissions) == 1
    assert (
        runner.executor.review_gate.metrics.snapshot()["counts"]["reviewer_execution_unobserved"]
        == 0
    )


def test_contract_accepts_additive_response_fields_but_not_coerced_decisions() -> None:
    response = copy.deepcopy(System1Broker().judgment)
    response["new_field"] = {"anything": True}
    assert System1Judgment.model_validate(response).decision is True
    request = CreateCardRequest.model_validate(
        {
            "filename": "review.md",
            "function": "transform",
            "request": "summarize",
            "review_context": context(),
        }
    )
    assert request.review_context is not None


def test_evaluation_capability_is_additive_for_the_normal_review_gate() -> None:
    payload = {**System1Broker().capabilities_payload, "system1_evaluation": True}
    capabilities = validate_capabilities(payload)
    assert capabilities.system1_judgments
    assert capabilities.raw["system1_evaluation"] is True


@pytest.mark.parametrize("shadow_mode", [False, True])
def test_native_attempt_notes_preserve_the_accepted_gate_flow(
    tmp_path: Path, shadow_mode: bool,
) -> None:
    fake = System1Broker()
    fake.judgment["attempts"][0].update(
        decision=True,
        confidence=0.99,
        alternatives=[{"value": False, "confidence": 0.01}],
        score_source="native",
    )
    result, _, _ = execute(tmp_path, fake, config=gate_config(shadow_mode=shadow_mode))
    audit = result.audit["review_gate"]
    assert audit["accepted"] is True and audit["would_skip"] is True
    assert audit["reviewer_skipped"] is (not shadow_mode)
    assert len(fake.submissions) == int(shadow_mode)
    # Evaluation-only targeting would disable the broker's normal provider fallback.
    assert "target" not in fake.judgments[0]


@pytest.mark.parametrize(
    ("reason_code", "score_source", "attempt_confidence"),
    [("LOW_CONFIDENCE", "native", 0.62), ("SELF_REPORTED_SCORE", "self_reported", 1.0)],
)
def test_rejected_attempt_notes_never_become_a_business_approval(
    tmp_path: Path, reason_code: str, score_source: str, attempt_confidence: float,
) -> None:
    fake = System1Broker()
    fake.judgment.update(
        accepted=False,
        decision=None,
        confidence=None,
        alternatives=[],
        fallback_used=True,
        reason_code=reason_code,
    )
    fake.judgment["attempts"][0].update(
        reason_code=reason_code,
        decision=True,
        confidence=attempt_confidence,
        alternatives=[{"value": False, "confidence": 1.0 - attempt_confidence}],
        score_source=score_source,
    )
    # Even the explicit raw-score opt-in cannot override a broker rejection.
    result, gate, _ = execute(tmp_path, fake, config=gate_config(uncalibrated_policy="allow"))
    audit = result.audit["review_gate"]
    assert audit["reason"] == "BROKER_REJECTED"
    assert audit["broker_reason_code"] == reason_code
    assert audit["decision"] is None and audit["confidence"] is None
    assert audit["would_skip"] is False and audit["reviewer_skipped"] is False
    assert len(fake.submissions) == gate.metrics.counts["reviewers_executed"] == 1
    assert "review-approval.json" not in result.artifacts


def test_autoapproval_reuses_durable_decision_after_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = System1Broker()
    application, runner = _runner(tmp_path, fake)
    runner.executor.review_gate = ReviewGate(gate_config())
    path = create_card(application.board)
    card = Card.load(path)
    card.metadata["review_context"] = context()
    card.save()
    with monkeypatch.context() as patch:

        def close(*_args: Any, **_kwargs: Any) -> Any:
            raise AgoraApiError(503, "close unavailable")

        patch.setattr(type(runner.agora), "close_card", close)
        assert runner.run_once().status == "failed"
    assert runner.run_once().status == "completed"
    assert len(fake.judgments) == 1 and fake.submissions == []
    assert runner.executor.review_gate.metrics.counts["review_replays"] == 1


def test_changed_review_input_invalidates_cached_approval(tmp_path: Path) -> None:
    fake = System1Broker()
    application, runner = _runner(tmp_path, fake)
    runner.executor.review_gate = ReviewGate(gate_config())
    path = create_card(application.board)
    card = Card.load(path)
    card.metadata["review_context"] = context()
    card.save()
    work = runner.agora.work(("summarizer",))[0]
    first = runner.executor.execute(work, (), checkpoint=lambda _task, _key: None)
    card.metadata["review_gate"] = first.audit["review_gate"]
    card.metadata["review_context"]["agent_output"] = "Changed report"
    card.save()
    work = runner.agora.work(("summarizer",))[0]
    runner.executor.execute(work, (), checkpoint=lambda _task, _key: None)
    assert len(fake.judgments) == 2


def test_invalid_cached_score_requires_a_fresh_valid_judgment(tmp_path: Path) -> None:
    fake = System1Broker()
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    card = Card.create(function="transform", request="summarize", origin="human")
    card.metadata["review_context"] = context()
    executor = BrokerExecutor(
        _broker_client(fake),
        tmp_path / "AGENTS",
        BrokerPolicy(),
        review_gate=ReviewGate(gate_config()),
    )
    first = executor.execute(_work(card), (), checkpoint=lambda _task, _key: None)
    card.metadata["review_gate"] = first.audit["review_gate"]
    card.metadata["review_gate"]["confidence"] = "0.99"
    second = executor.execute(_work(card), (), checkpoint=lambda _task, _key: None)
    assert second.audit["review_gate"]["reviewer_skipped"]
    assert len(fake.judgments) == 2


@pytest.mark.parametrize("source", ["card", "profile"])
def test_existing_mandatory_flags_cannot_be_cleared_by_context(
    tmp_path: Path,
    source: str,
) -> None:
    fake = System1Broker()
    path = write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    card = Card.create(function="transform", request="summarize", origin="human")
    card.metadata["review_context"] = {**context(), "mandatory_review": False}
    if source == "card":
        card.metadata["mandatory_review"] = True
    else:
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "name: summarizer", "mandatory_review: true\nname: summarizer"
            ),
            encoding="utf-8",
        )
    executor = BrokerExecutor(
        _broker_client(fake),
        tmp_path / "AGENTS",
        BrokerPolicy(),
        review_gate=ReviewGate(gate_config()),
    )
    result = executor.execute(_work(card), (), checkpoint=lambda _task, _key: None)
    assert result.audit["review_gate"]["reason"] == "MANDATORY_REVIEW"
    assert fake.judgments == [] and len(fake.submissions) == 1


def test_unresolved_attachments_keep_reviewer(tmp_path: Path) -> None:
    fake = System1Broker()
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    card = Card.create(function="transform", request="summarize", origin="human")
    card.metadata["review_context"] = context()
    executor = BrokerExecutor(
        _broker_client(fake),
        tmp_path / "AGENTS",
        BrokerPolicy(),
        review_gate=ReviewGate(gate_config()),
    )
    result = executor.execute(
        _work(card), ({"type": "broker_file"},), checkpoint=lambda _task, _key: None
    )
    assert result.audit["review_gate"]["reason"] == "UNRESOLVED_ATTACHMENTS"
    assert fake.judgments == [] and len(fake.submissions) == 1


def test_config_template_and_cli_keep_explicit_activation_and_shadow_defaults() -> None:
    path = Path(__file__).resolve().parents[1] / "examples" / "review-gate.yml"
    config = ReviewGateConfig.load(path)
    assert config.enabled is False and config.shadow_mode is True
    assert config.threshold == 0.97 and config.timeout_seconds == 75.0
    assert config.uncalibrated_policy == "review" and config.reviewer_profiles == []
    args = _parser().parse_args(
        [
            "https://board.local",
            "--runner-id",
            "ai-1",
            "--profile",
            "summarizer",
            "--profiles-root",
            "AGENTS",
            "--ca-cert",
            "ca.pem",
            "--review-gate-config",
            str(path),
        ]
    )
    assert args.review_gate_config == path


def test_old_create_request_replays_after_api_upgrade(tmp_path: Path) -> None:
    application, runner = _runner(tmp_path, System1Broker())
    create_card(application.board)
    legacy = {
        "filename": "task.md",
        "function": "transform",
        "request": "summarize document",
        "inputs": None,
        "destination": None,
        "priority": "normal",
        "recipient": None,
        "max_attempts": 3,
        "body": "",
        "external_reference": None,
    }
    store = IdempotencyStore(tmp_path / ".agora" / "idempotency.json")
    store.save(
        "create",
        "old-create",
        store.digest(legacy),
        status_code=201,
        payload={"filename": "task.md", "state": BoardState.PENDING.value, "replayed": False},
    )
    client = runner.agora.client  # type: ignore[attr-defined]
    response = client.post("/api/v1/cards", headers={"Idempotency-Key": "old-create"}, json=legacy)
    assert response.status_code == 201, response.text
    assert response.json()["replayed"] is True
    assert len(application.board.paths(BoardState.PENDING)) == 1


def test_old_progress_request_replays_after_api_upgrade(tmp_path: Path) -> None:
    application, runner = _runner(tmp_path, System1Broker())
    create_card(application.board)
    application.board.claim("task.md", "ai-runner")
    legacy = {"runner_id": "ai-runner", "milestones": ["old progress"], "checkpoint": None}
    store = IdempotencyStore(tmp_path / ".agora" / "idempotency.json")
    store.save(
        "progress",
        "old-progress",
        store.digest({"filename": "task.md", "body": legacy}),
        status_code=200,
        payload={"filename": "task.md", "state": BoardState.IN_PROGRESS.value, "replayed": False},
    )
    client = runner.agora.client  # type: ignore[attr-defined]
    response = client.post(
        "/api/v1/cards/task.md/progress", headers={"Idempotency-Key": "old-progress"}, json=legacy
    )
    assert response.status_code == 200, response.text
    assert response.json()["replayed"] is True


def test_changed_review_context_is_not_an_idempotent_replay(tmp_path: Path) -> None:
    _, runner = _runner(tmp_path, System1Broker())
    client = runner.agora.client  # type: ignore[attr-defined]
    body: dict[str, Any] = {
        "filename": "review.md",
        "function": "transform",
        "request": "summarize result",
        "review_context": context(),
    }
    headers = {"Idempotency-Key": "create-review"}
    assert client.post("/api/v1/cards", headers=headers, json=body).status_code == 201
    body["review_context"]["agent_output"] = "A different report"
    assert client.post("/api/v1/cards", headers=headers, json=body).status_code == 409


def test_reviewer_replay_does_not_double_count_execution_or_shadow_disagreement(
    tmp_path: Path,
) -> None:
    fake = System1Broker()
    body = b'{"approved": false}'
    fake.artifact_bodies["art-final"] = body
    fake.artifacts[0]["sha256"] = hashlib.sha256(body).hexdigest()
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    card = Card.create(function="transform", request="summarize", origin="human")
    card.metadata["review_context"] = context()
    gate = ReviewGate(gate_config(shadow_mode=True, reviewer_verdict_field="approved"))
    executor = BrokerExecutor(
        _broker_client(fake), tmp_path / "AGENTS", BrokerPolicy(), review_gate=gate
    )
    for _ in range(2):
        result = executor.execute(_work(card), (), checkpoint=lambda _task, _key: None)
        assert result.audit["review_gate"]["shadow_discrepancy"] is True
    counts = gate.metrics.snapshot()["counts"]
    assert counts["reviewers_submitted"] == 2
    assert len(fake.billed_keys) == counts["reviewers_executed"] == 1
    assert counts["reviewer_execution_unobserved"] == 0
    assert counts["shadow_comparisons"] == counts["shadow_discrepancies"] == 1


@pytest.mark.parametrize(
    "config",
    [
        {"threshold": 0},
        {"threshold": 1.01},
        {"threshold": "0.97"},
        {"threshold": True},
        {"thresholds_by_task_type": {"report": float("nan")}},
        {"thresholds_by_task_type": {"report": 0.9}},
        {"thresholds_by_task_type": {"translation": 0.99}},
        {"instructions": "   "},
        {"instructions": ""},
        {"timeout_seconds": 0},
        {"enabled": "true"},
        {"shadow_mode": "false"},
        {"use_case": "not a valid id"},
    ],
)
def test_invalid_config_cannot_enable_gate(config: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        gate_config(**config)


def test_configured_instructions_are_sent_to_the_broker(tmp_path: Path) -> None:
    # The Broker profile has no instructions: Agora must always send some.
    fake = System1Broker()
    execute(tmp_path, fake, config=gate_config(instructions="Judge strictly."))
    assert fake.judgments[0]["instructions"] == "Judge strictly."
    default = System1Broker()
    execute(tmp_path / "default", default)
    assert default.judgments[0]["instructions"] == ReviewGateConfig().instructions
