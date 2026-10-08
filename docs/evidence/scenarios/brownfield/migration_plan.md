# Migration plan

- forward: `ALTER TABLE links ADD COLUMN max_clicks INTEGER`
  - rollback: `ALTER TABLE links DROP COLUMN max_clicks`
  - backfill: none (NULL = unlimited)

Strategy: expand-only; additive nullable column; app tolerates both schemas
