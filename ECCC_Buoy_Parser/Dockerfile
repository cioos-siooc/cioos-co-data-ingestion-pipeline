FROM ghcr.io/astral-sh/uv:python3.12-bookworm

WORKDIR /app

# 1. Copy path dependency (db-loader) and manifests for harvester
COPY ./app /app
#COPY ./db-loader /app/db-loader
#COPY ./harvester/pyproject.toml ./harvester/uv.lock ./harvester/README.md ./harvester/

#WORKDIR /app/harvester

# 2. Install dependencies only (no project code yet)
RUN --mount=type=cache,target=/root/.cache/uv \
uv sync --locked --no-install-project || (echo "Lock mismatch, regenerating" && uv lock && uv sync --no-install-project)

# 3. Copy source code
#COPY ./harvester/cde_harvester ./cde_harvester
#COPY ./run.sh ./run.sh

# 4. Install project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked || (echo "Lock mismatch final stage, aborting" && exit 1)

CMD ["sh","run.sh"]
