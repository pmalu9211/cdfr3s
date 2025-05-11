import requests
import uuid
from celery import shared_task
from celery.exceptions import Retry
from sqlalchemy.orm import Session
from .database import SessionLocal
from . import crud, schemas
from .cache import get_subscription_from_cache, set_subscription_in_cache
from .config import settings
from datetime import datetime, timezone, timedelta
import logging

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Helper to get timezone-aware current time
def utcnow():
    return datetime.now(timezone.utc)

@shared_task(bind=True, max_retries=settings.celery_max_retries, default_retry_backoff=True, default_retry_delay=settings.celery_base_retry_delay_seconds)
def process_delivery(self, webhook_id: str):
    """
    Celery task to process a webhook delivery attempt.
    """
    webhook_uuid = uuid.UUID(webhook_id)
    db: Session = SessionLocal()
    webhook = None
    subscription = None
    attempt_number = self.request.retries + 1 # Current attempt number (1 for first attempt)
    next_attempt_at_for_log = None

    logger.info(f"Attempt {attempt_number} for webhook {webhook_id}")

    try:
        # Get webhook details
        webhook = crud.get_webhook(db, webhook_uuid)
        if not webhook:
            logger.error(f"Webhook {webhook_id} not found. Skipping delivery.")
            # If webhook record is already gone, we can't log the permanent failure against it.
            return

        # Get subscription details (try cache first)
        subscription = get_subscription_from_cache(webhook.subscription_id)
        if not subscription:
            # Cache miss, get from DB
            db_subscription = crud.get_subscription(db, webhook.subscription_id)
            if not db_subscription:
                logger.error(f"Subscription {webhook.subscription_id} not found for webhook {webhook_id}. Skipping delivery.")
                 # Log permanent failure directly if sub is gone
                crud.create_delivery_attempt(
                    db, webhook_uuid, attempt_number, "permanently_failed",
                    error_details=f"Subscription {webhook.subscription_id} not found."
                )
                crud.update_webhook_status(db, webhook_uuid, "failed")
                return
            # Populate cache
            subscription = schemas.SubscriptionRead.model_validate(db_subscription)
            set_subscription_in_cache(subscription)

        target_url = str(subscription.target_url) # Ensure it's a string for requests
        payload = webhook.payload

        # Perform HTTP POST request
        response = None
        status_code = None
        error_details = None
        outcome = "failed_attempt" # Default outcome if request fails

        try:
            headers = {"Content-Type": "application/json"} # Basic headers

            response = requests.post(
                target_url,
                json=payload,
                timeout=settings.webhook_delivery_timeout_seconds,
                headers=headers,
                # verify=False
            )
            status_code = response.status_code
            logger.info(f"Webhook {webhook_id} attempt {attempt_number} to {target_url} returned status code: {status_code}")

            if 200 <= status_code < 300:
                outcome = "succeeded"
                crud.update_webhook_status(db, webhook_uuid, "succeeded")
                logger.info(f"Webhook {webhook_id} successfully delivered on attempt {attempt_number}.")
            else:
                # Enhanced error details for non-2xx responses
                outcome = "failed_attempt"
                error_details = f"HTTP Status Code: {status_code}"
                try:
                     # Attempt to include response body preview
                     response_text = response.text
                     if response_text:
                         error_details += f", Response Body: {response_text[:500]}" # Limit size
                except Exception:
                     pass # Handle cases where response.text fails

        except requests.exceptions.Timeout:
            outcome = "failed_attempt"
            error_details = f"HTTP Timeout after {settings.webhook_delivery_timeout_seconds} seconds."
            logger.warning(f"Webhook {webhook_id} attempt {attempt_number} timed out.")
        except requests.exceptions.RequestException as e: # Catch all requests exceptions
             outcome = "failed_attempt"
             # Include exception type and message
             error_details = f"Request Error: {e.__class__.__name__} - {e}"
             logger.warning(f"Webhook {webhook_id} attempt {attempt_number} request error: {e}")
        except Exception as e:
            # Catch any other unexpected errors during the request part
            outcome = "failed_attempt"
            error_details = f"An unexpected error occurred during request: {e.__class__.__name__} - {e}"
            logger.error(f"Webhook {webhook_id} attempt {attempt_number} unexpected error during request: {e}")

        # --- Logic for Retries and Logging ---

        # Check if a retry is possible *before* logging the attempt
        is_eligible_for_retry = outcome == "failed_attempt" and attempt_number < settings.celery_max_retries

        if is_eligible_for_retry:
             # Calculate the delay for the *next* attempt for logging purposes
             # Celery's built-in backoff handles the actual scheduling delay
             delay = settings.celery_base_retry_delay_seconds * (2**(attempt_number - 1))
             # Optional: cap the delay at a maximum value (e.g., 1 hour)
             # max_delay = 3600
             # delay = min(delay, max_delay)
             next_attempt_at_for_log = utcnow() + timedelta(seconds=delay)
             logger.info(f"Attempt {attempt_number} failed. Next retry ({attempt_number + 1}) scheduled around: {next_attempt_at_for_log}")

        # Log the delivery attempt record
        attempt = crud.create_delivery_attempt(
            db,
            webhook_uuid,
            attempt_number,
            outcome,
            status_code,
            error_details,
            next_attempt_at_for_log # Use the calculated value (will be None if no retry)
        )
        logger.info(f"Logged attempt {attempt.id} for webhook {webhook_id}: {outcome}")


        # If failed and eligible for retry, raise Retry exception to trigger Celery retry
        if is_eligible_for_retry:
             # The self.retry call handles the actual scheduling in Celery's queue.
             # The countdown parameter here is just for this specific retry call,
             # but default_retry_backoff=True makes Celery calculate the actual delay.
             # We already calculated the delay and next_attempt_at_for_log for the log entry.
             raise self.retry(exc=RuntimeError(error_details)) # Re-raise with details for visibility

        # If failed and no more retries
        if outcome == "failed_attempt" and not is_eligible_for_retry:
             crud.update_webhook_status(db, webhook_uuid, "failed")
             logger.warning(f"Webhook {webhook_id} permanently failed after {attempt_number} attempts.")
             # No retry needed, task is finished

        # If successful, the task finishes here.

    except Retry:
        # This block is executed when self.retry is called.
        # Celery handles the retry logic. The current task instance will stop here.
        logger.info(f"Webhook {webhook_id} attempt {attempt_number} failed, retry scheduled by Celery.")
        pass # Do nothing else in this task instance

    except Exception as e:
        # Catch any unexpected errors that weren't handled by specific try/except blocks
        logger.critical(f"Unhandled critical exception processing webhook {webhook_id} attempt {attempt_number}: {e}", exc_info=True)
        # Log this as a permanent failure if the webhook object exists
        if webhook:
             try:
                 # Ensure next_attempt_at is None for a permanent failure log
                 crud.create_delivery_attempt(
                    db, webhook_uuid, attempt_number, "permanently_failed",
                    error_details=f"Unhandled critical error: {e.__class__.__name__} - {e}",
                    next_attempt_at=None # Explicitly set to None for permanent failure
                 )
                 crud.update_webhook_status(db, webhook_uuid, "failed")
                 logger.warning(f"Webhook {webhook_id} marked as permanently failed due to critical error.")
             except Exception as db_error:
                 logger.critical(f"Failed to log permanent failure for webhook {webhook_id} after critical error: {db_error}", exc_info=True)
        else:
             logger.critical(f"Webhook object missing when attempting to log critical failure for {webhook_id}. Cannot log.")
    finally:
        # Always ensure the database session is closed
        if db:
            db.close()


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
        if db:
            db.close()
