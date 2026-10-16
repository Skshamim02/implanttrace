class DomainError(Exception):
    """Base class for business-rule violations raised by the service layer."""


class NotFound(DomainError):
    pass


class Conflict(DomainError):
    """The request is valid but breaks a production rule (e.g. steps out of order)."""


class PermissionDenied(DomainError):
    pass


class AuthenticationFailed(DomainError):
    pass


class ValidationFailed(DomainError):
    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.details = details or []
