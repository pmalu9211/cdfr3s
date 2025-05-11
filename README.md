# Webhook Delivery Service

This project implements a robust backend system designed to function as a reliable webhook delivery service. It is built using Python with FastAPI, Celery, Redis, and PostgreSQL, and is fully containerized using Docker and orchestrated with Docker Compose.

The service is capable of ingesting incoming webhook payloads, queuing them for asynchronous processing, attempting delivery to subscribed target URLs with retry logic for failures, and providing visibility into the delivery status. It also includes bonus features for payload signature verification and event type filtering for enhanced security and efficiency.

## Project Structure

```
.
├── docker-compose.yml          # Defines and orchestrates services
├── Dockerfile                  # Defines the application image
├── init.sql                    # Database schema initialization script
├── requirements.txt            # Python dependencies
├── sha256Generator.py          # Script for generating webhook signatures
└── app/
    ├── __init__.py
    ├── cache.py                # Redis cache operations
    ├── celery_app.py           # Celery app instance and config
    ├── config.py               # Configuration loading
    ├── crud.py                 # Database operations (CRUD)
    ├── database.py             # Database session setup
    ├── main.py                 # FastAPI app, API endpoints (Ingestion, Status)
    ├── models.py               # SQLAlchemy ORM models
    ├── schemas.py              # Pydantic models (Request/Response schemas)
    └── tasks.py                # Celery tasks (Delivery, Cleanup)
└── tests/
    ├── conftest.py
    ├── test_ingestion.py
    ├── test_status_logs.py
    ├── test_subscriptions.py
    └── test_tasks.py
```

> **Note:** The `tests/` directory contains test files, but tests were not fully completed.

## Core Requirements Implemented

- **Subscription Management**: CRUD operations for webhook subscriptions (`/subscriptions`).
- **Webhook Ingestion**: Accepts payloads via POST to `/ingest/{subscription_id}`, queues for async processing, returns 202 Accepted.
- **Asynchronous Delivery Processing**: Background Celery workers process queued tasks.
- **Retry Mechanism**: Exponential backoff for failed deliveries (configured via environment variables).
- **Delivery Logging**: Logs status and details of each attempt to the database.
- **Log Retention**: Background task (Celery Beat) for periodic log cleanup.
- **Status/Analytics Endpoints**: Retrieve webhook status (`/status/{webhook_id}`) and subscription logs (`/subscriptions/{subscription_id}/logs`, `/logs/`).
- **Caching**: Redis caching for subscription details during ingestion and processing.

## Bonus Points Implemented

- **Payload Signature Verification**: Verifies `X-Hub-Signature-256` header using HMAC-SHA256 and the subscription secret. Rejects requests with invalid signatures.
- **Event Type Filtering**: Allows subscriptions to specify `event_types`. Ingestion endpoint filters webhooks based on the `event_type` field in the payload.

## Technical Stack

- **Language**: Python
- **Web Framework**: FastAPI
- **Database**: PostgreSQL (containerized)
- **Asynchronous Tasks / Queuing / Caching**: Celery and Redis (containerized)
- **Containerisation**: Docker and Docker Compose

## Local Setup and Running

These instructions assume you have Docker and Docker Compose installed on your machine.

1. **Clone the repository**:
   ```bash
   git clone https://github.com/pmalu9211/cdfr3s.git
   cd cdfr3s/
   ```

2. **Build the Docker images**:
   ```bash
   docker compose build  # Use 'docker compose' if you have the plugin
   # or
   # docker-compose build  # Use 'docker-compose' for older standalone
   ```

3. **Start the services**:
   ```bash
   docker compose up -d  # Use 'docker compose'
   # or
   # docker-compose up -d  # Use 'docker-compose'
   ```
   This will start the PostgreSQL database, Redis, FastAPI API, Celery Worker, and Celery Beat services in detached mode. The database schema will be initialized automatically on the first run using `init.sql`.

4. **Verify services are running**:
   ```bash
   docker compose ps  # Use 'docker compose'
   # or
   # docker-compose ps  # Use 'docker-compose'
   ```
   All services should be listed with State as `Up`. The `db` service should eventually show `(healthy)`.

5. **Access the API**:
   The FastAPI API is exposed on port 8000 on your local machine. Open your web browser to http://localhost:8000/docs to access the interactive Swagger UI.

6. **To stop the services**:
   ```bash
   docker compose down  # or docker-compose down
   ```

7. **To stop services and remove volumes** (this will delete your database and Redis data):
   ```bash
   docker compose down --volumes  # or docker-compose down --volumes
   ```

## Deployed Application

The application is deployed and accessible at:
- http://segwise.prathamalu.xyz

The Swagger UI is available at:
- http://segwise.prathamalu.xyz/docs

## Architecture Choices

- **FastAPI**: Chosen for its high performance, ease of use, automatic interactive API documentation (Swagger UI), and built-in support for asynchronous operations, which aligns well with the non-blocking nature of webhook ingestion.
- **Celery**: A powerful and mature distributed task queue. Essential for decoupling webhook ingestion from the delivery process, handling retries with configurable strategies, and scheduling background tasks like log cleanup.
- **Redis**: Used as the message broker and backend for Celery, providing efficient queuing and storage of task results/states. Also utilized as a cache for frequently accessed data like subscription details to reduce database load.
- **PostgreSQL**: A robust and reliable relational database. Suitable for storing structured data like subscriptions and the potentially large volume of delivery attempt logs. Provides strong data integrity and supports indexing for efficient querying.
- **Docker and Docker Compose**: Provides a consistent development and deployment environment by containerizing all application components and defining their relationships and configurations in a single file. Enables easy local setup and portability to various hosting environments.

## Database Schema and Indexing

The database schema consists of three main tables:

### `subscriptions`: Stores webhook recipient configurations.
- `id` (UUID PK)
- `target_url` (VARCHAR)
- `secret` (VARCHAR, Optional)
- `created_at` (TIMESTAMP WITH TIME ZONE)
- `updated_at` (TIMESTAMP WITH TIME ZONE)
- `event_types` (TEXT[], Optional)
- **Indexing**: Primary key index on `id`.

### `webhooks`: Stores details of each incoming webhook payload.
- `id` (UUID PK)
- `subscription_id` (UUID FK to subscriptions, ON DELETE CASCADE)
- `payload` (JSONB)
- `event_type` (VARCHAR, Optional)
- `ingested_at` (TIMESTAMP WITH TIME ZONE)
- `status` (VARCHAR)
- **Indexing**: Indexes on `id`, `subscription_id`, and `status`. `subscription_id` is crucial for linking webhooks to subscriptions and querying logs. `status` helps in quickly finding webhooks in specific states (e.g., queued, failed).

### `delivery_attempts`: Logs every attempt to deliver a specific webhook.
- `id` (UUID PK)
- `webhook_id` (UUID FK to webhooks, ON DELETE CASCADE)
- `attempt_number` (INTEGER)
- `attempted_at` (TIMESTAMP WITH TIME ZONE)
- `outcome` (VARCHAR)
- `http_status_code` (INTEGER, Optional)
- `error_details` (TEXT, Optional)
- `next_attempt_at` (TIMESTAMP WITH TIME ZONE, Optional)
- **Indexing**: Indexes on `webhook_id` (for retrieving all attempts for a webhook), `attempted_at` (for ordering logs and retention cleanup), and `outcome`.

Indexing strategies focus on columns used in WHERE clauses, ORDER BY clauses, and foreign key relationships to optimize read performance for API endpoints and background tasks.

## Sample API Endpoints and Usage

You can interact with the API using curl or the Swagger UI at `/docs`.

- **Base URL (Local)**: http://localhost:8000
- **Base URL (Deployed)**: http://segwise.prathamalu.xyz

### 1. Create Subscription

```bash
curl -X POST 'http://localhost:8000/subscriptions/' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "target_url": "http://your-webhook-receiver.com/endpoint",
  "secret": "your_secure_secret_key",
  "event_types": ["order.created", "user.updated"]
}'
```
- Replace `http://your-webhook-receiver.com/endpoint` with the actual URL where you want webhooks sent.
- Replace `your_secure_secret_key` with a strong, unique secret for this subscription (keep this secret confidential and share it with the system that will send webhooks).
- Modify the `event_types` list as needed for filtering.

### 2. Get Subscriptions

```bash
curl -X GET 'http://localhost:8000/subscriptions/' \
  -H 'accept: application/json'
```

### 3. Get Specific Subscription

```bash
curl -X GET 'http://localhost:8000/subscriptions/{subscription_id}' \
  -H 'accept: application/json'
```
Replace `{subscription_id}` with the actual ID of the subscription.

### 4. Update Subscription

```bash
curl -X PUT 'http://localhost:8000/subscriptions/{subscription_id}' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "target_url": "http://updated-receiver.com/new-endpoint",
  "secret": "updated_secret",
  "event_types": ["product.deleted"]
}'
```
Replace `{subscription_id}` with the actual ID and provide the complete updated subscription data.

### 5. Delete Subscription

```bash
curl -X DELETE 'http://localhost:8000/subscriptions/{subscription_id}' \
  -H 'accept: application/json'
```
Replace `{subscription_id}` with the actual ID.

### 6. Ingest Webhook

This endpoint requires the `subscription_id` in the URL and expects a JSON body with `payload` and optional `event_type`. If the subscription has a secret configured, you must include the `X-Hub-Signature-256` header with a valid signature.

```bash
curl -X POST 'http://localhost:8000/ingest/{subscription_id}' \
  -H 'accept: application/json' \
  -H 'X-Hub-Signature-256: sha256=your_calculated_signature' \
  -H 'Content-Type: application/json' \
  -d '{
  "payload": {
    "user_id": 123,
    "action": "created",
    "timestamp": "..."
  },
  "event_type": "user.created"
}'
```
- Replace `{subscription_id}` with the target subscription ID.
- Replace `your_calculated_signature` with the signature generated using `sha256Generator.py`.
- Modify the JSON payload and event_type as needed.

#### How to Generate the X-Hub-Signature-256 Header:

Use the `sha256Generator.py` script located in the root of the repository.

1. **Save the script**: Ensure `sha256Generator.py` is saved locally.
2. **Edit the script**: Open `sha256Generator.py` in a text editor.
   - Modify the `subscription_secret` variable to match the exact secret key of your target subscription.
   - Modify the `webhook_payload` dictionary to match the exact Python dictionary representation of the JSON payload you intend to send in the webhook request body.
3. **Run the script**: Execute the script from your terminal:
   ```bash
   python sha256Generator.py
   ```
4. **Get the output**: The script will print the required `X-Hub-Signature-256` header value (e.g., `sha256=...`).
5. **Use the header**: Copy the entire output string (including `sha256=`) and use it as the value for the `X-Hub-Signature-256` header in your curl command or Swagger UI request.

### 7. Get Webhook Status and Attempts

```bash
curl -X GET 'http://localhost:8000/status/{webhook_id}' \
  -H 'accept: application/json'
```
Replace `{webhook_id}` with the ID returned from a successful ingest request.

### 8. List Recent Delivery Attempts for a Subscription

```bash
curl -X GET 'http://localhost:8000/subscriptions/{subscription_id}/logs?limit=20' \
  -H 'accept: application/json'
```
- Replace `{subscription_id}` with the actual subscription ID.
- Adjust the 'limit' query parameter as needed.

### 9. List All Recent Delivery Attempts

```bash
curl -X GET 'http://localhost:8000/logs/?skip=0&limit=100' \
  -H 'accept: application/json'
```
Adjust `skip` and `limit` query parameters for pagination.

## Cost Estimation (AWS Free Tier)

Assuming deployment to a single AWS EC2 t2.micro or t3.micro instance within the free tier limits, running all application components (API, Worker, Beat, PostgreSQL, Redis) using Docker Compose, and based on moderate traffic (5000 webhooks ingested/day, average 1.2 delivery attempts per webhook):

- **EC2**: One t2.micro or t3.micro instance (750 hours/month free). Estimated cost: $0/month.
- **EBS (Storage)**: Default root volume (up to 30 GiB free tier). Estimated cost: $0/month.
- **Data Transfer**: Outbound data transfer (delivering webhooks). 5000 webhooks/day * 1.2 attempts/webhook * ~1KB avg payload ≈ 6MB/day ≈ 180MB/month. AWS Free Tier includes 100 GB outbound transfer. Estimated cost: $0/month.
- **Elastic IP**: Free only when associated with a running EC2 instance. Estimated cost: $0/month if associated.
- **Route 53 (DNS)**: First 1 million queries per month are free. Estimated cost: $0/month for this traffic volume.

**Total Estimated Monthly Cost (within Free Tier limits)**: $0/month.

> Note: Exceeding any free tier limit (e.g., running the instance for more than 750 hours, using a larger instance type, exceeding storage or data transfer limits) will incur charges.

## Assumptions Made

- Incoming webhook payloads are valid JSON.
- Target URLs are accessible from the network where the Docker containers run.
- The system sending webhooks correctly calculates the HMAC-SHA256 signature using the shared secret and raw payload body.
- The provided `init.sql` is sufficient for the database schema.
- Basic error logging to standard output (caught by Docker logs) is sufficient for this assignment's scope.
- The minimal UI via Swagger is acceptable.

## Testing Status

Please note that while test files are included in the repository (`tests/` directory), the implementation of comprehensive unit and integration tests was not fully completed within the scope of this submission. The provided tests may not cover all functionality or edge cases.

## Credits

- [FastAPI](https://fastapi.tiangolo.com/)
- [Celery](https://docs.celeryq.dev/)
- [Redis](https://redis.io/)
- [PostgreSQL](https://www.postgresql.org/)
- [SQLAlchemy](https://www.sqlalchemy.org/)
- [Requests](https://requests.readthedocs.io/)
- [Docker](https://www.docker.com/)
- [Pytest](https://docs.pytest.org/)
- [Pytest-Asyncio](https://pytest-asyncio.readthedocs.io/)
- [Pytest-Mock](https://pytest-mock.readthedocs.io/)
- [Httpx](https://www.python-httpx.org/)# Webhook Delivery Service

This project implements a robust backend system designed to function as a reliable webhook delivery service. It is built using Python with FastAPI, Celery, Redis, and PostgreSQL, and is fully containerized using Docker and orchestrated with Docker Compose.

The service is capable of ingesting incoming webhook payloads, queuing them for asynchronous processing, attempting delivery to subscribed target URLs with retry logic for failures, and providing visibility into the delivery status. It also includes bonus features for payload signature verification and event type filtering for enhanced security and efficiency.

## Project Structure

```
.
├── docker-compose.yml          # Defines and orchestrates services
├── Dockerfile                  # Defines the application image
├── init.sql                    # Database schema initialization script
├── requirements.txt            # Python dependencies
├── sha256Generator.py          # Script for generating webhook signatures
└── app/
    ├── __init__.py
    ├── cache.py                # Redis cache operations
    ├── celery_app.py           # Celery app instance and config
    ├── config.py               # Configuration loading
    ├── crud.py                 # Database operations (CRUD)
    ├── database.py             # Database session setup
    ├── main.py                 # FastAPI app, API endpoints (Ingestion, Status)
    ├── models.py               # SQLAlchemy ORM models
    ├── schemas.py              # Pydantic models (Request/Response schemas)
    └── tasks.py                # Celery tasks (Delivery, Cleanup)
└── tests/
    ├── conftest.py
    ├── test_ingestion.py
    ├── test_status_logs.py
    ├── test_subscriptions.py
    └── test_tasks.py
```

> **Note:** The `tests/` directory contains test files, but tests were not fully completed.

## Core Requirements Implemented

- **Subscription Management**: CRUD operations for webhook subscriptions (`/subscriptions`).
- **Webhook Ingestion**: Accepts payloads via POST to `/ingest/{subscription_id}`, queues for async processing, returns 202 Accepted.
- **Asynchronous Delivery Processing**: Background Celery workers process queued tasks.
- **Retry Mechanism**: Exponential backoff for failed deliveries (configured via environment variables).
- **Delivery Logging**: Logs status and details of each attempt to the database.
- **Log Retention**: Background task (Celery Beat) for periodic log cleanup.
- **Status/Analytics Endpoints**: Retrieve webhook status (`/status/{webhook_id}`) and subscription logs (`/subscriptions/{subscription_id}/logs`, `/logs/`).
- **Caching**: Redis caching for subscription details during ingestion and processing.

## Bonus Points Implemented

- **Payload Signature Verification**: Verifies `X-Hub-Signature-256` header using HMAC-SHA256 and the subscription secret. Rejects requests with invalid signatures.
- **Event Type Filtering**: Allows subscriptions to specify `event_types`. Ingestion endpoint filters webhooks based on the `event_type` field in the payload.

## Technical Stack

- **Language**: Python
- **Web Framework**: FastAPI
- **Database**: PostgreSQL (containerized)
- **Asynchronous Tasks / Queuing / Caching**: Celery and Redis (containerized)
- **Containerisation**: Docker and Docker Compose

## Local Setup and Running

These instructions assume you have Docker and Docker Compose installed on your machine.

1. **Clone the repository**:
   ```bash
   git clone https://github.com/pmalu9211/cdfr3s.git
   cd cdfr3s/
   ```

2. **Create a .env file**:
   Create a file named `.env` in the root directory of the project. This file will store your environment variables.
   ```bash
   nano .env  # Or use your preferred text editor
   ```

3. **Add environment variables to .env**:
   ```
   POSTGRES_DB=mydatabase
   POSTGRES_USER=user
   POSTGRES_PASSWORD=your_secure_password_for_local_db  # Choose a strong password
   DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
   REDIS_URL=redis://redis:6379/0
   CACHE_TTL_SECONDS=300
   WEBHOOK_DELIVERY_TIMEOUT_SECONDS=10
   CELERY_MAX_RETRIES=7
   CELERY_BASE_RETRY_DELAY_SECONDS=10
   LOG_RETENTION_HOURS=72
   ```
   Save and close the file.

4. **Build the Docker images**:
   ```bash
   docker compose build  # Use 'docker compose' if you have the plugin
   # or
   # docker-compose build  # Use 'docker-compose' for older standalone
   ```

5. **Start the services**:
   ```bash
   docker compose up -d  # Use 'docker compose'
   # or
   # docker-compose up -d  # Use 'docker-compose'
   ```
   This will start the PostgreSQL database, Redis, FastAPI API, Celery Worker, and Celery Beat services in detached mode. The database schema will be initialized automatically on the first run using `init.sql`.

6. **Verify services are running**:
   ```bash
   docker compose ps  # Use 'docker compose'
   # or
   # docker-compose ps  # Use 'docker-compose'
   ```
   All services should be listed with State as `Up`. The `db` service should eventually show `(healthy)`.

7. **Access the API**:
   The FastAPI API is exposed on port 8000 on your local machine. Open your web browser to http://localhost:8000/docs to access the interactive Swagger UI.

8. **To stop the services**:
   ```bash
   docker compose down  # or docker-compose down
   ```

9. **To stop services and remove volumes** (this will delete your database and Redis data):
   ```bash
   docker compose down --volumes  # or docker-compose down --volumes
   ```

## Deployed Application

The application is deployed and accessible at:
- http://segwise.prathamalu.xyz

The Swagger UI is available at:
- http://segwise.prathamalu.xyz/docs

## Architecture Choices

- **FastAPI**: Chosen for its high performance, ease of use, automatic interactive API documentation (Swagger UI), and built-in support for asynchronous operations, which aligns well with the non-blocking nature of webhook ingestion.
- **Celery**: A powerful and mature distributed task queue. Essential for decoupling webhook ingestion from the delivery process, handling retries with configurable strategies, and scheduling background tasks like log cleanup.
- **Redis**: Used as the message broker and backend for Celery, providing efficient queuing and storage of task results/states. Also utilized as a cache for frequently accessed data like subscription details to reduce database load.
- **PostgreSQL**: A robust and reliable relational database. Suitable for storing structured data like subscriptions and the potentially large volume of delivery attempt logs. Provides strong data integrity and supports indexing for efficient querying.
- **Docker and Docker Compose**: Provides a consistent development and deployment environment by containerizing all application components and defining their relationships and configurations in a single file. Enables easy local setup and portability to various hosting environments.

## Database Schema and Indexing

The database schema consists of three main tables:

### `subscriptions`: Stores webhook recipient configurations.
- `id` (UUID PK)
- `target_url` (VARCHAR)
- `secret` (VARCHAR, Optional)
- `created_at` (TIMESTAMP WITH TIME ZONE)
- `updated_at` (TIMESTAMP WITH TIME ZONE)
- `event_types` (TEXT[], Optional)
- **Indexing**: Primary key index on `id`.

### `webhooks`: Stores details of each incoming webhook payload.
- `id` (UUID PK)
- `subscription_id` (UUID FK to subscriptions, ON DELETE CASCADE)
- `payload` (JSONB)
- `event_type` (VARCHAR, Optional)
- `ingested_at` (TIMESTAMP WITH TIME ZONE)
- `status` (VARCHAR)
- **Indexing**: Indexes on `id`, `subscription_id`, and `status`. `subscription_id` is crucial for linking webhooks to subscriptions and querying logs. `status` helps in quickly finding webhooks in specific states (e.g., queued, failed).

### `delivery_attempts`: Logs every attempt to deliver a specific webhook.
- `id` (UUID PK)
- `webhook_id` (UUID FK to webhooks, ON DELETE CASCADE)
- `attempt_number` (INTEGER)
- `attempted_at` (TIMESTAMP WITH TIME ZONE)
- `outcome` (VARCHAR)
- `http_status_code` (INTEGER, Optional)
- `error_details` (TEXT, Optional)
- `next_attempt_at` (TIMESTAMP WITH TIME ZONE, Optional)
- **Indexing**: Indexes on `webhook_id` (for retrieving all attempts for a webhook), `attempted_at` (for ordering logs and retention cleanup), and `outcome`.

Indexing strategies focus on columns used in WHERE clauses, ORDER BY clauses, and foreign key relationships to optimize read performance for API endpoints and background tasks.

## Sample API Endpoints and Usage

You can interact with the API using curl or the Swagger UI at `/docs`.

- **Base URL (Local)**: http://localhost:8000
- **Base URL (Deployed)**: http://segwise.prathamalu.xyz

### 1. Create Subscription

```bash
curl -X POST 'http://localhost:8000/subscriptions/' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "target_url": "http://your-webhook-receiver.com/endpoint",
  "secret": "your_secure_secret_key",
  "event_types": ["order.created", "user.updated"]
}'
```
- Replace `http://your-webhook-receiver.com/endpoint` with the actual URL where you want webhooks sent.
- Replace `your_secure_secret_key` with a strong, unique secret for this subscription (keep this secret confidential and share it with the system that will send webhooks).
- Modify the `event_types` list as needed for filtering.

### 2. Get Subscriptions

```bash
curl -X GET 'http://localhost:8000/subscriptions/' \
  -H 'accept: application/json'
```

### 3. Get Specific Subscription

```bash
curl -X GET 'http://localhost:8000/subscriptions/{subscription_id}' \
  -H 'accept: application/json'
```
Replace `{subscription_id}` with the actual ID of the subscription.

### 4. Update Subscription

```bash
curl -X PUT 'http://localhost:8000/subscriptions/{subscription_id}' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "target_url": "http://updated-receiver.com/new-endpoint",
  "secret": "updated_secret",
  "event_types": ["product.deleted"]
}'
```
Replace `{subscription_id}` with the actual ID and provide the complete updated subscription data.

### 5. Delete Subscription

```bash
curl -X DELETE 'http://localhost:8000/subscriptions/{subscription_id}' \
  -H 'accept: application/json'
```
Replace `{subscription_id}` with the actual ID.

### 6. Ingest Webhook

This endpoint requires the `subscription_id` in the URL and expects a JSON body with `payload` and optional `event_type`. If the subscription has a secret configured, you must include the `X-Hub-Signature-256` header with a valid signature.

```bash
curl -X POST 'http://localhost:8000/ingest/{subscription_id}' \
  -H 'accept: application/json' \
  -H 'X-Hub-Signature-256: sha256=your_calculated_signature' \
  -H 'Content-Type: application/json' \
  -d '{
  "payload": {
    "user_id": 123,
    "action": "created",
    "timestamp": "..."
  },
  "event_type": "user.created"
}'
```
- Replace `{subscription_id}` with the target subscription ID.
- Replace `your_calculated_signature` with the signature generated using `sha256Generator.py`.
- Modify the JSON payload and event_type as needed.

#### How to Generate the X-Hub-Signature-256 Header:

Use the `sha256Generator.py` script located in the root of the repository.

1. **Save the script**: Ensure `sha256Generator.py` is saved locally.
2. **Edit the script**: Open `sha256Generator.py` in a text editor.
   - Modify the `subscription_secret` variable to match the exact secret key of your target subscription.
   - Modify the `webhook_payload` dictionary to match the exact Python dictionary representation of the JSON payload you intend to send in the webhook request body.
3. **Run the script**: Execute the script from your terminal:
   ```bash
   python sha256Generator.py
   ```
4. **Get the output**: The script will print the required `X-Hub-Signature-256` header value (e.g., `sha256=...`).
5. **Use the header**: Copy the entire output string (including `sha256=`) and use it as the value for the `X-Hub-Signature-256` header in your curl command or Swagger UI request.

### 7. Get Webhook Status and Attempts

```bash
curl -X GET 'http://localhost:8000/status/{webhook_id}' \
  -H 'accept: application/json'
```
Replace `{webhook_id}` with the ID returned from a successful ingest request.

### 8. List Recent Delivery Attempts for a Subscription

```bash
curl -X GET 'http://localhost:8000/subscriptions/{subscription_id}/logs?limit=20' \
  -H 'accept: application/json'
```
- Replace `{subscription_id}` with the actual subscription ID.
- Adjust the 'limit' query parameter as needed.

### 9. List All Recent Delivery Attempts

```bash
curl -X GET 'http://localhost:8000/logs/?skip=0&limit=100' \
  -H 'accept: application/json'
```
Adjust `skip` and `limit` query parameters for pagination.

## Cost Estimation (AWS Free Tier)

Assuming deployment to a single AWS EC2 t2.micro or t3.micro instance within the free tier limits, running all application components (API, Worker, Beat, PostgreSQL, Redis) using Docker Compose, and based on moderate traffic (5000 webhooks ingested/day, average 1.2 delivery attempts per webhook):

- **EC2**: One t2.micro or t3.micro instance (750 hours/month free). Estimated cost: $0/month.
- **EBS (Storage)**: Default root volume (up to 30 GiB free tier). Estimated cost: $0/month.
- **Data Transfer**: Outbound data transfer (delivering webhooks). 5000 webhooks/day * 1.2 attempts/webhook * ~1KB avg payload ≈ 6MB/day ≈ 180MB/month. AWS Free Tier includes 100 GB outbound transfer. Estimated cost: $0/month.
- **Elastic IP**: Free only when associated with a running EC2 instance. Estimated cost: $0/month if associated.
- **Route 53 (DNS)**: First 1 million queries per month are free. Estimated cost: $0/month for this traffic volume.

**Total Estimated Monthly Cost (within Free Tier limits)**: $0/month.

> Note: Exceeding any free tier limit (e.g., running the instance for more than 750 hours, using a larger instance type, exceeding storage or data transfer limits) will incur charges.

## Assumptions Made

- Incoming webhook payloads are valid JSON.
- Target URLs are accessible from the network where the Docker containers run.
- The system sending webhooks correctly calculates the HMAC-SHA256 signature using the shared secret and raw payload body.
- The provided `init.sql` is sufficient for the database schema.
- Basic error logging to standard output (caught by Docker logs) is sufficient for this assignment's scope.
- The minimal UI via Swagger is acceptable.

## Testing Status

Please note that while test files are included in the repository (`tests/` directory), the implementation of comprehensive unit and integration tests was not fully completed within the scope of this submission. The provided tests may not cover all functionality or edge cases.

## Credits

- [Chatgpt](https://chat.openai.com/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Celery](https://docs.celeryq.dev/)
- [Redis](https://redis.io/)
- [PostgreSQL](https://www.postgresql.org/)
- [SQLAlchemy](https://www.sqlalchemy.org/)
- [Requests](https://requests.readthedocs.io/)
- [Docker](https://www.docker.com/)
- [Pytest](https://docs.pytest.org/)
- [Pytest-Asyncio](https://pytest-asyncio.readthedocs.io/)
- [Pytest-Mock](https://pytest-mock.readthedocs.io/)
- [Httpx](https://www.python-httpx.org/)
