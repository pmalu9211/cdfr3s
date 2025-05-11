# tests/test_ingestion.py
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import uuid
import pytest
import json
import hmac
import hashlib

# The fixtures from conftest.py (db_session, test_client, mock_redis, mock_celery_app)
# are automatically available to tests in this directory.

# Helper to calculate the signature (should match the server's standardization)
def calculate_test_signature(secret: str, payload_dict: dict) -> str:
    """Calculates HMAC-SHA256 signature for a standardized payload."""
    # This MUST match the standardization logic in app/main.py
    standardized_json_string = json.dumps(
        payload_dict,
        separators=(',', ':'),
        sort_keys=True
    )
    standardized_body_bytes = standardized_json_string.encode('utf-8')

    secret_bytes = secret.encode('utf-8')
    signature = hmac.new(
        secret_bytes,
        standardized_body_bytes,
        hashlib.sha256
    ).hexdigest()
    return signature


def test_ingest_webhook_success(test_client: TestClient, db_session: Session, mock_redis, mock_celery_app):
    """Test successful webhook ingestion without signature/filtering."""
    from app import crud, schemas
    # Create a subscription without a secret or event types
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://testserver/webhook/receiver"))

    # Define the webhook payload to send
    webhook_payload = {
        "payload": {"user_id": 123, "action": "created"},
        "event_type": "user.created"
    }

    # Make a POST request to the ingest endpoint
    response = test_client.post(f"/ingest/{sub.id}", json=webhook_payload)

    # Assert the response status code is 202 Accepted
    assert response.status_code == 202

    # Assert the response body contains the webhook_id
    response_body = response.json()
    assert "message" in response_body
    assert response_body["message"] == "Webhook accepted for processing"
    assert "webhook_id" in response_body
    webhook_id = uuid.UUID(response_body["webhook_id"], version=4) # Check if ID is a valid UUID

    # Verify the webhook was saved in the database
    db_webhook = crud.get_webhook(db_session, webhook_id)
    assert db_webhook is not None
    assert db_webhook.subscription_id == sub.id
    assert db_webhook.payload == webhook_payload["payload"]
    assert db_webhook.event_type == webhook_payload["event_type"]
    assert db_webhook.status == "queued" # Should be queued initially

    # Verify the Celery task was sent
    mock_celery_app.send_task.assert_called_once_with(
        'app.tasks.process_delivery',
        args=[str(webhook_id)],
    )

    # Verify cache was checked for the subscription
    mock_redis.get.assert_called_once_with(f"subscription:{sub.id}")


def test_ingest_webhook_subscription_not_found(test_client: TestClient, db_session):
    """Test ingestion for a non-existent subscription."""
    non_existent_id = uuid.uuid4()
    webhook_payload = {"payload": {"data": "test"}, "event_type": "test.event"}

    # Make a POST request to the ingest endpoint with a non-existent ID
    response = test_client.post(f"/ingest/{non_existent_id}", json=webhook_payload)

    # Assert the response status code is 404 Not Found
    assert response.status_code == 404
    assert response.json() == {"detail": "Subscription not found"}


def test_ingest_webhook_invalid_json(test_client: TestClient, db_session: Session, mock_redis):
    """Test ingestion with invalid JSON payload."""
    from app import crud, schemas
    # Create a subscription
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://testserver/receiver"))

    # Send invalid JSON
    invalid_json_body = b'{"payload": "invalid json", "event_type": "test.event"' # Missing closing brace

    # Make a POST request with invalid JSON
    response = test_client.post(f"/ingest/{sub.id}", content=invalid_json_body, headers={"Content-Type": "application/json"})

    # Assert the response status code is 400 Bad Request
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid JSON payload."}

    # Verify no webhook was saved and no task was sent
    from app import models
    assert db_session.query(models.Webhook).count() == 0
    # mock_celery_app is not needed or used in this test, so no assertion on it


def test_ingest_webhook_signature_required_missing_header(test_client: TestClient, db_session: Session, mock_redis, mock_celery_app):
    """Test ingestion requiring signature, but header is missing."""
    from app import crud, schemas
    # Create a subscription with a secret
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://testserver/receiver", secret="required_secret"))

    webhook_payload = {"payload": {"data": "test"}, "event_type": "test.event"}

    # Make a POST request without the signature header
    response = test_client.post(f"/ingest/{sub.id}", json=webhook_payload)

    # Assert the response status code is 401 Unauthorized
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing X-Hub-Signature-256 header."}

    # Verify no webhook was saved and no task was sent
    from app import models
    assert db_session.query(models.Webhook).count() == 0
    # mock_celery_app is not needed or used in this test, so no assertion on it


def test_ingest_webhook_signature_required_invalid_format(test_client: TestClient, db_session: Session, mock_redis, mock_celery_app):
    """Test ingestion requiring signature, but header format is invalid."""
    from app import crud, schemas
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://testserver/receiver", secret="required_secret"))

    webhook_payload = {"payload": {"data": "test"}, "event_type": "test.event"}

    # Make a POST request with an invalid header format
    response = test_client.post(f"/ingest/{sub.id}", json=webhook_payload, headers={"X-Hub-Signature-256": "invalid_format"})

    # Assert the response status code is 401 Unauthorized
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid X-Hub-Signature-256 format. Expected 'sha256=...'."}

    # Verify no webhook was saved and no task was sent
    from app import models
    assert db_session.query(models.Webhook).count() == 0
    # mock_celery_app is not needed or used in this test, so no assertion on it


def test_ingest_webhook_signature_required_invalid_signature(test_client: TestClient, db_session: Session, mock_redis, mock_celery_app):
    """Test ingestion requiring signature, but signature value is incorrect."""
    from app import crud, schemas
    secret = "required_secret"
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://testserver/receiver", secret=secret))

    webhook_payload = {"payload": {"data": "test"}, "event_type": "test.event"}

    # Calculate a signature for a DIFFERENT payload or secret
    invalid_signature = calculate_test_signature("wrong_secret", webhook_payload)

    # Make a POST request with an invalid signature
    response = test_client.post(f"/ingest/{sub.id}", json=webhook_payload, headers={"X-Hub-Signature-256": f"sha256={invalid_signature}"})

    # Assert the response status code is 403 Forbidden
    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid signature."}

    # Verify no webhook was saved and no task was sent
    from app import models
    assert db_session.query(models.Webhook).count() == 0
    # mock_celery_app is not needed or used in this test, so no assertion on it


def test_ingest_webhook_signature_required_success(test_client: TestClient, db_session: Session, mock_redis, mock_celery_app):
    """Test successful ingestion when signature is required and valid."""
    from app import crud, schemas
    secret = "super_secret_key"
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://testserver/receiver", secret=secret))

    webhook_payload = {
        "payload": {"order_id": 456, "status": "shipped"},
        "event_type": "order.updated"
    }

    # Calculate the correct signature for the payload and secret
    correct_signature = calculate_test_signature(secret, webhook_payload)

    # Make a POST request with the correct signature
    response = test_client.post(f"/ingest/{sub.id}", json=webhook_payload, headers={"X-Hub-Signature-256": f"sha256={correct_signature}"})

    # Assert the response status code is 202 Accepted
    assert response.status_code == 202

    # Assert the response body contains the webhook_id
    response_body = response.json()
    assert "webhook_id" in response_body
    webhook_id = uuid.UUID(response_body["webhook_id"], version=4)

    # Verify the webhook was saved and the task was sent
    from app import models
    db_webhook = crud.get_webhook(db_session, webhook_id)
    assert db_webhook is not None
    assert db_webhook.payload == webhook_payload["payload"]
    assert db_webhook.event_type == webhook_payload["event_type"]
    mock_celery_app.send_task.assert_called_once_with(
        'app.tasks.process_delivery',
        args=[str(webhook_id)],
    )


def test_ingest_webhook_event_filter_no_match(test_client: TestClient, db_session: Session, mock_redis, mock_celery_app):
    """Test ingestion when event type filter is set, but incoming event does not match."""
    from app import crud, schemas
    # Create a subscription with event type filters
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://testserver/receiver", event_types=["user.created", "order.paid"]))

    # Send a webhook with an event type that does NOT match the filter
    webhook_payload = {
        "payload": {"product_id": 789},
        "event_type": "product.added" # This event type is not in the filter
    }

    # Make a POST request
    response = test_client.post(f"/ingest/{sub.id}", json=webhook_payload)

    # Assert the response status code is 202 Accepted (because the request itself is valid)
    assert response.status_code == 202
    # Assert the response indicates it was filtered
    assert response.json() == {"message": "Webhook accepted but filtered by event type: 'product.added'."}

    # Verify NO webhook was saved and NO task was sent
    from app import models
    assert db_session.query(models.Webhook).count() == 0
    # mock_celery_app is not needed or used in this test, so no assertion on it


def test_ingest_webhook_event_filter_match(test_client: TestClient, db_session: Session, mock_redis, mock_celery_app):
    """Test ingestion when event type filter is set, and incoming event matches."""
    from app import crud, schemas
    # Create a subscription with event type filters
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://testserver/receiver", event_types=["user.created", "order.paid"]))

    # Send a webhook with an event type that DOES match the filter
    webhook_payload = {
        "payload": {"user_data": {}},
        "event_type": "user.created" # This event type IS in the filter
    }

    # Make a POST request
    response = test_client.post(f"/ingest/{sub.id}", json=webhook_payload)

    # Assert the response status code is 202 Accepted
    assert response.status_code == 202
    assert "webhook_id" in response.json() # Should contain webhook_id

    # Verify the webhook was saved and the task was sent
    from app import models
    assert db_session.query(models.Webhook).count() == 1
    mock_celery_app.send_task.assert_called_once()


def test_ingest_webhook_event_filter_missing_event_type_in_payload(test_client: TestClient, db_session: Session, mock_redis, mock_celery_app):
    """Test ingestion when event type filter is set, but 'event_type' is missing in payload."""
    from app import crud, schemas
    # Create a subscription with event type filters
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://testserver/receiver", event_types=["user.created"]))

    # Send a webhook payload missing the 'event_type' field
    webhook_payload = {
        "payload": {"data": "test"}
        # "event_type" is missing here
    }

    # Make a POST request
    response = test_client.post(f"/ingest/{sub.id}", json=webhook_payload)

    # Assert the response status code is 400 Bad Request
    assert response.status_code == 400
    assert response.json() == {"detail": "Event type filter configured for subscription, but 'event_type' field is missing in the payload."}

    # Verify no webhook was saved and no task was sent
    from app import models
    assert db_session.query(models.Webhook).count() == 0
    # mock_celery_app is not needed or used in this test, so no assertion on it
