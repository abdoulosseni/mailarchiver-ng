# Haute disponibilité (HA)

L'application est **stateless et scalable horizontalement** (smtp-gateway, api,
workers → N réplicas derrière un load-balancer). La HA porte donc sur les
**datastores**. Cible : aucun point de défaillance unique (no SPOF).

> ⚠️ Ces topologies ne tournent pas sur une machine de dev mono-hôte (RAM). Elles
> sont destinées à un cluster (plusieurs hôtes). Exemples ci-dessous.

## Faut-il Swarm ou Kubernetes ? NON.

Un orchestrateur n'est **pas obligatoire** pour la HA — c'est juste un moyen de
gérer les réplicas/bascules. L'application étant **12-factor** (toute la config
par variables d'env : `DATABASE_URL`, `AMQP_URL`, `S3_ENDPOINT`, `OPENSEARCH_URL`),
elle tourne à l'identique sur n'importe quelle cible. Du plus simple au plus lourd :

1. **Services managés (recommandé)** — la voie la plus simple vers une vraie HA,
   sans opérer aucun cluster soi-même :
   - PostgreSQL → **AWS RDS Multi-AZ / Aurora**, GCP Cloud SQL HA, Azure Database
     (bascule auto, PITR, sauvegardes gérées).
   - Stockage → **AWS S3 / GCS / Azure Blob** (HA native, 11 nines) au lieu de MinIO.
   - Recherche → **AWS OpenSearch Service / Elastic Cloud**.
   - File → **CloudAMQP** (RabbitMQ managé) ou Amazon MQ.
   On ne change que les URLs dans l'env ; l'app reste telle quelle.

2. **HA « classique » sur VM / bare-metal, sans orchestrateur** :
   - PostgreSQL : **Patroni + etcd + HAProxy/keepalived** (VIP) — ou repmgr+pgpool.
   - RabbitMQ : cluster 3 nœuds + HAProxy (quorum queues, déjà utilisées par l'app).
   - MinIO : distribué 4 nœuds + LB ; OpenSearch : 3 nœuds.
   - Applicatif : plusieurs instances (systemd ou conteneurs) derrière HAProxy/nginx
     + **keepalived** pour la VIP. Provisionnement via **Ansible**.

3. **Orchestrateurs** — pratiques mais optionnels :
   - **Docker Swarm** : `docker-compose.ha.yml` (ce dépôt) — le plus léger.
   - **Nomad + Consul** : alternative légère à K8s.
   - **Kubernetes** : `deploy/k8s/` + opérateurs (CloudNativePG, RabbitMQ, MinIO) —
     le plus riche (autoscaling KEDA, self-healing) mais le plus d'exploitation.

**Reco** : pour la plupart des équipes, **datastores managés + quelques instances
applicatives derrière un LB** = la HA la plus robuste avec le moins d'exploitation.
Réserver Swarm/K8s aux contextes qui le justifient (multi-tenant, fort autoscaling).

## Déploiement Docker Swarm (fourni)

`docker-compose.ha.yml` (racine) + `.env.ha.example` :
```bash
docker swarm init
docker node update --label-add role=db <n1>; ... (mq, s3, search)
docker config create nginx_prod services/web/nginx.prod.conf
for s in master.key signing_private.pem signing_public.pem smtp_cert.pem smtp_key.pem; do
  docker secret create $s secrets/$s; done
docker compose build && docker compose push        # vers $REGISTRY
docker stack deploy -c docker-compose.ha.yml mailarchiver
```

## PostgreSQL — réplication + bascule automatique
- **Patroni** (+ etcd/Consul) ou opérateur **CloudNativePG** / Crunchy sur K8s :
  1 primaire + ≥1 réplica en streaming, bascule automatique, endpoint unique.
- Combiner avec le **partitionnement** (`deploy/db/partitioning.sql`).
- `DATABASE_URL` pointe vers le service/VIP du primaire (réécrit à la bascule).
- Sauvegarde : WAL archiving + PITR (cf. `deploy/backup/`).

```yaml
# Exemple CloudNativePG (extrait)
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: { name: mailarchiver-pg, namespace: mailarchiver }
spec:
  instances: 3                 # 1 primaire + 2 réplicas
  storage: { size: 500Gi }
  postgresql:
    parameters: { max_connections: "300" }
```

## RabbitMQ — cluster + files quorum
- **Cluster ≥3 nœuds** (nombre impair) + **quorum queues** (réplication des
  messages, tolérance à la panne d'un nœud). L'opérateur RabbitMQ Cluster Kubernetes
  gère le tout.
- Passer `raw_mail`, `raw_mail_dead`, `restore_jobs` en `x-queue-type: quorum`.
- `AMQP_URL` pointe vers le service cluster (round-robin sur les nœuds).

```yaml
apiVersion: rabbitmq.com/v1beta1
kind: RabbitmqCluster
metadata: { name: mailarchiver-mq, namespace: mailarchiver }
spec:
  replicas: 3
```

## MinIO / S3 — distribué (erasure coding)
- **MinIO distribué** (≥4 nœuds, erasure coding) → tolérance à la perte de
  disques/nœuds ; ou **S3 managé** (AWS S3, déjà HA + 11 nines de durabilité).
- Activer **versioning** + **object lock (WORM)** pour la conformité d'archivage.
- Réplication inter-sites (bucket replication) pour le PRA.

## OpenSearch — déjà en cluster
- Géré nativement (cf. `docker-compose.yml`, `OPENSEARCH_SHARDS/REPLICAS`).
  En production : nœuds dédiés par rôle (manager / data / ingest), ≥3 manager
  pour le quorum, `number_of_replicas >= 1`, et **ILM** (rollover/tiering) sur
  les index journaliers `messages-*`.

## Application (sans état)
- `api`, `smtp-gateway`, `web` : ≥2 réplicas, sondes liveness/readiness sur
  `/health`, load-balancer devant. `archiver-worker` : scalé par KEDA
  (`deploy/k8s/keda-scaledobject.yaml`).
- Note : le consommateur `restore_jobs` est intégré à l'API → avec N réplicas
  d'API, chaque job n'est livré qu'à **une** instance (file de travail). Pour de
  très gros jobs (> ack-timeout RabbitMQ ~30 min), l'externaliser en worker dédié
  et/ou découper en lots avec checkpoint.
```
