# MailArchiver-NG — Documentation du code

## 1. Organisation du dépôt

```
mailarchiver-ng/
├── docker-compose.yml          # orchestration (infra + 6 services)
├── Makefile                    # make keys / up / down / logs / test
├── .env.example                # variables d'environnement
├── libs/
│   └── mailarchiver_common/    # bibliothèque partagée (installée dans chaque image)
│       └── mailarchiver_common/
│           ├── config.py       # Settings (pydantic-settings), get_settings()
│           ├── crypto.py       # hash, zlib, AES-256-GCM (seal/unseal), Ed25519, secrets
│           ├── queue.py        # RabbitMQ : publish/consume, DLQ, events fanout
│           ├── storage.py      # BlobStore S3/MinIO (client réutilisé)
│           ├── models.py       # SQLAlchemy 2.0 async (toutes les tables)
│           └── fetch.py        # collecte IMAP/POP3 (stdlib) + run_and_publish
├── services/
│   ├── smtp-gateway/           # serveur SMTP (aiosmtpd) → file
│   ├── archiver-worker/        # pipeline d'archivage
│   ├── fetch-worker/           # collecte IMAP/POP3 planifiée
│   ├── retention-worker/       # purge planifiée
│   ├── api/                    # API FastAPI
│   └── web/                    # SPA Vue 3 (Vite) + Nginx
├── loadtest/                   # injecteurs (inject.py, inject_pj.py, inject_large.py, measure.sh, test_ingestion.py)
├── tests/                      # unitaires (crypto) + nonreg.py (non-régression E2E, cf. docs/TESTS.md)
├── deploy/                     # artefacts de prod : monitoring, k8s/KEDA, db, ha, backup
└── docs/                       # cette documentation (dont PRODUCTION.md)
```

Chaque service est un package Python autonome (`pyproject.toml` + `Dockerfile`)
qui dépend de `mailarchiver_common`. Le `Dockerfile` installe d'abord la lib
partagée puis le service.

## 2. Bibliothèque partagée (`libs/mailarchiver_common`)

- **`config.py`** — `Settings` (DB, AMQP, S3, OpenSearch, chemins des clés).
  `get_settings()` renvoie un singleton.
- **`crypto.py`** — cœur cryptographique :
  - `content_hash(bytes)` → SHA-256 (déduplication & adressage).
  - `compress`/`decompress` (zlib).
  - `seal(plaintext)` → `SealedBlob` (zlib + AES-256-GCM, *envelope encryption*
    avec DEK par blob scellée par la clé maître) ; `unseal(blob)`.
  - `archive_fingerprint(...)`, `sign`/`verify` (Ed25519).
  - `encrypt_secret`/`decrypt_secret` (mots de passe IMAP/POP/SMTP).
- **`queue.py`** — `connect()` (robuste), `publish_raw_mail`/`consume_raw_mail`
  (ack manuel, réessai borné + **dead-letter** `raw_mail_dead`), exchange
  `mail_events`, et file durable `restore_jobs` (`publish_restore_job`/
  `consume_restore_jobs`).
- **`storage.py`** — `BlobStore` (client S3 **réutilisé** ; `put` idempotent sans
  HEAD préalable — perf), `ping()` (healthcheck).
- **`models.py`** — modèles SQLAlchemy + `get_sessionmaker()` (async, `pool_pre_ping`).
  Tables : **messages (partitionnée par mois, PK composite `(id, signed_at)`)**,
  **message_dedup** (unicité globale `archive_hash`), attachments,
  message_attachments (sans FK vers messages), users (rôle, email, audited_emails,
  restore_imap, failed_logins/locked_until), app_settings, fetch_sources,
  transfer_jobs, audit_events. **`ensure_schema(engine)`** : create_all pour les
  tables simples + DDL brut partitionné pour `messages` + partitions mensuelles.
- **`fetch.py`** — `fetch_imap`/`fetch_pop3`, `fetch_source`, `run_and_publish`
  (collecte) et **`imap_append`** (restauration IMAP façon imapsync).
- **`logging.py`** — `configure_logging()` (structlog ; console, ou JSON si
  `LOG_JSON=true` ; niveau `LOG_LEVEL`). Appelé au démarrage de chaque service.

## 3. Services

### smtp-gateway
- `main.py` : serveur SMTP **sur la boucle asyncio courante** via
  `loop.create_server` (PAS le `Controller` threadé → évite « attached to a
  different loop »). STARTTLS si certificat présent.
- `handler.py` : `handle_DATA` valide la taille puis publie le mail brut ; renvoie
  `451` (réessai) si la file est indisponible → pas de perte.

### archiver-worker
- `parser.py` : parse MIME → `ParsedMail` (en-têtes canoniques, corps, PJ).
- `pipeline.py` : `ArchivePipeline.process(raw)` — pipeline complet ;
  idempotence (`archive_hash` unique, garde `IntegrityError`) ; **ré-indexation
  sur doublon** (cicatrise un index manquant).
- `indexer.py` : `SearchIndexer` — **index template** `messages-*` (shards/replicas
  via `OPENSEARCH_SHARDS/REPLICAS`), écriture dans l'**index journalier**
  `messages-AAAA.MM.JJ` (`daily_index`).
- `main.py` : crée le schéma (verrou consultatif Postgres), consomme la file.

### fetch-worker
- `main.py` : `AsyncIOScheduler` ; toutes les 60 s, relève les `fetch_sources`
  dues (intervalle) via `run_and_publish`, met à jour le statut.

### retention-worker
- `main.py` : cron (`RETENTION_SCAN_CRON`) ; lit `retention_days` (`app_settings`),
  **supprime les index journaliers** antérieurs au seuil (`_drop_old_indices`,
  instantané), puis purge en base `signed_at < now - retention_days` (hors
  `legal_hold`) + GC des blobs (`ref_count`). `selectinload` obligatoire (async).

### api (FastAPI)
- `main.py` : application, dépendances (`current_user`, `require_admin`),
  handler 503 (erreurs DB/asyncpg), endpoints (voir §4).
- `auth.py` : `AuthenticatedUser`, `LdapAuthenticator`.
- `tokens.py` : JWT HS256 (porte rôle, email, audited_emails).
- `users.py` : `UserRepo` (CRUD, bcrypt, rôles ; email, audited_emails, restore_imap).
- `fixtures.py` : amorçage du compte admin par défaut au démarrage.
- `search.py` : `SearchService.advanced` — lecture `messages-*`, restriction par
  périmètre, tri `date`+`doc_id`, **`search_after`**, total plafonné, corps exclu.
- `mail_access.py` : déchiffrement/parse pour consultation & export, `_can_access`.
- `smtp_relay.py` : envoi via le relais SMTP des Paramètres.
- `settings_store.py` : `SettingsStore` (rétention + relais SMTP + **SMTPD**).
- `fetch_sources.py` / `transfer_jobs.py` : dépôts dédiés.
- Restauration : consommateur de `restore_jobs` (`_handle_restore_command`),
  exécution par lots `search_after` via SMTP ou **IMAP APPEND**.
- DLQ : `/dlq` (compteur temps réel + aperçu), `/dlq/replay`, `/dlq/purge`.
- Métriques/santé : `/metrics/throughput` (RabbitMQ), `/health/components`,
  handler **503** (erreurs DB/asyncpg), `configure_logging()` au top du module.

### web (Vue 3)
- `src/main.js` : applique le thème (clair par défaut) avant montage.
- `src/api.js` : client REST (token en localStorage), wrappers d'endpoints.
- `src/App.vue` : layout, onglets, bascule de thème, footer.
- `src/components/` : `LoginView`, `SearchView` (pagination `search_after`),
  `LiveView` (SSE), `UsersView` (édition inline, restauration IMAP/SMTP),
  `SourcesView`, `SettingsView` (stats, conservation, SMTPD, relais SMTP, santé,
  débit, DLQ), `AccountView`, `MessageModal`.
- `nginx.conf` : sert le SPA + proxy l'API (résolveur DNS dynamique → survit aux
  redéploiements ; SSE sans bufferisation ; cache `index.html` désactivé,
  assets immuables).

## 4. Endpoints API (référence)

| Méthode & route | Accès | Rôle |
|---|---|---|
| `POST /auth/login` | public | obtenir un JWT |
| `POST /auth/change-password` | authentifié | changer son mot de passe |
| `GET /health` | public | sonde |
| `GET /events/stream?token=` | authentifié | flux SSE temps réel |
| `POST /search/advanced` | authentifié | recherche (filtrée par périmètre) |
| `GET /messages/{id}` | authentifié | consultation (parsé) |
| `GET /messages/{id}/eml` | authentifié | export EML |
| `POST /messages/forward` | authentifié | transfert SMTP |
| `POST /import/eml` | authentifié | import de .eml |
| `GET/POST/DELETE /users` … | admin | gestion des comptes |
| `PATCH /users/{id}/password\|email\|active\|audited-emails\|restore-imap` | admin | modifications |
| `POST /users/{id}/transfer-perimeter?method=imap\|smtp\|auto` | admin | restauration (async, durable) |
| `GET /transfer-jobs` | admin | suivi des restaurations |
| `GET/POST/DELETE /fetch-sources`, `POST /fetch-sources/{id}/run` | admin | sources IMAP/POP3 |
| `GET/PATCH /settings` | admin | rétention + relais SMTP + SMTPD |
| `GET /stats` | admin | statistiques d'archivage |
| `GET /health/components` | admin | healthcheck réel des composants |
| `GET /metrics/throughput` | admin | débit injection/traitement, backlog, DLQ |
| `GET /dlq`, `POST /dlq/replay`, `POST /dlq/purge` | admin | quarantaine (inspect/rejeu/vide) |
| `GET /metrics/prometheus` | interne | métriques Prometheus (bloqué côté ingress) |

Sécurité : `POST /auth/login` applique un **verrouillage** après
`LOGIN_MAX_ATTEMPTS` échecs (→ HTTP **423** pendant `LOGIN_LOCKOUT_MINUTES`).

## 5. Conventions & pièges (à connaître)

- **Toute nouvelle route API** doit être ajoutée au proxy `services/web/nginx.conf`
  (sinon 404/405 via le port 8080).
- **Changement de modèle SQLAlchemy** : `create_all` ne fait pas d'`ALTER`. Sur une
  table existante → `ALTER TABLE` à chaud ; nouvelle table → créée au démarrage de
  l'API. Reconstruire **tous** les services qui font `create_all` (api + workers).
- **Compresser puis chiffrer** ; **dédupliquer sur le clair**.
- Le **périmètre d'accès** (email / audited_emails) est porté par le JWT →
  prend effet à la prochaine connexion de l'utilisateur concerné.
- Python ≥ 3.11 requis (le `python` par défaut de la machine peut être 3.10 → un
  venv 3.13 est utilisé pour les tests/outils locaux).

## 6. Build, exécution, tests

```bash
make keys                 # secrets (clé maître, signature, cert STARTTLS)
cp .env.example .env       # ajuster les secrets
docker compose up --build  # démarre toute la stack

# tests unitaires crypto (hors infra)
.venv/bin/pip install ./libs/mailarchiver_common pytest
.venv/bin/python -m pytest tests/

# test de charge
bash loadtest/measure.sh 6000 24
```

Itérer sur un service : `docker compose build <svc> && docker compose up -d <svc>`.
