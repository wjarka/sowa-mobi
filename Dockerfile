FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
ENV SOWA_CONFIG=/config/sowa.json
EXPOSE 8000

CMD ["uvicorn", "sowa_mobi.api:app", "--host", "0.0.0.0", "--port", "8000"]
