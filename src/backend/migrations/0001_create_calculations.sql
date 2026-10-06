CREATE TABLE calculations (
    calculation_id uuid PRIMARY KEY,
    operation text NOT NULL CHECK (operation IN ('add', 'sub', 'mul', 'div')),
    operand_a numeric NOT NULL,
    operand_b numeric NOT NULL,
    result numeric NOT NULL,
    occurred_at timestamptz NOT NULL,
    correlation_id text
);

-- Serves the history order (most recent first, ties by id) and its keyset cursor.
CREATE INDEX calculations_history_idx ON calculations (occurred_at DESC, calculation_id DESC);
