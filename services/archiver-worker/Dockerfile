# Build context = racine du repo (voir docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY libs/mailarchiver_common /app/libs/mailarchiver_common
RUN pip install /app/libs/mailarchiver_common

COPY services/archiver-worker /app/services/archiver-worker
RUN pip install /app/services/archiver-worker

CMD ["python", "-m", "archiver_worker.main"]
