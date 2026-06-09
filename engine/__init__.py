"""Scan monitoring engine.

Evaluates running scans against configured thresholds and orchestrates
cancellation and notification workflows.
"""

from __future__ import annotations

import logging
from typing import Optional

from clients import PurviewClient, PurviewClientError
from config import ConfigProvider
from models import MonitoringResult, ScanRun
from notifications import NotificationHandler

logger = logging.getLogger(__name__)


class ScanMonitorEngine:
    """Core engine that evaluates scans and takes enforcement actions."""

    def __init__(
        self,
        purview_client: PurviewClient,
        config_provider: ConfigProvider,
        notification_handler: NotificationHandler,
    ):
        self._purview = purview_client
        self._config = config_provider
        self._notifications = notification_handler

    def execute(self) -> list[MonitoringResult]:
        """Run one monitoring cycle. Idempotent and safe to re-invoke."""
        results: list[MonitoringResult] = []
        config = self._config.get_config()

        logger.info("Starting scan monitoring cycle.")

        try:
            running_scans = self._purview.get_all_running_scans()
        except PurviewClientError as e:
            logger.error("Failed to retrieve running scans: %s", e)
            self._notifications.notify_error(
                "Failed to retrieve running scans from Purview", str(e)
            )
            return results

        if not running_scans:
            logger.info("No running scans found. Cycle complete.")
            return results

        for scan_run in running_scans:
            result = self._evaluate_scan(scan_run, config)
            results.append(result)

        breached = [r for r in results if r.exceeded]
        logger.info(
            "Monitoring cycle complete: %d scans evaluated, %d threshold breaches.",
            len(results),
            len(breached),
        )
        return results

    def _evaluate_scan(self, scan_run: ScanRun, config) -> MonitoringResult:
        """Evaluate a single scan against its threshold."""
        result = MonitoringResult(scan_run=scan_run)
        threshold = self._config.get_threshold_for_scan(scan_run)
        result.threshold_minutes = threshold

        duration = scan_run.duration_minutes
        logger.info(
            "[%s] Scan '%s/%s' running for %.1f min (threshold: %d min) | correlation=%s",
            scan_run.status.value,
            scan_run.data_source_name,
            scan_run.scan_name,
            duration,
            threshold,
            result.correlation_id,
        )

        if duration <= threshold:
            result.exceeded = False
            return result

        # Threshold exceeded
        result.exceeded = True
        logger.warning(
            "THRESHOLD EXCEEDED: Scan '%s/%s' run=%s running %.1f min > %d min | correlation=%s",
            scan_run.data_source_name,
            scan_run.scan_name,
            scan_run.run_id,
            duration,
            threshold,
            result.correlation_id,
        )

        # Notify about threshold breach
        if config.notification_enabled:
            self._notifications.notify_threshold_exceeded(scan_run, threshold, result.correlation_id)
            result.notification_sent = True

        # Auto-cancel if enabled
        if config.auto_cancel_enabled:
            result = self._cancel_scan(scan_run, result, config)

        return result

    def _cancel_scan(self, scan_run: ScanRun, result: MonitoringResult, config) -> MonitoringResult:
        """Attempt to cancel a scan that exceeded its threshold."""
        try:
            success = self._purview.cancel_scan_run(
                data_source_name=scan_run.data_source_name,
                scan_name=scan_run.scan_name,
                run_id=scan_run.run_id,
            )
            result.cancelled = success
            if success:
                logger.info(
                    "Cancelled scan '%s/%s' run=%s | correlation=%s",
                    scan_run.data_source_name,
                    scan_run.scan_name,
                    scan_run.run_id,
                    result.correlation_id,
                )
                if config.notification_enabled:
                    self._notifications.notify_scan_cancelled(
                        scan_run, result.correlation_id
                    )
            else:
                result.cancel_error = "Cancel API returned non-success status"
                logger.error(
                    "Cancel failed for '%s/%s' run=%s | correlation=%s",
                    scan_run.data_source_name,
                    scan_run.scan_name,
                    scan_run.run_id,
                    result.correlation_id,
                )
        except PurviewClientError as e:
            result.cancel_error = str(e)
            logger.error(
                "Cancel error for '%s/%s' run=%s: %s | correlation=%s",
                scan_run.data_source_name,
                scan_run.scan_name,
                scan_run.run_id,
                e,
                result.correlation_id,
            )
            if config.notification_enabled:
                self._notifications.notify_error(
                    f"Failed to cancel scan {scan_run.scan_name}/{scan_run.run_id}",
                    str(e),
                )

        return result
