"""Configuration provider for scan monitoring.

Reads threshold configuration from Azure Table Storage using Managed Identity.
Caches configuration in memory for the lifetime of a single function execution.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
from typing import Optional

from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential

from models import MonitorConfig, ScanRun, ThresholdOverride

logger = logging.getLogger(__name__)


class ConfigProvider:
    """Provides scan monitoring configuration from Azure Table Storage."""

    def __init__(
        self,
        storage_account_url: Optional[str] = None,
        table_name: Optional[str] = None,
        credential: Optional[DefaultAzureCredential] = None,
    ):
        self._storage_url = storage_account_url or os.environ["CONFIG_STORAGE_ACCOUNT_URL"]
        self._table_name = table_name or os.environ.get("CONFIG_TABLE_NAME", "ScanMonitorConfig")
        self._credential = credential or DefaultAzureCredential()
        self._cached_config: Optional[MonitorConfig] = None

    def get_config(self) -> MonitorConfig:
        """Retrieve monitoring configuration. Cached per instance lifetime."""
        if self._cached_config is not None:
            return self._cached_config

        try:
            self._cached_config = self._load_from_table()
        except Exception as e:
            logger.warning("Failed to load config from Table Storage: %s. Using defaults.", e)
            self._cached_config = MonitorConfig()

        return self._cached_config

    def get_threshold_for_scan(self, scan_run: ScanRun) -> int:
        """Resolve effective threshold for a given scan run.

        Priority: scan_id override > scan_name pattern > global default.
        """
        config = self.get_config()

        # Check scan ID overrides first (highest priority)
        for override in config.overrides:
            if override.scan_id and override.scan_id == scan_run.run_id:
                logger.info(
                    "Using ID-based threshold %d min for run %s",
                    override.threshold_minutes,
                    scan_run.run_id,
                )
                return override.threshold_minutes

        # Check scan name pattern overrides
        for override in config.overrides:
            if override.scan_name and fnmatch.fnmatch(scan_run.scan_name, override.scan_name):
                logger.info(
                    "Using name-pattern threshold %d min for scan '%s' (pattern: '%s')",
                    override.threshold_minutes,
                    scan_run.scan_name,
                    override.scan_name,
                )
                return override.threshold_minutes

        return config.default_threshold_minutes

    def _load_from_table(self) -> MonitorConfig:
        """Load configuration from Azure Table Storage."""
        table_service = TableServiceClient(
            endpoint=self._storage_url, credential=self._credential
        )
        table_client = table_service.get_table_client(self._table_name)

        # Main config is stored as partition "config", row "main"
        try:
            entity = table_client.get_entity(partition_key="config", row_key="main")
        except Exception:
            logger.info("No config entity found, using defaults.")
            return MonitorConfig()

        default_threshold = int(entity.get("DefaultThresholdMinutes", 60))
        auto_cancel = entity.get("AutoCancelEnabled", True)
        notification_enabled = entity.get("NotificationEnabled", True)

        # Load overrides from separate rows
        overrides: list[ThresholdOverride] = []
        override_entities = table_client.query_entities("PartitionKey eq 'override'")
        for ov in override_entities:
            overrides.append(
                ThresholdOverride(
                    threshold_minutes=int(ov.get("ThresholdMinutes", default_threshold)),
                    scan_name=ov.get("ScanName"),
                    scan_id=ov.get("ScanId"),
                )
            )

        config = MonitorConfig(
            default_threshold_minutes=default_threshold,
            overrides=overrides,
            notification_enabled=bool(notification_enabled),
            auto_cancel_enabled=bool(auto_cancel),
        )
        logger.info(
            "Loaded config: default=%d min, %d overrides, cancel=%s",
            config.default_threshold_minutes,
            len(config.overrides),
            config.auto_cancel_enabled,
        )
        return config
