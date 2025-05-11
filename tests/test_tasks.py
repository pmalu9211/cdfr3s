# tests/test_tasks.py
from sqlalchemy.orm import Session
import uuid
import pytest
from unittest.mock import MagicMock, patch

# The fixtures from conftest.py (db_session, mock_requests, mock_redis)
# are automatically available to tests in this directory.
# Note: We don't need mock_celery_app here as we're not sending tasks,
# but testing the logic *within* a task execution.

from app import crud, models, schemas, tasks
from app.config import settings # Import settings to access retry config
from celery.exceptions import Retry # Import Retry exception

# Mock the Celery Task instance itself to control retries
# This is needed because process_delivery uses 'self.retry'
@pytest.fixture
def mock_celery_task_instance(mocker):
    """Mocks the 'self' object within a Celery task."""
    mock_task = mocker.MagicMock()
    # Mock the request attribute to simulate retry attempts
    mock_task.request.retries = 0 # Start with 0 retries (first attempt)
    # Mock the retry method
    mock_task.retry.side_effect = Retry # Raise the actual Retry exception

    return mock_task


def test_process_delivery_success(db_session: Session, mock_requests, mock_redis, mock_celery_task_instance):
    """Test successful webhook delivery."""
    # Create a subscription
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://success.com/webhook"))

    # Create a webhook associated with the subscription
    webhook_payload_data = {"data": "success"}
    webhook = crud.create_webhook(db_session, sub.id, webhook_payload_data, event_type="test.success")

    # Configure the mock requests.post to return a successful response (200-299)
    mock_requests.post.return_value.status_code = 200
    mock_requests.post.return_value.text = "OK" # Mock response body

    # Call the task's core logic directly
    # Pass the mock_celery_task_instance as the first argument 'self'
    tasks.process_delivery(mock_celery_task_instance, str(webhook.id))

    # Verify requests.post was called with the correct URL and payload
    mock_requests.post.assert_called_once_with(
        str(sub.target_url), # Ensure URL is string
        json=webhook_payload_data,
        timeout=settings.webhook_delivery_timeout_seconds,
        headers={"Content-Type": "application/json"},
        # verify=False # If verify=False is used in tasks, include it here
    )

    # Verify a delivery attempt was logged as 'succeeded'
    attempts = crud.get_delivery_attempts_for_webhook(db_session, webhook.id)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.webhook_id == webhook.id
    assert attempt.attempt_number == 1 # First attempt
    assert attempt.outcome == "succeeded"
    assert attempt.http_status_code == 200
    assert attempt.error_details is None
    assert attempt.next_attempt_at is None # No retry needed

    # Verify the webhook status was updated to 'succeeded'
    db_webhook_after = crud.get_webhook(db_session, webhook.id)
    assert db_webhook_after.status == "succeeded"

    # Verify self.retry was NOT called
    mock_celery_task_instance.retry.assert_not_called()


def test_process_delivery_failed_retryable(db_session: Session, mock_requests, mock_redis, mock_celery_task_instance):
    """Test failed webhook delivery that is eligible for retry."""
    from requests.exceptions import RequestException # Use base RequestException

    # Create a subscription
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://fail-retry.com/webhook"))

    # Create a webhook
    webhook_payload_data = {"data": "fail-retry"}
    webhook = crud.create_webhook(db_session, sub.id, webhook_payload_data, event_type="test.fail")

    # Configure the mock requests.post to return a failed response (e.g., 500)
    mock_requests.post.return_value.status_code = 500
    mock_requests.post.return_value.text = "Internal Server Error" # Mock response body

    # We expect the task to raise Retry, so use pytest.raises
    with pytest.raises(Retry):
        tasks.process_delivery(mock_celery_task_instance, str(webhook.id))

    # Verify requests.post was called
    mock_requests.post.assert_called_once()

    # Verify a delivery attempt was logged as 'failed_attempt'
    attempts = crud.get_delivery_attempts_for_webhook(db_session, webhook.id)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.webhook_id == webhook.id
    assert attempt.attempt_number == 1 # First attempt
    assert attempt.outcome == "failed_attempt"
    assert attempt.http_status_code == 500
    assert "HTTP Status Code: 500" in attempt.error_details
    assert "Response Body: Internal Server Error" in attempt.error_details # Check for enhanced error details

    # Verify next_attempt_at was populated in the log (since it's retryable)
    assert attempt.next_attempt_at is not None

    # Verify the webhook status is still 'queued' or 'processing' (not 'failed' yet)
    db_webhook_after = crud.get_webhook(db_session, webhook.id)
    assert db_webhook_after.status not in ["succeeded", "failed"]

    # Verify self.retry was called
    mock_celery_task_instance.retry.assert_called_once()


def test_process_delivery_failed_max_retries(db_session: Session, mock_requests, mock_redis, mock_celery_task_instance):
    """Test failed webhook delivery that reaches maximum retries."""
    from requests.exceptions import RequestException

    # Create a subscription
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://fail-max.com/webhook"))

    # Create a webhook
    webhook_payload_data = {"data": "fail-max"}
    webhook = crud.create_webhook(db_session, sub.id, webhook_payload_data, event_type="test.fail")

    # Configure the mock requests.post to return a failed response (e.g., 400)
    mock_requests.post.return_value.status_code = 400
    mock_requests.post.return_value.text = "Bad Request"

    # Simulate being at the maximum retry attempt
    mock_celery_task_instance.request.retries = settings.celery_max_retries - 1 # This is the last attempt

    # Call the task's core logic
    # This time, it should NOT raise Retry, but log as permanently_failed
    tasks.process_delivery(mock_celery_task_instance, str(webhook.id))

    # Verify requests.post was called
    mock_requests.post.assert_called_once()

    # Verify a delivery attempt was logged as 'permanently_failed'
    attempts = crud.get_delivery_attempts_for_webhook(db_session, webhook.id)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.webhook_id == webhook.id
    assert attempt.attempt_number == settings.celery_max_retries # Should be the max attempt number
    assert attempt.outcome == "permanently_failed"
    assert attempt.http_status_code == 400
    assert "HTTP Status Code: 400" in attempt.error_details
    assert attempt.next_attempt_at is None # No more retries

    # Verify the webhook status was updated to 'failed'
    db_webhook_after = crud.get_webhook(db_session, webhook.id)
    assert db_webhook_after.status == "failed"

    # Verify self.retry was NOT called
    mock_celery_task_instance.retry.assert_not_called()


def test_process_delivery_connection_error(db_session: Session, mock_requests, mock_redis, mock_celery_task_instance):
    """Test webhook delivery failure due to connection error."""
    from requests.exceptions import ConnectionError as RequestsConnectionError # Alias to avoid name clash

    # Create a subscription
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://connection-error.com/webhook"))

    # Create a webhook
    webhook_payload_data = {"data": "conn-error"}
    webhook = crud.create_webhook(db_session, sub.id, webhook_payload_data, event_type="test.conn")

    # Configure the mock requests.post to raise a ConnectionError
    mock_requests.post.side_effect = RequestsConnectionError("Mock connection failed")

    # We expect the task to raise Retry (unless it's the last attempt)
    with pytest.raises(Retry):
        tasks.process_delivery(mock_celery_task_instance, str(webhook.id))

    # Verify requests.post was called
    mock_requests.post.assert_called_once()

    # Verify a delivery attempt was logged as 'failed_attempt'
    attempts = crud.get_delivery_attempts_for_webhook(db_session, webhook.id)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.webhook_id == webhook.id
    assert attempt.attempt_number == 1 # First attempt
    assert attempt.outcome == "failed_attempt"
    assert attempt.http_status_code is None # No HTTP status code on connection error
    assert "Request Error: RequestsConnectionError" in attempt.error_details # Check for error details

    # Verify next_attempt_at was populated
    assert attempt.next_attempt_at is not None

    # Verify self.retry was called
    mock_celery_task_instance.retry.assert_called_once()


def test_process_delivery_webhook_not_found(db_session: Session, mock_requests, mock_redis, mock_celery_task_instance):
    """Test processing a task for a webhook ID that doesn't exist (e.g., deleted)."""
    # Use a random UUID that won't exist
    non_existent_webhook_id = uuid.uuid4()

    # Call the task's core logic directly
    # Pass the mock_celery_task_instance as the first argument 'self'
    # FIX: Ensure only two arguments are passed (self and webhook_id)
    tasks.process_delivery(mock_celery_task_instance, str(non_existent_webhook_id))

    # Verify requests.post was NOT called
    mock_requests.post.assert_not_called()

    # Verify no delivery attempt was logged for this ID
    from app import models
    assert db_session.query(models.DeliveryAttempt).filter_by(webhook_id=non_existent_webhook_id).count() == 0

    # Verify self.retry was NOT called
    mock_celery_task_instance.retry.assert_not_called()


def test_cleanup_old_logs(db_session: Session):
    """Test the cleanup_old_logs task logic."""
    from app import crud, models, schemas
    from datetime import datetime, timezone, timedelta

    # Create a subscription
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://cleanup.com"))

    # Create webhooks and attempts with different timestamps
    now = datetime.now(timezone.utc)
    retention_hours = settings.log_retention_hours
    old_threshold = now - timedelta(hours=retention_hours + 1) # Older than retention
    recent_threshold = now - timedelta(hours=retention_hours - 1) # Newer than retention

    # Create an old webhook with old attempts (should be deleted)
    old_webhook = crud.create_webhook(db_session, sub.id, {"old": True}, status="failed") # Must be final status
    old_webhook.ingested_at = old_threshold - timedelta(hours=1) # Ensure webhook is old
    db_session.add(old_webhook) # Re-add after modifying timestamp
    db_session.commit()
    crud.create_delivery_attempt(db_session, old_webhook.id, 1, "failed_attempt", attempted_at=old_threshold - timedelta(minutes=10))
    crud.create_delivery_attempt(db_session, old_webhook.id, 2, "permanently_failed", attempted_at=old_threshold)


    # Create a recent webhook with recent attempts (should NOT be deleted)
    recent_webhook = crud.create_webhook(db_session, sub.id, {"recent": True}, status="succeeded") # Must be final status
    recent_webhook.ingested_at = recent_threshold # Ensure webhook is recent
    db_session.add(recent_webhook) # Re-add after modifying timestamp
    db_session.commit()
    crud.create_delivery_attempt(db_session, recent_webhook.id, 1, "succeeded", attempted_at=recent_threshold)

    # Create a webhook that is old but still 'queued' or 'processing' (should NOT be deleted by default logic)
    # The current cleanup logic only deletes old webhooks with final status.
    old_processing_webhook = crud.create_webhook(db_session, sub.id, {"processing": True}, status="queued")
    old_processing_webhook.ingested_at = old_threshold - timedelta(hours=2)
    db_session.add(old_processing_webhook)
    db_session.commit()
    crud.create_delivery_attempt(db_session, old_processing_webhook.id, 1, "failed_attempt", attempted_at=old_threshold - timedelta(minutes=30))


    db_session.commit() # Commit all changes

    # Verify initial counts
    assert db_session.query(models.Webhook).count() == 3
    assert db_session.query(models.DeliveryAttempt).count() == 3

    # Call the cleanup task logic directly
    tasks.cleanup_old_logs()

    # Verify counts after cleanup
    # Should delete attempts and webhook for old_webhook
    # Should NOT delete attempts or webhook for recent_webhook
    # Should NOT delete old_processing_webhook or its attempt (due to status filter in crud)
    assert db_session.query(models.Webhook).count() == 2 # recent_webhook and old_processing_webhook remain
    assert db_session.query(models.DeliveryAttempt).count() == 2 # Attempts for recent_webhook and old_processing_webhook remain

    # Verify specific items were deleted/kept
    assert crud.get_webhook(db_session, old_webhook.id) is None
    assert crud.get_webhook(db_session, recent_webhook.id) is not None
    assert crud.get_webhook(db_session, old_processing_webhook.id) is not None # Still exists due to status filter

    assert db_session.query(models.DeliveryAttempt).filter_by(webhook_id=old_webhook.id).count() == 0
    assert db_session.query(models.DeliveryAttempt).filter_by(webhook_id=recent_webhook.id).count() == 1
    assert db_session.query(models.DeliveryAttempt).filter_by(webhook_id=old_processing_webhook.id).count() == 1
