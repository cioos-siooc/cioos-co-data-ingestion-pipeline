FROM ghcr.io/astral-sh/uv:python3.12-bookworm

WORKDIR /app

# Copy manifests + source
COPY ./app /app

# Install dependencies only (no project code yet), then the project itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project || (echo "Lock mismatch, regenerating" && uv lock && uv sync --no-install-project)

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked || (echo "Lock mismatch final stage, aborting" && exit 1)

CMD ["sh","run.sh"]
