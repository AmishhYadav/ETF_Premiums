"""Alerts (SPEC section 10): rules, Telegram sender, and the run loop behind ``etfp alerts``."""

from etfp.alerts.rules import Alert, AlertConfig, evaluate, load_alert_config
from etfp.alerts.runner import AlertDeliveryError, AlertsAlreadyRunningError, run_alerts
from etfp.alerts.telegram import MissingCredentialsError, TelegramError

__all__ = [
    "Alert",
    "AlertConfig",
    "AlertDeliveryError",
    "AlertsAlreadyRunningError",
    "MissingCredentialsError",
    "TelegramError",
    "evaluate",
    "load_alert_config",
    "run_alerts",
]
