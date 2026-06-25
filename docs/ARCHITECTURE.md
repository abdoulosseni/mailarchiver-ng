# MailArchiver-NG — Documentation de la solution (architecture)

## 1. Objectif

Plateforme d'**archivage d'e-mails** à valeur de conformité : recevoir des mails
(par SMTP journaling ou collecte IMAP/POP3), les conserver de façon **immuable**
(compressés, chiffrés, signés), les **dédupliquer**, les **indexer** pour la
recherche, et les rendre consultables via une **interface web** avec gestion des
accès par rôle.

## 2. Vue d'ensemble

```
   MTA (journaling, STARTTLS)        Boîtes IMAP/POP3 externes
            │                                  │
            ▼                                  ▼
   ┌─────────────────┐                ┌──────────────────┐
   │  smtp-gateway   │                │  fetch-worker    │  (collecte planifiée)
   │  aiosmtpd+TLS   │                └────────┬─────────┘
   └────────┬────────┘                         │
            └───────────────┬──────────────────┘
                            ▼
                  RabbitMQ : file `raw_mail` (durable)
                    │            └── DLQ `raw_mail_dead` (poison)
                    ▼
            ┌──────────────────┐   archiver-worker × N (compétition de consommateurs)
            │ parse → dédup PJ │
            │ → zlib → AES-256 │
            │ → signature Ed25519
            │ → métadonnées    │──► PostgreSQL  (métadonnées, dédup, comptes, audit, …)
            │ → blobs scellés  │──► MinIO/S3    (corps + pièces jointes chiffrés)
            │ → indexation     │──► OpenSearch  (recherche plein-texte + filtres)
            │ → événement      │──► RabbitMQ `mail_events` (fanout, temps réel)
            └──────────────────┘
                            ▲
                            │ REST / SSE
   navigateur ──► web (Vue 3 + Nginx) ──► api (FastAPI) ──► AD/LDAP (option)
                                              │
   retention-worker (purge planifiée) ───────┘ (lit la politique de conservation)
```

## 3. Composants

| Service | Rôle | Stack |
|---|---|---|
| `smtp-gateway` | Réception SMTP + STARTTLS, dépôt dans la file | aiosmtpd, aio-pika |
| `archiver-worker` | Pipeline d'archivage (parse, dédup, crypto, signature, index) | cryptography, SQLAlchemy, opensearch-py, aioboto3 |
| `fetch-worker` | Collecte planifiée IMAP/POP3 → file | APScheduler, imaplib/poplib |
| `retention-worker` | Purge selon la politique de conservation | APScheduler |
| `api` | API REST/SSE : auth, recherche, consultation, comptes, sources, paramètres, audit | FastAPI, ldap3, PyJWT, bcrypt, aiosmtplib |
| `web` | Interface web (SPA) | Vue 3, Vite, Nginx |
| `libs/mailarchiver_common` | Code partagé (config, crypto, queue, storage, models, fetch) | — |

Infrastructure : **PostgreSQL** (métadonnées), **RabbitMQ** (files + dead-letter),
**OpenSearch en cluster** (recherche, nombre de nœuds/shards/replicas paramétrable
via `.env`), **MinIO/S3** (stockage objet).

> Worker supplémentaire : `restore-job` est exécuté par un **consommateur intégré
> à l'API** lisant une file durable `restore_jobs` (survit au redémarrage).

## 4. Principes d'architecture

- **Découplage ingestion / traitement** : le SMTP accuse réception et délègue à
  une file durable → débit élevé et résilience aux pics (backpressure).
- **Stockage immuable + signature** : une archive ne se modifie jamais ; la
  signature numérique prouve l'inviolabilité.
- **Compétition de consommateurs** : N `archiver-worker` sans état tirent de la
  même file → scaling horizontal et tolérance aux pannes.
- **Idempotence** : clé unique `archive_hash` → un mail rejoué n'est pas dupliqué.
- **Stateless services** : aucun état local dans les services applicatifs ; tout
  l'état est en base / objet / index.

## 5. Pipeline d'archivage (détail)

1. **Parse MIME** → en-têtes, corps texte, pièces jointes.
2. **Empreinte + signature** : `archive_hash` = SHA-256(en-têtes canoniques +
   hash du corps + hashes des PJ) ; signé en **Ed25519**.
3. **Déduplication PJ** : chaque pièce jointe est identifiée par le SHA-256 de son
   contenu en clair ; stockée une seule fois (compteur `ref_count`).
4. **Compression Zlib** puis **chiffrement AES-256-GCM** (*envelope encryption* :
   une clé de données par blob, scellée par la clé maître).
5. **Stockage** : corps + PJ dans MinIO (clé = hash, sharding `ab/cd/<sha>`).
6. **Métadonnées** en PostgreSQL (transaction atomique : message + refs PJ).
7. **Indexation** OpenSearch dans un **index journalier** `messages-AAAA.MM.JJ`
   (dérivé de la date d'archivage ; corps tronqué à 64 Ko côté index).
8. **Événement temps réel** publié sur l'exchange fanout `mail_events`.

## 5bis. Recherche à grande échelle

- **Index journaliers** (`messages-AAAA.MM.JJ`) créés via un **index template**
  `messages-*` (shards/replicas/`max_result_window` paramétrables). La recherche
  lit le motif `messages-*`.
- **Pagination par curseur `search_after`** (tri stable `date` + `doc_id`) :
  latence constante quelle que soit la profondeur (vs `from/size`).
- **Total plafonné** (`track_total_hits: 10000`, affiché « 10000+ ») et **corps
  exclu** des résultats de liste → recherche rapide même sur des millions de docs.
- **Cluster** : shards répartis sur N nœuds (parallélisme) + replicas (résilience).

Ordre clé : *compresser puis chiffrer* ; *dédupliquer sur le clair* (hash avant
scellage).

## 6. Modèle de données (PostgreSQL)

| Table | Contenu |
|---|---|
| `messages` | métadonnées (from/to/cc, sujet, date, taille, `archive_hash`, `signature`, `signed_at`, rétention) |
| `attachments` | PJ dédupliquées (`sha256`, `ref_count`) |
| `message_attachments` | association N..N message ↔ PJ |
| `users` | comptes locaux (rôle, e-mail, `audited_emails`, mot de passe bcrypt) |
| `app_settings` | paramètres globaux (rétention, relais SMTP) |
| `fetch_sources` | sources IMAP/POP3 (mot de passe chiffré) |
| `transfer_jobs` | jobs de restauration/transfert (statut, progression) |
| `audit_events` | journal append-only des actions |

Les **corps et pièces jointes** ne sont pas en base : uniquement dans MinIO
(chiffrés). PostgreSQL ne contient que des métadonnées → léger et rapide.

## 7. Sécurité

- **Transport** : STARTTLS sur le SMTP entrant ; LDAPS/IMAPS/SMTPS pour les
  intégrations.
- **Au repos** : corps/PJ en **AES-256-GCM** ; mots de passe comptes en **bcrypt** ;
  mots de passe IMAP/POP/SMTP **chiffrés** (clé maître).
- **Inviolabilité** : signature **Ed25519** vérifiée à la consultation/export
  (en-tête `X-Archive-Integrity`).
- **Authentification** : comptes locaux (JWT HS256) + AD/LDAP optionnel.
- **Autorisation (RBAC)** : rôles `admin` / `auditor` / `user` ; cloisonnement
  des mails par adresse (un utilisateur ne voit que ses mails, un auditeur son
  périmètre). Appliqué côté serveur (recherche, consultation, export, temps réel).
- **Audit** : toute action sensible (login, recherche, consultation, export,
  transfert, purge, gestion de comptes) est journalisée.

## 8. Résilience / tolérance aux pannes

- **File durable + messages persistants** : aucun mail accepté n'est perdu.
- **Idempotence** : rejeu sans doublon.
- **DLQ** (`raw_mail_dead`) : réessai borné puis mise en quarantaine (pas de
  boucle de message empoisonné) ; erreurs DB transitoires → réessai.
- **Reconnexion automatique** à RabbitMQ / PostgreSQL après redémarrage.
- **`restart: unless-stopped`** sur tous les services.
- **503** (au lieu de 500) quand la base est momentanément indisponible.
- **Auto-cicatrisation de l'index** : un mail archivé pendant une panne
  OpenSearch est ré-indexé au passage suivant.

Validé par des tests de chaos : kill de workers (0 perte), redémarrage
RabbitMQ/PostgreSQL, panne MinIO (DLQ), panne OpenSearch (cicatrisation),
API arrêtée (ingestion continue).

## 9. Capacité & dimensionnement

- **Ingestion SMTP** : ~500 msg/s (gateway non bloquant).
- **Traitement (archivage complet)** : ~200 msg/s avec 8 workers sur une machine
  de dev mono-hôte. Réglable via `archiver-worker.deploy.replicas`.
- **24/7 soutenu** : nécessite une infra de production distribuée (workers sur
  nœuds dédiés, PostgreSQL partitionné par date + réplication, OpenSearch en
  cluster + ILM, object storage scalable + tiering, HA sur chaque composant,
  monitoring de la profondeur de file + autoscaling). Voir §11.

Volumétrie à 200 msg/s soutenu : ~17,3 M mails/jour, ~6,3 Md/an, de ~315 To à
~1 Po/an selon la taille moyenne (rétention par défaut 1 an).

## 10. Déploiement

Développement : `docker compose up` (voir `README.md`). Volumes nommés
(`pgdata`, `osdata`, `miniodata`) → `down -v` réinitialise réellement.

Secrets (`make keys`) : clé maître AES-256, paire de signature Ed25519,
certificat STARTTLS — montés en lecture seule dans `/secrets`.

## 11. État de l'industrialisation

**Déjà en place :**
- **OpenSearch en cluster** (2 nœuds, paramétrable) + **index journaliers** (~ILM
  par drop) + `search_after`.
- **structlog** configuré sur tous les services (console ou JSON via `LOG_JSON`).
- **Job de restauration persistant** : file durable `restore_jobs` consommée par
  l'API (rejoué si l'API redémarre).
- **Quarantaine (DLQ)** consultable/rejouable depuis l'IHM ; métriques de débit
  et de profondeur de file exposées (`/metrics/throughput`) ; healthcheck réel
  des composants (`/health/components`).
- **Sécurité** : **verrouillage de compte** anti-bruteforce (423 après N échecs),
  secrets forts (`make keys` / `make secret-key`), scrape Prometheus non public.
- **Métriques Prometheus** (`/metrics/prometheus`) + **alerting** clés en main
  (`deploy/monitoring/` : Prometheus + Alertmanager + règles).

- **Partitionnement PostgreSQL** par mois **actif** (`messages` RANGE sur
  `signed_at`, PK composite, idempotence via `message_dedup`) — créé par
  `models.ensure_schema()`.

**Artefacts de déploiement fournis** (`deploy/`, cf. `docs/PRODUCTION.md`) :
- **Autoscaling KEDA** (`deploy/k8s/`) sur la profondeur de `raw_mail`.
- DDL de référence partitionnement + pg_cron + migration (`deploy/db/`).
- **HA** PostgreSQL/RabbitMQ/MinIO/OpenSearch (`deploy/ha/`).
- **Sauvegarde / PRA** des 3 datastores (`deploy/backup/`).

**Restant (spécifique à l'infra cible) :**
- Déployer les clusters HA + appliquer le partitionnement (évolution de schéma)
  en production ; TLS/auth sur OpenSearch/RabbitMQ/MinIO ; ingress HTTPS.
- Job de restauration : ack-timeout RabbitMQ (~30 min) pour les très gros jobs →
  découper en lots ou worker dédié.
