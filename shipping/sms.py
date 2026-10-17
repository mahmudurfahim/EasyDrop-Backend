"""BulkSMSBD client. MOCK mode (prints the SMS) until SMS_API_KEY is set and SMS_MOCK=0."""
import os, requests

class SMSError(Exception):
    pass

def _env(k, default=""):  # keys come ONLY from environment variables (Render > Environment). A "YOUR_..." placeholder counts as missing.
    v = os.getenv(k, "").strip()
    return default if not v or v.startswith("YOUR_") else v

LAST = {}  # mock mode: last message per number (used by tests)

def mock_mode():
    return os.getenv("SMS_MOCK", "0") == "1"

def send_sms(number, message):
    if mock_mode():
        LAST[number] = message
        print(f"[SMS mock] {number}: {message}")
        return True
    key, sender = _env("SMS_API_KEY"), _env("SMS_SENDER_ID")
    if not key or not sender:
        raise SMSError("SMS is not configured: set SMS_API_KEY and SMS_SENDER_ID")
    n = str(number)
    try:
        r = requests.post(_env("SMS_URL", "http://bulksmsbd.net/api/smsapi"), timeout=15, data={
            "api_key": key, "senderid": sender,
            "number": "88" + n if n.startswith("01") else n, "message": message})
        code = r.json().get("response_code")
    except (requests.RequestException, ValueError):
        raise SMSError("SMS service unreachable")
    if code != 202:  # 202 = submitted successfully
        raise SMSError(f"SMS failed (code {code})")
    return True
