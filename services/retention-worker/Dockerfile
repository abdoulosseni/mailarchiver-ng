# Build context = racine du repo (voir docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY libs/mailarchiver_common /app/libs/mailarchiver_common
RUN pip install /app/libs/mailarchiver_common

COPY services/retention-worker /app/services/retention-worker
RUN pip install /app/services/retention-worker

CMD ["python", "-m", "retention_worker.main"]
