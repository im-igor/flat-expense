FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev && useradd --uid 10001 --no-create-home app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "flat_expense.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
