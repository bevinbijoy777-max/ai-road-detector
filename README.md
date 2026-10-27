# AI Road Detector

Modern smart-city Android app that finds **potholes** and **road cracks**. Detection runs **on the device** (offline). Saved reports sync later when the phone is online. The GPS map uses **Google Maps satellite** imagery.

Python OOP code in `python/` is the same domain model: detector, reports, offline store, and cloud sync.

## Screens

1. **Home / Scan Road** — Take Photo, Upload Image, Live Detection  
2. **AI Detection Results** — road image, defect labels, confidence %, GPS  
3. **Road Damage History** — saved reports and sync status  
4. **GPS Map** — satellite view with green defect markers  

Theme: dark navy background, green accents, white text. App icon is a road + location pin (`android/app/src/main/res/drawable/ic_logo.xml`).

## Offline AI

- Inference never needs the internet.
- Android: `OnDeviceDetector` (lightweight, CPU-friendly; swap in a quantized TFLite model later).
- Python: `HeuristicDetector` / `OfflineDetectorFacade` — same idea, OOP (`BaseDetector` subclass).
- Reports are stored locally (`Room` on Android, JSON store in Python) with `synced = false`.
- `ReportSyncWorker` (WorkManager) and `CloudSyncService` upload pending reports when connectivity returns.

## Google Maps satellite view

1. Create a key in [Google Cloud Console](https://console.cloud.google.com/) with **Maps SDK for Android** enabled.  
2. Copy `android/local.properties.example` to `android/local.properties`.  
3. Set `sdk.dir` and `MAPS_API_KEY`.  
4. `MapFragment` sets `googleMap.mapType = GoogleMap.MAP_TYPE_SATELLITE`.

## Android (Android Studio)

1. Open the `android/` folder.  
2. Let Gradle sync, then run on a device or emulator (Google Play image for Maps).  
3. Grant camera and location when asked.

## Python OOP backend / CLI

```bash
cd python
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Offline detect (no internet)
set PYTHONPATH=.
python -m airoad path\to\road.jpg --lat 12.9716 --lng 77.5946

# Sync API (run when you want the phone to upload)
uvicorn airoad.api:app --host 0.0.0.0 --port 8000
```

OOP layout:

| Class | Role |
| --- | --- |
| `DefectType`, `GeoLocation`, `DefectPrediction`, `DetectionResult`, `DamageReport` | Domain models |
| `BaseDetector` | Abstract detector |
| `HeuristicDetector`, `TFLiteDetector` | On-device implementations |
| `OfflineDetectorFacade` | Chooses a local model |
| `OfflineReportStore` | Local persistence |
| `SyncQueue`, `CloudSyncService` | Later sync |

Point the Android worker URL (`ReportSyncWorker.DEFAULT_URL`) at your PC’s LAN IP if you run the FastAPI server.

## GitHub

```bash
cd C:\Users\USER\ai-road-detector
git add .
git commit -m "Initial AI Road Detector app with offline on-device detection"
gh repo create ai-road-detector --private --source=. --remote=origin --push
```

CI: `.github/workflows/python-tests.yml` runs the model unit tests on push.
