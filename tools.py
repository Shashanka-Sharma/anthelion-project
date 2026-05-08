"""
SQL-backed agent tools. Each function queries PostgreSQL and returns a
JSON-serializable dict for the LLM to reason over.
"""
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://anthelion:anthelion@localhost:5432/anthelion"
)

_pool: psycopg2.pool.SimpleConnectionPool | None = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, DATABASE_URL)
    return _pool


@contextmanager
def _db():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)


def get_price_history(ticker: str, start_date: str, end_date: str) -> dict:
    """
    Get daily split-adjusted stock prices (OHLCV) for a NYSE ticker.
    Price data is available for 2016-01-04 through 2016-12-30 only.

    Args:
        ticker: NYSE ticker symbol, e.g. 'AAPL', 'MSFT', 'GOOGL'
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        Dict containing per-day OHLCV records and a summary with start/end close,
        min/max close, percentage change, and average daily volume.
    """
    with _db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT date::text, open, close, high, low, volume
                FROM prices_adjusted
                WHERE symbol = %s AND date BETWEEN %s AND %s
                ORDER BY date
                """,
                (ticker.upper(), start_date, end_date),
            )
            rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return {"error": f"No price data for {ticker} in {start_date} to {end_date}"}

    closes = [r["close"] for r in rows]
    return {
        "ticker": ticker.upper(),
        "period": f"{start_date} to {end_date}",
        "trading_days": len(rows),
        "prices": rows,
        "summary": {
            "start_close": round(closes[0], 2),
            "end_close": round(closes[-1], 2),
            "min_close": round(min(closes), 2),
            "max_close": round(max(closes), 2),
            "pct_change": round((closes[-1] - closes[0]) / closes[0] * 100, 2),
            "avg_daily_volume": int(sum(r["volume"] for r in rows) / len(rows)),
        },
    }


def get_news_headlines(date: str) -> dict:
    """
    Get ABC News headlines published on a specific date.
    Data spans 2003-02-19 to 2021-12-31.

    Args:
        date: Date in YYYY-MM-DD format

    Returns:
        Dict with up to 30 headlines for that date and the total headline count.
    """
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT headline_text FROM headlines WHERE publish_date = %s LIMIT 30",
                (date,),
            )
            headlines = [row[0] for row in cur.fetchall()]
            cur.execute(
                "SELECT COUNT(*) FROM headlines WHERE publish_date = %s", (date,)
            )
            total = cur.fetchone()[0]

    if not headlines:
        return {"error": f"No headlines found for {date}"}

    return {
        "date": date,
        "total_headlines": total,
        "headlines_shown": len(headlines),
        "headlines": headlines,
    }


def get_fundamentals(ticker: str) -> dict:
    """
    Get the most recent annual financial fundamentals for a company.
    Covers ~448 S&P 500 companies, data from 2003–2017.

    Args:
        ticker: NYSE ticker symbol

    Returns:
        Dict with key financial metrics: revenue, net income, EPS, margins, ROE,
        assets, liabilities, equity, cash, R&D spend, capex, and long-term debt.
    """
    KEY_METRICS = [
        "ticker_symbol", "period_ending",
        "total_revenue", "gross_profit", "operating_income", "net_income",
        "earnings_per_share", "gross_margin", "profit_margin", "after_tax_roe",
        "total_assets", "total_liabilities", "total_equity",
        "cash_and_cash_equivalents", "research_and_development",
        "long_term_debt", "capital_expenditures",
    ]
    with _db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'fundamentals'"
            )
            existing = {r["column_name"] for r in cur.fetchall()}
            cols = [c for c in KEY_METRICS if c in existing]
            if not cols:
                return {"error": "Fundamentals table not yet loaded"}

            cur.execute(
                f"SELECT {', '.join(cols)} FROM fundamentals "
                "WHERE UPPER(ticker_symbol) = %s "
                "ORDER BY period_ending DESC LIMIT 1",
                (ticker.upper(),),
            )
            row = cur.fetchone()

    if not row:
        return {"error": f"No fundamentals data found for {ticker}"}

    result = dict(row)
    if result.get("period_ending"):
        result["period_ending"] = str(result["period_ending"])[:10]
    return result


def get_company_info(ticker: str) -> dict:
    """
    Get S&P 500 company metadata: name, GICS sector, sub-industry, headquarters.

    Args:
        ticker: NYSE ticker symbol

    Returns:
        Dict with company name, GICS sector, sub-industry, headquarters address, and CIK.
    """
    with _db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT ticker_symbol, security, gics_sector, gics_sub_industry, "
                "address_of_headquarters, cik "
                "FROM securities WHERE UPPER(ticker_symbol) = %s",
                (ticker.upper(),),
            )
            row = cur.fetchone()

    if not row:
        return {"error": f"No company info found for {ticker}"}

    return dict(row)
