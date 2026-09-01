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
