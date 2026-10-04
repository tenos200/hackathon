-- Rare Disease Atlas: private snapshot schema, immutable published content and
-- least-privilege group roles. Apply with a migration/admin identity, never
-- with the API or publisher roles. Idempotent where practical.
--
-- Supabase: keep the `atlas` schema OUT of the Data API "exposed schemas".
-- Login users for the two group roles are created separately with
-- scripts/create_login_roles.sql (passwords are never committed).

BEGIN;

CREATE SCHEMA IF NOT EXISTS atlas;

CREATE TABLE IF NOT EXISTS atlas.schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- One row per complete published package. snapshot_id is derived from the
-- canonical content hash; inserted in the same transaction as its content.
CREATE TABLE IF NOT EXISTS atlas.snapshots (
    snapshot_id     text PRIMARY KEY CHECK (snapshot_id ~ '^snap_[0-9a-f]{64}$'),
    content_sha256  text NOT NULL CHECK (snapshot_id = 'snap_' || content_sha256),
    package_format  text NOT NULL,
    record_counts   jsonb NOT NULL,
    release         jsonb NOT NULL,
    inserted_at     timestamptz NOT NULL DEFAULT now()   -- operational only; not part of the content hash
);

-- Generic content tables: (snapshot_id, id) keys and validated JSONB payloads.
-- Foreign keys are snapshot-aware and deferred so a complete package can be
-- inserted in one transaction and validated at commit.
CREATE TABLE IF NOT EXISTS atlas.entities (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL CHECK (length(id) BETWEEN 1 AND 180),
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id)
);

CREATE TABLE IF NOT EXISTS atlas.sources (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id)
);

CREATE TABLE IF NOT EXISTS atlas.contexts (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL CHECK (length(id) BETWEEN 1 AND 180),
    disease_id  text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, disease_id) REFERENCES atlas.entities (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS atlas.mappings (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL,
    source_id   text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, source_id) REFERENCES atlas.entities (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS atlas.evidence (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL,
    source_id   text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, source_id) REFERENCES atlas.sources (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS atlas.assertions (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL CHECK (length(id) BETWEEN 1 AND 180),
    subject_id  text NOT NULL,
    object_id   text NOT NULL,
    context_id  text,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, subject_id) REFERENCES atlas.entities (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id, object_id) REFERENCES atlas.entities (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id, context_id) REFERENCES atlas.contexts (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS atlas.supports (
    snapshot_id  text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id           text NOT NULL,
    assertion_id text NOT NULL,
    evidence_id  text NOT NULL,
    payload      jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, assertion_id) REFERENCES atlas.assertions (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id, evidence_id) REFERENCES atlas.evidence (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS atlas.conflicts (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id)
);

CREATE TABLE IF NOT EXISTS atlas.calculations (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL CHECK (length(id) BETWEEN 1 AND 180),
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id)
);

CREATE TABLE IF NOT EXISTS atlas.comparisons (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL,
    context_a   text NOT NULL,
    context_b   text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, context_a) REFERENCES atlas.contexts (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id, context_b) REFERENCES atlas.contexts (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS atlas.opportunities (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL,
    context_id  text NOT NULL,
    asset_id    text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, context_id) REFERENCES atlas.contexts (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (snapshot_id, asset_id) REFERENCES atlas.entities (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS atlas.gaps (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL,
    context_id  text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, context_id) REFERENCES atlas.contexts (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS atlas.coverage (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id)
);

CREATE TABLE IF NOT EXISTS atlas.explanations (
    snapshot_id text NOT NULL REFERENCES atlas.snapshots (snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    id          text NOT NULL,
    context_id  text NOT NULL,
    payload     jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, context_id) REFERENCES atlas.contexts (snapshot_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- Published content is immutable: no UPDATE, DELETE or TRUNCATE, for any role.
CREATE OR REPLACE FUNCTION atlas.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'atlas snapshots are immutable: % on %.% is not allowed', TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['snapshots','entities','sources','contexts','mappings','evidence','assertions','supports',
                             'conflicts','calculations','comparisons','opportunities','gaps','coverage','explanations']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON atlas.%I', t || '_immutable', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON atlas.%I FOR EACH ROW EXECUTE FUNCTION atlas.forbid_mutation()',
                       t || '_immutable', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON atlas.%I', t || '_no_truncate', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON atlas.%I FOR EACH STATEMENT EXECUTE FUNCTION atlas.forbid_mutation()',
                       t || '_no_truncate', t);
    END LOOP;
END;
$$;

-- Group roles (NOLOGIN). Login users are granted membership separately.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_api') THEN
        CREATE ROLE atlas_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_publisher') THEN
        CREATE ROLE atlas_publisher NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END;
$$;

REVOKE ALL ON SCHEMA atlas FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA atlas FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA atlas FROM PUBLIC;

-- Supabase's exposed roles get nothing here (no-op on plain PostgreSQL).
DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA atlas FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA atlas FROM %I', r);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA atlas REVOKE ALL ON TABLES FROM %I', r);
        END IF;
    END LOOP;
END;
$$;

GRANT USAGE ON SCHEMA atlas TO atlas_api, atlas_publisher;

-- API: read-only on published content.
GRANT SELECT ON atlas.snapshots, atlas.entities, atlas.sources, atlas.contexts, atlas.mappings, atlas.evidence,
    atlas.assertions, atlas.supports, atlas.conflicts, atlas.calculations, atlas.comparisons, atlas.opportunities,
    atlas.gaps, atlas.coverage, atlas.explanations TO atlas_api;

-- Publisher: insert complete packages and read them back for verification. No UPDATE/DELETE/DDL.
GRANT SELECT, INSERT ON atlas.snapshots, atlas.entities, atlas.sources, atlas.contexts, atlas.mappings, atlas.evidence,
    atlas.assertions, atlas.supports, atlas.conflicts, atlas.calculations, atlas.comparisons, atlas.opportunities,
    atlas.gaps, atlas.coverage, atlas.explanations TO atlas_publisher;

ALTER DEFAULT PRIVILEGES IN SCHEMA atlas REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA atlas REVOKE ALL ON FUNCTIONS FROM PUBLIC;

-- Row level security as defense in depth: only the two group roles have policies.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['snapshots','entities','sources','contexts','mappings','evidence','assertions','supports',
                             'conflicts','calculations','comparisons','opportunities','gaps','coverage','explanations']
    LOOP
        EXECUTE format('ALTER TABLE atlas.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS api_read ON atlas.%I', t);
        EXECUTE format('CREATE POLICY api_read ON atlas.%I FOR SELECT TO atlas_api USING (true)', t);
        EXECUTE format('DROP POLICY IF EXISTS publisher_read ON atlas.%I', t);
        EXECUTE format('CREATE POLICY publisher_read ON atlas.%I FOR SELECT TO atlas_publisher USING (true)', t);
        EXECUTE format('DROP POLICY IF EXISTS publisher_insert ON atlas.%I', t);
        EXECUTE format('CREATE POLICY publisher_insert ON atlas.%I FOR INSERT TO atlas_publisher WITH CHECK (true)', t);
    END LOOP;
END;
$$;

INSERT INTO atlas.schema_migrations (version) VALUES ('0001_atlas_schema') ON CONFLICT (version) DO NOTHING;

COMMIT;
