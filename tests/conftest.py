# tests/conftest.py
import pytest
import sys
import os
from typing import Generator, Any, List, Optional, Dict
from fastapi.testclient import TestClient
# Import necessary SQLAlchemy components
from sqlalchemy import create_engine, Column, String, DateTime, ForeignKey, Integer
# Import necessary types and TypeDecorator
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.ext.declarative import declarative_base # Import declarative_base for test models
from sqlalchemy.types import TypeDecorator, Text, UserDefinedType # Import necessary types
# Correct imports for dialects and Dialect
import sqlalchemy.dialects
from sqlalchemy.engine.interfaces import Dialect
import json
# Import StrictRedis to patch it
from redis import StrictRedis
from unittest.mock import patch, MagicMock
import uuid
from datetime import datetime, timezone

# Add the project root to the sys.path to allow importing app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

# Import necessary components from your app
from app.main import app
from app.database import get_db
from app.config import settings
# Import the actual app's Redis client and Celery app to reference them for mocking
from app.cache import redis_client as app_redis_client
from app.celery_app import celery_app as app_celery_app


# --- Custom Type for SQLite UUID Handling ---
# SQLite doesn't have a native UUID type, map to TEXT
class SQLiteUUID(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Optional[uuid.UUID], dialect: Dialect) -> Optional[str]:
        if value is None:
            return None
        if dialect.name == 'postgresql':
            return value
        return str(value)

    def process_result_value(self, value: Optional[str], dialect: Dialect) -> Optional[uuid.UUID]:
        if value is None:
            return None
        if dialect.name == 'postgresql':
            return value
        try:
            return uuid.UUID(value)
        except (ValueError, TypeError):
            return None


# --- Custom Type for SQLite ARRAY(String) Handling ---
# This TypeDecorator will handle ARRAY(String) for SQLite by storing as JSON TEXT
class SQLiteStringArray(TypeDecorator):
    impl = Text # Store as TEXT in SQLite
    cache_ok = True

    @property
    def python_type(self):
        return list # The Python type is a list

    def process_bind_param(self, value: Optional[List[str]], dialect: Dialect) -> Optional[str]:
        """Process Python list to database TEXT (JSON string) for SQLite."""
        if value is None:
            return None
        if dialect.name == 'postgresql':
            return value
        return json.dumps(value)

    def process_result_value(self, value: Optional[str], dialect: Dialect) -> Optional[List[str]]:
        """Process database TEXT (JSON string) to Python list for SQLite."""
        if value is None:
            return None
        if dialect.name == 'postgresql':
            return value
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []


# --- Custom Type for SQLite JSONB Handling ---
# This TypeDecorator will handle JSONB for SQLite by storing as JSON TEXT
class SQLiteJSONB(TypeDecorator):
    impl = Text # Store as TEXT in SQLite
    cache_ok = True

    @property
    def python_type(self):
        return dict # The Python type is a dict

    def process_bind_param(self, value: Optional[Dict], dialect: Dialect) -> Optional[str]:
        """Process Python dict to database TEXT (JSON string) for SQLite."""
        if value is None:
            return None
        if dialect.name == 'postgresql':
             return value
        return json.dumps(value, separators=(',', ':'), sort_keys=True)

    def process_result_value(self, value: Optional[str], dialect: Dialect) -> Optional[Dict]:
        """Process database TEXT (JSON string) to Python dict for SQLite."""
        if value is None:
            return None
        if dialect.name == 'postgresql':
            return value
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}


# --- Test Database Setup ---

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

TestBase = declarative_base()

# Define Test Models that mirror app.models, using SQLite-compatible types
class TestSubscription(TestBase):
    __tablename__ = "subscriptions"
    id = Column(SQLiteUUID, primary_key=True)
    target_url = Column(String, nullable=False)
    secret = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
    event_types = Column(SQLiteStringArray, nullable=True)

    webhooks = relationship("TestWebhook", back_populates="subscription")


class TestWebhook(TestBase):
    __tablename__ = "webhooks"
    id = Column(SQLiteUUID, primary_key=True)
    subscription_id = Column(SQLiteUUID, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    payload = Column(SQLiteJSONB, nullable=False)
    event_type = Column(String, nullable=True)
    ingested_at = Column(DateTime(timezone=True))
    status = Column(String, nullable=False)

    subscription = relationship("TestSubscription", back_populates="webhooks")
    attempts = relationship("TestDeliveryAttempt", back_populates="webhook", order_by="TestDeliveryAttempt.attempted_at")


class TestDeliveryAttempt(TestBase):
    __tablename__ = "delivery_attempts"
    id = Column(SQLiteUUID, primary_key=True)
    webhook_id = Column(SQLiteUUID, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    attempted_at = Column(DateTime(timezone=True), nullable=False)
    outcome = Column(String, nullable=False)
    http_status_code = Column(Integer, nullable=True)
    error_details = Column(String, nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)

    webhook = relationship("TestWebhook", back_populates="attempts")


# --- Fixture to create tables using TestBase ---

@pytest.fixture(scope="function")
def create_test_tables() -> Generator[None, Any, None]:
    """
    Fixture that creates tables in the test database using TestBase metadata.
    """
    # REMOVE: Explicit compiler registration lines
    # from sqlalchemy.dialects import sqlite
    # sqlite_dialect = sqlite.dialect()
    # sqlite_dialect.type_compiler.process = SQLiteStringArray.ArrayCompiler(sqlite_dialect, None).process
    # sqlite_dialect.type_compiler.process = SQLiteJSONB.JSONBCompiler(sqlite_dialect, None).process
    # from sqlalchemy.sql.compiler import SQLCompiler
    # sqlite_dialect.type_compiler.compilers[ARRAY] = SQLiteStringArray.ArrayCompiler
    # sqlite_dialect.type_compiler.compilers[JSONB] = SQLiteJSONB.JSONBCompiler

    # Create the tables defined in TestBase with the configured engine
    TestBase.metadata.create_all(bind=engine)
    yield
    # Drop the tables after the test
    TestBase.metadata.drop_all(bind=engine)


# --- Database Session Fixture (uses TestBase metadata) ---

@pytest.fixture(scope="function")
def db_session(create_test_tables) -> Generator[Session, Any, None]:
    """
    Fixture that provides a SQLAlchemy session for testing.
    Depends on create_test_tables to ensure tables exist.
    """
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Override FastAPI's DB Dependency (uses TestingSessionLocal) ---

@pytest.fixture(scope="function")
def override_get_db(db_session: Session) -> Generator[None, Any, None]:
    """
    Fixture to override the get_db dependency in FastAPI.
    Ensures API endpoints use the test database session.
    """
    def _get_db_override():
        try:
            yield db_session
        finally:
            db_session.close()

    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.clear()


# --- Test Client Fixture ---

@pytest.fixture(scope="function")
def test_client(override_get_db) -> TestClient:
    """
    Fixture that provides a TestClient for making requests to the FastAPI app.
    Uses the overridden database dependency.
    """
    with TestClient(app) as client:
        yield client


# --- Mock Redis Fixture (Patching StrictRedis class) ---

@pytest.fixture(scope="function")
def mock_redis(mocker) -> MagicMock:
    """
    Fixture to mock the Redis client initialization.
    Patches redis.StrictRedis to return a mock client instance.
    """
    # Create a mock Redis client instance
    mock_client_instance = mocker.MagicMock(spec=StrictRedis)

    # Patch the StrictRedis class itself
    mock_redis_class = mocker.patch('redis.StrictRedis', return_value=mock_client_instance)

    # Mock common methods on the mock instance
    mock_client_instance.get.return_value = None # Default cache miss
    mock_client_instance.set.return_value = True
    mock_client_instance.delete.return_value = 1 # Number of keys deleted

    yield mock_client_instance

    # Optional: Assert that StrictRedis was instantiated with the expected URL if needed
    # mock_redis_class.assert_called_once_with(settings.redis_url, decode_responses=True)


# --- Mock Celery Fixture ---

@pytest.fixture(scope="function")
def mock_celery_app(mocker) -> MagicMock:
    """
    Fixture to mock the Celery app instance used by the app.
    """
    mock_app = mocker.MagicMock(spec=app_celery_app)
    # Patch the actual celery_app instance in the app module
    mocker.patch('app.celery_app.celery_app', mock_app)
    # Mock the send_task method
    mock_app.send_task.return_value = MagicMock(id=uuid.uuid4())
    yield mock_app


# --- Mock Requests Fixture (for Task Testing) ---

@pytest.fixture(scope="function")
def mock_requests(mocker) -> MagicMock:
    """
    Fixture to mock the 'requests' library used for webhook delivery.
    """
    mock_requests = mocker.patch('app.tasks.requests')
    mock_requests.post.return_value = MagicMock()
    mock_requests.post.return_value.status_code = 200
    mock_requests.post.return_value.text = "OK"
    yield mock_requests
