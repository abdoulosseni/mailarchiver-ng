"""Internationalisation minimale (messages d'interface API).

Les catalogues complets seront gérés via Babel (.po/.mo) ; ce module fournit le
socle et la négociation de langue (en-tête Accept-Language / paramètre ?locale=).
"""

from __future__ import annotations

import os

DEFAULT_LOCALE = os.environ.get("DEFAULT_LOCALE", "fr")
SUPPORTED = set(os.environ.get("SUPPORTED_LOCALES", "fr,en,de,es").split(","))

_CATALOG: dict[str, dict[str, str]] = {
    "fr": {
        "auth.failed": "Échec de l'authentification",
        "search.empty": "Aucun résultat",
        "forward.sent": "Message transféré",
    },
    "en": {
        "auth.failed": "Authentication failed",
        "search.empty": "No results",
        "forward.sent": "Message forwarded",
    },
}


def negotiate(accept_language: str | None, query_locale: str | None) -> str:
    if query_locale in SUPPORTED:
        return query_locale
    if accept_language:
        for part in accept_language.split(","):
            code = part.split(";")[0].strip().split("-")[0]
            if code in SUPPORTED:
                return code
    return DEFAULT_LOCALE


def t(key: str, locale: str) -> str:
    return _CATALOG.get(locale, {}).get(key) or _CATALOG[DEFAULT_LOCALE].get(key, key)
