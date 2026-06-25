-- ============================================================================
-- Partitionnement de `messages` par mois (RANGE sur signed_at = date d'archivage)
-- ============================================================================
-- Objectif : à des milliards de lignes, garder les requêtes et la purge rapides.
--  - Recherche par date → partition pruning (ne scanne que les mois concernés).
--  - Rétention = DROP de la partition du mois expiré (instantané, comme les
--    index journaliers OpenSearch côté recherche).
--  - Index plus petits par partition, VACUUM/maintenance localisés.
--
-- ✅ DÉJÀ IMPLÉMENTÉ dans le code : `mailarchiver_common.models.ensure_schema()`
--    crée cette structure au démarrage (table partitionnée + message_dedup +
--    partitions du mois courant +2). Ce fichier sert de référence/standalone et
--    documente la planification pg_cron et la migration d'une base existante.
--
-- ⚠️ ÉVOLUTION DE SCHÉMA. PostgreSQL impose que la clé de partition fasse partie
--    de toute contrainte UNIQUE/PK. La PK de `messages` devient donc
--    (id, signed_at), et la table d'association porte aussi message_signed_at.
--    Le code ORM doit être adapté en conséquence (clé composite). À déployer sur
--    une base neuve, ou via migration copie (voir bas de fichier).
-- ----------------------------------------------------------------------------

-- 1. Table partitionnée (installation neuve)
CREATE TABLE messages (
    id              BIGINT       GENERATED ALWAYS AS IDENTITY,
    signed_at       TIMESTAMPTZ  NOT NULL,
    message_id      VARCHAR(998) NOT NULL,
    date            TIMESTAMPTZ  NOT NULL,
    from_addr       VARCHAR(998) NOT NULL,
    to_addrs        JSONB        NOT NULL DEFAULT '[]',
    cc_addrs        JSONB        NOT NULL DEFAULT '[]',
    subject         TEXT         NOT NULL DEFAULT '',
    size_bytes      BIGINT       NOT NULL DEFAULT 0,
    body_sha256     VARCHAR(64)  NOT NULL,
    archive_hash    VARCHAR(64)  NOT NULL,
    signature       TEXT         NOT NULL,
    retention_class VARCHAR(64)  NOT NULL DEFAULT 'default',
    expires_at      TIMESTAMPTZ,
    legal_hold      BOOLEAN      NOT NULL DEFAULT false,
    PRIMARY KEY (id, signed_at),
    UNIQUE (archive_hash, signed_at)          -- idempotence (inclut la clé de part.)
) PARTITION BY RANGE (signed_at);

CREATE INDEX ON messages (date);
CREATE INDEX ON messages (from_addr);
CREATE INDEX ON messages (archive_hash);

-- Table d'association (la PJ référencée doit inclure la clé de partition).
CREATE TABLE message_attachments (
    message_id        BIGINT      NOT NULL,
    message_signed_at TIMESTAMPTZ NOT NULL,
    attachment_id     BIGINT      NOT NULL REFERENCES attachments(id) ON DELETE RESTRICT,
    PRIMARY KEY (message_id, message_signed_at, attachment_id),
    FOREIGN KEY (message_id, message_signed_at)
        REFERENCES messages(id, signed_at) ON DELETE CASCADE
);

-- 2. Création automatique des partitions mensuelles (à appeler en avance).
CREATE OR REPLACE FUNCTION ensure_message_partition(p_month DATE)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    start_d DATE := date_trunc('month', p_month);
    end_d   DATE := start_d + INTERVAL '1 month';
    part    TEXT := format('messages_%s', to_char(start_d, 'YYYY_MM'));
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF messages
             FOR VALUES FROM (%L) TO (%L)', part, start_d, end_d);
END $$;

-- Mois courant + 2 mois d'avance (à planifier mensuellement, ex. pg_cron).
SELECT ensure_message_partition(now()::date);
SELECT ensure_message_partition((now() + INTERVAL '1 month')::date);
SELECT ensure_message_partition((now() + INTERVAL '2 month')::date);

-- Planification (extension pg_cron) :
--   SELECT cron.schedule('msg-partitions', '0 0 25 * *',
--     $$ SELECT ensure_message_partition((now() + INTERVAL '2 month')::date) $$);

-- 3. Rétention = drop de la partition entière (au lieu de DELETE par lignes) :
--   DROP TABLE messages_2024_01;   -- mois hors période de conservation

-- 4. Migration d'une base existante (non partitionnée) :
--   a. Renommer l'ancienne table : ALTER TABLE messages RENAME TO messages_old;
--   b. Créer la table partitionnée ci-dessus + partitions couvrant l'historique.
--   c. INSERT INTO messages SELECT ... FROM messages_old;  (par lots)
--   d. Recréer message_attachments en complétant message_signed_at par jointure.
--   e. DROP TABLE messages_old.
