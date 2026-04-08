import os

C2_URL = os.getenv("C2_URL", "https://your-c2.example")
CALLBACK_INTERVAL = int(os.getenv("C2_CALLBACK_INTERVAL", "5"))
JITTER = int(os.getenv("C2_JITTER", "2"))
AGENT_AUTH_TOKEN = os.getenv("C2_AUTH_TOKEN", "replace-me-with-a-bootstrap-token")
REQUEST_TIMEOUT = 30
MAX_REGISTRATION_RETRIES = 3
FILE_ENCODING = "base64"
SCREENSHOT_FORMAT = "png"
MAX_SCREENSHOT_SIZE = (1920, 1080)
