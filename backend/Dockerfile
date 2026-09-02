# Build context is the REPO ROOT (see docker-compose.yml: `context: ..`),
# not backend/ -- required so this file can also reach the root-level
# personas/ and skills/ content directories, which are siblings of
# backend/, not inside it.
#
# The image mirrors the host's directory depth exactly: /app plays the
# role of the repo root, /app/backend holds this package, /app/personas
# and /app/skills sit beside it. This isn't cosmetic -- both
# tapestry/graph/build.py and tapestry/adapters/web_adapter/api.py locate
# personas/skills by walking a fixed number of parents up from their own
# `__file__`, matching this exact host layout. Flattening tapestry/ onto
# /app directly (an earlier version of this file did) silently breaks
# that resolution inside the container -- found by actually running the
# built image and hitting a real endpoint, not by reading the code.

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY backend/pyproject.toml backend/uv.lock* backend/
COPY personas ./personas
COPY skills ./skills

WORKDIR /app/backend
RUN uv sync --frozen --no-dev || uv sync --no-dev

COPY backend/tapestry ./tapestry

EXPOSE 8000

CMD ["uv", "run", "python", "-m", "tapestry.main"]
