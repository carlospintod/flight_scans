# Deploying to Streamlit Community Cloud

Step-by-step. ~30 minutes end-to-end; most of it is account signups.

## What you'll end up with

- A public URL (e.g. `https://flight-tracker-carlos.streamlit.app`) you
  can hit from any device.
- Your existing 3,500+ rows of price history preserved, sitting on
  Turso (free cloud SQLite that survives Streamlit Cloud restarts).
- A password gate so randos who guess the URL can't burn your API
  quota.
- The local copy still works the same way as before — you can sweep
  locally too and the data syncs across.

## Prereqs

You'll create three free accounts:

1. **Turso** — cloud SQLite, https://turso.tech
2. **Streamlit Community Cloud** — app hosting, https://share.streamlit.io
3. (You already have **GitHub** at carlospintod)

## Step 1 — Push the code to GitHub

Currently your 17+ commits are local only. The Streamlit Cloud deploy
flow pulls from a GitHub repo, so the code has to live there first.

```powershell
cd C:\Users\Carlos\flight_scans
git push -u origin main
```

If git push errors with "permission denied" or similar, run
`gh auth status` to verify you're signed in.

### Important: make the repo public OR invite Streamlit

Streamlit Community Cloud's free tier deploys from public repos
without setup. For private repos, you grant the Streamlit Cloud
GitHub app collaborator access on the repo settings page.

If you go public: nothing sensitive lives in the repo (`.env` is
gitignored, `data/tracker.db` is gitignored). Your search patterns,
flight history etc. won't be visible — those live in Turso, not the
repo. Public is the simpler path.

## Step 2 — Sign up for Turso, create the DB

1. Go to https://turso.tech, sign up with GitHub. Free.
2. Install the Turso CLI:
   ```powershell
   irm get.turso.tech/install.ps1 | iex
   ```
   (Or scoop, choco, whatever you prefer.)
3. Log in and create the DB:
   ```powershell
   turso auth signup        # if you haven't via web
   turso db create flight-tracker
   turso db show flight-tracker --url
   # copy the URL — looks like libsql://flight-tracker-<your-org>.turso.io
   turso db tokens create flight-tracker
   # copy the long token string
   ```
4. Add the URL and token to your local `.env`:
   ```
   TURSO_DATABASE_URL=libsql://flight-tracker-<your-org>.turso.io
   TURSO_AUTH_TOKEN=<the token>
   ```

## Step 3 — Migrate your local data to Turso

One-time. Copies your existing 3,500+ rows up to Turso so the cloud
deploy starts with history (otherwise alerts wouldn't fire for a
month).

```powershell
.\.venv\Scripts\python.exe -m pip install libsql-experimental
.\.venv\Scripts\python.exe migrate_to_turso.py
```

The script prints how many rows it copies per table. Verify with:
```powershell
turso db shell flight-tracker "SELECT COUNT(*) FROM calendar_snapshots"
```

Should match your local count (currently ~1,960+ depending on
recent runs).

## Step 4 — Sign up for Streamlit Community Cloud

1. Go to https://share.streamlit.io, sign in with GitHub.
2. Click **New app**.
3. Repository: `carlospintod/flight_scans`
4. Branch: `main`
5. Main file: `ui/app.py`
6. App URL: pick something like `flight-tracker-carlos`.
7. Click **Deploy** but don't watch the logs yet — it'll fail because
   secrets aren't set. That's the next step.

## Step 5 — Set Streamlit Cloud secrets

In the Streamlit Cloud dashboard for the app, click **Settings → Secrets**.

Paste the TOML below, filling in your real values. The structure
matters: Streamlit copies `[env]` keys into `os.environ` at runtime,
which is what your code reads.

```toml
[env]
SEARCHAPI_KEY = "sk_..."
RAPIDAPI_KEY = "..."
TRAVELPAYOUTS_TOKEN = "..."

TURSO_DATABASE_URL = "libsql://flight-tracker-<your-org>.turso.io"
TURSO_AUTH_TOKEN = "..."

APP_PASSWORD = "pick-a-strong-passphrase-only-you-know"
```

Save. Streamlit will redeploy automatically — takes ~60 seconds.

## Step 6 — Verify

Open your app URL. You should see:

- The password prompt (because `APP_PASSWORD` is set as a secret).
- After auth, the System status row with your existing
  `last sweep` and `rows captured` numbers (from Turso).
- The Explore page should show your full heatmap + alternatives.

Click **Refresh SearchAPI quota** to confirm the API keys propagated
correctly. If the toast says "67 / unknown" or similar, you're live.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `TURSO_DATABASE_URL is set but libsql_experimental is not installed` | requirements.txt needs to include `libsql-experimental`. Push a commit that updates it, Streamlit will redeploy. |
| Password prompt appears locally too | You set `APP_PASSWORD` in your local `.env`. Remove it from `.env` to bypass locally; keep it only in Streamlit Cloud secrets. |
| App stuck on "Please wait..." | First container start can take a couple of minutes for a fresh Turso sync of many rows. Watch the Streamlit Cloud logs. |
| Streamlit shows old data | Cache. Click "Rerun" in the top-right hamburger menu, or restart the app from the dashboard. |

## What stays the same

- Your local copy still works exactly as before. If you set
  `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` locally, your local
  sweeps write to Turso too — so the cloud version sees the same
  data. If you don't, local writes go to `data/tracker.db` only.
- The CLI (`python tracker.py ...`) also respects the Turso env vars
  in the same way.
