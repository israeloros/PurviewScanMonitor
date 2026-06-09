"""Notification handler with pluggable backends.

Supports webhook (Logic App/generic), SendGrid email, and extensible SMS.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

from models import ScanRun

logger = logging.getLogger(__name__)


class NotificationHandler:
    """Pluggable notification system for scan monitoring alerts."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        sendgrid_api_key: Optional[str] = None,
        email_to: Optional[str] = None,
        email_from: Optional[str] = None,
    ):
        self._webhook_url = webhook_url or os.environ.get("NOTIFICATION_WEBHOOK_URL", "")
        self._sendgrid_key = sendgrid_api_key or os.environ.get("SENDGRID_API_KEY", "")
        self._email_to = email_to or os.environ.get("NOTIFICATION_EMAIL_TO", "")
        self._email_from = email_from or os.environ.get("NOTIFICATION_EMAIL_FROM", "")

    def notify_threshold_exceeded(
        self, scan_run: ScanRun, threshold: int, correlation_id: str
    ) -> None:
        """Notify that a scan has exceeded its threshold."""
        message = (
            f"⚠️ Scan Threshold Exceeded\n"
            f"Data Source: {scan_run.data_source_name}\n"
            f"Scan: {scan_run.scan_name}\n"
            f"Run ID: {scan_run.run_id}\n"
            f"Duration: {scan_run.duration_minutes:.1f} minutes\n"
            f"Threshold: {threshold} minutes\n"
            f"Correlation ID: {correlation_id}"
        )
        self._send(
            subject="Purview Scan Threshold Exceeded",
            message=message,
            severity="warning",
            correlation_id=correlation_id,
        )

    def notify_scan_cancelled(self, scan_run: ScanRun, correlation_id: str) -> None:
        """Notify that a scan has been auto-cancelled."""
        message = (
            f"🛑 Scan Auto-Cancelled\n"
            f"Data Source: {scan_run.data_source_name}\n"
            f"Scan: {scan_run.scan_name}\n"
            f"Run ID: {scan_run.run_id}\n"
            f"Duration: {scan_run.duration_minutes:.1f} minutes\n"
            f"Correlation ID: {correlation_id}"
        )
        self._send(
            subject="Purview Scan Cancelled",
            message=message,
            severity="critical",
            correlation_id=correlation_id,
        )

    def notify_error(self, context: str, error: str) -> None:
        """Notify about an error during monitoring."""
        message = f"❌ Monitoring Error\nContext: {context}\nError: {error}"
        self._send(
            subject="Purview Scan Monitor Error",
            message=message,
            severity="error",
            correlation_id="",
        )

    def _send(self, subject: str, message: str, severity: str, correlation_id: str) -> None:
        """Send notification through all configured channels."""
        if self._webhook_url:
            self._send_webhook(subject, message, severity, correlation_id)
        if self._sendgrid_key and self._email_to:
            self._send_email(subject, message)

    def _send_webhook(
        self, subject: str, message: str, severity: str, correlation_id: str
    ) -> None:
        """Send notification via webhook (e.g., Logic App, Teams, Slack)."""
        payload = {
            "subject": subject,
            "message": message,
            "severity": severity,
            "correlationId": correlation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            response = requests.post(
                self._webhook_url,
                json=payload,
                timeout=10,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code < 300:
                logger.info("Webhook notification sent: %s", subject)
            else:
                logger.warning(
                    "Webhook notification failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
        except requests.exceptions.RequestException as e:
            logger.error("Webhook notification error: %s", e)

    def _send_email(self, subject: str, message: str) -> None:
        """Send email notification via SendGrid."""
        payload = {
            "personalizations": [{"to": [{"email": self._email_to}]}],
            "from": {"email": self._email_from},
            "subject": subject,
            "content": [{"type": "text/plain", "value": message}],
        }
        try:
            response = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                timeout=10,
                headers={
                    "Authorization": f"Bearer {self._sendgrid_key}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code in (200, 202):
                logger.info("Email notification sent to %s: %s", self._email_to, subject)
            else:
                logger.warning(
                    "Email send failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
        except requests.exceptions.RequestException as e:
            logger.error("Email notification error: %s", e)
