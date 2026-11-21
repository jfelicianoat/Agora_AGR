"""Typed failures for the Agora core."""


class AgoraError(Exception):
    """Base class for expected Agora failures."""


class DocumentFormatError(AgoraError):
    """A Markdown contract has invalid or unreadable front matter."""


class CardFormatError(DocumentFormatError):
    """A CARD does not satisfy the F0 contract."""


class ProfileFormatError(DocumentFormatError):
    """A PROFILE does not satisfy the F0 contract."""


class SkillFormatError(DocumentFormatError):
    """A SKILL does not satisfy the shared-knowledge contract."""


class ClaimConflict(AgoraError):
    """Another worker won an atomic claim."""


class InvalidTransition(AgoraError):
    """A board state transition is not permitted."""


class BoardBusy(AgoraError):
    """Otro proceso tiene el candado del tablero; reintentar en un momento.

    No es un conflicto de estado: el API lo devuelve como 503 con `Retry-After`
    para que un cliente no lo confunda con «esa transición no vale» (B-3).
    """
