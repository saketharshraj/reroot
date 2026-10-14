FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Same image serves every Cloud Run service; pick the app with SERVICE_MODULE.
ENV SERVICE_MODULE=reroot.api.main:app
CMD exec uvicorn "$SERVICE_MODULE" --host 0.0.0.0 --port "${PORT:-8080}"
