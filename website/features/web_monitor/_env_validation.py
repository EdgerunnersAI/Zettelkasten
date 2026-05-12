"""WAVE-D Phase 1 WM-14: boot-time validation for SLACK_WEBHOOK_* env vars.

Each of the 3 web_monitor channels (app_errors, do_alerts, user_activity)
reads its webhook URL lazily from ``os.getenv(SLACK_ENV_VAR)`` at each
notify call. That degrades gracefully (logs the event locally + skips
Slack post) but the misconfig is invisible until the FIRST event fires —
which on a quiet production box could be hours after a deploy.

This module exposes ``log_web_monitor_env_warnings()`` so the FastAPI
factory can audit env at boot. A missing variable yields a single
``logger.warning`` line per channel so the structured-logs pipeline picks
it up and ops can confirm webhook wiring without waiting for the first
alert.

Why warn-not-fail: a deploy with missing webhook env vars is degraded, not
broken — the API still serves traffic. Crashing the worker on boot for a
missing optional secret would block deploys for a non-critical feature.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("website.web_monitor.env_validation")

# Tuple kept in sync with website.features.web_monitor.{App_Errors,
# DO_Alerts, User_Activity} SLACK_ENV_VAR module constants. Hard-coded
# rather than imported to avoid pulling the 3 channel modules into module
# scope before app factory has set up the logger.
_REQUIRED_SLACK_WEBHOOKS: tuple[tuple[str, str], ...] = (
    ("SLACK_WEBHOOK_APP_ERRORS", "app_errors"),
    ("SLACK_WEBHOOK_DO_ALERT", "do_alerts"),
    ("SLACK_WEBHOOK_USER_ACTIVITY", "user_activity"),
)


def log_web_monitor_env_warnings() -> list[str]:
    """Audit Slack webhook env vars at app boot. Returns the unset var names.

    Side-effect: emits ``logger.warning`` for each missing variable. Return
    value is the list of unset env-var names — primarily so tests can
    assert the audit detected the expected misconfig without re-reading
    the captured log stream.

    Called once from ``create_app`` during startup. Safe to call from a
    pre-lifespan context — does not touch the event loop or any FastAPI
    machinery.
    """
    unset: list[str] = []
    for env_name, channel in _REQUIRED_SLACK_WEBHOOKS:
        if not os.getenv(env_name):
            unset.append(env_name)
            logger.warning(
                "web_monitor: %s is unset at boot — %s channel will log only "
                "(no Slack delivery). Set the webhook URL in the droplet "
                "env-file to enable alerts.",
                env_name,
                channel,
            )
    if not unset:
        logger.info("web_monitor: all 3 Slack webhook env vars present at boot")
    return unset


__all__ = ["log_web_monitor_env_warnings"]
