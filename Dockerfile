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

# Create the runtime UID before copying application source so ownership is
# assigned while the layer is created.  Avoid `chown -R /app`: recursive
# ownership changes duplicate the entire application tree into another image
# layer and do nothing to change ownership of host bind mounts at runtime.
RUN useradd --create-home --uid 1000 appuser

# Runtime cache is intentionally writable by the unprivileged ATS process.
# The remainder of /app stays immutable/root-owned.
RUN install -d -o appuser -g appuser -m 0750 /app/cache

COPY --chown=appuser:appuser . .
RUN pip install --no-cache-dir -e .

# Runtime-writable paths are bind-mounted and their HOST ownership is handled
# by the deployment scripts.  The image itself therefore needs no recursive
# ownership mutation.
USER appuser

CMD ["pytest", "tests"]
