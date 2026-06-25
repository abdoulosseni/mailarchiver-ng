# Build context = racine du repo (voir docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY libs/mailarchiver_common /app/libs/mailarchiver_common
RUN pip install /app/libs/mailarchiver_common

COPY services/api /app/services/api
RUN pip install /app/services/api

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
