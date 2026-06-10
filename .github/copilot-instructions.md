# Copilot Instructions — PurviewScanMonitor

## Build & Test

```powershell
# Activate venv (Python 3.11 required)
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest tests/ -v

# Run a single test
pytest tests/test_engine.py::test_scan_over_threshold_is_cancelled -v
```

There is no linter or formatter configured. The project uses Azure Functions Core Tools for local execution:

```powershell
func start
```

## Architecture

This is a **timer-triggered Azure Function** (Python v2 programming model) that monitors Microsoft Purview Data Map scan jobs. On each invocation:

1. `function_app.py` — Entry point. Composes dependencies and calls the engine.
2. `engine/` — `ScanMonitorEngine` orchestrates the monitoring cycle: fetches running scans, evaluates thresholds, cancels long-running scans, sends notifications.
3. `clients/` — `PurviewClient` wraps the Purview Scanning REST API with retry logic and Managed Identity auth (`DefaultAzureCredential`).
4. `config/` — `ConfigProvider` reads threshold configuration from Azure Table Storage (partition `config`/row `main` for defaults, partition `override` for per-scan rules). Cached per invocation.
5. `models/` — Pure dataclasses (`ScanRun`, `MonitorConfig`, `ThresholdOverride`, `MonitoringResult`). No Pydantic — uses stdlib `dataclasses`.
6. `notifications/` — `NotificationHandler` sends alerts via webhook (Teams/Slack/Logic App) and/or SendGrid email.
7. `infrastructure/` — Bicep IaC template for all Azure resources.

### Threshold Resolution Order

1. Exact scan run ID match (highest priority)
2. Scan name glob/wildcard pattern (`fnmatch`)
3. Global `default_threshold_minutes` (fallback)

## Key Conventions

- **Authentication**: Always use `DefaultAzureCredential` (Managed Identity). Never hardcode secrets. Storage uses identity-based access (`AzureWebJobsStorage__accountName`) — shared key access is disabled.
- **Configuration from environment**: All settings come from `os.environ` (mapped from `local.settings.json` locally, Application Settings in Azure).
- **Composition root pattern**: Dependencies are constructed in `function_app.py` and injected into `ScanMonitorEngine`. Tests mock at the constructor boundary.
- **Structured logging**: Use `logging.getLogger(__name__)` with `%s` formatting (not f-strings in log calls). Include `correlation_id` in log messages for traceability.
- **Error handling**: Custom `PurviewClientError` wraps API failures. The engine catches client errors per-scan to avoid one failure blocking other scans.
- **Idempotent execution**: The monitoring cycle is safe to re-run at any time without side effects beyond cancellations.
- **Tests use `unittest.mock`**: No fixtures or conftest. Helper functions like `_make_scan_run()` create test data. Mock all external dependencies.
