"""AWS Cost Explorer + S3 metrics exporter for Prometheus.

Queries AWS Cost Explorer API every hour and CloudWatch S3 metrics
every 15 minutes. Exposes all on port 9101 in Prometheus text format.

Metrics exposed:
  aws_cost_daily_usd{service="..."} — today's cost per AWS service
  aws_cost_monthly_usd — current month total cost
  aws_cost_monthly_forecast_usd — forecasted month-end cost
  aws_cost_yesterday_usd{service="..."} — yesterday's cost per service
  aws_s3_bucket_size_bytes{bucket="...",storage_class="..."} — bucket size
  aws_s3_object_count{bucket="...",storage_class="..."} — number of objects
  aws_s3_requests_total{bucket="...",request_type="..."} — request counts
  aws_s3_errors_total{bucket="...",error_type="..."} — 4xx/5xx errors
  aws_s3_bytes_downloaded{bucket="..."} — bytes downloaded
  aws_s3_bytes_uploaded{bucket="..."} — bytes uploaded
  aws_s3_first_byte_latency_ms{bucket="..."} — avg first byte latency
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
_s3_bucket_sizes: dict[tuple[str, str], float] = {}  # (bucket, storage_class) -> bytes
_s3_object_counts: dict[tuple[str, str], int] = {}   # (bucket, storage_class) -> count
_s3_requests: dict[tuple[str, str], float] = {}       # (bucket, request_type) -> count
_s3_errors: dict[tuple[str, str], float] = {}          # (bucket, error_type) -> count
_s3_bytes_down: dict[str, float] = {}                  # bucket -> bytes
_s3_bytes_up: dict[str, float] = {}                    # bucket -> bytes
_s3_latency: dict[str, float] = {}                     # bucket -> ms
_lock = threading.Lock()

# S3 refresh interval: 15 minutes (CloudWatch updates every ~5-10 min)
S3_REFRESH_INTERVAL = 900


def _get_ce_client():
    return boto3.client("ce", region_name="us-east-1")


def _enable_s3_request_metrics():
    """Enable S3 request metrics on all buckets (required for request/error/bandwidth data).

    Creates a metrics configuration named 'EntireBucket' on each bucket.
    This is idempotent — AWS overwrites existing config with same ID.
    Metrics typically appear in CloudWatch within 15 minutes of enabling.
    """
    try:
        s3 = boto3.client("s3")
        buckets = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]

        enabled = 0
        for bucket in buckets:
            try:
                s3.put_bucket_metrics_configuration(
                    Bucket=bucket,
                    Id="EntireBucket",
                    MetricsConfiguration={
                        "Id": "EntireBucket",
                        # No Filter = entire bucket
                    },
                )
                enabled += 1
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code == "AccessDenied":
                    logger.warning("Cannot enable metrics on %r: access denied", bucket)
                else:
                    logger.warning("Cannot enable metrics on %r: %s", bucket, code)

        logger.info("S3 request metrics enabled on %d/%d buckets", enabled, len(buckets))

    except NoCredentialsError:
        logger.error("No AWS credentials — cannot enable S3 request metrics")
    except Exception:
        logger.exception("Failed to enable S3 request metrics")


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


def _fetch_s3_metrics():
    """Fetch S3 bucket metrics from CloudWatch and S3 API."""
    global _s3_bucket_sizes, _s3_object_counts, _s3_requests, _s3_errors
    global _s3_bytes_down, _s3_bytes_up, _s3_latency

    try:
        s3 = boto3.client("s3")
        cw = boto3.client("cloudwatch", region_name=_get_region())

        # List all buckets
        buckets = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
        if not buckets:
            logger.info("No S3 buckets found")
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        # CloudWatch daily storage metrics have 1-day period, look back 2 days
        start_time = now - datetime.timedelta(days=2)

        new_sizes: dict[tuple[str, str], float] = {}
        new_counts: dict[tuple[str, str], int] = {}
        new_requests: dict[tuple[str, str], float] = {}
        new_errors: dict[tuple[str, str], float] = {}
        new_bytes_down: dict[str, float] = {}
        new_bytes_up: dict[str, float] = {}
        new_latency: dict[str, float] = {}

        for bucket in buckets:
            # BucketSizeBytes (daily metric, per storage type)
            for storage_class in ["StandardStorage", "IntelligentTieringStorage",
                                  "GlacierStorage", "ReducedRedundancyStorage"]:
                try:
                    resp = cw.get_metric_statistics(
                        Namespace="AWS/S3",
                        MetricName="BucketSizeBytes",
                        Dimensions=[
                            {"Name": "BucketName", "Value": bucket},
                            {"Name": "StorageType", "Value": storage_class},
                        ],
                        StartTime=start_time,
                        EndTime=now,
                        Period=86400,
                        Statistics=["Average"],
                    )
                    dps = resp.get("Datapoints", [])
                    if dps:
                        latest = max(dps, key=lambda d: d["Timestamp"])
                        new_sizes[(bucket, storage_class)] = latest["Average"]
                except ClientError:
                    pass

            # NumberOfObjects (daily metric)
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/S3",
                    MetricName="NumberOfObjects",
                    Dimensions=[
                        {"Name": "BucketName", "Value": bucket},
                        {"Name": "StorageType", "Value": "AllStorageTypes"},
                    ],
                    StartTime=start_time,
                    EndTime=now,
                    Period=86400,
                    Statistics=["Average"],
                )
                dps = resp.get("Datapoints", [])
                if dps:
                    latest = max(dps, key=lambda d: d["Timestamp"])
                    new_counts[(bucket, "AllStorageTypes")] = int(latest["Average"])
            except ClientError:
                pass

            # Request metrics (only available if S3 request metrics are enabled on bucket)
            req_start = now - datetime.timedelta(hours=1)
            for metric_name, key in [("GetRequests", "Get"), ("PutRequests", "Put"),
                                      ("DeleteRequests", "Delete"), ("HeadRequests", "Head"),
                                      ("ListRequests", "List")]:
                try:
                    resp = cw.get_metric_statistics(
                        Namespace="AWS/S3",
                        MetricName=metric_name,
                        Dimensions=[
                            {"Name": "BucketName", "Value": bucket},
                            {"Name": "FilterId", "Value": "EntireBucket"},
                        ],
                        StartTime=req_start,
                        EndTime=now,
                        Period=3600,
                        Statistics=["Sum"],
                    )
                    dps = resp.get("Datapoints", [])
                    if dps:
                        total = sum(d["Sum"] for d in dps)
                        if total > 0:
                            new_requests[(bucket, key)] = total
                except ClientError:
                    pass

            # Error metrics
            for metric_name, key in [("4xxErrors", "4xx"), ("5xxErrors", "5xx")]:
                try:
                    resp = cw.get_metric_statistics(
                        Namespace="AWS/S3",
                        MetricName=metric_name,
                        Dimensions=[
                            {"Name": "BucketName", "Value": bucket},
                            {"Name": "FilterId", "Value": "EntireBucket"},
                        ],
                        StartTime=req_start,
                        EndTime=now,
                        Period=3600,
                        Statistics=["Sum"],
                    )
                    dps = resp.get("Datapoints", [])
                    if dps:
                        total = sum(d["Sum"] for d in dps)
                        if total > 0:
                            new_errors[(bucket, key)] = total
                except ClientError:
                    pass

            # BytesDownloaded / BytesUploaded
            for metric_name, store in [("BytesDownloaded", new_bytes_down),
                                        ("BytesUploaded", new_bytes_up)]:
                try:
                    resp = cw.get_metric_statistics(
                        Namespace="AWS/S3",
                        MetricName=metric_name,
                        Dimensions=[
                            {"Name": "BucketName", "Value": bucket},
                            {"Name": "FilterId", "Value": "EntireBucket"},
                        ],
                        StartTime=req_start,
                        EndTime=now,
                        Period=3600,
                        Statistics=["Sum"],
                    )
                    dps = resp.get("Datapoints", [])
                    if dps:
                        total = sum(d["Sum"] for d in dps)
                        if total > 0:
                            store[bucket] = total
                except ClientError:
                    pass

            # FirstByteLatency
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/S3",
                    MetricName="FirstByteLatency",
                    Dimensions=[
                        {"Name": "BucketName", "Value": bucket},
                        {"Name": "FilterId", "Value": "EntireBucket"},
                    ],
                    StartTime=req_start,
                    EndTime=now,
                    Period=3600,
                    Statistics=["Average"],
                )
                dps = resp.get("Datapoints", [])
                if dps:
                    avg = sum(d["Average"] for d in dps) / len(dps)
                    new_latency[bucket] = avg
            except ClientError:
                pass

        with _lock:
            _s3_bucket_sizes.clear()
            _s3_bucket_sizes.update(new_sizes)
            _s3_object_counts.clear()
            _s3_object_counts.update(new_counts)
            _s3_requests.clear()
            _s3_requests.update(new_requests)
            _s3_errors.clear()
            _s3_errors.update(new_errors)
            _s3_bytes_down.clear()
            _s3_bytes_down.update(new_bytes_down)
            _s3_bytes_up.clear()
            _s3_bytes_up.update(new_bytes_up)
            _s3_latency.clear()
            _s3_latency.update(new_latency)

        logger.info(
            "S3 metrics updated: %d buckets, %d size entries, %d request entries",
            len(buckets), len(new_sizes), len(new_requests),
        )

    except NoCredentialsError:
        logger.error("No AWS credentials for S3 metrics")
    except Exception:
        logger.exception("Failed to fetch S3 metrics")


def _get_region() -> str:
    """Get AWS region from env or default."""
    import os
    return os.environ.get("AWS_DEFAULT_REGION", "eu-west-1")


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

        # --- S3 Metrics ---

        # Bucket size
        if _s3_bucket_sizes:
            lines.append("# HELP aws_s3_bucket_size_bytes S3 bucket size in bytes")
            lines.append("# TYPE aws_s3_bucket_size_bytes gauge")
            for (bucket, sc), size in sorted(_s3_bucket_sizes.items()):
                lines.append(f'aws_s3_bucket_size_bytes{{bucket="{bucket}",storage_class="{sc}"}} {size:.0f}')

        # Object count
        if _s3_object_counts:
            lines.append("# HELP aws_s3_object_count Number of objects in S3 bucket")
            lines.append("# TYPE aws_s3_object_count gauge")
            for (bucket, sc), count in sorted(_s3_object_counts.items()):
                lines.append(f'aws_s3_object_count{{bucket="{bucket}",storage_class="{sc}"}} {count}')

        # Request counts
        if _s3_requests:
            lines.append("# HELP aws_s3_requests_total S3 request count by type")
            lines.append("# TYPE aws_s3_requests_total gauge")
            for (bucket, req_type), count in sorted(_s3_requests.items()):
                lines.append(f'aws_s3_requests_total{{bucket="{bucket}",request_type="{req_type}"}} {count:.0f}')

        # Error counts
        if _s3_errors:
            lines.append("# HELP aws_s3_errors_total S3 error count by type")
            lines.append("# TYPE aws_s3_errors_total gauge")
            for (bucket, err_type), count in sorted(_s3_errors.items()):
                lines.append(f'aws_s3_errors_total{{bucket="{bucket}",error_type="{err_type}"}} {count:.0f}')

        # Bytes downloaded
        if _s3_bytes_down:
            lines.append("# HELP aws_s3_bytes_downloaded Bytes downloaded from S3 bucket")
            lines.append("# TYPE aws_s3_bytes_downloaded gauge")
            for bucket, val in sorted(_s3_bytes_down.items()):
                lines.append(f'aws_s3_bytes_downloaded{{bucket="{bucket}"}} {val:.0f}')

        # Bytes uploaded
        if _s3_bytes_up:
            lines.append("# HELP aws_s3_bytes_uploaded Bytes uploaded to S3 bucket")
            lines.append("# TYPE aws_s3_bytes_uploaded gauge")
            for bucket, val in sorted(_s3_bytes_up.items()):
                lines.append(f'aws_s3_bytes_uploaded{{bucket="{bucket}"}} {val:.0f}')

        # First byte latency
        if _s3_latency:
            lines.append("# HELP aws_s3_first_byte_latency_ms Average first byte latency in ms")
            lines.append("# TYPE aws_s3_first_byte_latency_ms gauge")
            for bucket, val in sorted(_s3_latency.items()):
                lines.append(f'aws_s3_first_byte_latency_ms{{bucket="{bucket}"}} {val:.2f}')

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


def _cost_refresh_loop():
    """Background thread that refreshes costs every hour."""
    while True:
        _fetch_costs()
        time.sleep(REFRESH_INTERVAL)


def _s3_refresh_loop():
    """Background thread that refreshes S3 metrics every 15 minutes."""
    while True:
        _fetch_s3_metrics()
        time.sleep(S3_REFRESH_INTERVAL)


def main():
    logger.info("Starting AWS Cost + S3 Exporter on :9101")

    # Enable S3 request metrics on all buckets (idempotent)
    _enable_s3_request_metrics()

    # Initial fetch
    _fetch_costs()
    _fetch_s3_metrics()

    # Background refresh threads
    t1 = threading.Thread(target=_cost_refresh_loop, daemon=True)
    t1.start()
    t2 = threading.Thread(target=_s3_refresh_loop, daemon=True)
    t2.start()

    # Serve metrics
    server = HTTPServer(("0.0.0.0", 9101), MetricsHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
