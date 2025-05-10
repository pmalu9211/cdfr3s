import requests
import uuid
from datetime import datetime, timezone, timedelta
from celery import shared_task
from celery.exceptions import Retry
from sqlalchemy.orm import Session
from .database import SessionLocal
from . import crud, schemas
from .cache import get_subscription_from_cache, set_subscription_in_cache
from .config import settings
from datetime import datetime, timezone
import logging

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Helper to get timezone-aware current time
def utcnow():
    return datetime.now(timezone.utc)

@shared_task(bind=True, max_retries=settings.celery_max_retries, default_retry_delay=settings.celery_base_retry_delay_seconds)
def process_delivery(self, webhook_id: str):
    """
    Celery task to process a webhook delivery attempt.
    """
    webhook_uuid = uuid.UUID(webhook_id)
    db: Session = SessionLocal()
    webhook = None
    subscription = None
    attempt_number = self.request.retries + 1 # Current attempt number (1 for first attempt)
    next_attempt_at = None # Will be set if retrying

    logger.info(f"Attempt {attempt_number} for webhook {webhook_id}")

    try:
        # Get webhook details
        webhook = crud.get_webhook(db, webhook_uuid)
        if not webhook:
            logger.error(f"Webhook {webhook_id} not found. Skipping delivery.")
            # Maybe log this as a permanent failure in a separate system if needed,
            # but no DB update here as the webhook record is gone.
            return

        # Get subscription details (try cache first)
        subscription = get_subscription_from_cache(webhook.subscription_id)
        if not subscription:
            # Cache miss, get from DB
            db_subscription = crud.get_subscription(db, webhook.subscription_id)
            if not db_subscription:
                logger.error(f"Subscription {webhook.subscription_id} not found for webhook {webhook_id}. Skipping delivery.")
                crud.create_delivery_attempt(
                    db, webhook_uuid, attempt_number, "permanently_failed",
                    error_details=f"Subscription {webhook.subscription_id} not found."
                )
                crud.update_webhook_status(db, webhook_uuid, "failed")
                return
            # Populate cache
            subscription = schemas.SubscriptionRead.model_validate(db_subscription)
            set_subscription_in_cache(subscription)

        target_url = subscription.target_url
        payload = webhook.payload

        # Optional: Implement event type filtering here if the bonus is done
        # if subscription.event_types and webhook.event_type not in subscription.event_types:
        #     logger.info(f"Webhook {webhook_id} event type '{webhook.event_type}' does not match subscription filter. Skipping delivery.")
        #     # Log skipped attempt? Or just don't create the task in ingestion?
        #     # If filtered here, should log a 'skipped' attempt.
        #     # Let's assume filtering happens during ingestion for this example.

        # Perform HTTP POST request
        response = None
        status_code = None
        error_details = None
        outcome = "attempted" # Default outcome before success or failure

        try:
            # Optional: Add signature header if secret is present (Bonus)
            # headers = {"Content-Type": "application/json"}
            # if subscription.secret:
            #     signature = calculate_signature(payload, subscription.secret) # Implement this function
            #     headers['X-Hub-Signature-256'] = f"sha256={signature}"

            headers = {"Content-Type": "application/json"} # Basic headers

            response = requests.post(
                target_url,
                json=payload,
                timeout=settings.webhook_delivery_timeout_seconds,
                headers=headers
            )
            status_code = response.status_code
            logger.info(f"Webhook {webhook_id} attempt {attempt_number} to {target_url} returned status code: {status_code}")

            if 200 <= status_code < 300:
                outcome = "succeeded"
                crud.update_webhook_status(db, webhook_uuid, "succeeded")
            else:
                outcome = "failed_attempt"
                error_details = f"HTTP Status Code: {status_code}"
                # Optionally include response body preview:
                # error_details += f", Response Body Preview: {response.text[:200]}"

        except requests.exceptions.Timeout:
            outcome = "failed_attempt"
            error_details = f"HTTP Timeout after {settings.webhook_delivery_timeout_seconds} seconds."
            logger.warning(f"Webhook {webhook_id} attempt {attempt_number} timed out.")
        except requests.exceptions.ConnectionError as e:
            outcome = "failed_attempt"
            error_details = f"Connection Error: {e}"
            logger.warning(f"Webhook {webhook_id} attempt {attempt_number} connection error: {e}")
        except Exception as e:
            outcome = "failed_attempt"
            error_details = f"An unexpected error occurred during request: {e}"
            logger.error(f"Webhook {webhook_id} attempt {attempt_number} unexpected error: {e}")

        # Log the delivery attempt
        if outcome == "failed_attempt" and attempt_number < settings.celery_max_retries:
             # Calculate next retry time
            delay = settings.celery_base_retry_delay_seconds * (2**(attempt_number - 1))
            # Optional: cap the delay at a maximum value
            # max_delay = 3600 # e.g., 1 hour
            # delay = min(delay, max_delay)
            next_attempt_at = utcnow() + timedelta(seconds=delay)
            logger.info(f"Scheduling retry {attempt_number + 1} for webhook {webhook_id} in {delay} seconds.")

        attempt = crud.create_delivery_attempt(
            db,
            webhook_uuid,
            attempt_number,
            outcome,
            status_code,
            error_details,
            next_attempt_at
        )

        # If failed and eligible for retry, raise Retry exception
        if outcome == "failed_attempt" and attempt_number < settings.celery_max_retries:
            raise self.retry(countdown=delay, exc=RuntimeError(error_details)) # Use Runtime error to capture details

        # If failed and no more retries
        if outcome == "failed_attempt" and attempt_number >= settings.celery_max_retries:
             crud.update_webhook_status(db, webhook_uuid, "failed")
             logger.warning(f"Webhook {webhook_id} permanently failed after {attempt_number} attempts.")


    except Retry:
        # Celery handles the retry, just pass
        pass
    except Exception as e:
        # Catch any unexpected errors that weren't handled, log permanent failure
        logger.critical(f"Unhandled exception processing webhook {webhook_id} attempt {attempt_number}: {e}", exc_info=True)
        if webhook: # Ensure webhook object exists before attempting DB update
             crud.create_delivery_attempt(
                db, webhook_uuid, attempt_number, "permanently_failed",
                error_details=f"Unhandled error: {e}"
             )
             crud.update_webhook_status(db, webhook_uuid, "failed")
    finally:
        db.close() # Ensure DB session is closed


@shared_task
def cleanup_old_logs():
    """
    Celery task to clean up old delivery logs and webhooks.
    """
    db: Session = SessionLocal()
    logger.info(f"Starting log cleanup task. Retention period: {settings.log_retention_hours} hours.")
    try:
        deleted_attempts, deleted_webhooks = crud.cleanup_old_logs(db, settings.log_retention_hours)
        logger.info(f"Log cleanup finished. Deleted {deleted_attempts} delivery attempts and {deleted_webhooks} webhooks.")
    except Exception as e:
        logger.error(f"Error during log cleanup: {e}", exc_info=True)
    finally:
        db.close()

# Helper function for signature verification (Bonus) - needs implementation
# import hmac
# import hashlib
# def calculate_signature(payload: dict, secret: str) -> str:
#     """Calculates HMAC-SHA256 signature."""
#     # Ensure payload is a consistent string representation (e.g., sorted keys)
#     payload_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
#     signature = hmac.new(secret.encode('utf-8'), payload_str.encode('utf-8'), hashlib.sha256).hexdigest()
#     return signature