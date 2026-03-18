class TradingBotError(Exception):
    """Base exception for the trading bot."""


class BudgetExceededError(TradingBotError):
    """Raised when an agent or global budget is exhausted."""


class InvalidModelResponseError(TradingBotError):
    """Raised when a model response cannot be parsed."""


class RiskBlockedError(TradingBotError):
    """Raised when deterministic risk rules block an action."""


class ConfigError(TradingBotError):
    """Raised when runtime configuration is invalid."""
