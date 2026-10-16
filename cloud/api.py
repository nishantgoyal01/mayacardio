import os
import logging
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ValidationError, conint, conlist
import psycopg2
from psycopg2.extras import execute_values

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
DATABASE_URL = os.getenv("POSTGRES_URL", "postgresql://nishantgoyal:nishant01@localhost:5432/mayacardio_cloud")

# ------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------
app = FastAPI(title="MayaCardio Cloud Ingestion Service")
logger = logging.getLogger("mayacardio.cloud")

# ------------------------------------------------------------
# Pydantic models – strict validation of the payload
# ------------------------------------------------------------
class Sample(BaseModel):
    session_id: str
    sensor: str
    index: conint(ge=0)  # sample index must be non‑negative
    ch1: conint(ge=0, le=2**24 - 1)
    ch2: conint(ge=0, le=2**24 - 1)
    ch3: conint(ge=0, le=2**24 - 1)

class IngestPayload(BaseModel):
    samples: conlist(Sample, min_length=1)

# ------------------------------------------------------------
# Database helper – create table if missing
# ------------------------------------------------------------
def init_db() -> None:
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    session_id TEXT NOT NULL,
                    sensor TEXT NOT NULL,
                    sample_index INTEGER NOT NULL,
                    ch1 INTEGER NOT NULL,
                    ch2 INTEGER NOT NULL,
                    ch3 INTEGER NOT NULL,
                    PRIMARY KEY (session_id, sensor, sample_index)
                );
                """
            )
        conn.commit()
    logger.info("Database initialised – samples table ready")

# Run the initialisation at import time
init_db()

# ------------------------------------------------------------
# Ingestion endpoint – idempotent insert
# ------------------------------------------------------------
@app.post("/ingest")
async def ingest(payload: Request):
    """Accept a JSON body that contains a list of samples.

    The function validates the payload using the Pydantic models above.
    Inserts are performed with ``ON CONFLICT DO NOTHING`` so duplicate
    records are silently ignored, satisfying the exactly‑once guarantee.
    """
    try:
        raw = await payload.json()
        data = IngestPayload(**raw)
    except ValidationError as ve:
        raise HTTPException(status_code=422, detail=ve.errors())
    except Exception as e:
        logger.exception("Failed to parse request body")
        raise HTTPException(status_code=400, detail=str(e))

    # Prepare rows for bulk insertion
    rows = [
        (s.session_id, s.sensor, s.index, s.ch1, s.ch2, s.ch3)
        for s in data.samples
    ]

    insert_sql = """
        INSERT INTO samples (session_id, sensor, sample_index, ch1, ch2, ch3)
        VALUES %s
        ON CONFLICT DO NOTHING;
    """
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                execute_values(cur, insert_sql, rows, template=None)
            conn.commit()
        return {"ok": True}
    except Exception as exc:
        logger.exception("Database insert failed")
        raise HTTPException(status_code=500, detail="Database error")
