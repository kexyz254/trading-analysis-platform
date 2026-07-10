# Production Readiness Audit

Conducted 2026-07-10, ahead of first VPS deployment (Hetzner server
already created). Scope: is this genuinely production-ready, crash-safe,
and deployable as-is - not a code-quality review (see the earlier trading
logic review for that). Findings are graded by real consequence, not by
how the code reads.

## Verdict

**Not yet safe to `docker compose up -d` and walk away.** The core
engine's runtime resilience (circuit breaker, warm-start reconciliation,
protective stops, graceful shutdown, health scoring) is genuinely solid -
better than most indie trading systems. But the *deployment path itself*
has never been exercised: nobody has built or run this Docker image, and
two of RUNBOOK.md's own four checklist items (backup automation, external
health monitoring) are explicitly still TODO in that document. Ship-readiness
and code-readiness are different claims, and only the second is currently true.

## BLOCKERS - fix or verify before `docker compose up -d` on the VPS

### 1. The Docker image has never actually been built or run
No CI step builds the image; `.github/workflows/python-tests.yml` only
runs `pytest` directly on GitHub's `ubuntu-latest` runner - an environment
that already has build tooling and shared libraries GitHub preinstalls,
which is NOT the same environment as `python:3.11-slim` on a fresh Hetzner
box. Concretely at risk:
- **xgboost/scikit-learn native dependencies.** `python:3.11-slim` is a
  minimal Debian image; compiled ML wheels (xgboost in particular)
  historically need `libgomp1` present on the OS, which this Dockerfile
  never installs (`apt-get install` only adds `git`). Some xgboost wheel
  versions bundle this internally and some don't - **this must be
  verified by actually building and running the image**, not assumed
  either way. This is the single most likely "works everywhere except
  where it matters" failure.
- Nothing has ever confirmed `pip install -e .` + the full agent import
  chain succeeds inside the slim image at all.

**Action before deploying**: on the VPS, run `docker compose build` and
then a short smoke test (`docker compose run --rm engine python
run_system.py --cycles 1`) BEFORE `docker compose up -d`. RUNBOOK.md's own
step 3 already says to do this - the gap is that nothing forces it to
happen, and it hasn't yet.

### 2. No resource limits on either container
Neither service in `docker-compose.yml` sets a memory or CPU limit. This
system trains RandomForest + XGBoost models per symbol during retrain/
tournament passes (up to ~30 symbols per the current `symbol_discovery`
config) - on a $5/mo VPS with limited RAM, an unbounded memory spike
during a retrain pass (or a slow leak the existing `MAX_HISTORY_LENGTH`
caps don't happen to cover) can OOM the whole box, not just the container -
potentially taking down SSH access too, with `restart: unless-stopped`
then repeatedly failing to come back if the box itself is memory-starved.
**Add explicit `mem_limit`/`cpus` (Compose v2) or `deploy.resources.limits`
to both services**, sized to the actual VPS spec.

### 3. Docker's own container logs are unbounded
The app's file logging is already correctly capped (`RotatingFileHandler`,
10MB x 5 = 50MB max, in `logging_setup.py`) - but every log line is *also*
written to stdout (`JsonFormatter` on a `StreamHandler`), which Docker
captures into its own `json-file` log driver. That driver has **no size
cap by default**. Over months of real 24/7 operation this silently fills
the VPS disk from Docker's own log storage alone, independent of the
already-solved app-level log rotation. **Add explicit `logging: {driver:
json-file, options: {max-size: "10m", max-file: "5"}}` to both services**
in `docker-compose.yml`.

### 4. Backup automation and external health monitoring are not set up yet
RUNBOOK.md itself is explicit that these are still manual TODOs (sections
3 and 4): `scripts/backup_database.py` exists and is tested, but no cron
entry has actually been installed; `/api/health` exists and is real, but
nothing external is polling it yet. **This isn't a discovery, it's a
confirmation that the RUNBOOK's own checklist isn't finished** - don't
let "the code for it exists" read as "it's operational."

## HIGH - real risk, not immediately fatal

### 5. Container runs as root
No `USER` directive in the `Dockerfile` - both containers run as root
inside the container, with `trading_system.db`, `logs/`, and
`trading_assistant/models/` bind-mounted directly from the host. Standard
hardening: add a non-root user and `USER` directive. Not a blocker (Docker
root != host root without an additional escape), but a real blast-radius
reduction a "professional" audit shouldn't skip, especially with live API
keys in the container's environment.

### 6. `docker compose build` will bake the real trading database into an image layer
`.dockerignore` excludes `.env`, `__pycache__/`, `*.log`, and IDE files -
but NOT `.git/`, `trading_system.db*`, `backups/`, or `archive/`. The
`docker-compose.yml` bind mount for `trading_system.db` means the baked-in
copy is masked at runtime, but if `docker compose build` is ever re-run
after the database has real trade history, that real data (and the full
git history) gets copied into the build context and baked into an image
layer permanently - a real problem the moment that image is ever pushed
to a registry, shared, or archived. **Add to `.dockerignore`: `.git/`,
`trading_system.db*`, `backups/`, `archive/`, `.pytest_cache/`,
`.coverage`, `htmlcov/`, `native/`, `fuzz/`, `tests/`.**

### 7. Crash-loop has no backoff
`restart: unless-stopped` restarts unconditionally on any exit, with no
rate limiting. `build_exchange()` already correctly refuses to start
loudly on a live-mode config with missing API keys (`raise RuntimeError`)
- but if that (or any other immediate-startup failure) happens on a fresh
VPS, Docker will restart it in a tight crash loop indefinitely, hammering
logs (see #3) and potentially the exchange's rate limits if the crash
happens after the exchange client is constructed. Compose doesn't have
built-in exponential backoff; consider a wrapper (or `on-failure` with a
low max-retries plus manual investigation) rather than infinite immediate
restarts for genuine startup failures.

### 8. The engine container has no Docker-level healthcheck
Only `dashboard` has a `healthcheck:` block. A hung engine (event loop
deadlock, a blocking call with no timeout somewhere not already covered by
`exchange.timeout_ms`) shows as "Up" in `docker compose ps` regardless -
the only real signal is the dashboard's `/api/health`, which itself
depends on the dashboard container also being up and able to read the
shared journal. This is a reasonable design (health is deliberately
journal-based and cross-process, per RUNBOOK), but it means `docker
inspect` on the engine container alone tells you nothing - worth an
explicit note in the RUNBOOK so a future operator doesn't misread a
healthy-looking `docker compose ps` as proof the engine itself is fine.

## Already solid - credit where due

- **Crash/restart safety of trading state itself is genuinely good**: OS-level
  singleton lock (`process_lock.py`) that self-heals even after a `SIGKILL`
  (confirmed by design, not just claimed); `reconcile_with_exchange` on
  warm start re-syncs real positions from the exchange rather than trusting
  in-memory state; circuit breaker trip state persists across restarts so a
  Docker auto-restart can never silently un-trip a real safety event.
- **Graceful shutdown is real on the actual target platform**: `SIGTERM` ->
  `request_shutdown()` -> checked between symbols in the retrain/tournament
  loops (not just the outer loop), bounding worst-case shutdown to ~45s,
  comfortably inside the `stop_grace_period: 60s` Compose already sets.
  (Confirmed inert on this Windows dev box by the code's own comment - not
  a gap, since the actual deployment target is Linux.)
- **Log rotation is correctly handled at the application level** (10MB x 5
  backups via `RotatingFileHandler`).
- **Dependencies are exact-pinned** in `requirements.txt` (`==`, not `>=`) -
  reproducible builds, no surprise transitive upgrades.
- **The dashboard's no-auth-by-design decision is handled correctly**:
  bound to `127.0.0.1:8000` only, with an explicit, loud comment in
  `docker-compose.yml` about never changing this on a public VPS.
- **Config validation fails loudly, not silently** - an invalid `mode` or
  an unrecognized key in `config.yaml` raises immediately at startup
  rather than silently ignoring a typo.
- **Real trading state (protective stops) is exchange-side, not
  process-side** - a resting `STOP_LOSS_LIMIT` order survives this
  process being down entirely, which is the only way a stop-loss can
  actually be trusted under 24/7 unattended operation.

## Recommended order of operations before `docker compose up -d` for real

1. Fix `.dockerignore` (#6) - cheap, zero risk, do it first.
2. Add resource limits and log-driver caps to `docker-compose.yml` (#2, #3).
3. `docker compose build` on the actual VPS and run the `--cycles 1` smoke
   test (#1) - this is the one that actually proves or disproves whether
   this deploys at all.
4. Add `USER` non-root directive to the `Dockerfile` (#5).
5. Wire the backup cron entry and the external health-check watcher
   RUNBOOK.md sections 3-4 already specify (#4).
6. Only then: `docker compose up -d`, followed by RUNBOOK.md's own
   first-deployment checklist (steps 1-8) in full, including the kill-and-
   confirm-auto-restart test and the 48-72h testnet observation window
   before ever considering `mode: live` on the VPS specifically (this
   machine is currently already running `mode: live` for local
   development - confirm that's still the intended state for the VPS too,
   or intentionally start the VPS deployment on testnet first).
