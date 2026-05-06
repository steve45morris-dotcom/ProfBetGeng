-- M8-c: Outcome Tracker — v1.1.1
-- Records settled ticket outcomes for performance analytics

CREATE TABLE IF NOT EXISTS ticket_outcomes (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  api_key      text        NOT NULL,
  ticket_ref   text        NOT NULL,
  outcome      text        NOT NULL CHECK (outcome IN ('WIN', 'LOSS', 'VOID', 'PENDING')),
  payout_odds  float,
  settled_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ticket_outcomes_api_key ON ticket_outcomes (api_key);
CREATE INDEX IF NOT EXISTS idx_ticket_outcomes_settled_at ON ticket_outcomes (settled_at DESC);
