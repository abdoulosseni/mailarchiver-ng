"""Indexation des métadonnées dans OpenSearch.

Stratégie scalable :
- **Index journaliers** `messages-AAAA.MM.JJ` (dérivés de la date d'archivage).
  La recherche lit le motif `messages-*` ; la rétention supprime des index
  entiers (drop) au lieu de millions de suppressions unitaires.
- **Index template** `messages-*` : chaque index journalier hérite des réglages
  (shards/replicas paramétrables via .env, max_result_window) et du mapping.
"""

from __future__ import annotations

import os

from opensearchpy import AsyncOpenSearch

from mailarchiver_common.config import get_settings

_MAX_RESULT_WINDOW = 100000
_SHARDS = int(os.environ.get("OPENSEARCH_SHARDS", "2"))
_REPLICAS = int(os.environ.get("OPENSEARCH_REPLICAS", "1"))

_MAPPINGS = {
    "properties": {
        "doc_id": {"type": "long"},  # id message (tie-breaker pour search_after)
        "message_id": {"type": "keyword"},
        "date": {"type": "date"},
        "archived_at": {"type": "date"},
        "from_addr": {"type": "keyword"},
        "to_addrs": {"type": "keyword"},
        "cc_addrs": {"type": "keyword"},
        "subject": {"type": "text"},
        "body": {"type": "text"},
        "has_attachment": {"type": "boolean"},
        "attachment_names": {"type": "text"},
        "size_bytes": {"type": "long"},
        "retention_class": {"type": "keyword"},
    }
}


class SearchIndexer:
    def __init__(self) -> None:
        self._s = get_settings()
        self._client = AsyncOpenSearch(self._s.opensearch_url)
        self._base = self._s.opensearch_index  # "messages"

    async def ensure_index(self) -> None:
        """Crée/maj l'index template appliqué à tous les index journaliers."""
        template = {
            "index_patterns": [f"{self._base}-*"],
            "template": {
                "settings": {
                    "index": {
                        "number_of_shards": _SHARDS,
                        "number_of_replicas": _REPLICAS,
                        "max_result_window": _MAX_RESULT_WINDOW,
                    }
                },
                "mappings": _MAPPINGS,
            },
        }
        await self._client.indices.put_index_template(name=self._base, body=template)

    def daily_index(self, archived_at_iso: str) -> str:
        """messages-AAAA.MM.JJ dérivé de la date d'archivage."""
        d = (archived_at_iso or "")[:10].replace("-", ".")
        return f"{self._base}-{d}" if len(d) == 10 else f"{self._base}-unknown"

    async def index_message(self, db_id: int, doc: dict) -> None:
        await self._client.index(index=self.daily_index(doc.get("archived_at", "")), id=str(db_id), body=doc)

    async def close(self) -> None:
        await self._client.close()
