#!/usr/bin/env bash
# Smoke-test a deployed AI Road Detector API.
#
#   ./scripts/smoke.sh https://your-project.vercel.app
#
# Hits /health, /limits and one real /detect upload, printing each response.
# Exits non-zero with a reason on failure. Needs curl, python3 and Pillow
# (Pillow is a runtime dependency of the API itself).

set -uo pipefail

BASE_URL="${1:-http://127.0.0.1:8000}"
BASE_URL="${BASE_URL%/}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
IMAGE="$TMP_DIR/road.jpg"
BODY="$TMP_DIR/detect.json"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

echo "== GET $BASE_URL/health"
health="$(curl -fsS "$BASE_URL/health")" || fail "/health did not return 2xx - is the deployment up?"
echo "$health"
echo "$health" | grep -q '"status":"ok"' || fail "/health did not report status ok"
echo "$health" | grep -q '"durable_report_storage":true' \
    || echo "WARN: durable_report_storage is false - DATABASE_URL is not set for this environment"

echo
echo "== GET $BASE_URL/limits"
limits="$(curl -fsS "$BASE_URL/limits")" || fail "/limits did not return 2xx"
echo "$limits"
echo "$limits" | grep -q '"max_upload_bytes"' || fail "/limits did not report max_upload_bytes"

echo
echo "== POST $BASE_URL/detect"
python3 - "$IMAGE" <<'PY' || fail "could not build a test image (python3 with Pillow is required)"
import sys

from PIL import Image, ImageDraw

image = Image.new("RGB", (320, 240), (150, 150, 150))
ImageDraw.Draw(image).rectangle([80, 60, 240, 180], fill=(25, 25, 25))
image.save(sys.argv[1], format="JPEG", quality=90)
PY

code="$(curl -sS -o "$BODY" -w '%{http_code}' \
    -X POST "$BASE_URL/detect" \
    -F "image=@$IMAGE;type=image/jpeg" \
    -F "latitude=12.9716" \
    -F "longitude=77.5946")" || fail "could not reach $BASE_URL/detect"
echo "HTTP $code"
cat "$BODY"
echo

case "$code" in
    200) grep -q '"predictions"' "$BODY" || fail "/detect answered 200 without predictions" ;;
    413) fail "/detect answered 413 - the 4.5 MB body cap was hit" ;;
    429) fail "/detect answered 429 - rate limited by DETECT_RATE_LIMIT, retry after the window" ;;
    503) fail "/detect answered 503 - DATABASE_URL is missing, so the shared rate limiter is off" ;;
    *) fail "/detect answered HTTP $code" ;;
esac

echo
echo "OK: health, limits and detection all answered from $BASE_URL"
