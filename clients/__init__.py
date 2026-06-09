"""Microsoft Purview Data Map REST API client.

Uses Managed Identity for authentication. Handles retries and error responses.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests
from azure.identity import DefaultAzureCredential

from models import ScanRun, ScanStatus

logger = logging.getLogger(__name__)

PURVIEW_SCAN_ENDPOINT = "https://{account}.purview.azure.com/scan"
PURVIEW_SCOPE = "https://purview.azure.net/.default"

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2


class PurviewClientError(Exception):
    """Custom exception for Purview API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class PurviewClient:
    """Client for Microsoft Purview Scanning REST APIs."""

    def __init__(
        self,
        account_name: Optional[str] = None,
        credential: Optional[DefaultAzureCredential] = None,
    ):
        self._account_name = account_name or os.environ["PURVIEW_ACCOUNT_NAME"]
        self._credential = credential or DefaultAzureCredential()
        self._base_url = PURVIEW_SCAN_ENDPOINT.format(account=self._account_name)
        self._session = requests.Session()
        self._token: Optional[str] = None

    def _get_token(self) -> str:
        """Acquire access token via Managed Identity."""
        token = self._credential.get_token(PURVIEW_SCOPE)
        self._token = token.token
        return self._token

    def _get_headers(self) -> dict:
        """Build authorization headers."""
        token = self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request with retry logic."""
        import time

        last_error: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._session.request(
                    method, url, headers=self._get_headers(), timeout=30, **kwargs
                )
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", RETRY_BACKOFF_SECONDS))
                    logger.warning("Rate limited. Retrying after %d seconds.", retry_after)
                    time.sleep(retry_after)
                    continue
                if response.status_code >= 500:
                    logger.warning(
                        "Server error %d on attempt %d", response.status_code, attempt + 1
                    )
                    time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                return response
            except requests.exceptions.RequestException as e:
                last_error = e
                logger.warning("Request failed on attempt %d: %s", attempt + 1, e)
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))

        raise PurviewClientError(
            f"Request failed after {MAX_RETRIES} retries: {last_error}"
        )

    def list_data_sources(self) -> list[dict]:
        """List all registered data sources."""
        url = f"{self._base_url}/datasources?api-version=2023-09-01"
        response = self._request("GET", url)
        if response.status_code != 200:
            raise PurviewClientError(
                f"Failed to list data sources: {response.text}",
                status_code=response.status_code,
            )
        return response.json().get("value", [])

    def list_scans(self, data_source_name: str) -> list[dict]:
        """List scans for a data source."""
        url = f"{self._base_url}/datasources/{data_source_name}/scans?api-version=2023-09-01"
        response = self._request("GET", url)
        if response.status_code != 200:
            raise PurviewClientError(
                f"Failed to list scans for {data_source_name}: {response.text}",
                status_code=response.status_code,
            )
        return response.json().get("value", [])

    def list_scan_runs(self, data_source_name: str, scan_name: str) -> list[ScanRun]:
        """List recent scan runs, returning only active/running ones."""
        url = (
            f"{self._base_url}/datasources/{data_source_name}"
            f"/scans/{scan_name}/runs?api-version=2023-09-01"
        )
        response = self._request("GET", url)
        if response.status_code != 200:
            raise PurviewClientError(
                f"Failed to list runs for {data_source_name}/{scan_name}: {response.text}",
                status_code=response.status_code,
            )

        runs: list[ScanRun] = []
        for run_data in response.json().get("value", []):
            status_str = run_data.get("status", "")
            try:
                status = ScanStatus(status_str)
            except ValueError:
                continue

            start_time = self._parse_datetime(run_data.get("startTime"))
            end_time = self._parse_datetime(run_data.get("endTime"))

            scan_run = ScanRun(
                scan_name=scan_name,
                data_source_name=data_source_name,
                run_id=run_data.get("id", ""),
                status=status,
                start_time=start_time,
                end_time=end_time,
                scan_level=run_data.get("scanLevel"),
            )
            if scan_run.is_running:
                runs.append(scan_run)

        return runs

    def get_all_running_scans(self) -> list[ScanRun]:
        """Retrieve all currently running scan jobs across all data sources."""
        running_scans: list[ScanRun] = []

        data_sources = self.list_data_sources()
        logger.info("Found %d data sources to check.", len(data_sources))

        for ds in data_sources:
            ds_name = ds.get("name", "")
            if not ds_name:
                continue

            try:
                scans = self.list_scans(ds_name)
            except PurviewClientError as e:
                logger.warning("Skipping data source '%s': %s", ds_name, e)
                continue

            for scan in scans:
                scan_name = scan.get("name", "")
                if not scan_name:
                    continue
                try:
                    runs = self.list_scan_runs(ds_name, scan_name)
                    running_scans.extend(runs)
                except PurviewClientError as e:
                    logger.warning(
                        "Failed to get runs for %s/%s: %s", ds_name, scan_name, e
                    )

        logger.info("Total running scans found: %d", len(running_scans))
        return running_scans

    def cancel_scan_run(self, data_source_name: str, scan_name: str, run_id: str) -> bool:
        """Cancel a specific scan run. Returns True on success."""
        url = (
            f"{self._base_url}/datasources/{data_source_name}"
            f"/scans/{scan_name}/runs/{run_id}/:cancel?api-version=2023-09-01"
        )
        response = self._request("POST", url)
        if response.status_code in (200, 202):
            logger.info(
                "Successfully cancelled scan run %s/%s/%s", data_source_name, scan_name, run_id
            )
            return True
        else:
            logger.error(
                "Failed to cancel scan run %s/%s/%s: %d %s",
                data_source_name,
                scan_name,
                run_id,
                response.status_code,
                response.text,
            )
            return False

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime string to timezone-aware datetime."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None
