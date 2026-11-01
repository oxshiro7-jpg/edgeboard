-- Invalidate everything that did not come from a verified provider fixture.
-- model_predictions / market_predictions are append-only (see 001), so they
-- are invalidated by reference; recommendations are deleted outright.

CREATE TABLE IF NOT EXISTS prediction_invalidations (
    id            SERIAL PRIMARY KEY,
    prediction_id INTEGER NOT NULL REFERENCES model_predictions(id),
    reason        VARCHAR(200) NOT NULL,
    at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_prediction_invalidations_pred ON prediction_invalidations(prediction_id);

ALTER TABLE model_predictions ADD COLUMN IF NOT EXISTS provider VARCHAR(40);
ALTER TABLE model_predictions ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20);

-- 1) Anything from the removed demo/synthetic providers or without a provider.
INSERT INTO prediction_invalidations (prediction_id, reason)
SELECT mp.id, 'Produced from an unverified (demo/synthetic) fixture'
FROM model_predictions mp
WHERE (mp.fixture_id LIKE 'demo-%' OR mp.fixture_id LIKE 'test-%' OR mp.provider IS NULL
       OR mp.provider NOT IN ('api_football') OR COALESCE(mp.verification_status, '') <> 'VERIFIED')
  AND NOT EXISTS (SELECT 1 FROM prediction_invalidations pi WHERE pi.prediction_id = mp.id);

-- 2) Published picks on those fixtures.
DELETE FROM bets WHERE recommendation_id IN (
    SELECT r.id FROM recommendations r
    JOIN market_predictions m ON m.id = r.market_prediction_id
    JOIN prediction_invalidations pi ON pi.prediction_id = m.prediction_id);
DELETE FROM recommendations r USING market_predictions m, prediction_invalidations pi
WHERE m.id = r.market_prediction_id AND pi.prediction_id = m.prediction_id;

-- 3) Fixtures and teams not created by a fixtures API.
DELETE FROM fixtures WHERE id LIKE 'demo-%' OR id LIKE 'test-%';

-- Readers must use this view, which hides invalidated predictions.
CREATE OR REPLACE VIEW valid_model_predictions AS
SELECT mp.* FROM model_predictions mp
WHERE mp.verification_status = 'VERIFIED'
  AND NOT EXISTS (SELECT 1 FROM prediction_invalidations pi WHERE pi.prediction_id = mp.id);
