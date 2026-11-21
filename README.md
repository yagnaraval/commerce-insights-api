# Commerce Insights API
> **Author & Attribution**
> **Name:** Yagna Raval · **LinkedIn:** https://www.linkedin.com/in/yagna-raval/ · **GitHub:** https://github.com/yagnaraval

# Purchase Recorder + Return-Risk Model

Django / DRF service that (A) records purchases under strict transactional rules and (B) imports
historical orders, serves reporting endpoints and a scikit-learn return-risk prediction.

## Problem statement
A small shop sells two items with limited stock and one add-on. A payment provider may retry the
same charge, two people may race for the last unit, and old orders arrive as a messy CSV. We need
correct, idempotent purchase recording (no half-written state) and a re-runnable import that feeds
reporting and a simple model estimating whether a new order will be returned.

## Architecture
```
 HTTP ──► views.py (parse/shape JSON) ──► services/purchases.py ──► models (SQLite)
                │                              (all rules, 1 transaction)   ▲
                │                                                           │
                ├──► history.py (filter / paginate / GROUP BY stats) ───────┤
                │                                                           │
 CLI ──► import_history ──► services/importer.py ──► validation.py ─────────┤
 CLI ──► train_return_model ──► ml/training.py ──► models/*.joblib, metrics.json
 HTTP ─► ml/predict.py ──► ml/registry.py (load once, reload on file change)
                         └► ml/features.py (shared by train + serve)
```
Layers: `views` (thin) → `services`/`history`/`ml` (logic) → `models`. `validation.py` and
`ml/features.py` are pure and DB-free. Errors are a typed hierarchy (`errors.py`) mapped to
`{"error": code}` in one place (`api_errors.py`).

### Key design trade-offs
- **One service, one transaction.** `record_purchase` evaluates rules in the specified order inside
  `atomic()`; a `DomainError` rolls everything back, so "sold out never leaves a customer" holds
  by construction. Stock is decremented with a conditional `UPDATE ... WHERE remaining > 0`, safe
  without row locks (SQLite) and on Postgres.
- **Idempotency** via the unique `external_charge_id`; a lost race is caught as `IntegrityError`
  in an inner savepoint and turned into a replay.
- **DB backstop** `UNIQUE(customer, item)` backs the duplicate rule.
- **Import streams** rows in batches of 500: one `IN` query + one `bulk_create` per batch, inside a
  single transaction. Re-runs skip existing `order_id`s.
- **Stats** are a single `GROUP BY` (`Count`, `Sum`, filtered `Count`) – no Python loops.
- **Model serving** loads the joblib once and re-checks only `stat()` (mtime/size) per request, so
  retraining in another process is picked up without a restart. Artifacts are written atomically.
- Feature code is shared by training and serving to avoid train/serve skew.

## Quickstart (Python 3.12+)
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate                               # setup: schema + seed (lamp, stool, cushion)
python manage.py import_history data/order_history.csv # 1) import first
python manage.py train_return_model                    # 2) then train (needs imported history)
python manage.py runserver                             # dev server
python manage.py test                                  # tests
```
`make all` runs install → migrate → import → train → test. `docker build -t shop . && docker run -p 8000:8000 shop` also works.
Config is via env vars (see `.env.example`).

### Example usage
```bash
curl -X POST localhost:8000/api/purchases/ -H 'Content-Type: application/json' \
  -d '{"email":"Ann@Example.com","sku":"lamp","externalChargeId":"ch_lamp00001"}'
curl localhost:8000/api/catalog/lamp
curl localhost:8000/api/outbox
curl 'localhost:8000/api/history/?sku=stool&channel=web&from=2026-05-01&to=2026-05-31&page=1'
curl 'localhost:8000/api/history/stats?from=2026-05-01&to=2026-05-31'
curl -X POST localhost:8000/api/predictions/return-risk -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","sku":"stool","extraCode":"cushion","channel":"app","createdAt":"2026-07-01T10:00:00Z"}'
```
Stats response shape: `{"lamp": {"orders": 43, "revenueCents": 344000, "returnRate": 0.28}, "stool": {...}}`
(every catalogue sku appears; `returnRate` is `null` when it has no orders). Purchase body missing a
required field → `400 {"error":"missing_fields"}`.

### Decisions on points the brief leaves open
- Prediction validates input (400) before checking for a model (503).
- `duplicate_order_id` counts any earlier line with the same id, even if that line was itself rejected.
- Charge ids are validated first, before replay lookup; they are not trimmed.
- Prediction errors other than `unknown_item`: `extra_not_allowed`, `bad_channel`, `bad_date`, `invalid_email`.

## How the expected test numbers were derived
`order_history.csv` has 122 rows: 112 import, 10 rejected (duplicate_order_id 2, invalid_email 2,
unknown_sku 1, extra_not_allowed 1, bad_total 1, bad_date 2, bad_returned 1). These and the May 2026
stats (lamp 43 orders / 344 000 / 12 returned; stool 20 / 265 000 / 6) were counted with a separate
throw-away script that does not use the app's code.

## Time spent
Approximately 6 hours, covering requirement analysis, project architecture, implementation, testing, debugging, and documentation.

## AI tools
ChatGPT (GPT-6) was used as a development and review assistant.

- Used for breaking down requirements and reviewing implementation decisions.

- Used for discussing edge cases involving database transactions, idempotency, CSV validation and ML feature consistency.

- Used for reviewing test coverage, identifying potential improvements and refining documentation.

All suggestions were reviewed against the actual requirements before being incorporated.

One improvement retained after review was the use of a conditional database stock update instead of relying exclusively on an application-level stock check, providing stronger protection against concurrent purchases.

## What I'd improve
- **10 M history rows:** the importer's `seen` set is O(n) memory – move to a staging table with a
  unique index (`INSERT … ON CONFLICT DO NOTHING`) and `COPY`; move to Postgres; partition
  `HistoricalOrder` by month; keep the `(sku, created_at)` index for stats or pre-aggregate daily rollups.
- Paginate with keyset (`created_at, order_id`) instead of `OFFSET`/`COUNT(*)`.
- Training would stream features, compute `prior_orders` with a window function in SQL, and run as a
  job writing versioned artifacts to object storage with a registry pointer.
- A real outbox relay worker; `select_for_update` + Postgres for stronger concurrency tests; load tests.
- Auth/rate limiting on the API; OpenAPI schema.
