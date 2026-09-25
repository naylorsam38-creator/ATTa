# Deploying ATT on Coolify

> **Status:** the Coolify server side is ready and tested. ATT's code hasn't been shared yet, so the settings below that depend on ATT are listed as open questions rather than guessed.
> When the ATT code arrives, fill in the table and the deploy is about ten minutes of work.

## What ATT needs to tell us

| # | Question | Why Coolify needs it | Answer |
|---|---|---|---|
| 1 | Where is the code? (GitHub repo URL, public or private) | Coolify pulls and builds from git | _pending_ |
| 2 | Language / framework (Node, Python, PHP, Go, static site…) | Picks the build method | _pending_ |
| 3 | Is there a `Dockerfile` or `docker-compose.yml`? | If yes, Coolify uses it as-is | _pending_ |
| 4 | Port the app listens on inside the container | Coolify's proxy routes the domain to it | _pending_ |
| 5 | Environment variables and secrets (names only, not values) | Set in Coolify, encrypted at rest | _pending_ |
| 6 | Database / cache (Postgres, MySQL, Redis, Mongo…) | Created as Coolify resources with backups | _pending_ |
| 7 | Files that must survive redeploys (uploads, SQLite) | Need a persistent volume | _pending_ |
| 8 | Health-check URL (for example `/health`) | Zero-downtime deploys, and it catches bad releases | _pending_ |
| 9 | Domain (for example `att.yourdomain.com`) | HTTPS certificate and routing | _pending_ |
| 10 | Background workers / cron jobs? | Separate process or scheduled task | _pending_ |

## Deploy steps (once the table is filled in)

### 1. Connect GitHub (private repos)

In Coolify go to **Sources → Add → GitHub App** and follow the prompts.
This installs a GitHub App on the ATT repo only, so Coolify can pull the code and **auto-deploy on every push**.
Public repos can skip this step and use **Public Repository** in step 2.

### 2. Create the app

1. **Projects → New Project**, named `ATT`.
2. **Add Resource → Private Repository (with GitHub App)** and pick the ATT repo and branch (usually `main`).
3. **Build Pack**:
   - **Dockerfile** if the repo has one. This is the most predictable option and the preferred one.
   - **Docker Compose** if it has a `docker-compose.yml` that describes the whole stack.
   - **Railpack / Nixpacks** otherwise. Coolify detects the language and builds automatically.
4. **Ports Exposes**: the port from question 4.
5. **Domains**: `https://att.yourdomain.com`. The `https://` prefix makes Coolify get a Let's Encrypt certificate.

### 3. Configuration

- **Environment Variables**: add each variable from question 5. Tick **Build Variable** only for values needed at build time.
- **Health Checks**: enable, and set the path from question 8.
- **Persistent Storage**: add a volume for anything from question 7.

### 4. Database (if needed)

1. In the same project: **Add Resource → Database**, then choose the engine from question 6.
2. Copy its **internal** connection URL into ATT's environment variables, for example `DATABASE_URL`.
   Use the internal URL, not a public one. The database should not be exposed to the internet.
3. **Backups tab**: set a schedule (for example daily at 03:00) and an S3 destination.

### 5. Deploy

Click **Deploy** and watch the build log.
When it finishes, open `https://att.yourdomain.com`.

With the GitHub App connected, every push to the branch triggers a new deploy.
Coolify can also build **preview deployments** for pull requests; turn this on under the app's **Preview Deployments** tab.

## After the first deploy

- [ ] `https://att.yourdomain.com` loads with a valid certificate
- [ ] Push a trivial commit and confirm it auto-deploys
- [ ] Database backup ran once (Backups tab shows a success)
- [ ] Notifications (Settings → Notifications: email, Discord, Telegram…) are set up for failed deploys and backups
- [ ] `./scripts/verify.sh` is still green
