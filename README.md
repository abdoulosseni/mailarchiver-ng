# MailArchiver-NG

Plateforme d'archivage d'e-mails — architecture **microservices** en **Python**.

Reçoit les mails par SMTP (journaling depuis un MTA), les archive de façon
**immuable** (compression Zlib + chiffrement AES-256-GCM + signature numérique),
**déduplique les pièces jointes**, les **indexe** pour la recherche, et expose une
**API/UI web** avec authentification **AD/LDAP**, i18n et audit complet.

## Architecture

```
   MTA (journaling, STARTTLS)
            │
            ▼
   ┌─────────────────┐    RabbitMQ      ┌──────────────────┐
   │  smtp-gateway   │ ───(raw mail)──► │ archiver-worker  │
   │  aiosmtpd       │                  │  parse > dedup > │
   │  STARTTLS       │                  │  zlib > AES-256 >│
   └─────────────────┘                  │  sign > index    │
                                        └───┬───────┬──────┘
                          ┌─────────────────┘       └────────────┐
                    ┌─────▼──────┐                        ┌───────▼──────┐
                    │   MinIO    │                        │  OpenSearch  │
                    │ (blobs:    │                        │  (métadonnées│
                    │  corps+PJ) │                        │   indexées)  │
                    └────────────┘                        └──────────────┘
                    ┌────────────┐
                    │ PostgreSQL │  (métadonnées, refs dédup, audit, rétention)
                    └────────────┘
            ▲
            │  REST/JSON  + forward SMTP
   ┌─────────────────┐        ┌──────────────────┐
   │      api        │ ─────► │   AD / LDAP      │
   │    FastAPI      │        └──────────────────┘
   └─────────────────┘
   ┌─────────────────┐
   │ retention-worker│  (scheduler des politiques de conservation)
   └─────────────────┘
```

## Services

| Service | Rôle | Stack |
|---|---|---|
| `smtp-gateway` | Réception SMTP + STARTTLS, spool vers la queue | aiosmtpd, aio-pika |
| `archiver-worker` | Pipeline d'archivage (dédup, compression, crypto, signature, index) | cryptography, opensearch-py, SQLAlchemy |
| `api` | API REST, recherche, export/import EML, forward SMTP, auth LDAP, i18n, audit | FastAPI, ldap3, Babel |
| `retention-worker` | Application des politiques de conservation | APScheduler |
| `libs/mailarchiver_common` | Code partagé (config, crypto, queue, modèles, storage) | — |

## Démarrage

Installation **en une commande** sur un environnement vierge :

```bash
make install     # crée .env, génère les secrets manquants, build + démarre
```

`make install` est **idempotent et sûr** : il ne réécrit ni `.env` ni les secrets
existants (régénérer la clé maître rendrait les archives chiffrées illisibles).
La génération des clés repose sur `openssl` (aucune dépendance Python côté hôte).

Tout le reste s'amorce **automatiquement** au démarrage : schéma + partitions
PostgreSQL, compte admin (`admin` / `admin`), bucket MinIO, index template
OpenSearch, formation du cluster.

Étapes équivalentes en manuel :

```bash
cp .env.example .env          # variables (durcir les secrets avant la prod)
make keys                     # clé maître AES + signature Ed25519 + cert STARTTLS
docker compose up -d --build
```

> Production : `make secret-key` pour un `API_SECRET_KEY` fort, changer le mot de
> passe admin à la 1re connexion. Cf. [docs/PRODUCTION.md](docs/PRODUCTION.md).

- IHM web : http://localhost:8080 (`admin` / `admin`) · HTTPS : https://localhost:8443 (cert auto-signé)
- API : http://localhost:8000/docs
- SMTP gateway : `localhost:2525` (STARTTLS)
- RabbitMQ UI : http://localhost:15672 · OpenSearch : http://localhost:9200 · MinIO : http://localhost:9001
- Vérifier : `make nonreg` (non-régression E2E)

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture de la solution
- [docs/CODE.md](docs/CODE.md) — organisation et référence du code
- [docs/FONCTIONNALITES.md](docs/FONCTIONNALITES.md) — fonctionnalités détaillées
- [docs/PRODUCTION.md](docs/PRODUCTION.md) — mise en production (sécurité, HA, WORM, PRA)
- [docs/TESTS.md](docs/TESTS.md) — tests & non-régression (`make nonreg`)

## Capacité

Cible : **100 mails/s**. Le débit se règle via le nombre de réplicas de
`archiver-worker` (voir `docker-compose.yml`). Le SMTP accuse réception
immédiatement et délègue tout le traitement à la queue (backpressure absorbée
par RabbitMQ).

## Licence

Distribué sous licence **MIT** (cf. [LICENSE](LICENSE)). Logiciel fourni « EN
L'ÉTAT », sans garantie. Voir l'avertissement de maturité en tête de ce README.
