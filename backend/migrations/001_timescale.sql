-- Run after `python -m app.db.init` has created the tables.
-- Optional: requires the TimescaleDB extension (falls back to plain Postgres tables).
CREATE EXTENSION IF NOT EXISTS timescaledb;
SELECT create_hypertable('odds_history', 'captured_at', if_not_exists => TRUE, migrate_data => TRUE);
SELECT create_hypertable('bankroll_history', 'recorded_at', if_not_exists => TRUE, migrate_data => TRUE);
CREATE INDEX IF NOT EXISTS ix_odds_history_fixture_time ON odds_history (fixture_id, captured_at DESC);
-- Keep raw ticks 180 days, then rely on odds (latest/closing) + compressed chunks.
ALTER TABLE odds_history SET (timescaledb.compress, timescaledb.compress_segmentby = 'fixture_id, market_key');
SELECT add_compression_policy('odds_history', INTERVAL '14 days', if_not_exists => TRUE);

-- Predictions are append-only: block UPDATE/DELETE at the database level.
CREATE OR REPLACE FUNCTION forbid_change() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'model_predictions/market_predictions are append-only'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS mp_immutable ON model_predictions;
CREATE TRIGGER mp_immutable BEFORE UPDATE OR DELETE ON model_predictions FOR EACH ROW EXECUTE FUNCTION forbid_change();
DROP TRIGGER IF EXISTS mkp_immutable ON market_predictions;
CREATE TRIGGER mkp_immutable BEFORE UPDATE OR DELETE ON market_predictions FOR EACH ROW EXECUTE FUNCTION forbid_change();
