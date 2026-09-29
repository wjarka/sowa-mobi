FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev --no-editable

ARG SOWA_REVISION=unknown
ENV SOWA_REVISION=${SOWA_REVISION}
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV SOWA_CONFIG=/config/sowa.json
EXPOSE 8000

CMD ["uvicorn", "sowa_mobi.api:app", "--host", "0.0.0.0", "--port", "8000"]
