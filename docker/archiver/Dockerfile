FROM python:3.12-slim

WORKDIR /app

# Install only the runtime dependencies needed by the archiver script.
# psycopg2-binary bundles libpq so no system packages are required.
RUN pip install --no-cache-dir boto3==1.35.* psycopg2-binary==2.9.*

COPY scripts/archive_jobs.py /app/archive_jobs.py

# Run weekly: sleep 7 days between each archiver execution.
# The first run fires immediately on container start, then repeats every 7 days.
ENTRYPOINT ["/bin/sh", "-c", \
  "while true; do \
     echo \"[archiver] $(date -u +%Y-%m-%dT%H:%M:%SZ) Starting archive run\"; \
     python /app/archive_jobs.py \"$@\"; \
     echo \"[archiver] $(date -u +%Y-%m-%dT%H:%M:%SZ) Sleeping 7 days\"; \
     sleep 604800; \
   done", "--"]
