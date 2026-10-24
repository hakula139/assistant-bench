"""Errors raised by notification channels."""


class NotifyError(Exception):
    """Base class for notify errors."""


class ConfigError(NotifyError):
    """A channel configuration is missing or invalid."""


class DeliveryError(NotifyError):
    """The transport rejected a notification."""
