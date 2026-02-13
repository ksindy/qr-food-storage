#!/bin/sh
set -e

# Ensure upload directory exists on the persistent volume
mkdir -p /data/uploads

# Run with gunicorn + uvicorn workers
exec gunicorn app.main:app \
    --bind 0.0.0.0:8000 \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --timeout 120
