# Mise en production — MailArchiver-NG

Ce guide couvre le passage d'un déploiement mono-hôte (dev) à une exploitation
**24/7 résiliente**. Les artefacts référencés sont dans `deploy/`.

| Domaine | État | Où |
|---|---|---|
| Durcissement sécurité | **implémenté** (TLS ingress, en-têtes, rate-limit, verrouillage) | code + §1 |
| Conformité WORM + legal hold | **implémenté** (Object Lock + legal hold) | `storage.py`, §5 |
| Métriques & alerting | **implémenté + validé** | `/metrics/prometheus`, `deploy/monitoring/` |
| Partitionnement PostgreSQL | **implémenté** (actif) | `models.ensure_schema` + `deploy/db/` |
| HA — files de travail | **implémenté** (quorum queues) | `queue.py` |
| HA — datastores | guide + exemples | `deploy/ha/` |
| Autoscaling (KEDA) | artefact | `deploy/k8s/` |
| Sauvegarde / PRA | scripts + **drill testé** | `deploy/backup/` |
| Migrations (Alembic) | scaffold | `migrations/` |
| CI/CD | pipeline | `.github/workflows/ci.yml` |
| Charge | **testé** (~10k mails, 0 DLQ, stable) | `loadtest/` |

---

## 1. Durcissement sécurité

**Implémenté dans le code :**
- **TLS sur l'ingress** : nginx écoute en HTTPS (`:443` → `8443`), certificat
  monté depuis `./secrets`. **En-têtes de sécurité** (X-Frame-Options, X-Content-
  Type-Options, Referrer-Policy, HSTS). **Rate-limiting** sur `/auth/` (anti-bruteforce).
- **Verrouillage de compte** : après `LOGIN_MAX_ATTEMPTS` (5) échecs → HTTP **423**
  pendant `LOGIN_LOCKOUT_MINUTES` (15). Réinitialisé à la connexion réussie.
- **Secrets forts** : `make keys` (openssl, idempotent) ; `make secret-key`.
- **Mots de passe** bcrypt ; secrets IMAP/POP/SMTP chiffrés (AES-256, clé maître).
- **Scrape Prometheus** non exposé via l'ingress public (nginx `403`).

**À faire au déploiement :**
- Remplacer le certificat auto-signé par un **certificat d'AC** ; forcer la
  redirection HTTP→HTTPS au niveau de l'ingress/LB.
- Définir un `API_SECRET_KEY` fort et **changer le mot de passe admin** par défaut.
- Activer le **plugin de sécurité OpenSearch** (TLS + auth interne) et l'auth
  RabbitMQ/MinIO ; datastores sur réseau privé. Secrets via Vault/KMS.

## 2. Observabilité (métriques & alerting)

L'API expose `GET /metrics/prometheus` (scrape **interne** `api:8000`) :
`mailarchiver_raw_mail_backlog`, `_raw_mail_consumers`, `_dlq_messages`,
`_messages_estimate`.

```bash
docker compose -f docker-compose.yml \
               -f deploy/monitoring/docker-compose.monitoring.yml up -d
# Prometheus :9090 · Alertmanager :9093 · Grafana :3000
```

Règles d'alerte (`deploy/monitoring/alerts.yml`) : API down, aucun worker,
backlog élevé/critique, présence DLQ. Brancher le receiver Alertmanager
(e-mail/Slack/PagerDuty) dans `alertmanager.yml`. Ajouter les exporters d'infra
(postgres/rabbitmq/opensearch/node) pour une couverture complète.

## 3. Autoscaling des workers (KEDA)

`archiver-worker` est sans état → scale horizontal. **KEDA** ajuste le nombre de
réplicas selon la profondeur de `raw_mail` (`deploy/k8s/keda-scaledobject.yaml` :
2→50 réplicas, 1 worker / 500 mails en attente). Déployer `archiver-worker.yaml`
puis le `ScaledObject`.

## 4. Partitionnement PostgreSQL (actif)

`messages` est **partitionnée par mois** (RANGE sur `signed_at`), créée par
`mailarchiver_common.models.ensure_schema()` au démarrage (PK composite
`(id, signed_at)`, partition `DEFAULT` de sécurité + mois courant +2 d'avance).
L'idempotence globale d'`archive_hash` est portée par la table non partitionnée
**`message_dedup`** ; l'association `message_attachments` n'a pas de FK vers
`messages` (gérée par l'ORM). Bénéfices : *partition pruning* (recherches/purge
par date), index/maintenance localisés, et option **DROP de partition** pour la
rétention (cf. `deploy/db/partitioning.sql` : fonction `ensure_message_partition`,
planification pg_cron, migration d'une base existante).

## 5. Conformité WORM & legal hold (implémenté)

- **WORM** : si `S3_OBJECT_LOCK_DAYS > 0`, le bucket est créé avec **Object Lock**
  + versioning et une **rétention par défaut** (`storage.ensure_bucket`). Chaque
  blob (corps + PJ chiffrés) devient **immuable** N jours (mode GOVERNANCE par
  défaut, ou COMPLIANCE). À aligner sur `DEFAULT_RETENTION_DAYS`. ⚠️ Object Lock
  n'est activable qu'à la **création** du bucket (bucket neuf requis).
- **Legal hold** : `PATCH /messages/{id}/legal-hold` (admin) + bouton dans la
  consultation. Pose un legal hold **WORM** sur le blob (immuabilité indéfinie) et
  exclut le message de la purge (`Message.legal_hold`).
- Reclamation d'espace après expiration : ajouter une règle **ILM** MinIO/S3
  (expire-current-version + delete-markers) — la rétention applicative crée un
  delete marker, l'ILM purge physiquement.

## 6. Haute disponibilité

- **Files de travail** : `raw_mail`, `raw_mail_dead`, `restore_jobs` sont des
  **quorum queues** (Raft) → répliquées sur un cluster RabbitMQ (implémenté ;
  fonctionnent aussi en mono-nœud).
- **Datastores** (`deploy/ha/README.md`) : PostgreSQL (Patroni/CloudNativePG +
  réplicas + bascule auto), RabbitMQ (cluster ≥3), MinIO (distribué erasure coding
  ou S3 managé + WORM), OpenSearch (nœuds par rôle + ILM). Applicatif en ≥2 réplicas
  derrière un load-balancer.

## 6bis. Sauvegarde / PRA

`deploy/backup/` : `backup.sh` (PostgreSQL `pg_dump` + MinIO mirror) et `restore.sh`.
**Drill de restauration validé** (dump → base neuve → comptes identiques, partitions
préservées). Production : WAL archiving + PITR, replication MinIO + versioning WORM ;
l'index OpenSearch reste **reconstructible** depuis les EML. Vérifier l'intégrité
post-restauration (`X-Archive-Integrity: valid`).

## 7. Capacité

~200 msg/s soutenu/hôte → ~17 M mails/jour. Scaler horizontalement (workers via
KEDA, cluster OpenSearch, partitions PG). Estimer le stockage selon la taille
moyenne (corps + PJ chiffrés) et la durée de conservation.

---

Variables d'environnement utiles (au-delà de `.env.example`) :

| Variable | Défaut | Rôle |
|---|---|---|
| `LOGIN_MAX_ATTEMPTS` | 5 | échecs avant verrouillage |
| `LOGIN_LOCKOUT_MINUTES` | 15 | durée de verrouillage |
| `LOG_JSON` | false | logs JSON (agrégation) |
| `LOG_LEVEL` | INFO | niveau de log |
| `OPENSEARCH_HEAP` | 512m | heap par nœud OpenSearch |
| `OPENSEARCH_SHARDS` / `_REPLICAS` | 2 / 1 | sharding/réplication des index |
| `S3_OBJECT_LOCK_DAYS` | 365 (.env) | WORM : immuabilité des blobs (0 = off) |
| `S3_OBJECT_LOCK_MODE` | GOVERNANCE | mode Object Lock (ou COMPLIANCE) |
