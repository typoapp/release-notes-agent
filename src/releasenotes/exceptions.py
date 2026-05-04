class ReleaseNotesError(Exception):
    pass


class ConfigError(ReleaseNotesError):
    pass


class IngestionError(ReleaseNotesError):
    def __init__(self, source: str, message: str):
        super().__init__(f"[{source}] {message}")
        self.source = source


class LLMError(ReleaseNotesError):
    pass


class ValidationError(ReleaseNotesError):
    pass


class RateLimitError(IngestionError):
    pass
