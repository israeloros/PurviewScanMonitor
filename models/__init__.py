"""Data models for scan monitoring."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ScanStatus(str, Enum):
    """Purview scan run statuses."""

    ACCEPTED = "Accepted"
    IN_PROGRESS = "InProgress"
    QUEUED = "Queued"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    CANCELING = "Canceling"


@dataclass
class ScanRun:
    """Represents a Purview scan run."""

    scan_name: str
    data_source_name: str
    run_id: str
    status: ScanStatus
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    scan_level: Optional[str] = None

    @property
    def duration_minutes(self) -> float:
        """Calculate running duration in minutes from start to now."""
        if not self.start_time:
            return 0.0
        now = datetime.now(timezone.utc)
        return (now - self.start_time).total_seconds() / 60.0

    @property
    def is_running(self) -> bool:
        return self.status in (ScanStatus.IN_PROGRESS, ScanStatus.QUEUED, ScanStatus.ACCEPTED)


@dataclass
class ThresholdOverride:
    """Configuration override for specific scans."""

    threshold_minutes: int
    scan_name: Optional[str] = None
    scan_id: Optional[str] = None


@dataclass
class MonitorConfig:
    """Full monitoring configuration."""

    default_threshold_minutes: int = 60
    overrides: list[ThresholdOverride] = field(default_factory=list)
    notification_enabled: bool = True
    auto_cancel_enabled: bool = True


@dataclass
class MonitoringResult:
    """Result of monitoring a single scan."""

    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scan_run: Optional[ScanRun] = None
    threshold_minutes: int = 0
    exceeded: bool = False
    cancelled: bool = False
    cancel_error: Optional[str] = None
    notification_sent: bool = False
