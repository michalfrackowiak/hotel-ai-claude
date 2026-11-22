class OpenLineError(Exception):
    """Base class for every error raised by django-openline."""


class OpenLineConfigError(OpenLineError):
    """The OPENLINE setting is missing something required."""


class OpenLineConnectionError(OpenLineError):
    """The OpenLine server could not be reached (DNS, TLS, timeout, ...)."""


class OpenLineAPIError(OpenLineError):
    """The OpenLine server answered with an HTTP error status."""

    def __init__(self, status_code, message="", body=None):
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__(f"OpenLine API error {status_code}: {message}".rstrip(": "))
