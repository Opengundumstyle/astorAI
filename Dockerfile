# Astor engine (FastAPI) — serves the read API + chat assistant.
# The serving path only reads the DB and calls Anthropic (embedding/matching is an
# offline pipeline step), so we install just the `.[api]` extra — no Voyage/nltk/scipy
# chain. Keeps the image small and the build fast.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Package metadata + source (src layout, per pyproject `where = ["src"]`).
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations

RUN pip install ".[api]"

# Hosts (Render/Fly/Railway) inject $PORT; default 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn astor.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
