
# Commerce Insights API

## Technical Architecture & Implementation Guide

A technical overview of the application's architecture, core modules, data processing workflows, machine learning pipeline, and engineering decisions.

---

## 1. Architecture Overview

Commerce Insights API is a Django REST application combining transactional purchase processing, historical order analytics, CSV ingestion, and machine-learning-based return-risk prediction.

The application follows a modular architecture with clearly separated responsibilities.

| Layer | Responsibility |
|---|---|
| API Layer | HTTP request handling, validation, routing, and response formatting |
| Service Layer | Purchase processing and historical data ingestion |
| Data Layer | Database models, constraints, and persistence |
| Analytics Layer | Historical filtering, pagination, and aggregation |
| ML Layer | Feature engineering, model training, artifact management, and prediction |
| Management Commands | Operational workflows for data import and model training |

### Core Data Flows

**Purchase processing**

`HTTP Request → PurchaseView → Purchase Service → Database → API Response`

**Historical data ingestion**

`CSV File → Import Command → Import Service → Validation → HistoricalOrder`

**Model training**

`HistoricalOrder → Feature Engineering → Logistic Regression → Model Artifact`

**Return-risk prediction**

`HTTP Request → Prediction View → Feature Engineering → Model Registry → Prediction`

---

## 2. Application Configuration

### `config/settings.py`

**Purpose:** Centralizes application configuration and environment-specific settings.

Key configuration includes:

- `DJANGO_SECRET_KEY` – Django secret configuration.
- `DATABASE_PATH` – database location.
- `MODEL_DIR` – trained model artifact directory.
- `LOG_LEVEL` – application logging level.
- JSON-only Django REST Framework configuration.
- Custom API exception handling.

The `_flag()` helper handles environment-driven boolean settings.

The model registry resolves `MODEL_DIR` dynamically, allowing tests to override artifact locations without depending on a fixed directory.

**Design considerations:**

- Environment-driven configuration keeps deployment-specific values outside application code.
- Unused admin, authentication, and session components are excluded.
- `APPEND_SLASH=False` supports the API's explicit URL conventions.

### `config/urls.py`, `config/wsgi.py`, `manage.py`

**Purpose:** Provide the application's entry points.

- `config/urls.py` mounts application routes under `/api/`.
- `config/wsgi.py` exposes the WSGI application.
- `manage.py` provides access to Django management commands.

---

## 3. Data Models & Persistence

### `shop/models.py`

**Purpose:** Defines the database schema and persistence constraints.

| Model | Responsibility |
|---|---|
| `Item` | Product information, pricing, and available stock |
| `Extra` | Optional product additions and eligibility |
| `Customer` | Customer identity using normalized email |
| `Order` | Transactional purchase records |
| `OutboxMessage` | Persisted events associated with successful purchases |
| `HistoricalOrder` | Imported historical purchase records |

### Data Integrity

Important business invariants are enforced at the database level:

- Unique customer email prevents duplicate customer records.
- Unique external charge ID provides purchase idempotency.
- Unique customer-item combinations prevent duplicate purchases.
- Foreign-key constraints maintain relational integrity.

### Historical Data Isolation

`HistoricalOrder` is separate from the live `Order` model.

Historical imports therefore do not modify current inventory or interfere with live purchase processing.

### Money Representation

All monetary values are stored as integer cents rather than floating-point numbers, avoiding precision and rounding issues.

### Database Indexing

Indexes support common query patterns:

- `(created_at, order_id)` for historical ordering and pagination.
- `(sku, created_at)` for SKU-based historical filtering.

### Database Migrations

- `shop/migrations/0001_initial.py` defines the initial database schema.
- `shop/migrations/0002_seed_catalog.py` initializes the product catalog.

The initial catalog contains:

| Product | Price | Initial Stock |
|---|---:|---:|
| Maple lamp | 8,000 cents | 2 |
| Cedar stool | 12,000 cents | 1 |
| Cushion | 2,500 cents | Stool only |

The `seed()` migration uses `update_or_create()` to make catalog initialization repeatable.

---

## 4. Validation & Error Handling

### `shop/validation.py`

**Purpose:** Provides shared normalization and validation utilities across API requests, CSV imports, and prediction workflows.

Key functions:

- `normalise_email()` – trims and lowercases email addresses.
- `normalise_token()` – normalizes identifier values.
- `normalise_optional()` – handles optional input values.
- `is_valid_charge_id()` – validates external charge identifiers.
- `is_valid_email()` – performs application-level email validation.
- `parse_iso_datetime()` – validates timezone-aware ISO timestamps and converts them to UTC.
- `parse_bool()` – handles supported boolean representations.
- `parse_query_date()` – validates strict `YYYY-MM-DD` query parameters.

The datetime parser combines a strict format check with Python's `datetime.fromisoformat()` to reject date-only, timezone-naive, and invalid calendar dates.

Shared validators keep API, importer, and prediction behaviour consistent.

### `shop/errors.py`

**Purpose:** Defines the application's domain exception hierarchy.

| Exception | HTTP Status |
|---|---:|
| `SoldOut` | 409 |
| `DuplicatePurchase` | 409 |
| `InvalidChargeId` | 400 |
| `UnknownItem` | 400 |
| `ExtraNotAllowed` | 400 |
| `MissingFields` | 400 |
| `InvalidInput` | 400 |
| `ModelNotTrained` | 503 |

Services raise typed domain exceptions without embedding HTTP response logic in business operations.

### `shop/api_errors.py`

**Purpose:** Centralizes Django REST Framework exception handling.

It maintains a consistent error response structure:

`{"error": "error_code"}`

It also handles malformed JSON and not-found responses.

---

## 5. Transactional Purchase Processing

### `shop/services/purchases.py`

**Purpose:** Implements the core purchase workflow, including idempotency, inventory protection, customer creation, order persistence, and outbox generation.

### Purchase Processing Flow

1. Validate the external charge identifier.
2. Check whether the charge ID already exists.
3. If it exists, return the original order without modifying inventory or creating additional records.
4. Resolve the requested catalog item.
5. Validate whether the optional extra is allowed.
6. Check inventory availability.
7. Check whether the customer has already purchased the item.
8. Commit the purchase and related records inside a database transaction.

### Key Functions

**`record_purchase()`**

Coordinates validation, replay detection, inventory checks, duplicate detection, and transaction execution.

**`_find_replay()`**

Retrieves an existing order using its external charge ID.

Repeated requests return the original order, even when subsequent requests contain different customer or product details.

**`_commit_purchase()`**

Performs the actual purchase write operations:

- Conditionally decrements inventory.
- Creates or retrieves the customer.
- Creates the order with the correct total.
- Creates the associated outbox messages.

### Atomic Inventory Updates

The implementation uses a conditional database update requiring:

`remaining > 0`

The stock decrement happens only when inventory is available, preventing negative inventory during concurrent purchase attempts.

### Transactional Outbox

Outbox messages are persisted in the same database transaction as the purchase.

- `account_created` is recorded when a new customer is created.
- `purchase_recorded` is recorded for a successful purchase.

This ensures that outbox records cannot survive a rolled-back purchase.

### Concurrency Handling

Database integrity constraints protect critical invariants.

A competing request that encounters an integrity error uses a savepoint to roll back partial writes. If another request has already committed the same charge ID, the original order is returned as a replay.

**Scaling considerations:**

For a production deployment with higher concurrency, PostgreSQL, explicit row locking, and a dedicated outbox relay worker would provide a stronger foundation for distributed processing.

---

## 6. API Layer

### `shop/views.py` and `shop/urls.py`

**Purpose:** Provide HTTP adapters between incoming requests and application services.

The API layer keeps business logic outside request handlers.

| Endpoint | Responsibility |
|---|---|
| `POST /api/purchases/` | Record a purchase or return an existing order |
| `GET /api/catalog/<sku>` | Retrieve catalog information and remaining stock |
| `GET /api/outbox` | Retrieve transactional outbox records |
| `GET /api/history/` | Retrieve filtered and paginated historical orders |
| `GET /api/history/stats` | Retrieve historical sales and return statistics |
| `POST /api/predictions/return-risk` | Predict return probability |

### Request and Response Handling

- Purchase responses distinguish new purchases from replays using HTTP status codes.
- Request payloads are validated before reaching the service layer.
- Order responses are formatted consistently through `order_payload`.
- The outbox endpoint returns records in creation order.

The views remain intentionally thin, delegating business behaviour to the appropriate service or analytics module.

---

## 7. Catalog Abstraction

### `shop/catalog.py`

**Purpose:** Provides the `PriceCatalog` abstraction for retrieving catalog information and validating sellable product combinations.

Key functionality:

- Loads catalog items and their permitted extras.
- Resolves item pricing.
- Calculates purchase totals.
- Returns `None` when a requested combination is not sellable.

This keeps pricing and product-extra relationships centralized instead of duplicating catalog rules across purchases, imports, and predictions.

---

## 8. Historical Data Ingestion

### `shop/services/importer.py`

**Purpose:** Implements a repeatable CSV ingestion pipeline for historical orders.

The importer processes records in batches and tracks the first validation failure for each rejected row.

### Import Workflow

1. Read CSV rows incrementally.
2. Detect duplicate order IDs within the input file.
3. Normalize and validate each row.
4. Group valid records into batches of 500.
5. Query the database for existing order IDs.
6. Skip records that already exist.
7. Bulk-insert new historical records.
8. Generate a JSON summary.

### Validation Rules

Validation is performed in a defined order:

1. `duplicate_order_id`
2. `invalid_email`
3. `unknown_sku`
4. `extra_not_allowed`
5. `bad_total`
6. `bad_date`
7. `bad_returned`
8. `bad_channel`

### Input Normalization

The importer handles:

- UTF-8 BOM files.
- Whitespace and case normalization.
- Empty optional extras.
- Timezone conversion to UTC.
- Supported boolean representations.
- CSV files opened with `newline=""`.

### Idempotency & Performance

Existing records are skipped rather than duplicated, making repeated imports safe.

The implementation uses batch queries and `bulk_create()` instead of querying and inserting individual records one at a time.

The file-level `seen` set requires memory proportional to the number of unique IDs. For substantially larger datasets, a staging table with a unique index and database conflict handling would be a suitable alternative.

### `shop/management/commands/import_history.py`

Provides the command-line interface:

`python manage.py import_history path/to/order_history.csv`

Expected file and input errors are converted into Django `CommandError` exceptions.

---

## 9. Historical Analytics

### `shop/history.py`

**Purpose:** Implements historical filtering, pagination, and reporting functionality.

### Filtering

`filter_history()` supports:

- SKU.
- Sales channel.
- Start date.
- End date.

Date boundaries are interpreted in UTC.

The end date is inclusive, implemented using an exclusive upper boundary at midnight of the following day.

### Pagination

`paginate()` returns 10 records per page, ordered by:

`created_at, order_id`

This provides deterministic ordering across pages.

### Aggregation

`sku_stats()` calculates:

- Order count.
- Revenue in cents.
- Returned order count.
- Return rate.

Aggregation uses database-level `GROUP BY`, `Count`, `Sum`, and filtered counts rather than loading all historical records into application memory.

### Return-Rate Calculation

`_rate()` uses Python `Decimal` with half-up rounding to two decimal places.

When no orders exist, the return rate is `null`.

### Scaling Considerations

For larger historical datasets, possible improvements include:

- Keyset pagination.
- Precomputed reporting tables.
- Periodic aggregation jobs.
- Additional indexes based on actual query patterns.

---

## 10. Machine Learning Pipeline

The ML implementation separates feature engineering, training, artifact management, and prediction.

### `shop/ml/features.py`

**Purpose:** Defines the shared feature representation used by model training and inference.

The six model features are:

| Feature | Description |
|---|---|
| `is_stool` | Whether the SKU is a stool |
| `has_extra` | Whether an extra is included |
| `channel_app` | Whether the channel is app |
| `channel_web` | Whether the channel is web |
| `hour` | UTC hour of the order |
| `prior_orders` | Earlier historical orders from the same email |

Store is represented by the baseline when both channel indicators are zero.

**Key functions:**

- `feature_vector()` constructs individual feature vectors.
- `build_training_set()` generates the training dataset and calculates historical prior-order counts.

Prior-order counts are computed using grouped records and binary search.

Only orders with a strictly earlier timestamp contribute to `prior_orders`.

Using shared feature-generation logic reduces the risk of training-serving skew.

### `shop/ml/training.py`

**Purpose:** Implements model training, evaluation, and artifact persistence.

**Training configuration:**

- Algorithm: `LogisticRegression`
- `max_iter=1000`
- `random_state=0`
- Train/test split: 80/20
- Stratification enabled
- Split `random_state=0`

The model uses exactly the six features defined in the shared feature module.

### Model Evaluation

Training calculates:

- Holdout accuracy.
- Majority-class baseline accuracy.
- Total dataset rows.
- Training split row count.
- Model version.
- Feature names.

The metrics are persisted in `models/metrics.json`.

Model performance is reported alongside the baseline to provide context about predictive performance on the available dataset.

### Artifact Persistence

`_atomic_write()` writes temporary artifacts and uses `os.replace()` to publish completed files.

This prevents consumers from reading partially written model artifacts.

Each training run generates a new model version using a UTC timestamp and random suffix.

### `shop/management/commands/train_return_model.py`

Provides the training interface:

`python manage.py train_return_model`

---

## 11. Model Registry & Prediction

### `shop/ml/registry.py`

**Purpose:** Manages model loading and process-level caching.

The registry:

- Loads the trained model using `joblib`.
- Avoids deserializing the model on every request.
- Checks artifact path, modification time, and file size.
- Reloads the model when the artifact changes.
- Uses thread-safe access to the cached model.

This reduces prediction overhead while allowing newly trained artifacts to be picked up without restarting the application.

Model artifacts must come from trusted sources because joblib uses Python serialization.

### `shop/ml/predict.py`

**Purpose:** Implements the return-risk prediction workflow.

### Prediction Flow

1. Validate the request fields.
2. Resolve the requested SKU and optional extra.
3. Check model availability.
4. Calculate the customer's prior historical orders.
5. Build the shared feature vector.
6. Obtain the predicted return probability.
7. Return the probability and model version.

### Prediction Response

The response includes:

- `probability` – predicted return probability, rounded to three decimal places.
- `modelVersion` – identifier of the model used.

The model's class labels are inspected through `classes_` rather than assuming a fixed probability-column position.

If no trained model is available, the API returns HTTP 503.

---

## 12. Automated Testing

The test suite is organized around the application's major workflows.

### `shop/tests/test_purchases.py`

**Coverage:**

- New customer and order creation.
- Email normalization.
- Optional extra pricing.
- Outbox event ordering.
- Sold-out behaviour.
- Duplicate purchase prevention.
- Invalid item and extra handling.
- Invalid charge identifiers.
- Replay behaviour with changed request details.
- Transaction rollback and side-effect prevention.
- Purchase API status codes and catalog responses.

The tests compare stock, customers, orders, and outbox state before and after failed operations to verify that transactions do not leave partial writes.

### `shop/tests/test_history.py`

**Coverage:**

- Historical CSV ingestion.
- Validation and rejection reasons.
- Duplicate detection.
- Idempotent repeated imports.
- Email, SKU, and channel normalization.
- Pagination and filtering.
- Inclusive date boundaries.
- Historical statistics.
- Return-rate calculation.
- Database-level aggregation query behaviour.

Expected statistics are independently checked against manually calculated values.

### `shop/tests/test_prediction.py`

**Coverage:**

- Behaviour before model training.
- Successful predictions.
- Probability formatting and model version.
- Metrics artifact generation.
- Version changes after retraining.
- Model registry caching.
- Invalid prediction inputs.
- Prior-order calculation.
- Training behaviour when data is insufficient.

The tests use isolated model directories and reset registry state to avoid depending on previously generated artifacts.

---

## 13. Supporting Project Files

| File | Responsibility |
|---|---|
| `requirements.txt` | Python dependencies |
| `Makefile` | Common development commands |
| `Dockerfile` | Container image definition |
| `docker-entrypoint.sh` | Container initialization |
| `.env.example` | Environment configuration template |
| `.gitignore` | Excludes local and generated files |
| `.dockerignore` | Excludes unnecessary Docker build context |
| `data/order_history.csv` | Historical input dataset |

The project supports local development and containerized execution, with migrations and operational commands integrated into the setup workflow.

---

## 14. Engineering Considerations & Future Improvements

The implementation prioritizes transactional correctness, predictable data processing, and separation of responsibilities.

Potential improvements for a larger production deployment include:

- **Database:** Move from SQLite to PostgreSQL for higher concurrent write throughput and stronger operational capabilities.
- **Outbox processing:** Introduce a dedicated background relay worker for asynchronous event delivery.
- **Historical ingestion:** Use database staging tables for very large imports.
- **Analytics:** Introduce materialized reporting tables for frequently requested aggregates.
- **Pagination:** Use keyset pagination for large historical datasets.
- **Observability:** Add structured logging, tracing, and operational metrics.
- **ML lifecycle:** Add model monitoring, artifact lineage, and periodic retraining workflows.
- **Deployment:** Replace Django's development server with a production-grade WSGI server and configure appropriate health checks.

These improvements can be introduced independently as application traffic, data volume, and operational requirements grow.

---

## 15. Project Quality & Verification

The implementation includes automated coverage across transactional workflows, historical data processing, analytics, and ML prediction.

The verification process includes:

- Automated Django test suite.
- Database migration checks.
- Historical import and idempotency checks.
- Model training and artifact generation.
- Prediction API verification.
- Docker-based execution.

The application keeps transactional operations, historical processing, and ML workflows separated while sharing validation and feature-generation logic where consistency is important.
