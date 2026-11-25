-- Atomic allocator for the human-readable sequential numbers (P…, E…, RX…, M…).
-- The old "SELECT max()+1" let two concurrent requests pick the same number (one then died on the UNIQUE index → HTTP 500).
-- A counter row is incremented with a single UPDATE inside the caller's transaction, so concurrent writers serialise on SQLite's
-- write lock and a rolled-back request gives its number back (no gaps). Rows are created lazily, seeded from the current maximum.
CREATE TABLE number_counters (
	key VARCHAR(40) NOT NULL,
	value INTEGER NOT NULL DEFAULT 0,
	CONSTRAINT pk_number_counters PRIMARY KEY (key)
)
