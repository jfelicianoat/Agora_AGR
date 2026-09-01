"""Remote-work service preserving the BOARD's single filesystem owner."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import asdict
from pathlib import Path

from agora.api.contracts import CloseRequest, CreateCardRequest, InputResource, WorkItem
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
            origin=principal,
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
            try:
                inputs = list(self._input_resources(card, outcome.card))
            except InvalidTransition:
                continue
            items.append(
                WorkItem(
                    filename=outcome.card,
                    function=card.function,
                    request=card.request,
                    priority=str(card.metadata.get("priority", "normal")),
                    attempts=card.attempts,
                    profile=outcome.profile,
                    card_document=card.serialize(),
                    inputs=inputs,
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

    def progress(
        self,
        filename: str,
        runner_id: str,
        milestones: list[str],
        checkpoint: dict[str, str] | None = None,
    ) -> Path:
        path = self._owned_claim(filename, runner_id)
        if checkpoint is not None:
            allowed = {"system", "task_id", "idempotency_key"}
            if set(checkpoint) != allowed or any(
                not value.strip() for value in checkpoint.values()
            ):
                raise InvalidTransition("remote checkpoint has invalid fields")
        self.board.progress(path, runner_id, milestones)
        if checkpoint is not None:
            card = Card.load(path)
            card.metadata["remote"] = dict(checkpoint)
            card.save(path)
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
        if request.execution_audit is not None:
            card = Card.load(claimed)
            card.metadata["execution"] = request.execution_audit
            card.save(claimed)
        return self.board.close(
            claimed,
            actor=request.runner_id,
            paths=paths,
            model=request.model,
        )

    def yield_card(
        self,
        filename: str,
        runner_id: str,
        reason: str,
        *,
        increment_attempts: bool = False,
    ) -> Path:
        claimed = self._owned_claim(filename, runner_id)
        return self.board.return_pending(
            claimed,
            actor=runner_id,
            reason=f"Remote runner yielded: {reason}",
            increment_attempts=increment_attempts,
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

    def locate(self, filename: str) -> tuple[BoardState, dict[str, object]]:
        name = _plain_card_name(filename)
        for state in BoardState:
            if (self.board.directory(state) / name).is_file():
                return state, self.card_payload(state, name)
        raise FileNotFoundError(f"CARD not found: {name}")

    def cancel(self, filename: str, *, principal: str, reason: str) -> Path:
        return self.board.cancel(filename, actor=principal, reason=reason)

    def artifact_path(self, filename: str, index: int) -> Path:
        _state, payload = self.locate(filename)
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise FileNotFoundError("CARD payload has no artifacts")
        paths = metadata.get("paths")
        if not isinstance(paths, list) or index < 0 or index >= len(paths):
            raise FileNotFoundError("CARD artifact not found")
        path = Path(str(paths[index])).resolve()
        try:
            path.relative_to(self.application.workspace)
        except ValueError as exc:
            raise InvalidTransition("artifact must stay inside Agora workspace") from exc
        if not path.is_file():
            raise FileNotFoundError("CARD artifact is missing")
        return path

    def claimed(self, runner_id: str) -> tuple[WorkItem, ...]:
        items: list[WorkItem] = []
        for path in self.board.paths(BoardState.IN_PROGRESS):
            card = Card.load(path)
            if card.metadata.get("agent") != runner_id:
                continue
            profile = card.metadata.get("profile")
            if not isinstance(profile, str) or not profile:
                continue
            items.append(
                WorkItem(
                    filename=path.name,
                    function=card.function,
                    request=card.request,
                    priority=str(card.metadata.get("priority", "normal")),
                    attempts=card.attempts,
                    profile=profile,
                    card_document=card.serialize(),
                    inputs=list(self._input_resources(card, path.name)),
                )
            )
        return tuple(items)

    def input_path(self, filename: str, key: str) -> Path:
        name = _plain_card_name(filename)
        for state in (BoardState.PENDING, BoardState.IN_PROGRESS):
            card_path = self.board.directory(state) / name
            if not card_path.is_file():
                continue
            card = Card.load(card_path)
            for resource, path in self._input_resources_with_paths(card, name):
                if resource.key == key:
                    return path
        raise FileNotFoundError(f"input not found for CARD {name}: {key}")

    def _owned_claim(self, filename: str, runner_id: str) -> Path:
        name = _plain_card_name(filename)
        path = self.board.directory(BoardState.IN_PROGRESS) / name
        card = Card.load(path)
        if card.metadata.get("agent") != runner_id:
            raise InvalidTransition("runner does not own this claim")
        return path

    def _input_resources(self, card: Card, filename: str) -> tuple[InputResource, ...]:
        return tuple(
            resource for resource, _path in self._input_resources_with_paths(card, filename)
        )

    def _input_resources_with_paths(
        self, card: Card, filename: str
    ) -> tuple[tuple[InputResource, Path], ...]:
        resources: list[tuple[InputResource, Path]] = []
        for label, candidate in _named_input_paths(card.metadata.get("inputs")):
            path = candidate if candidate.is_absolute() else self.application.workspace / candidate
            resolved = path.resolve()
            try:
                resolved.relative_to(self.application.workspace)
            except ValueError as exc:
                raise InvalidTransition("remote input must stay inside Agora workspace") from exc
            if not resolved.is_file():
                continue
            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
            safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-") or "input"
            key = f"{safe_label}-{digest[:12]}"
            resource = InputResource(
                key=key,
                filename=resolved.name,
                size_bytes=resolved.stat().st_size,
                sha256=digest,
                download_url=f"/api/v1/work/{filename}/inputs/{key}",
            )
            resources.append((resource, resolved))
        return tuple(resources)


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


def _named_input_paths(value: object, prefix: str = "input"):
    if isinstance(value, str) and value.strip():
        yield prefix, Path(value)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _named_input_paths(item, f"{prefix}-{index}")
    elif isinstance(value, dict):
        if isinstance(value.get("path"), str):
            yield prefix, Path(value["path"])
        else:
            for key, item in value.items():
                yield from _named_input_paths(item, f"{prefix}-{key}")
