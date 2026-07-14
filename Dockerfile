FROM ghcr.io/astral-sh/uv:python3.12-bookworm

WORKDIR /app

# Install dependencies first (cached while only source changes), then the project.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-extras --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-extras

ENV PATH="/app/.venv/bin:$PATH"

# The pipeline to run is chosen per docker-compose service, e.g.
#   command: ["cioos-ingest", "meds"]
CMD ["cioos-ingest", "--help"]
