import hmac
import hashlib
import json
import sys

def generate_signature_header(secret: str, payload: dict) -> str:
    """
    Generates the X-Hub-Signature-256 header value for a given payload and secret.
    Uses a standardized, compact JSON format for signature calculation.

    Args:
        secret: The subscription secret key (string).
        payload: The webhook payload (Python dictionary).

    Returns:
        The formatted signature header value (string), e.g., "sha256=..."
    """
    # Convert the Python dictionary to a STANDARDIZED, compact JSON string
    # This format MUST match how the server standardizes the payload for signature.
    try:
        standardized_json_string = json.dumps(
            payload,
            separators=(',', ':'), # Use compact separators (no spaces after comma/colon)
            sort_keys=True         # Sort keys alphabetically for consistent order
        )
        # Encode the JSON string to bytes (UTF-8 is standard)
        standardized_body_bytes = standardized_json_string.encode('utf-8')

    except Exception as e:
        print(f"Error serializing payload to JSON: {e}", file=sys.stderr)
        return ""

    # Calculate the HMAC-SHA256 signature
    try:
        secret_bytes = secret.encode('utf-8')
        signature = hmac.new(
            secret_bytes,          # Secret must be bytes
            standardized_body_bytes, # Standardized payload bytes
            hashlib.sha256
        ).hexdigest()              # Get the hexadecimal representation

    except Exception as e:
        print(f"Error calculating HMAC signature: {e}", file=sys.stderr)
        return ""

    # Format the signature for the header
    header_value = f"sha256={signature}"

    return header_value

if __name__ == "__main__":
    # --- Example Usage ---

    # Replace with the actual secret key for the subscription
    subscription_secret = "string" # Example secret

    # Replace with the actual webhook payload dictionary
    webhook_payload = {
  "payload": {},
  "event_type": "string"
}

    # --- Generate the header value ---
    signature_header = generate_signature_header(subscription_secret, webhook_payload)

    if signature_header:
        print(f"For Secret: '{subscription_secret}'")
        print(f"And Payload Dictionary: {webhook_payload}") # Print dictionary nicely
        print(f"\nGenerated X-Hub-Signature-256 Header Value:")
        print(signature_header)
        print("\nUse this value in the 'X-Hub-Signature-256' HTTP header when sending the webhook.")
        print("\nNote: The signature is calculated on a standardized, compact JSON representation of the payload.")
    else:
        print("\nFailed to generate signature.")






















    # --- Example with a different payload ---
    # print("-" * 20)
    # subscription_secret_2 = "another_secret"
    # webhook_payload_2 = {
    #     "event": "order.created",
    #     "data": {
    #         "id": 123,
    #         "amount": 100.50
    #     }
    # }
    # signature_header_2 = generate_signature_header(subscription_secret_2, webhook_payload_2)
    # if signature_header_2:
    #      print(f"\nFor Secret: '{subscription_secret_2}'")
    #      print(f"And Payload Dictionary: {webhook_payload_2}")
    #      print(f"\nGenerated X-Hub-Signature-256 Header Value:")
    #      print(signature_header_2)
    # else:
    #      print("\nFailed to generate signature.")
