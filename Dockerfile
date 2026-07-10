FROM python:3.11-slim

WORKDIR /app

# git: required for `pip install -e .` / any VCS-based dependency resolution.
# libgomp1: xgboost's compiled extension links against OpenMP at runtime -
# python:3.11-slim (unlike GitHub Actions' ubuntu-latest runner, which
# already has this) does not include it by default. Missing this produces
# an `OSError: libgomp.so.1: cannot open shared object file` the FIRST time
# anything imports xgboost - which nothing in CI would ever catch, since CI
# never builds/runs this image, only `pytest` directly on a runner that
# already has the library preinstalled.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install -e .

# Real API keys live in this container's environment (via .env) - running
# as root needlessly widens the blast radius if the process is ever
# compromised (a malicious dependency, a deserialization bug in a loaded
# model file, etc). Directories the containers actually need to write to
# (logs/, trading_assistant/models/) are all bind-mounted from the host at
# runtime per docker-compose.yml - chown them so the non-root user can
# still write through the mount.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["pytest", "tests"]
