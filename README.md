# AI Road Detector

Modern smart-city Android app that finds **potholes** and **road cracks**. Detection runs **on the device** (offline). Saved reports sync later when the phone is online. The GPS map uses **Google Maps satellite** imagery.

The Python OOP core in `python/` is the same domain model: detector, reports and the sync API phones upload to.

## Status

| Piece | State |
| --- | --- |
| `python/airoad` domain models, detectors, CLI, FastAPI sync API | Implemented, tested |
| Report persistence (`DATABASE_URL` → Postgres, SQLite for dev) | Implemented, tested |
| Tests + CI (`.github/workflows/python-tests.yml`) | Implemented, runs on push/PR |
| Vercel deployment (`app.py` entrypoint, `.python-version`) | Configured, see [Deploying on Vercel](#deploying-on-vercel) |
| Android app (`android/`) — screens, `OnDeviceDetector`, Room store, `ReportSyncWorker`, Maps view | **Not in this repository yet** |

The Python half below is deployable today. The Android half is described under [Planned Android app](#planned-android-app) so the roadmap stays in one place.

## Screens (planned Android app)

1. **Home / Scan Road** — Take Photo, Upload Image, Live Detection
2. **AI Detection Results** — road image, defect labels, confidence %, GPS
3. **Road Damage History** — saved reports and sync status
4. **GPS Map** — satellite view with green defect markers

Theme: dark navy background, green accents, white text.

## Offline AI

- Inference never needs the internet.
- Python: `HeuristicDetector` / `OfflineDetectorFacade` — a light, CPU-friendly model (`BaseDetector` subclass). `TFLiteDetector` is a documented placeholder for a quantized TFLite model.
- Reports carry `synced = false` until the sync API accepts them.

## Python setup

Run from the repository root. `requirements.txt` lives at the root, not in `python/`.

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -e ".[dev]"   # installs the package + runtime deps + pytest
# ...or just the runtime dependencies, running straight from the source tree:
pip install -r requirements.txt
```

`pip install -e .` puts `airoad` on the path, so none of the commands below need `PYTHONPATH`.

### CLI — offline detection

With the package installed (`pip install -e .`), from anywhere:

```bash
airoad path/to/road.jpg --lat 12.9716 --lng 77.5946
```

Running from a plain checkout instead, from the repository root:

```bash
PYTHONPATH=python python -m airoad path/to/road.jpg --lat 12.9716 --lng 77.5946
```

```powershell
# Windows PowerShell
$env:PYTHONPATH="python"
python -m airoad path\to\road.jpg --lat 12.9716 --lng 77.5946
```

Add `--json` for machine-readable output. Exit codes: `0` success, `2` missing/unreadable
image or invalid coordinates, `3` photo over the upload limit.

Check whether a photo fits the 4.5 MB upload cap before sending it:

```bash
airoad path/to/road.jpg --check-upload
# path/to/road.jpg: 1.2 MB - fits the 4.5 MB upload limit
```

Exit code `3` means it is over the limit and should be downscaled first.

Sample output:

```
Image:    path/to/road.jpg
Model:    on-device-heuristic-v1 (offline: yes)
Location: 12.971600,77.594600
Primary:  Pothole (100%)
  - Pothole: 100%
  - Road Crack: 24%
```

### Sync API

```bash
uvicorn airoad.api:app --host 0.0.0.0 --port 8000
```

From a plain checkout without installing: `cd python && PYTHONPATH=. uvicorn airoad.api:app --port 8000`.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness probe; reports the active model and whether durable report storage is configured |
| `GET /limits` | Upload size cap a client should check before sending a photo |
| `POST /detect` | Multipart `image` plus optional `latitude`/`longitude` form fields → detection result JSON; **413** with the limit when the photo is too large |
| `POST /reports` | Persists the reports the phone queued offline; returns `accepted` / `stored` / `skipped` / `total_synced` |
| `GET /reports` | Already-synced reports, newest first (`?limit=`, default 50) |

Interactive docs are served at `http://localhost:8000/docs`.

### Report storage

Synced reports go through `airoad/store.py`, which speaks SQL through SQLAlchemy and is
configured by `DATABASE_URL`:

- **Set** (`postgresql://…` or `sqlite:///…`) — reports are written there and a `reports`
  table is created on first use.
- **Unset** — local development falls back to `sqlite:///reports.db` (gitignored). On
  Vercel a fallback file would silently lose data, so `/reports` answers **503** instead
  and `/health` reports `"durable_report_storage": false`.

`report_id` is the table's primary key, so a phone retrying an upload after a dropped
connection does not create duplicates: the repeat is reported in `skipped`.

Point the Android worker URL (`ReportSyncWorker.DEFAULT_URL`) at your PC's LAN IP if you run this server.

## Deploying the API

The app is a standard ASGI application, so any host that runs `uvicorn` works:

```bash
pip install .
uvicorn airoad.api:app --host 0.0.0.0 --port ${PORT:-8000}
```

Installing the package (`pip install .`, or `pip install -e .` for development) makes
`airoad` importable, so no `PYTHONPATH` is needed. Dependencies are declared in both
`pyproject.toml` and `requirements.txt` — keep the two lists in sync.

## Deploying on Vercel

`app.py` in the repository root is the Vercel entrypoint. Vercel's FastAPI preset looks
for a module-level `app`, and the real application lives in `python/airoad/api.py`, so
`app.py` puts that source root on `sys.path` and re-exports it. No `vercel.json`,
rewrites or build command are needed.

1. Push the repo to GitHub.
2. In Vercel, choose **Add New → Project** and import the repository.
3. Leave the detected framework preset as **FastAPI** and deploy.

Or from the CLI:

```bash
npm i -g vercel
vercel          # preview deployment
vercel --prod   # production deployment
```

The first `vercel` run links the project to your account; there is nothing else to
configure. Dependencies are installed from `requirements.txt` / `pyproject.toml`, and
`.python-version` pins the runtime to Python 3.12.

### Vercel limits that affect this API

| Limit | Effect |
| --- | --- |
| 4.5 MB request body | `POST /detect` returns **413** above this. Downscale photos on the device before uploading. |
| 500 MB uncompressed function bundle (Python) | Plenty for numpy + Pillow. |
| 300 s max duration on Hobby | Detection resizes to 224 px and finishes in well under a second. |

### Give the deployment a database

Without `DATABASE_URL`, `/reports` answers **503** on Vercel: a serverless filesystem is
read-only apart from an ephemeral `/tmp`, so there is nowhere durable to write. Any
Postgres works; Vercel Postgres is the shortest path:

1. In the Vercel project open **Storage → Create → Postgres** (or use Neon/Supabase).
2. Copy the **pooled** connection string (contains `-pooler`) — serverless functions open
   many short-lived connections, which the pooler is built for.
3. Add it to the project's environment variables as `DATABASE_URL` and redeploy.

`GET /health` then reports `"durable_report_storage": true`, and the `reports` table is
created on the first upload. Reports outlive individual instances because they are in the
database, not in the function's memory.

## OOP layout

| Class | Role |
| --- | --- |
| `DefectType`, `GeoLocation`, `DefectPrediction`, `DetectionResult`, `DamageReport` | Domain models (`airoad/models.py`) |
| `BaseDetector` | Abstract detector |
| `HeuristicDetector`, `TFLiteDetector` | On-device implementations (`airoad/detector.py`) |
| `OfflineDetectorFacade` | Chooses a local model |
| `ReportStore`, `SqlReportStore` | Report persistence (`airoad/store.py`) |
| `MAX_UPLOAD_BYTES`, `check_upload_size`, `check_upload_file` | Upload limit helpers (`airoad/limits.py`) |

## Tests and CI

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

`conftest.py` puts `python/` on `sys.path`, so tests run from the repository root without installing the package. Coverage: domain models, detector behaviour (including failure modes), report persistence
(including survival across a fresh store instance and duplicate suppression), upload limits,
the FastAPI endpoints and the CLI.

`.github/workflows/python-tests.yml` runs the suite on Python 3.10 and 3.12 for every push to `main` and every pull request, then smoke-tests the API and CLI imports.

## Planned Android app

Not yet committed. When added, it belongs in `android/` and needs:

1. Create a key in [Google Cloud Console](https://console.cloud.google.com/) with **Maps SDK for Android** enabled.
2. Copy `android/local.properties.example` to `android/local.properties` (gitignored).
3. Set `sdk.dir` and `MAPS_API_KEY`.
4. `MapFragment` sets `googleMap.mapType = GoogleMap.MAP_TYPE_SATELLITE`.
5. Open `android/` in Android Studio, let Gradle sync, run on a device or emulator (Google Play image for Maps), and grant camera and location when asked.
