GAMMA_API_URL = "https://gamma-api.polymarket.com"

CHECK_INTERVAL_MINUTES = 5

MIN_COMMENTS = 1
MAX_COMMENTS = 1000
KEYWORDS = []

TEST_MODE = False
TEST_EVENT_ID = None

LOG_LEVEL = "INFO"   # DEBUG / INFO / WARNING / ERROR / CRITICAL

DATA_DIR = "data"
DB_PATH = "data/polymarket.db"
TXT_FILE = "data/polymarket_twitter_grabbed.txt"
JSON_FILE = "data/polymarket_twitter_grabbed.json"
PROCESSED_USERNAMES_FILE = "data/processed_usernames.txt"

# Polymarket API rate limit (requests per second). Default 100 rps.
POLYMARKET_RATE_LIMIT_RPS = 100
# Retries and backoff for Polymarket requests
POLYMARKET_MAX_RETRIES = 5
POLYMARKET_BACKOFF_BASE = 0.5
