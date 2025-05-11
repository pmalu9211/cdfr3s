# tests/test_status_logs.py
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import uuid
import pytest
from datetime import datetime, timezone, timedelta

# The fixtures from conftest.py (db_session, test_client, mock_redis)
# are automatically available to tests in this directory.

# Helper to get timezone-aware current time
def utcnow():
    return datetime.now(timezone.utc)

def test_get_webhook_status(test_client: TestClient, db_session: Session):
    """Test retrieving status and attempts for a specific webhook."""
    from app import crud, models, schemas
    # Create a subscription
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://status.com/webhook", secret="status_secret"))

    # Create a webhook
    webhook_payload_data = {"data": "status_test"}
    webhook = crud.create_webhook(db_session, sub.id, webhook_payload_data, event_type="test.status")

    # Create some delivery attempts for this webhook
    attempt1 = crud.create_delivery_attempt(db_session, webhook.id, 1, "failed_attempt", http_status_code=500, error_details="Server Error")
    attempt2 = crud.create_delivery_attempt(db_session, webhook.id, 2, "failed_attempt", http_status_code=403, error_details="Forbidden", next_attempt_at=utcnow() + timedelta(minutes=5))
    attempt3 = crud.create_delivery_attempt(db_session, webhook.id, 3, "succeeded", http_status_code=200)

    # Update webhook status to succeeded (matching the last attempt)
    crud.update_webhook_status(db_session, webhook.id, "succeeded")

    # Make a GET request to the status endpoint
    response = test_client.get(f"/status/{webhook.id}")

    # Assert the response status code is 200 OK
    assert response.status_code == 200

    # Assert the response body matches the expected schema and data
    status_data = response.json()
    assert status_data["id"] == str(webhook.id)
    assert status_data["subscription_id"] == str(sub.id) # Check subscription_id is included
    assert status_data["status"] == "succeeded"
    assert "ingested_at" in status_data

    # Check latest_attempt
    latest_attempt_data = status_data["latest_attempt"]
    assert latest_attempt_data is not None
    assert latest_attempt_data["id"] == str(attempt3.id)
    assert latest_attempt_data["webhook_id"] == str(webhook.id)
    assert latest_attempt_data["subscription_id"] == str(sub.id) # Check subscription_id in attempt
    assert latest_attempt_data["target_url"] == str(sub.target_url) # Check target_url in attempt
    assert latest_attempt_data["attempt_number"] == 3
    assert latest_attempt_data["outcome"] == "succeeded"
    assert latest_attempt_data["http_status_code"] == 200
    assert latest_attempt_data["error_details"] is None
    assert latest_attempt_data["next_attempt_at"] is None

    # Check all attempts
    attempts_list = status_data["attempts"]
    assert isinstance(attempts_list, list)
    assert len(attempts_list) == 3

    # Verify data for each attempt (order should be chronological based on model relationship)
    assert attempts_list[0]["id"] == str(attempt1.id)
    assert attempts_list[0]["attempt_number"] == 1
    assert attempts_list[0]["outcome"] == "failed_attempt"
    assert attempts_list[0]["subscription_id"] == str(sub.id) # Check subscription_id
    assert attempts_list[0]["target_url"] == str(sub.target_url) # Check target_url

    assert attempts_list[1]["id"] == str(attempt2.id)
    assert attempts_list[1]["attempt_number"] == 2
    assert attempts_list[1]["outcome"] == "failed_attempt"
    assert attempts_list[1]["subscription_id"] == str(sub.id) # Check subscription_id
    assert attempts_list[1]["target_url"] == str(sub.target_url) # Check target_url
    assert attempts_list[1]["next_attempt_at"] is not None # Should be populated for retryable

    assert attempts_list[2]["id"] == str(attempt3.id)
    assert attempts_list[2]["attempt_number"] == 3
    assert attempts_list[2]["outcome"] == "succeeded"
    assert attempts_list[2]["subscription_id"] == str(sub.id) # Check subscription_id
    assert attempts_list[2]["target_url"] == str(sub.target_url) # Check target_url


def test_get_webhook_status_not_found(test_client: TestClient, db_session: Session):
    """Test retrieving status for a webhook that does not exist."""
    non_existent_id = uuid.uuid4()

    # Make a GET request for the non-existent ID
    response = test_client.get(f"/status/{non_existent_id}")

    # Assert the response status code is 404 Not Found
    assert response.status_code == 404
    assert response.json() == {"detail": "Webhook not found"}


def test_list_recent_subscription_logs(test_client: TestClient, db_session: Session):
    """Test listing recent logs for a specific subscription."""
    from app import crud, models, schemas
    # Create two subscriptions
    sub1 = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://logs1.com"))
    sub2 = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://logs2.com"))

    # Create webhooks and attempts for sub1
    webhook1_sub1 = crud.create_webhook(db_session, sub1.id, {"data": "w1s1"})
    attempt1_w1s1 = crud.create_delivery_attempt(db_session, webhook1_sub1.id, 1, "failed_attempt", attempted_at=utcnow() - timedelta(minutes=10))
    attempt2_w1s1 = crud.create_delivery_attempt(db_session, webhook1_sub1.id, 2, "succeeded", attempted_at=utcnow() - timedelta(minutes=8))

    webhook2_sub1 = crud.create_webhook(db_session, sub1.id, {"data": "w2s1"})
    attempt1_w2s1 = crud.create_delivery_attempt(db_session, webhook2_sub1.id, 1, "failed_attempt", attempted_at=utcnow() - timedelta(minutes=5))

    # Create webhooks and attempts for sub2 (should not appear in sub1 logs)
    webhook1_sub2 = crud.create_webhook(db_session, sub2.id, {"data": "w1s2"})
    attempt1_w1s2 = crud.create_delivery_attempt(db_session, webhook1_sub2.id, 1, "succeeded", attempted_at=utcnow() - timedelta(minutes=2))

    # Make a GET request to the logs endpoint for sub1
    response = test_client.get(f"/subscriptions/{sub1.id}/logs?limit=20")

    # Assert the response status code is 200 OK
    assert response.status_code == 200

    # Assert the response body is a list of logs for sub1, ordered by attempted_at desc
    logs = response.json()
    assert isinstance(logs, list)
    assert len(logs) == 3 # Should contain attempts from webhook1_sub1 and webhook2_sub1

    # Verify logs are for sub1 and ordered correctly (most recent first)
    assert logs[0]["webhook_id"] == str(webhook2_sub1.id) # attempt1_w2s1
    assert logs[0]["subscription_id"] == str(sub1.id)
    assert logs[0]["target_url"] == str(sub1.target_url)
    assert logs[0]["attempt_number"] == 1

    assert logs[1]["webhook_id"] == str(webhook1_sub1.id) # attempt2_w1s1
    assert logs[1]["subscription_id"] == str(sub1.id)
    assert logs[1]["target_url"] == str(sub1.target_url)
    assert logs[1]["attempt_number"] == 2

    assert logs[2]["webhook_id"] == str(webhook1_sub1.id) # attempt1_w1s1
    assert logs[2]["subscription_id"] == str(sub1.id)
    assert logs[2]["target_url"] == str(sub1.target_url)
    assert logs[2]["attempt_number"] == 1

    # Verify logs for sub2 are NOT included
    sub2_attempt_ids = [str(attempt1_w1s2.id)]
    log_ids = [log["id"] for log in logs]
    for sub2_id in sub2_attempt_ids:
        assert sub2_id not in log_ids

    # Test limit parameter
    response_limited = test_client.get(f"/subscriptions/{sub1.id}/logs?limit=1")
    assert response_limited.status_code == 200
    assert len(response_limited.json()) == 1
    assert response_limited.json()[0]["webhook_id"] == str(webhook2_sub1.id) # Most recent


def test_list_recent_subscription_logs_not_found(test_client: TestClient, db_session: Session):
    """Test listing logs for a non-existent subscription."""
    non_existent_id = uuid.uuid4()

    # Make a GET request for the non-existent subscription's logs
    response = test_client.get(f"/subscriptions/{non_existent_id}/logs")

    # Assert the response status code is 404 Not Found
    assert response.status_code == 404
    assert response.json() == {"detail": "Subscription not found"}


def test_list_all_logs(test_client: TestClient, db_session: Session):
    """Test listing all recent delivery attempts across all subscriptions."""
    from app import crud, models, schemas
    # Create two subscriptions
    sub1 = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://alllogs1.com"))
    sub2 = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://alllogs2.com"))

    # Create webhooks and attempts for sub1
    webhook1_sub1 = crud.create_webhook(db_session, sub1.id, {"data": "w1s1"})
    attempt1_w1s1 = crud.create_delivery_attempt(db_session, webhook1_sub1.id, 1, "failed_attempt", attempted_at=utcnow() - timedelta(minutes=10))
    attempt2_w1s1 = crud.create_delivery_attempt(db_session, webhook1_sub1.id, 2, "succeeded", attempted_at=utcnow() - timedelta(minutes=8)) # More recent

    # Create webhooks and attempts for sub2
    webhook1_sub2 = crud.create_webhook(db_session, sub2.id, {"data": "w1s2"})
    attempt1_w1s2 = crud.create_delivery_attempt(db_session, webhook1_sub2.id, 1, "succeeded", attempted_at=utcnow() - timedelta(minutes=5)) # Most recent

    # Make a GET request to the all logs endpoint
    response = test_client.get("/logs/?limit=20")

    # Assert the response status code is 200 OK
    assert response.status_code == 200

    # Assert the response body is a list of all logs, ordered by attempted_at desc
    logs = response.json()
    assert isinstance(logs, list)
    assert len(logs) == 3 # Should contain all attempts from both subscriptions

    # Verify logs are from both subscriptions and ordered correctly (most recent first)
    assert logs[0]["webhook_id"] == str(webhook1_sub2.id) # attempt1_w1s2
    assert logs[0]["subscription_id"] == str(sub2.id)
    assert logs[0]["target_url"] == str(sub2.target_url)
    assert logs[0]["attempt_number"] == 1

    assert logs[1]["webhook_id"] == str(webhook1_sub1.id) # attempt2_w1s1
    assert logs[1]["subscription_id"] == str(sub1.id)
    assert logs[1]["target_url"] == str(sub1.target_url)
    assert logs[1]["attempt_number"] == 2

    assert logs[2]["webhook_id"] == str(webhook1_sub1.id) # attempt1_w1s1
    assert logs[2]["subscription_id"] == str(sub1.id)
    assert logs[2]["target_url"] == str(sub1.target_url)
    assert logs[2]["attempt_number"] == 1

    # Test limit and skip parameters (optional but good)
    response_limited = test_client.get("/logs/?limit=1")
    assert response_limited.status_code == 200
    assert len(response_limited.json()) == 1
    assert response_limited.json()[0]["webhook_id"] == str(webhook1_sub2.id) # Most recent

    response_skipped = test_client.get("/logs/?skip=1&limit=1")
    assert response_skipped.status_code == 200
    assert len(response_skipped.json()) == 1
    assert response_skipped.json()[0]["webhook_id"] == str(webhook1_sub1.id) # The next most recent

