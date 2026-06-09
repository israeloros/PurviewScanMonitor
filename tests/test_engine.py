"""Unit tests for the scan monitoring engine."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import MonitorConfig, ScanRun, ScanStatus, ThresholdOverride
from config import ConfigProvider
from engine import ScanMonitorEngine


def _make_scan_run(
    scan_name: str = "TestScan",
    data_source_name: str = "TestDS",
    run_id: str = "run-001",
    minutes_running: float = 90.0,
) -> ScanRun:
    """Helper to create a scan run with specified duration."""
    start = datetime.now(timezone.utc) - timedelta(minutes=minutes_running)
    return ScanRun(
        scan_name=scan_name,
        data_source_name=data_source_name,
        run_id=run_id,
        status=ScanStatus.IN_PROGRESS,
        start_time=start,
    )


def test_scan_under_threshold_not_cancelled():
    """Scans under threshold should not be cancelled."""
    purview = MagicMock()
    purview.get_all_running_scans.return_value = [_make_scan_run(minutes_running=30)]

    config_provider = MagicMock(spec=ConfigProvider)
    config_provider.get_config.return_value = MonitorConfig(default_threshold_minutes=60)
    config_provider.get_threshold_for_scan.return_value = 60

    notifications = MagicMock()

    engine = ScanMonitorEngine(purview, config_provider, notifications)
    results = engine.execute()

    assert len(results) == 1
    assert results[0].exceeded is False
    assert results[0].cancelled is False
    purview.cancel_scan_run.assert_not_called()


def test_scan_over_threshold_is_cancelled():
    """Scans exceeding threshold should be cancelled."""
    purview = MagicMock()
    purview.get_all_running_scans.return_value = [_make_scan_run(minutes_running=90)]
    purview.cancel_scan_run.return_value = True

    config_provider = MagicMock(spec=ConfigProvider)
    config_provider.get_config.return_value = MonitorConfig(
        default_threshold_minutes=60, auto_cancel_enabled=True, notification_enabled=True
    )
    config_provider.get_threshold_for_scan.return_value = 60

    notifications = MagicMock()

    engine = ScanMonitorEngine(purview, config_provider, notifications)
    results = engine.execute()

    assert len(results) == 1
    assert results[0].exceeded is True
    assert results[0].cancelled is True
    purview.cancel_scan_run.assert_called_once()
    notifications.notify_threshold_exceeded.assert_called_once()
    notifications.notify_scan_cancelled.assert_called_once()


def test_no_running_scans():
    """When no scans are running, no actions should be taken."""
    purview = MagicMock()
    purview.get_all_running_scans.return_value = []

    config_provider = MagicMock(spec=ConfigProvider)
    config_provider.get_config.return_value = MonitorConfig()

    notifications = MagicMock()

    engine = ScanMonitorEngine(purview, config_provider, notifications)
    results = engine.execute()

    assert len(results) == 0
    purview.cancel_scan_run.assert_not_called()


def test_auto_cancel_disabled():
    """When auto-cancel is disabled, exceeded scans should not be cancelled."""
    purview = MagicMock()
    purview.get_all_running_scans.return_value = [_make_scan_run(minutes_running=120)]

    config_provider = MagicMock(spec=ConfigProvider)
    config_provider.get_config.return_value = MonitorConfig(
        default_threshold_minutes=60, auto_cancel_enabled=False, notification_enabled=True
    )
    config_provider.get_threshold_for_scan.return_value = 60

    notifications = MagicMock()

    engine = ScanMonitorEngine(purview, config_provider, notifications)
    results = engine.execute()

    assert results[0].exceeded is True
    assert results[0].cancelled is False
    purview.cancel_scan_run.assert_not_called()
    notifications.notify_threshold_exceeded.assert_called_once()


def test_pattern_matching_threshold():
    """Name pattern overrides should be applied correctly."""
    config_provider = ConfigProvider.__new__(ConfigProvider)
    config_provider._cached_config = MonitorConfig(
        default_threshold_minutes=60,
        overrides=[
            ThresholdOverride(scan_name="Finance*", threshold_minutes=30),
            ThresholdOverride(scan_id="special-run", threshold_minutes=10),
        ],
    )

    scan_finance = _make_scan_run(scan_name="Finance_Monthly")
    scan_other = _make_scan_run(scan_name="HR_Weekly")

    assert config_provider.get_threshold_for_scan(scan_finance) == 30
    assert config_provider.get_threshold_for_scan(scan_other) == 60
