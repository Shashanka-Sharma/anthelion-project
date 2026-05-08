# DECISIONS.md

## What I Built and Why

### Agent Framework: Google ADK
I used Google ADK (Agent Development Kit) as the agent framework. ADK provides session management, a tool execution loop, async streaming, and `DatabaseSessionService` for persistent sessions — exactly what a production agent platform needs without building it from scratch. The project already scaffolded ADK; I extended it with a PostgreSQL-backed session service and four SQL-backed data tools.

**Tradeoff:** ADK is opinionated about Gemini as the model backend. LangChain or a raw API integration would offer more model flexibility, but ADK's built-in observability hooks (event streaming, session replay) and `DatabaseSessionService` are production-appropriate out of the box.

---

### Data Storage: PostgreSQL

The datasets total ~162 MB across 2.95M rows. In-memory pandas would work for a demo. I chose PostgreSQL because:

- **Persistence** — data survives container restarts without re-loading from CSV
- **Indexing** — B-tree indexes on `(symbol, date)` and `publish_date` make range queries instant vs O(N) pandas scans over 1.24M rows
- **Session storage** — ADK's `DatabaseSessionService` needs a relational store; using the same PostgreSQL instance avoids a second service
- **Production path** — trivial to swap for Cloud SQL (GCP), RDS (AWS), or Neon without changing application code

**SQLite considered:** SQLite is natively supported by ADK, requires zero extra Docker services, and the data easily fits. I chose PostgreSQL because the project brief explicitly asks for a production-grade design, and a single-writer file-based store isn't that. SQLite would be the right call for a lightweight local-only tool.

**Vector store considered:** Skipped. The agent retrieves headlines by exact date, not semantic similarity. Adding pgvector or Pinecone would triple the infrastructure complexity with no benefit for date-correlated market analysis. It becomes relevant if the agent needs to answer "find news about supply chain issues" rather than "what happened on Jan 15 2016."

---

### Session Service: DatabaseSessionService

ADK's `DatabaseSessionService` persists conversation sessions in PostgreSQL (SQLAlchemy async + asyncpg). Sessions survive restarts, support multi-user access by varying `user_id`, and can be audited from the database. The code falls back to `InMemorySessionService` when `DATABASE_URL_ASYNC` is not set, so local development without PostgreSQL still works.

---

### Data Loading: Dedicated Init Container

A `db_init` service handles the one-time CSV load:

1. Waits for PostgreSQL to pass its healthcheck
2. Checks if data already exists (idempotent — safe to re-run)
3. Creates schema with explicit DDL and indexes
4. Loads large files (prices: 851K rows × 2, headlines: 1.24M rows) via PostgreSQL's `COPY` protocol through psycopg2 `copy_expert` — typically 10–50× faster than parameterized inserts
5. Loads the 79-column fundamentals table via pandas `to_sql` with SQLAlchemy (dynamic schema inference handles the column complexity)
6. Exits with code 0; the `agent` service depends on `service_completed_successfully`

On subsequent `docker compose up` runs, `db_init` checks for existing data and exits immediately, so startup is fast after the first run.

---

### Agent Tools

Four tools expose the datasets:

| Tool | Source Table | Index Used |
|---|---|---|
| `get_price_history(ticker, start, end)` | `prices_adjusted` | `(symbol, date)` |
| `get_news_headlines(date)` | `headlines` | `publish_date` |
| `get_fundamentals(ticker)` | `fundamentals` | `ticker_symbol` |
| `get_company_info(ticker)` | `securities` | `ticker_symbol` (PK) |

Tools use a module-level `SimpleConnectionPool` shared across all calls within a session. This avoids per-call connection overhead and is the minimal form of connection pooling. In production, a PgBouncer sidecar would manage this outside the application process.

Both `prices` (raw) and `prices_adjusted` (split-adjusted) tables are loaded. Tools use `prices_adjusted` for analysis because split-adjusted prices give correct return calculations across the full year. The raw table is available for reference queries.

---

### What I Left Out

| Feature | Rationale |
|---|---|
| Redis / query cache | No repeated hot-path queries in a single-session demo. Production path: cache `get_fundamentals` results (static data) with a 1-hour TTL |
| Message queue | No async agent triggering needed. Production path: Cloud Tasks or Pub/Sub to decouple task submission from agent execution |
| Authentication / API gateway | Out of scope for local demo. Production path: Cloud Run with IAM + API key validation |
| Semantic search on headlines | Date-scoped retrieval is sufficient. Production path: pgvector embeddings for open-ended news queries |
| Monitoring stack | Container stdout + PostgreSQL logs provide observability. Production path: OpenTelemetry → Cloud Trace / Datadog |
| Streaming agent output | ADK supports streaming; demo uses final response for clean stdout output |

---

## Production Evolution Path

### Data Scale

At 10× current volume, the schema holds with no changes. At 100×:
- **Partition** `prices` and `headlines` by year (PostgreSQL declarative partitioning)
- **TimescaleDB** for hypertable compression and time-bucket aggregations on price data
- **Parquet on object storage** (S3/GCS) + DuckDB for batch analytics; keep PostgreSQL for the agent's point lookups

### Concurrent Agents

Current setup: one agent process, one connection pool. For multi-tenant production:
- Replace `SimpleConnectionPool` with **PgBouncer** (transaction-mode pooling as a compose sidecar)
- Add a **task queue** (Celery + Redis, or Cloud Tasks) so tasks are pulled by worker replicas
- Use **Cloud Run** (autoscale on queue depth) or **GKE** for stateless agent workers

### Observability

- Wrap tool calls with **OpenTelemetry spans** to track per-tool latency and error rates
- Stream ADK events (tool calls, token counts, LLM latency) to a structured log sink
- Add `pg_stat_statements` to PostgreSQL for slow-query detection
- Expose a `/health` endpoint from the agent for liveness/readiness probes

### Reliability

- **Read replicas** for tool queries; primary only for session writes
- **Retry with backoff** (tenacity) on tool-level database errors
- **Circuit breaker** around the Gemini API call: fall back to a cached or simplified response rather than timing out
- **Secrets Manager** (GCP/AWS) for `GOOGLE_API_KEY` and database credentials instead of env vars

### GCP Deployment

| Compose service | GCP equivalent |
|---|---|
| `postgres` | Cloud SQL (PostgreSQL 16), private IP, automated backups |
| `db_init` | Cloud Run Job, triggered once per deploy, idempotent |
| `agent` | Cloud Run (stateless) or GKE Autopilot (if stateful sessions needed) |
| Volume mount for data | Cloud Storage → Dataflow pipeline → Cloud SQL import |
| Docker image | Artifact Registry |

The Docker Compose architecture maps 1:1 to this, making the local-to-cloud translation a configuration change, not a rewrite.
