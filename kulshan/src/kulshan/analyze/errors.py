"""Analysis error types."""


class CurAnalysisError(RuntimeError):
    """Raised when a local CUR analysis cannot be completed."""


# Backward-compatible alias
CurInvestigationError = CurAnalysisError
