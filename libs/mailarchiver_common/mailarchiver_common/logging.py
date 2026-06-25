"""Configuration structlog partagée par tous les services.

Appeler `configure_logging()` au démarrage de chaque service. Sortie sur stdout
(captée par Docker). Format console lisible par défaut, JSON si LOG_JSON=true
(recommandé en production pour l'agrégation de logs). Niveau via LOG_LEVEL.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    json_logs = os.environ.get("LOG_JSON", "false").lower() == "true"

    # Aligne la stdlib (utilisée par uvicorn) sur le même flux/niveau.
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
