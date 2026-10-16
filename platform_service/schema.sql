CREATE TABLE IF NOT EXISTS schema_versions(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS factor_versions(
 id text PRIMARY KEY, name text NOT NULL, version text NOT NULL, expression text NOT NULL,
 description text NOT NULL, engine_version text NOT NULL, content_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(name,version));
CREATE TABLE IF NOT EXISTS factor_sets(
 id text PRIMARY KEY, name text NOT NULL, version text NOT NULL, members jsonb NOT NULL,
 content_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS datasets(
 id uuid PRIMARY KEY, name text NOT NULL, snapshot_path text NOT NULL, provider_path text NOT NULL,
 source_hash text NOT NULL UNIQUE, config jsonb NOT NULL, acceptance jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS runs(
 id uuid PRIMARY KEY, idempotency_key text UNIQUE NOT NULL, request_hash text NOT NULL,
 title text NOT NULL, external_ref text, dataset_id uuid NOT NULL REFERENCES datasets(id),
 spec jsonb NOT NULL, status text NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
 attempt integer NOT NULL DEFAULT 0, cancel_requested boolean NOT NULL DEFAULT false,
 worker_id text, lease_token uuid, lease_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz,
 error text, result jsonb, origin text NOT NULL DEFAULT 'api');
CREATE INDEX IF NOT EXISTS runs_queue ON runs(created_at) WHERE status='queued';
CREATE TABLE IF NOT EXISTS artifacts(
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs(id), attempt integer NOT NULL,
 name text NOT NULL, path text NOT NULL, sha256 text NOT NULL, size bigint NOT NULL,
 media_type text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(run_id,attempt,name));
CREATE TABLE IF NOT EXISTS run_events(
 id bigserial PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs(id),
 event text NOT NULL, details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS schedules(
 id uuid PRIMARY KEY, name text NOT NULL, request jsonb NOT NULL, interval_seconds integer NOT NULL CHECK(interval_seconds>=60),
 enabled boolean NOT NULL DEFAULT false, next_run_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS heartbeats(name text PRIMARY KEY, updated_at timestamptz NOT NULL DEFAULT now());
INSERT INTO schema_versions(version) VALUES(1) ON CONFLICT DO NOTHING;
