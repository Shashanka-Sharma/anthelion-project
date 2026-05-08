"""
One-shot database initializer. Loads all CSV datasets into PostgreSQL.
Idempotent: exits immediately if the database is already populated.
"""
import io
import logging
import os
import re
import time

import pandas as pd
import psycopg2
import psycopg2.extras
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
SYNC_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
NYSE_DIR = os.path.join(DATA_DIR, "nyse_data")

DDL = """
CREATE TABLE IF NOT EXISTS securities (
    ticker_symbol           VARCHAR(20) PRIMARY KEY,
    security                TEXT,
    sec_filings             TEXT,
    gics_sector             TEXT,
    gics_sub_industry       TEXT,
    address_of_headquarters TEXT,
    date_first_added        DATE,
    cik                     VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS prices (
    date   DATE        NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    open   DOUBLE PRECISION,
    close  DOUBLE PRECISION,
    low    DOUBLE PRECISION,
    high   DOUBLE PRECISION,
    volume BIGINT,
    PRIMARY KEY (date, symbol)
);

CREATE TABLE IF NOT EXISTS prices_adjusted (
    date   DATE        NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    open   DOUBLE PRECISION,
    close  DOUBLE PRECISION,
    low    DOUBLE PRECISION,
    high   DOUBLE PRECISION,
    volume BIGINT,
    PRIMARY KEY (date, symbol)
);

CREATE TABLE IF NOT EXISTS headlines (
    id           SERIAL PRIMARY KEY,
    publish_date DATE NOT NULL,
    headline_text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_headlines_date  ON headlines (publish_date);
CREATE INDEX IF NOT EXISTS idx_prices_sym_date ON prices (symbol, date);
CREATE INDEX IF NOT EXISTS idx_padj_sym_date   ON prices_adjusted (symbol, date);
"""


def sanitize_col(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_") or "col"


def is_loaded(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='headlines')"
        )
        if not cur.fetchone()[0]:
            return False
        cur.execute("SELECT COUNT(*) FROM headlines LIMIT 1")
        return cur.fetchone()[0] > 0


def copy_df(conn, df: pd.DataFrame, table: str, columns: list[str]) -> None:
    buf = io.StringIO()
    df[columns].to_csv(buf, index=False, header=False)
    buf.seek(0)
    with conn.cursor() as cur:
        cur.copy_expert(
            f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT CSV, NULL '')",
            buf,
        )
    conn.commit()
    log.info("  %s: loaded %d rows", table, len(df))


def load_prices(conn) -> None:
    for filename, table in [
        ("prices.csv", "prices"),
        ("prices-split-adjusted.csv", "prices_adjusted"),
    ]:
        log.info("Loading %s → %s ...", filename, table)
        df = pd.read_csv(os.path.join(NYSE_DIR, filename))
        df["date"] = pd.to_datetime(df["date"], format="mixed").dt.date.astype(str)
        df["volume"] = df["volume"].fillna(0).astype(int)
        copy_df(conn, df, table, ["date", "symbol", "open", "close", "low", "high", "volume"])


def load_headlines(conn) -> None:
    log.info("Loading headlines (~1.2M rows) ...")
    df = pd.read_csv(
        os.path.join(DATA_DIR, "abcnews-date-text.csv"),
        dtype={"publish_date": str},
    )
    df["publish_date"] = pd.to_datetime(df["publish_date"], format="%Y%m%d").dt.date.astype(str)
    df["headline_text"] = df["headline_text"].astype(str)
    copy_df(conn, df, "headlines", ["publish_date", "headline_text"])


def load_securities(conn) -> None:
    log.info("Loading securities ...")
    df = pd.read_csv(os.path.join(NYSE_DIR, "securities.csv"))
    df.columns = [sanitize_col(c) for c in df.columns]
    df["date_first_added"] = pd.to_datetime(df["date_first_added"], errors="coerce").dt.date
    df["cik"] = df["cik"].astype(str).str.replace(r"\.0$", "", regex=True)

    cols = [
        "ticker_symbol", "security", "sec_filings", "gics_sector",
        "gics_sub_industry", "address_of_headquarters", "date_first_added", "cik",
    ]
    rows = [
        tuple(None if (v != v or str(v) in ("nan", "NaT", "None")) else v for v in row)
        for row in df[cols].itertuples(index=False)
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO securities ({', '.join(cols)}) VALUES %s ON CONFLICT DO NOTHING",
            rows,
        )
    conn.commit()
    log.info("  securities: loaded %d rows", len(rows))


def load_fundamentals() -> None:
    log.info("Loading fundamentals (79 columns) ...")
    engine = create_engine(SYNC_URL, echo=False)
    df = pd.read_csv(os.path.join(NYSE_DIR, "fundamentals.csv"), index_col=0)
    df.columns = [sanitize_col(c) for c in df.columns]
    df["period_ending"] = pd.to_datetime(df["period_ending"], errors="coerce")
    df.to_sql("fundamentals", engine, if_exists="replace", index=False, chunksize=500)
    with engine.connect() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_fund_ticker ON fundamentals (ticker_symbol)"))
        conn.commit()
    log.info("  fundamentals: loaded %d rows", len(df))


def main() -> None:
    log.info("Connecting to database ...")
    conn = psycopg2.connect(DATABASE_URL)

    if is_loaded(conn):
        log.info("Database already initialized — skipping.")
        conn.close()
        return

    log.info("Creating schema ...")
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    t0 = time.time()
    load_securities(conn)
    load_prices(conn)
    load_headlines(conn)
    conn.close()

    load_fundamentals()

    log.info("Initialization complete in %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
