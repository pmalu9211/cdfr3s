# tests/test_subscriptions.py
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app import schemas
import uuid
import pytest

# The fixtures from conftest.py (db_session, test_client, mock_redis)
# are automatically available to tests in this directory.

def test_create_subscription(test_client: TestClient, db_session: Session, mock_redis):
    """Test creating a new subscription."""
    # Define the subscription data to send
    subscription_data = {
        "target_url": "http://testserver/webhook",
        "secret": "testsecret",
        "event_types": ["order.created", "user.updated"]
    }

    # Make a POST request to the subscriptions endpoint
    response = test_client.post("/subscriptions/", json=subscription_data)

    # Assert the response status code is 201 Created
    assert response.status_code == 201

    # Assert the response body matches the expected schema and data
    created_subscription = response.json()
    assert "id" in created_subscription
    assert uuid.UUID(created_subscription["id"], version=4) # Check if ID is a valid UUID
    assert created_subscription["target_url"] == subscription_data["target_url"]
    assert created_subscription["secret"] == subscription_data["secret"]
    assert created_subscription["event_types"] == subscription_data["event_types"]
    assert "created_at" in created_subscription
    assert "updated_at" in created_subscription

    # Verify the subscription was actually saved in the database
    from app import crud, models
    db_sub = crud.get_subscription(db_session, uuid.UUID(created_subscription["id"]))
    assert db_sub is not None
    assert str(db_sub.id) == created_subscription["id"]
    assert db_sub.target_url == subscription_data["target_url"]
    assert db_sub.secret == subscription_data["secret"]
    assert db_sub.event_types == subscription_data["event_types"]

    # Verify cache invalidation was called
    mock_redis.delete.assert_called_once_with(f"subscription:{created_subscription['id']}")


def test_read_subscriptions(test_client: TestClient, db_session: Session, mock_redis):
    """Test reading multiple subscriptions."""
    from app import crud, models
    # Create a few subscriptions directly in the database for testing read
    sub1 = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://test1.com", secret="s1", event_types=["e1"]))
    sub2 = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://test2.com", secret="s2", event_types=["e2"]))

    # Make a GET request to the subscriptions endpoint
    response = test_client.get("/subscriptions/")

    # Assert the response status code is 200 OK
    assert response.status_code == 200

    # Assert the response body is a list of subscriptions
    subscriptions = response.json()
    assert isinstance(subscriptions, list)
    assert len(subscriptions) == 2 # Should contain the two subscriptions we created

    # Check if the created subscriptions are in the response
    sub_ids = [sub["id"] for sub in subscriptions]
    assert str(sub1.id) in sub_ids
    assert str(sub2.id) in sub_ids

    # Test limit and skip parameters (optional but good)
    response_limited = test_client.get("/subscriptions/?limit=1")
    assert response_limited.status_code == 200
    assert len(response_limited.json()) == 1

    response_skipped = test_client.get(f"/subscriptions/?skip=1&limit=1")
    assert response_skipped.status_code == 200
    assert len(response_skipped.json()) == 1
    # Check if the skipped subscription is the second one created (order might vary in real DB)
    # For SQLite, order by ID might be consistent enough for this simple test
    assert response_skipped.json()[0]["id"] == str(sub2.id)


def test_read_subscription(test_client: TestClient, db_session: Session, mock_redis):
    """Test reading a single subscription by ID."""
    from app import crud, models, schemas
    # Create a subscription directly in the database
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://test.com/single", secret="single_secret", event_types=["single.event"]))

    # Make a GET request to the specific subscription endpoint
    response = test_client.get(f"/subscriptions/{sub.id}")

    # Assert the response status code is 200 OK
    assert response.status_code == 200

    # Assert the response body matches the created subscription
    read_subscription = response.json()
    assert read_subscription["id"] == str(sub.id)
    assert read_subscription["target_url"] == sub.target_url
    assert read_subscription["secret"] == sub.secret
    assert read_subscription["event_types"] == sub.event_types

    # Verify cache was checked (get called) and then set
    mock_redis.get.assert_called_once_with(f"subscription:{sub.id}")
    mock_redis.set.assert_called_once() # Check if set was called


def test_read_subscription_not_found(test_client: TestClient, db_session: Session):
    """Test reading a subscription that does not exist."""
    # Use a random UUID that won't exist
    non_existent_id = uuid.uuid4()

    # Make a GET request for the non-existent ID
    response = test_client.get(f"/subscriptions/{non_existent_id}")

    # Assert the response status code is 404 Not Found
    assert response.status_code == 404
    assert response.json() == {"detail": "Subscription not found"}


def test_update_subscription(test_client: TestClient, db_session: Session, mock_redis):
    """Test updating an existing subscription."""
    from app import crud, models, schemas
    # Create a subscription directly in the database
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://old.com", secret="old_secret", event_types=["old.event"]))

    # Define the updated subscription data
    updated_data = {
        "target_url": "http://new.com/updated",
        "secret": "new_secret",
        "event_types": ["new.event1", "new.event2"]
    }

    # Make a PUT request to update the subscription
    response = test_client.put(f"/subscriptions/{sub.id}", json=updated_data)

    # Assert the response status code is 200 OK
    assert response.status_code == 200

    # Assert the response body reflects the updated data
    updated_subscription = response.json()
    assert updated_subscription["id"] == str(sub.id)
    assert updated_subscription["target_url"] == updated_data["target_url"]
    assert updated_subscription["secret"] == updated_data["secret"]
    assert updated_subscription["event_types"] == updated_data["event_types"]
    # updated_at should be different from created_at now
    assert updated_subscription["created_at"] != updated_subscription["updated_at"]

    # Verify the subscription was actually updated in the database
    db_sub = crud.get_subscription(db_session, sub.id)
    assert db_sub is not None
    assert db_sub.target_url == updated_data["target_url"]
    assert db_sub.secret == updated_data["secret"]
    assert db_sub.event_types == updated_data["event_types"]

    # Verify cache invalidation was called
    mock_redis.delete.assert_called_once_with(f"subscription:{sub.id}")


def test_update_subscription_not_found(test_client: TestClient, db_session: Session):
    """Test updating a subscription that does not exist."""
    non_existent_id = uuid.uuid4()
    updated_data = {"target_url": "http://fake.com", "secret": "fake", "event_types": []}

    # Make a PUT request for the non-existent ID
    response = test_client.put(f"/subscriptions/{non_existent_id}", json=updated_data)

    # Assert the response status code is 404 Not Found
    assert response.status_code == 404
    assert response.json() == {"detail": "Subscription not found"}


def test_delete_subscription(test_client: TestClient, db_session: Session, mock_redis):
    """Test deleting an existing subscription."""
    from app import crud, models, schemas
    # Create a subscription directly in the database
    sub = crud.create_subscription(db_session, schemas.SubscriptionCreate(target_url="http://delete.com", secret="delete_secret", event_types=[]))

    # Verify it exists initially
    db_sub_before = crud.get_subscription(db_session, sub.id)
    assert db_sub_before is not None

    # Make a DELETE request to delete the subscription
    response = test_client.delete(f"/subscriptions/{sub.id}")

    # Assert the response status code is 204 No Content
    assert response.status_code == 204
    # 204 responses typically have no body
    assert response.text == ""

    # Verify the subscription was actually deleted from the database
    db_sub_after = crud.get_subscription(db_session, sub.id)
    assert db_sub_after is None

    # Verify cache invalidation was called
    mock_redis.delete.assert_called_once_with(f"subscription:{sub.id}")


def test_delete_subscription_not_found(test_client: TestClient, db_session: Session):
    """Test deleting a subscription that does not exist."""
    non_existent_id = uuid.uuid4()

    # Make a DELETE request for the non-existent ID
    response = test_client.delete(f"/subscriptions/{non_existent_id}")

    # Assert the response status code is 404 Not Found
    assert response.status_code == 404
    assert response.json() == {"detail": "Subscription not found"}
