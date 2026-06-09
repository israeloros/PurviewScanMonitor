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

### Azure Table Storage Schema

**Partition: `config`, Row: `main`**

| Property | Type | Description |
|----------|------|-------------|
| DefaultThresholdMinutes | int | Global default threshold |
| AutoCancelEnabled | bool | Enable auto-cancellation |
| NotificationEnabled | bool | Enable notifications |

**Partition: `override`, Row: `<unique-id>`**

| Property | Type | Description |
|----------|------|-------------|
| ScanName | string | Wildcard pattern (e.g., `Finance*`) |
| ScanId | string | Specific scan run ID |
| ThresholdMinutes | int | Override threshold |

### Example Config (JSON representation)

```json
{
  "defaultThresholdMinutes": 60,
  "overrides": [
    { "scanName": "Finance*", "thresholdMinutes": 30 },
    { "scanName": "HR_*", "thresholdMinutes": 45 },
    { "scanId": "abc123-run-id", "thresholdMinutes": 15 }
  ]
}
```

### Threshold Resolution Priority

1. **Scan ID match** (exact) — highest priority
2. **Scan Name pattern** (wildcard/glob) — medium priority
3. **Global default** — fallback

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
