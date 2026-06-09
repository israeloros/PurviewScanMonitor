"""Azure Function App - Purview Scan Monitor.

Timer-triggered function that monitors running Purview scans,
evaluates them against configurable thresholds, and auto-cancels
long-running scans with notifications.
"""

import logging
import os

import azure.functions as func

from clients import PurviewClient
from config import ConfigProvider
from engine import ScanMonitorEngine
from notifications import NotificationHandler

app = func.FunctionApp()

logger = logging.getLogger(__name__)


@app.timer_trigger(
    schedule="%SCAN_MONITOR_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
)
def scan_monitor(timer: func.TimerRequest) -> None:
    """Timer-triggered function to monitor Purview scan jobs.

    Executes on a configurable CRON schedule (default: every 5 minutes).
    Idempotent — safe to re-run without side effects.
    """
    if timer.past_due:
        logger.warning("Timer is past due. Executing catch-up run.")

    logger.info("Scan monitor function triggered.")

    try:
        # Initialize components (DI-style composition root)
        config_provider = ConfigProvider()
        purview_client = PurviewClient()
        notification_handler = NotificationHandler()

        engine = ScanMonitorEngine(
            purview_client=purview_client,
            config_provider=config_provider,
            notification_handler=notification_handler,
        )

        # Execute monitoring cycle
        results = engine.execute()

        # Summary logging for Application Insights
        exceeded_count = sum(1 for r in results if r.exceeded)
        cancelled_count = sum(1 for r in results if r.cancelled)

        logger.info(
            "Scan monitor complete: scans_evaluated=%d, thresholds_exceeded=%d, scans_cancelled=%d",
            len(results),
            exceeded_count,
            cancelled_count,
        )

    except Exception as e:
        logger.exception("Unhandled error in scan monitor: %s", e)
        # Attempt error notification
        try:
            NotificationHandler().notify_error("Scan Monitor Function", str(e))
        except Exception:
            pass
        raise
