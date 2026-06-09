# Purview Scan Monitoring - Azure Function

An enterprise-grade Azure Function solution that monitors Microsoft Purview Data Map scan jobs, enforces configurable runtime thresholds, and auto-cancels long-running scans with notifications.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Azure Function App                     │
│                   (Consumption Plan)                      │
│                                                          │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │  Timer    │→ │  Monitor     │→ │  Purview Client   │ │
│  │  Trigger  │  │  Engine      │  │  (REST API)       │ │
│  └──────────┘  └──────┬───────┘  └───────────────────┘ │
│                        │                                 │
│              ┌─────────┴─────────┐                      │
│              │                    │                      │
│  ┌───────────▼──┐  ┌────────────▼────────┐             │
│  │  Config      │  │  Notification        │             │
│  │  Provider    │  │  Handler             │             │
│  └──────┬───────┘  └────────┬────────────┘             │
└─────────┼────────────────────┼──────────────────────────┘
          │                    │
          ▼                    ▼
┌──────────────────┐  ┌────────────────────┐
│ Azure Table      │  │ Webhook / SendGrid │
│ Storage (Config) │  │ (Notifications)    │
└──────────────────┘  └────────────────────┘
```

## Components

| Component | Purpose |
|-----------|---------|
| `function_app.py` | Entry point — timer-triggered Azure Function |
| `config/` | Configuration provider (Azure Table Storage) |
| `clients/` | Purview REST API client with retry logic |
| `engine/` | Core monitoring logic and threshold evaluation |
| `notifications/` | Pluggable notification system (webhook, email) |
| `models/` | Data models (ScanRun, MonitorConfig, etc.) |
| `infrastructure/` | Bicep IaC templates |

## Prerequisites

- Python 3.10+
- Azure CLI
- Azure Functions Core Tools v4
- Azure subscription with:
  - Microsoft Purview account
  - Storage account (for config + function runtime)
  - Application Insights

## Configuration

This project uses three JSON configuration files. Below is a detailed guide for each.

---

### `local.settings.json` — Application Settings

This file provides environment variables for the Azure Function runtime. It is used during local development and its values map to **Application Settings** in the Azure Portal when deployed.

> 📘 **Reference**: [Azure Functions local.settings.json](https://learn.microsoft.com/en-us/azure/azure-functions/functions-develop-local#local-settings-file)

| Setting | Description | Where to Obtain |
|---------|-------------|-----------------|
| `AzureWebJobsStorage` | Connection string for the Azure Function's internal storage (triggers, timers, etc.). Use `"UseDevelopmentStorage=true"` for local development with Azurite. | Azure Portal → Storage Account → **Access keys** → Connection string. [Learn more](https://learn.microsoft.com/en-us/azure/storage/common/storage-account-keys-manage) |
| `FUNCTIONS_WORKER_RUNTIME` | Must be `"python"`. Indicates the language runtime. | Static value — do not change. |
| `PURVIEW_ACCOUNT_NAME` | The name of your Microsoft Purview (Data Map) account. | Azure Portal → Microsoft Purview account → **Overview** → Account name. [Learn more](https://learn.microsoft.com/en-us/purview/create-microsoft-purview-portal) |
| `CONFIG_STORAGE_ACCOUNT_URL` | The Table Storage endpoint URL for configuration data. Format: `https://<account>.table.core.windows.net` | Azure Portal → Storage Account → **Endpoints** → Table service URL. [Learn more](https://learn.microsoft.com/en-us/azure/storage/common/storage-account-overview#storage-account-endpoints) |
| `CONFIG_TABLE_NAME` | Name of the Azure Table that stores monitoring configuration. Defaults to `ScanMonitorConfig`. | You create this table yourself (see [Deployment](#deployment) section). |
| `NOTIFICATION_WEBHOOK_URL` | URL for webhook-based notifications (e.g., Microsoft Teams, Slack, Logic App HTTP trigger). Leave empty to disable. | Teams: [Create Incoming Webhook](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook). Logic App: copy the HTTP trigger URL from the designer. |
| `SENDGRID_API_KEY` | API key for SendGrid email notifications. Leave empty to disable email. | [SendGrid API Keys](https://docs.sendgrid.com/ui/account-and-settings/api-keys) |
| `NOTIFICATION_EMAIL_TO` | Recipient email address for alert notifications. | Any valid email address. |
| `NOTIFICATION_EMAIL_FROM` | Sender email address (must be a verified sender in SendGrid). | [SendGrid Sender Identity](https://docs.sendgrid.com/for-developers/sending-email/sender-identity) |
| `SCAN_MONITOR_SCHEDULE` | CRON expression that controls how often the monitor runs. Default: `"0 */5 * * * *"` (every 5 minutes). | [Azure Functions Timer CRON expressions](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-timer?pivots=programming-language-python#ncrontab-expressions) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Connection string for Application Insights telemetry. | Azure Portal → Application Insights → **Overview** → Connection String. [Learn more](https://learn.microsoft.com/en-us/azure/azure-monitor/app/sdk-connection-string) |

#### How to Update `local.settings.json`

1. Copy the file template (it is already committed with placeholder values).
2. Replace each `<placeholder>` with the real value from the sources listed above.
3. **Never commit secrets** — this file is listed in `.gitignore`. For deployed environments, set these as Application Settings in the Azure Portal or via CLI:
   ```bash
   az functionapp config appsettings set \
     --name <function-app-name> \
     --resource-group <resource-group> \
     --settings "PURVIEW_ACCOUNT_NAME=my-purview-account" \
                "CONFIG_STORAGE_ACCOUNT_URL=https://mystorageacct.table.core.windows.net"
   ```
   > 📘 [Manage Application Settings](https://learn.microsoft.com/en-us/azure/azure-functions/functions-how-to-use-azure-function-app-settings#settings)

---

### `host.json` — Function Host Configuration

This file configures the Azure Functions runtime behavior including logging, timeout, and extension settings.

> 📘 **Reference**: [host.json reference for Azure Functions](https://learn.microsoft.com/en-us/azure/azure-functions/functions-host-json)

| Section | Setting | Description | When to Modify |
|---------|---------|-------------|----------------|
| `logging.applicationInsights.samplingSettings` | `isEnabled`, `excludedTypes` | Controls telemetry sampling. Requests are excluded to ensure every invocation is logged. | Adjust `excludedTypes` if you want to sample request telemetry to reduce costs. [Learn more](https://learn.microsoft.com/en-us/azure/azure-functions/configure-monitoring#configure-sampling) |
| `logging.logLevel` | `default`, `Function`, etc. | Sets minimum log verbosity. `Information` captures standard operational logs. | Set to `Debug` for troubleshooting; set to `Warning` in production to reduce noise. |
| `extensions.timers.maxOutstandingTimerInvocations` | `1` | Prevents overlapping timer executions. | Do not change unless you need concurrent runs. |
| `functionTimeout` | `"00:05:00"` | Maximum execution time per invocation. | Increase if you have many data sources. Max for Consumption plan: `00:10:00`. [Learn more](https://learn.microsoft.com/en-us/azure/azure-functions/functions-host-json#functiontimeout) |

#### How to Update `host.json`

- Edit the file directly. Changes take effect on the next function invocation (local) or deployment (cloud).
- This file **should be committed** to source control as it applies to all environments.

---

### `config.sample.json` — Scan Monitoring Thresholds

This file is a **reference example** of the monitoring configuration stored in Azure Table Storage. It documents the JSON structure used by the `ConfigProvider` to evaluate scan run durations.

| Field | Type | Description |
|-------|------|-------------|
| `defaultThresholdMinutes` | int | Global maximum allowed scan duration (in minutes) before an alert is triggered. |
| `autoCancel` | bool | When `true`, scans exceeding their threshold are automatically cancelled via the Purview REST API. |
| `notifications` | bool | When `true`, notifications (webhook/email) are sent for threshold breaches and cancellations. |
| `overrides` | array | List of per-scan threshold overrides (see below). |

**Override Object:**

| Field | Type | Description |
|-------|------|-------------|
| `scanName` | string | Wildcard/glob pattern matching Purview scan names (e.g., `Finance*`). |
| `scanId` | string | Exact scan run ID from Purview for a one-time override. |
| `thresholdMinutes` | int | Threshold (in minutes) for matching scans. |

#### Threshold Resolution Priority

1. **Scan ID match** (exact) — highest priority
2. **Scan Name pattern** (wildcard/glob) — medium priority
3. **Global default** — fallback

#### How to Find Scan Names and IDs

- **Scan names**: Azure Portal → Microsoft Purview → **Data Map** → Data Sources → select a source → view registered scans. Or via REST API:
  ```
  GET https://{account}.purview.azure.com/scan/datasources/{dsName}/scans?api-version=2023-09-01
  ```
- **Scan run IDs**: Azure Portal → Microsoft Purview → Data Map → Data Sources → select scan → **Run history**. Or via REST API:
  ```
  GET https://{account}.purview.azure.com/scan/datasources/{dsName}/scans/{scanName}/runs?api-version=2023-09-01
  ```

> 📘 **References**:
> - [Microsoft Purview Data Map scanning overview](https://learn.microsoft.com/en-us/purview/concept-scans-and-ingestion)
> - [Purview scanning REST API](https://learn.microsoft.com/en-us/rest/api/purview/scanningdataplane/scans)

#### How to Apply Configuration to Azure Table Storage

The actual configuration lives in Azure Table Storage (not this JSON file). Use this sample as a guide and apply it via Azure CLI:

```bash
# Set the main configuration
az storage entity insert \
  --account-name <storage-account> \
  --table-name ScanMonitorConfig \
  --entity PartitionKey=config RowKey=main \
    DefaultThresholdMinutes=60 \
    AutoCancelEnabled=true \
    NotificationEnabled=true

# Add scan name pattern overrides
az storage entity insert \
  --account-name <storage-account> \
  --table-name ScanMonitorConfig \
  --entity PartitionKey=override RowKey=finance-override \
    ScanName="Finance*" ThresholdMinutes=30

# Add a scan ID override for a specific run
az storage entity insert \
  --account-name <storage-account> \
  --table-name ScanMonitorConfig \
  --entity PartitionKey=override RowKey=specific-run-override \
    ScanId="abc123-run-id" ThresholdMinutes=15
```

> 📘 [Azure Table Storage CLI reference](https://learn.microsoft.com/en-us/cli/azure/storage/entity)

You can also manage table entities via [Azure Storage Explorer](https://learn.microsoft.com/en-us/azure/vs-azure-tools-storage-manage-with-storage-explorer) for a GUI experience.

## Deployment

### 1. Deploy Infrastructure (Bicep)

```bash
az group create --name rg-purview-monitor --location eastus2

az deployment group create \
  --resource-group rg-purview-monitor \
  --template-file infrastructure/main.bicep \
  --parameters purviewAccountName=<your-purview-account>
```

### 2. Assign Purview RBAC

Grant the Function App's Managed Identity the **Purview Data Source Administrator** role:

```bash
FUNC_PRINCIPAL_ID=$(az functionapp identity show \
  --name purview-scan-monitor-func \
  --resource-group rg-purview-monitor \
  --query principalId -o tsv)

# Assign at the Purview account level
az role assignment create \
  --assignee $FUNC_PRINCIPAL_ID \
  --role "Purview Data Source Administrator" \
  --scope /subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.Purview/accounts/<account>
```

### 3. Deploy Function Code

```bash
cd scan-monitoring
func azure functionapp publish purview-scan-monitor-func --python
```

### 4. Seed Configuration Table

```bash
az storage entity insert \
  --account-name <storage-account> \
  --table-name ScanMonitorConfig \
  --entity PartitionKey=config RowKey=main \
    DefaultThresholdMinutes=60 \
    AutoCancelEnabled=true \
    NotificationEnabled=true

az storage entity insert \
  --account-name <storage-account> \
  --table-name ScanMonitorConfig \
  --entity PartitionKey=override RowKey=finance-override \
    ScanName="Finance*" ThresholdMinutes=30
```

## Purview REST API Reference

### List Data Sources
```
GET https://{account}.purview.azure.com/scan/datasources?api-version=2023-09-01
Authorization: Bearer {token}
```

### List Scans for a Data Source
```
GET https://{account}.purview.azure.com/scan/datasources/{dsName}/scans?api-version=2023-09-01
```

### List Scan Runs
```
GET https://{account}.purview.azure.com/scan/datasources/{dsName}/scans/{scanName}/runs?api-version=2023-09-01
```

### Cancel a Scan Run
```
POST https://{account}.purview.azure.com/scan/datasources/{dsName}/scans/{scanName}/runs/{runId}/:cancel?api-version=2023-09-01
```

## Security

- **Managed Identity**: All Azure service authentication uses System-Assigned Managed Identity
- **No secrets in code**: Credentials are resolved at runtime via `DefaultAzureCredential`
- **Least privilege**: Function only needs Table Data Reader + Purview Data Source Admin
- **TLS 1.2**: Enforced on storage account

## Observability

All structured logs are emitted to **Application Insights**:

- Scan evaluation results (per scan)
- Threshold breach warnings
- Cancellation attempts and outcomes
- Correlation IDs for end-to-end tracing

### Sample KQL Query (App Insights)

```kql
traces
| where message contains "THRESHOLD EXCEEDED"
| project timestamp, message, customDimensions
| order by timestamp desc
```

## Local Development

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Run locally
func start
```

## Cost Optimization

| Design Choice | Benefit |
|--------------|---------|
| Consumption Plan | Pay only per execution |
| Table Storage config | ~$0.00/month for config reads |
| Query only running scans | Minimize API calls |
| In-memory config cache | One table read per invocation |
| 5-min schedule | ~8,640 executions/month (well within free tier) |

## Testing

```bash
pip install pytest
pytest tests/ -v
```

## License

MIT
