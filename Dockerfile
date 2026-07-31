FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "fastapi_mqtt_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]