"""The config error type, in its own module so every config module can import it without cycles."""


class ConfigError(ValueError):
    """Invalid configuration. Messages quote user-supplied values only via `secret_scan.show`."""
