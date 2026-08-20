"""Application-specific exceptions."""


class AtoError(Exception):
    """Base class for expected Ato errors."""


class ConfigurationError(AtoError):
    """Raised when required configuration is missing or invalid."""


class LLMError(AtoError):
    """Raised when a language model provider cannot produce a response."""


class MemoryStoreError(AtoError):
    """Raised when persistent memory cannot be read, written, or cleared."""


class ToolError(AtoError):
    """Raised when a tool request is invalid, unauthorized, or fails safely."""


class AuditError(ToolError):
    """Raised when required tool audit logging is unavailable."""
