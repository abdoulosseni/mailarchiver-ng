"""Recherche simple et avancée via OpenSearch.

Contrôle d'accès : un administrateur voit tous les mails ; un utilisateur non
admin ne voit que les mails qu'il a envoyés (from_addr) ou reçus (to/cc). Cette
restriction est appliquée comme un `filter` OpenSearch — impossible à contourner
côté client. Tri par date décroissante (du plus récent au moins récent).
"""

from __future__ import annotations

from opensearchpy import AsyncOpenSearch

from mailarchiver_common.config import get_settings


def _restriction_filter(addrs: list[str]) -> list[dict]:
    """Clause limitant aux mails impliquant l'une des `addrs` (from/to/cc).

    Liste vide => aucun accès (l'utilisateur/auditeur ne voit rien)."""
    if not addrs:
        return [{"bool": {"must_not": {"match_all": {}}}}]
    low = [a.lower() for a in addrs]
    return [
        {
            "bool": {
                "should": [
                    {"terms": {"from_addr": low}},
                    {"terms": {"to_addrs": low}},
                    {"terms": {"cc_addrs": low}},
                ],
                "minimum_should_match": 1,
            }
        }
    ]


class SearchService:
    def __init__(self) -> None:
        self._s = get_settings()
        self._client = AsyncOpenSearch(self._s.opensearch_url)
        # Lecture sur tous les index journaliers messages-AAAA.MM.JJ.
        self._read = f"{self._s.opensearch_index}-*"

    async def advanced(
        self,
        filters: dict,
        size: int = 50,
        restrict_addrs: list[str] | None = None,
        search_after: list | None = None,
    ) -> dict:
        must: list[dict] = []
        if filters.get("text"):
            must.append({"multi_match": {"query": filters["text"], "fields": ["subject", "body"]}})
        if filters.get("from"):
            must.append({"term": {"from_addr": filters["from"]}})
        if filters.get("to"):
            must.append({"term": {"to_addrs": filters["to"]}})
        if filters.get("participant"):
            # Expéditeur OU destinataire : présent dans from, to ou cc.
            p = filters["participant"]
            must.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"from_addr": p}},
                            {"term": {"to_addrs": p}},
                            {"term": {"cc_addrs": p}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        if filters.get("subject"):
            must.append({"match_phrase": {"subject": filters["subject"]}})
        if filters.get("has_attachment") is not None:
            must.append({"term": {"has_attachment": bool(filters["has_attachment"])}})

        date_range: dict = {}
        if filters.get("date_from"):
            date_range["gte"] = filters["date_from"]
        if filters.get("date_to"):
            date_range["lte"] = filters["date_to"]
        if date_range:
            must.append({"range": {"date": date_range}})

        size_range: dict = {}
        if filters.get("size_min"):
            size_range["gte"] = filters["size_min"]
        if filters.get("size_max"):
            size_range["lte"] = filters["size_max"]
        if size_range:
            must.append({"range": {"size_bytes": size_range}})

        return await self._search(must or [{"match_all": {}}], size, restrict_addrs, search_after)

    async def _search(
        self, must: list[dict], size: int, restrict_addrs: list[str] | None, search_after: list | None
    ) -> dict:
        # restrict_addrs = None signifie « aucune restriction » (admin).
        query: dict = {"bool": {"must": must}}
        if restrict_addrs is not None:
            query["bool"]["filter"] = _restriction_filter(restrict_addrs)
        body = {
            "size": size,
            # Total plafonné à 10 000 (au-delà = « 10000+ ») : compter l'exact sur
            # des millions de docs est très coûteux.
            "track_total_hits": 10000,
            # Corps exclu de la liste (chargé seulement à la consultation).
            "_source": {"excludes": ["body"]},
            "query": query,
            # Tri stable (date + tie-breaker doc_id) requis par search_after.
            "sort": [{"date": {"order": "desc"}}, {"doc_id": {"order": "desc"}}],
        }
        if search_after:
            body["search_after"] = search_after
        res = await self._client.search(
            index=self._read, body=body, ignore_unavailable=True, allow_no_indices=True
        )
        hits = res["hits"]
        total_obj = hits["total"]
        if isinstance(total_obj, dict):
            total = total_obj["value"]
            estimated = total_obj.get("relation") == "gte"
        else:
            total, estimated = total_obj, False
        rows = hits["hits"]
        return {
            "total": total,
            "total_estimated": estimated,
            "results": [{"id": h["_id"], **h["_source"]} for h in rows],
            # Curseur pour la page suivante (valeurs de tri du dernier résultat).
            "next_search_after": rows[-1]["sort"] if rows else None,
        }

    async def ping(self) -> None:
        """Vérifie la connectivité OpenSearch (lève si indisponible)."""
        if not await self._client.ping():
            raise RuntimeError("OpenSearch ne répond pas")

    async def count_all(self) -> int:
        try:
            res = await self._client.count(
                index=self._read, ignore_unavailable=True, allow_no_indices=True
            )
            return res.get("count", 0)
        except Exception:  # noqa: BLE001
            return 0

    async def close(self) -> None:
        await self._client.close()
