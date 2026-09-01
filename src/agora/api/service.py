"""Remote-work service preserving the BOARD's single filesystem owner."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
from pathlib import Path

from agora.api.contracts import CloseRequest, CreateCardRequest, WorkItem
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.cards import Card
from agora.dispatcher import Dispatcher, DispatchStatus
from agora.documents import atomic_write_bytes
from agora.errors import InvalidTransition
from agora.harnesses import DeterministicHarness


class RemoteWorkService:
    def __init__(self, application: AgoraApplication, *, artifact_limit_bytes: int = 25_000_000):
        self.application = application
        self.board = application.board
        self.artifact_limit_bytes = artifact_limit_bytes

    def create_card(self, request: CreateCardRequest, *, principal: str) -> Path:
        card = Card.create(
            function=request.function,
            request=request.request,
            origin="agora",
            inputs=request.inputs,
            destination=request.destination,
            priority=request.priority,
            recipient=request.recipient,
            max_attempts=request.max_attempts,
            body=request.body,
        )
        card.metadata["origin_identity"] = principal
        card.append_record("agora-api", [f"CARD accepted from authenticated client {principal}."])
        return self.board.create(request.filename, card)

    def work(self, profiles: tuple[str, ...]) -> tuple[WorkItem, ...]:
        allowed = {item.strip().lower() for item in profiles if item.strip()}
        if not allowed:
            return ()
        dispatcher = Dispatcher(
            self.board,
            self.application.profiles_root,
            DeterministicHarness(self.application.workspace),
            max_dispatches_per_round=1_000_000,
        )
        outcomes = dispatcher.run_once(dry_run=True)
        items: list[WorkItem] = []
        for outcome in outcomes:
            if (
                outcome.status is not DispatchStatus.WOULD_DISPATCH
                or outcome.profile is None
                or outcome.profile.lower() not in allowed
            ):
                continue
            card = Card.load(self.board.directory(BoardState.PENDING) / outcome.card)
            items.append(
                WorkItem(
                    filename=outcome.card,
                    function=card.function,
                    request=card.request,
                    priority=str(card.metadata.get("priority", "normal")),
                    attempts=card.attempts,
                    profile=outcome.profile,
                )
            )
        return tuple(items)

    def claim(self, filename: str, runner_id: str, profile: str) -> Path:
        eligible = {item.filename: item for item in self.work((profile,))}
        selected = eligible.get(filename)
        if selected is None or selected.profile.lower() != profile.lower():
            raise InvalidTransition("CARD is not eligible for this runner/profile")
        path = self.board.claim(filename, runner_id)
        self.board.progress(path, runner_id, [f"Remote profile selected: {profile}."])
        card = Card.load(path)
        card.metadata["profile"] = profile
        card.save(path)
        return path

    def progress(self, filename: str, runner_id: str, milestones: list[str]) -> Path:
        path = self._owned_claim(filename, runner_id)
        self.board.progress(path, runner_id, milestones)
        return path

    def close(self, filename: str, request: CloseRequest) -> Path:
        claimed = self._owned_claim(filename, request.runner_id)
        artifact_root = self.application.workspace / "artifacts" / "remote" / Path(filename).stem
        paths: list[Path] = []
        total = 0
        for artifact in request.artifacts:
            name = _plain_artifact_name(artifact.name)
            try:
                payload = base64.b64decode(artifact.content_base64, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise InvalidTransition(f"Artifact {name} is not valid base64") from exc
            total += len(payload)
            if total > self.artifact_limit_bytes:
                raise InvalidTransition("Artifact payload exceeds configured limit")
            target = artifact_root / name
            atomic_write_bytes(target, payload)
            paths.append(target)
        return self.board.close(
            claimed,
            actor=request.runner_id,
            paths=paths,
            model=request.model,
        )

    def yield_card(self, filename: str, runner_id: str, reason: str) -> Path:
        claimed = self._owned_claim(filename, runner_id)
        return self.board.return_pending(
            claimed,
            actor=runner_id,
            reason=f"Remote runner yielded: {reason}",
            increment_attempts=False,
        )

    def unblock(self, filename: str, *, principal: str, reason: str) -> Path:
        return self.board.unblock(filename, actor=principal, reason=reason)

    def card_payload(self, state: BoardState, filename: str) -> dict[str, object]:
        detail = self.application.card_detail(state, filename)
        return {
            "summary": asdict(detail.summary),
            "metadata": detail.metadata,
            "body": detail.body,
            "record": detail.record,
        }

    def _owned_claim(self, filename: str, runner_id: str) -> Path:
        name = _plain_card_name(filename)
        path = self.board.directory(BoardState.IN_PROGRESS) / name
        card = Card.load(path)
        if card.metadata.get("agent") != runner_id:
            raise InvalidTransition("runner does not own this claim")
        return path


def _plain_card_name(filename: str) -> str:
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix.lower() != ".md":
        raise ValueError("CARD filename must be a plain .md filename")
    return filename


def _plain_artifact_name(filename: str) -> str:
    candidate = Path(filename)
    if candidate.name != filename or filename in {".", ".."}:
        raise ValueError("artifact name must not contain a path")
    return filename
