# Public Preview Deploy

## Current conclusion

The project is now suitable for a public preview link, not just local use.

It is suitable for:

- one public preview environment
- single-process deployment
- low to moderate concurrent use
- server-side API key storage
- single-instance restart recovery through SQLite job index + local artifact files

It is not yet suitable for:

- multi-instance horizontal scaling
- cross-instance shared result storage
- authenticated multi-tenant usage
- strict quota accounting / billing
- worker-queue style long-running job isolation

## Fastest path

Use one Python web service on a platform like:

- Render
- Railway
- Fly.io
- Cloud Run

## Start commands

From the workspace root:

```bash
python -m travel_control_tower.run_web
```

Local preview:

```bash
python run_web_local.py
```

Container:

```bash
docker build -t travel-control-tower .
docker run --rm -p 8770:8770 travel-control-tower
```

## Runtime endpoints

- `/health`
  - lightweight liveness check
- `/ready`
  - readiness JSON for public-preview deployment
  - reports storage/database status and deployment warnings

Example:

```bash
curl http://127.0.0.1:8770/ready
```

## Required environment variables

Set only what you actually need:

```bash
AMAP_WEB_KEY=...
GOOGLE_MAPS_API_KEY=...
FLYAI_CMD=...
OPENAI_API_KEY=...
TRAVEL_PLANNER_MODE=auto
TRAVEL_PLANNER_MODEL=gpt-4.1-mini
```

Optional but recommended:

```bash
TRAVEL_WEB_HOST=0.0.0.0
TRAVEL_WEB_PORT=8770
TRAVEL_WEB_DATA_DIR=.runtime-data
TRAVEL_PREVIEW_ACCESS_TOKEN=your-preview-password
TRAVEL_PREVIEW_RATE_LIMIT_COUNT=6
TRAVEL_PREVIEW_RATE_LIMIT_WINDOW_SECONDS=600
TRAVEL_PREVIEW_JOB_RETENTION_HOURS=72
```

Notes:

- on most cloud platforms, do not set `TRAVEL_WEB_PORT`; let `PORT` drive the bind port
- `TRAVEL_WEB_DATA_DIR` controls where jobs, latest HTML/JSON/Excel, and the SQLite job index are written
- point `TRAVEL_WEB_DATA_DIR` to a persistent volume path if the platform provides one
- if `TRAVEL_PREVIEW_ACCESS_TOKEN` is set, the preview site requires a password before showing the planner UI
- preview submissions can be rate-limited per IP with `TRAVEL_PREVIEW_RATE_LIMIT_COUNT` and `TRAVEL_PREVIEW_RATE_LIMIT_WINDOW_SECONDS`
- completed / failed preview jobs can be auto-pruned with `TRAVEL_PREVIEW_JOB_RETENTION_HOURS`

## Minimum build dependencies

Install:

```bash
pip install -r travel_control_tower/requirements-web.txt
```

## Deployment files already included

- `render.yaml`
- `Procfile`
- `Dockerfile`

## What is already durable now

- job metadata is indexed in SQLite under the runtime data directory
- each job still keeps its own `job.json`, `plan.json`, `result.html`, `plan.xlsx`
- after a single-instance restart, the app can recover:
  - job status
  - latest successful result
  - missing HTML / Excel rebuilt from `plan.json`

## What still blocks a stronger beta

### 1. Storage is still single-instance local

The app is now durable enough for one preview instance, but results still live on local disk.

Needed next:

- object storage or mounted persistent volume
- shared storage if you want more than one web instance

### 2. Execution is still thread-based

This is acceptable for preview traffic, but not ideal for heavier usage.

Needed next:

- queue-backed jobs
- worker process separation

### 3. Access control is still lightweight

Current preview protection is password gate + rate limit. It is intentionally simple.

Needed next for beta:

- user accounts or admin-only access
- usage budgets / quotas
- better abuse monitoring

## Practical readiness estimate

### Public preview

Can be done now after wiring a cloud deployment and environment variables.

Expected effort:

- 0.5 to 1 day

### Stable beta

Needs shared storage, stronger access control, and queued execution.

Expected effort:

- 3 to 5 days for a usable beta
- longer if you want accounts, payment, or collaboration
