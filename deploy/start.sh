#!/bin/sh
# Cloud Run container entrypoint for the backend.
#
# /srv/workspace and /srv/var are built into the image (see Dockerfile.cloudrun),
# exactly as they are locally, so there is nothing to prepare here. The only
# reason this script exists rather than a plain CMD is $PORT: Cloud Run chooses
# the port at run time and health-checks it, so it has to be expanded by a
# shell.
#
# One worker on purpose: the app keeps per-company LangGraph checkpointer pools
# in memory, and several workers in one container would multiply database
# connections for no gain - Cloud Run scales by adding instances, not workers.
set -eu

exec uvicorn app.server:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
