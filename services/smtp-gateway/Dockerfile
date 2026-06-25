# Build context = racine du repo (voir docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Librairie partagée d'abord (meilleur cache de couches)
COPY libs/mailarchiver_common /app/libs/mailarchiver_common
RUN pip install /app/libs/mailarchiver_common

COPY services/smtp-gateway /app/services/smtp-gateway
RUN pip install /app/services/smtp-gateway

EXPOSE 2525
CMD ["python", "-m", "smtp_gateway.main"]
