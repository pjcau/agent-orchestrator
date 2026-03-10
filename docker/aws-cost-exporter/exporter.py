"""AWS Cost Explorer exporter for Prometheus.

Queries AWS Cost Explorer API every hour and exposes metrics
on port 9101 in Prometheus text exposition format.

Metrics exposed:
  aws_cost_daily_usd{service="..."} — today's cost per AWS service
  aws_cost_monthly_usd — current month total cost
  aws_cost_monthly_forecast_usd — forecasted month-end cost
  aws_cost_yesterday_usd{service="..."} — yesterday's cost per service
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Refresh interval: 1 hour (Cost Explorer data updates ~daily)
REFRESH_INTERVAL = 3600

# Global metrics store
_metrics: dict[str, float] = {}
_service_costs_today: dict[str, float] = {}
_service_costs_yesterday: dict[str, float] = {}
_lock = threading.Lock()


def _get_ce_client():
    return boto3.client("ce", region_name="us-east-1")


def _fetch_costs():
    """Fetch costs from AWS Cost Explorer."""
    global _metrics, _service_costs_today, _service_costs_yesterday

    try:
        ce = _get_ce_client()
        today = datetime.date.today()
        first_of_month = today.replace(day=1)
        yesterday = today - datetime.timedelta(days=1)

        # Monthly cost so far (grouped by service)
        monthly = ce.get_cost_and_usage(
            TimePeriod={"Start": first_of_month.isoformat(), "End": today.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )

        monthly_total = 0.0
        for group in monthly.get("ResultsByTime", [{}])[0].get("Groups", []):
            service = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            monthly_total += amount

        # Today's cost (from yesterday to today for a full day)
        daily_costs_today: dict[str, float] = {}
        try:
            daily = ce.get_cost_and_usage(
                TimePeriod={"Start": yesterday.isoformat(), "End": today.isoformat()},
                Granularity="DAILY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            for group in daily.get("ResultsByTime", [{}])[0].get("Groups", []):
                service = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                if amount > 0.0:
                    daily_costs_today[service] = amount
        except (ClientError, IndexError):
            pass

        # Yesterday's cost
        daily_costs_yesterday: dict[str, float] = {}
        day_before = yesterday - datetime.timedelta(days=1)
        try:
            daily_y = ce.get_cost_and_usage(
                TimePeriod={"Start": day_before.isoformat(), "End": yesterday.isoformat()},
                Granularity="DAILY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            for group in daily_y.get("ResultsByTime", [{}])[0].get("Groups", []):
                service = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                if amount > 0.0:
                    daily_costs_yesterday[service] = amount
        except (ClientError, IndexError):
            pass

        # Monthly forecast
        forecast_amount = 0.0
        try:
            forecast = ce.get_cost_forecast(
                TimePeriod={
                    "Start": today.isoformat(),
                    "End": (today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1).isoformat(),
                },
                Metric="UNBLENDED_COST",
                Granularity="MONTHLY",
            )
            forecast_amount = float(forecast["Total"]["Amount"])
        except ClientError:
            # Forecast may fail if not enough data
            pass

        with _lock:
            _metrics["aws_cost_monthly_usd"] = monthly_total
            _metrics["aws_cost_monthly_forecast_usd"] = forecast_amount
            _service_costs_today.clear()
            _service_costs_today.update(daily_costs_today)
            _service_costs_yesterday.clear()
            _service_costs_yesterday.update(daily_costs_yesterday)

        logger.info(
            "Costs updated: monthly=$%.2f, forecast=$%.2f, services_today=%d",
            monthly_total, forecast_amount, len(daily_costs_today),
        )

    except NoCredentialsError:
        logger.error("No AWS credentials found. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.")
    except Exception:
        logger.exception("Failed to fetch AWS costs")


def _format_metrics() -> str:
    """Format metrics as Prometheus text exposition."""
    lines: list[str] = []

    with _lock:
        # Monthly total
        lines.append("# HELP aws_cost_monthly_usd Current month AWS cost in USD")
        lines.append("# TYPE aws_cost_monthly_usd gauge")
        lines.append(f'aws_cost_monthly_usd {_metrics.get("aws_cost_monthly_usd", 0):.4f}')

        # Monthly forecast
        lines.append("# HELP aws_cost_monthly_forecast_usd Forecasted month-end AWS cost in USD")
        lines.append("# TYPE aws_cost_monthly_forecast_usd gauge")
        lines.append(f'aws_cost_monthly_forecast_usd {_metrics.get("aws_cost_monthly_forecast_usd", 0):.4f}')

        # Daily cost per service (yesterday, since today is incomplete)
        lines.append("# HELP aws_cost_daily_usd Yesterday cost per AWS service in USD")
        lines.append("# TYPE aws_cost_daily_usd gauge")
        for service, amount in sorted(_service_costs_today.items()):
            safe_service = service.replace('"', '\\"')
            lines.append(f'aws_cost_daily_usd{{service="{safe_service}"}} {amount:.6f}')

        # Day-before-yesterday for comparison
        lines.append("# HELP aws_cost_yesterday_usd Day-before-yesterday cost per service in USD")
        lines.append("# TYPE aws_cost_yesterday_usd gauge")
        for service, amount in sorted(_service_costs_yesterday.items()):
            safe_service = service.replace('"', '\\"')
            lines.append(f'aws_cost_yesterday_usd{{service="{safe_service}"}} {amount:.6f}')

    lines.append("")
    return "\n".join(lines)


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            body = _format_metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress per-request access logs
        pass


def _refresh_loop():
    """Background thread that refreshes costs every hour."""
    while True:
        _fetch_costs()
        time.sleep(REFRESH_INTERVAL)


def main():
    logger.info("Starting AWS Cost Exporter on :9101")

    # Initial fetch
    _fetch_costs()

    # Background refresh
    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()

    # Serve metrics
    server = HTTPServer(("0.0.0.0", 9101), MetricsHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
