# Sauvegarde & PRA

## Principe

| Datastore | Contenu | Criticité | Reconstructible ? |
|---|---|---|---|
| PostgreSQL | métadonnées, comptes, audit, paramètres | **haute** | non |
| MinIO/S3 | corps + PJ (chiffrés, signés) | **haute** | non |
| OpenSearch | index de recherche | moyenne | **oui** (réindexation depuis les EML) |

L'index OpenSearch est **reconstructible** à partir des EML conservés en MinIO :
en cas de perte, on peut réindexer. Le snapshot ne sert qu'à accélérer le PRA.
La **vraie** donnée à protéger = PostgreSQL + MinIO.

## Sauvegarde

```bash
BACKUP_DIR=/srv/backups ./deploy/backup/backup.sh   # à planifier (cron quotidien)
```

Recommandations production :
- **3-2-1** : 3 copies, 2 supports, 1 hors-site. Cible hors-site chiffrée.
- PostgreSQL : passer en **WAL archiving + PITR** (point-in-time recovery) plutôt
  qu'un simple dump quotidien (RPO proche de 0).
- MinIO : activer le **bucket replication** vers un site distant (RPO temps réel)
  + **versioning** + object lock (WORM) pour la conformité.

## Restauration

```bash
# PostgreSQL
docker compose exec -T postgres pg_restore -U mailarchiver -d mailarchiver --clean < postgres.dump

# MinIO
docker compose exec -T minio sh -c 'mc mirror /tmp/restore local/archives'

# OpenSearch : restaurer le snapshot OU réindexer depuis les EML.
```

## Enregistrer un dépôt de snapshot OpenSearch

```bash
# Monter un volume partagé path.repo=/snapshots sur chaque nœud, puis :
curl -XPUT localhost:9200/_snapshot/backup \
  -H 'Content-Type: application/json' \
  -d '{"type":"fs","settings":{"location":"/snapshots"}}'
# En production : utiliser le type "s3" (plugin repository-s3) vers MinIO/S3.
```

## Test de restauration

Tester la restauration **régulièrement** (un PRA non testé n'existe pas) :
restaurer sur un environnement isolé, vérifier l'intégrité des signatures
(`GET /messages/{id}/eml` renvoie l'en-tête `X-Archive-Integrity: valid`).
