# Deploy the sync API on Vercel

The Python half of this project deploys as one Vercel Function: `/health`, `/limits`,
`/detect`, `/reports` and `/docs`. `app.py` is the entrypoint, `.python-version` pins
Python 3.12, and `requirements.txt` lists the dependencies — there is no `vercel.json`
to write and no build command to set.

You need a Vercel account, a Postgres database, and the Vercel CLI (`npm i -g vercel`).

## 1. Get a pooled Postgres URL

`DATABASE_URL` is required: reports are stored there, and so is the `/detect` rate limit
that every instance shares. Without it `/reports` and `/detect` answer **503** by design.

In the Vercel dashboard: **Storage → Create → Postgres** (or use Neon/Supabase), then copy
the **pooled** connection string — the Neon/Vercel one contains `-pooler`, Supabase's is
`…pooler.supabase.com` (port 6543 for transaction mode).

Pooled matters: a serverless function opens many short-lived connections and the pooler
multiplexes them. The app already keeps exactly one connection per instance, reuses it, and
switches off psycopg's server-side prepared statements, so transaction-mode poolers work.

## 2. Deploy

```bash
cd ai-road-detector
vercel login
vercel link                 # creates/links the project; keep the detected FastAPI preset

vercel env add DATABASE_URL production        # paste the pooled string
vercel env add DETECT_RATE_LIMIT production   # optional, default 30/minute
vercel env add DATABASE_URL preview           # optional, so previews work too

vercel --prod
```

`vercel env add` prompts for the value; `printf '%s' "$URL" | vercel env add DATABASE_URL production`
does the same non-interactively. The dashboard equivalent is
**Project → Settings → Environment Variables**.

Environment variables are only picked up by new function instances, so redeploy
(`vercel --prod`) after changing one.

## 3. Smoke-test the deployment

```bash
./scripts/smoke.sh https://your-project.vercel.app
```

It calls `/health`, `/limits` and one real `/detect` upload, prints each response, and exits
non-zero (with a reason: `413` body cap, `429` rate limit, `503` missing `DATABASE_URL`)
if anything is wrong. Requires `curl`, `python3` and Pillow, which are runtime dependencies
of the API anyway.

Check `/health` reports `"durable_report_storage": true`. If it says `false`, `DATABASE_URL`
is not set for that environment (or the deployment predates it).

## 4. Create a driver token against the deployed database

`/reports` needs a per-driver bearer token, and only a SHA-256 hash is stored, so the token
is printed exactly once. Run the admin CLI against the same pooled URL:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

export DATABASE_URL="postgresql://…-pooler.…/db"     # the deployment's pooled URL
python -m airoad.admin create-driver "Asha"
# driver_id: 47b7e19e10037084
# name:      Asha
# token:     0Yb…        (printed once - only its hash is kept)
```

Keep that token for the phone build. Confirm it is accepted by the deployment:

```bash
curl -H "Authorization: Bearer $TOKEN" https://your-project.vercel.app/reports
```

`200` means the token and the database are good; `401` means unknown or revoked token;
`503` means the deployment has no `DATABASE_URL`. Other commands: `add-token DRIVER_ID`
(rotate), `list-drivers`, `revoke-token TOKEN`.

## Limits worth knowing

| Limit | Effect |
| --- | --- |
| 4.5 MB request body | `POST /detect` is rejected at the edge above this; downscale photos on the device and check `GET /limits` first. |
| 500 MB uncompressed Python bundle | The installed dependencies are ~200 MB, so there is plenty of room. |
| One connection per instance | The store, rate limiter and driver store share one cached engine; scale by instances, not by pool size. |
