CREATE TABLE IF NOT EXISTS samples (
    session_id TEXT NOT NULL,
    sensor TEXT NOT NULL,
    sample_index INTEGER NOT NULL,
    ch1 INTEGER NOT NULL,
    ch2 INTEGER NOT NULL,
    ch3 INTEGER NOT NULL,
    PRIMARY KEY (session_id, sensor, sample_index)
);
