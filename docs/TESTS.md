# Tests & non-régression

## Vue d'ensemble

| Suite | Emplacement | Nature | Lancement |
|---|---|---|---|
| Unitaires (crypto) | `tests/` (pytest) | hors stack | `make test` |
| **Non-régression E2E** | `tests/nonreg.py` | contre la stack en marche | `make nonreg` |
| Charge / volumétrie | `loadtest/` | injection massive | `python loadtest/inject.py …` |

## Non-régression E2E (`tests/nonreg.py`)

Exerce **toutes les fonctionnalités** de bout en bout contre une stack démarrée,
avec rapport `PASS/FAIL` par test et **code de sortie 1** si un test échoue
(intégrable en CI).

### Prérequis
- Stack démarrée : `make up` (ou `docker compose up -d`), tous les composants sains.
- `docker compose` accessible (le script interroge PostgreSQL/RabbitMQ via `exec`).
- Pour les tests de restauration : image `greenmail/standalone:2.1.0` accessible
  (téléchargée automatiquement au 1er run).

### Lancement
```bash
make nonreg                       # suite complète
.venv/bin/python tests/nonreg.py            # idem
.venv/bin/python tests/nonreg.py --quick    # sans les tests perturbateurs
.venv/bin/python tests/nonreg.py --base http://hote:8080
.venv/bin/python tests/nonreg.py --keep     # conserve les ressources créées (debug)
```
`--quick` saute les tests qui perturbent l'infra (DLQ coupe MinIO, rétention
antidate des lignes, restauration via GreenMail, SSE). Idéal en vérification rapide.

### Couverture (49 vérifications)

| Domaine | Ce qui est vérifié |
|---|---|
| **Santé & métriques** | `/health`, `/health/components` (tous composants `ok`), `/metrics/throughput`, scrape Prometheus interne exposé + public bloqué (403) |
| **Auth & sécurité** | login admin, mauvais mot de passe (401), **verrouillage** après 5 échecs (423) + déverrouillage, **RBAC** (non-admin → 403 sur `/users`, `/stats`) |
| **Ingestion SMTP** | injection STARTTLS d'un jeu de 12 mails + 1 doublon |
| **Archivage & recherche** | mails trouvés, recherche par expéditeur, filtre pièce jointe |
| **Idempotence & dédup** | doublon exact archivé **une seule fois**, PJ partagée `ref_count=2`, liens d'association |
| **Partitionnement** | table `messages` partitionnée, routage dans la partition du mois, `message_dedup` cohérent |
| **Crypto / intégrité** | consultation parsée, export EML, **signature `X-Archive-Integrity: valid`** |
| **Pagination** | `search_after` page 1→2 sans chevauchement |
| **RBAC / périmètre** | utilisateur limité à son adresse, auditeur limité aux adresses auditées |
| **Restauration** | job durable **SMTP** (dépôt GreenMail) + **IMAP APPEND** (dépôt GreenMail) |
| **Temps réel** | événement **SSE** reçu après archivage |
| **DLQ** | mail empoisonné (MinIO coupé) → quarantaine → **rejeu** → archivé |
| **Rétention** | purge d'un message antidaté (PK composite) + nettoyage `message_dedup` |
| **Import / Paramètres** | import `.eml` (multipart) archivé, modification de rétention persistée |

Le script **nettoie** les ressources créées en fin d'exécution (comptes de test,
conteneur GreenMail, paramètres SMTP), sauf avec `--keep`.

### Pièges connus (intégrés au script)
- Les marqueurs de sujet **n'utilisent pas `_`** : l'analyseur standard d'OpenSearch
  ne segmente pas sur l'underscore (un seul token) → recherche plein-texte fiable.
- Le contenu des pièces jointes de test est **unique par run** (sinon la dédup
  globale par sha256 fait grimper `ref_count` entre exécutions).

## Tests de charge (`loadtest/`)
```bash
python loadtest/inject.py --total 50000 --conns 16   # injection massive
python loadtest/inject_large.py                       # gros corps + PJ
python loadtest/measure.sh                            # débit de traitement
```
Surveiller l'archivage : `docker compose exec rabbitmq rabbitmqctl list_queues`
et la carte « Débit (temps réel) » des Paramètres (ou `/metrics/prometheus`).
