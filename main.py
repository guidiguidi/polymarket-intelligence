import os
import json
import time
import logging
import hashlib
import requests
import sqlite3
from datetime import datetime, timedelta
from colorama import init, Fore, Style
from urllib.parse import urlparse
import random
import config

init(autoreset=True)


class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.WHITE,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelname, "")
        base_msg = super().format(record)
        return f"{color}{base_msg}{Style.RESET_ALL}"


def setup_logger():
    log_level_name = str(getattr(config, "LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(ColorFormatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))

    logger.addHandler(console_handler)
    return logger


logger = setup_logger()

session = requests.Session()
api_cache = {}

CACHE_FILE_NAME = "api_cache.json"
DEFAULT_CACHE_TTL_MINUTES = getattr(config, "CACHE_TTL_MINUTES", 30)
PROFILE_CACHE_TTL_MINUTES = getattr(config, "PROFILE_CACHE_TTL_MINUTES", 1440)
COMMENTS_CACHE_TTL_MINUTES = getattr(config, "COMMENTS_CACHE_TTL_MINUTES", 30)
EVENTS_CACHE_TTL_MINUTES = getattr(config, "EVENTS_CACHE_TTL_MINUTES", 2)

# Simple per-host rate limiter state
host_last_request = {}

# Simple metrics counters (in-memory)
metrics = {
    'requests': 0,
    'success': 0,
    'status_429': 0,
    'status_5xx': 0,
    'errors': 0,
    'latencies': []
}

def rate_limit_for_host(host, rps):
    try:
        min_interval = 1.0 / float(rps)
    except Exception:
        return
    last = host_last_request.get(host)
    now_ts = time.time()
    if last is not None:
        elapsed = now_ts - last
        if elapsed < min_interval:
            to_sleep = min_interval - elapsed
            logger.debug(f"Rate limiting {host}: sleeping {to_sleep:.3f}s to respect {rps} rps")
            time.sleep(to_sleep)
    host_last_request[host] = time.time()


def setup_data_dir():
    if not os.path.exists(config.DATA_DIR):
        os.makedirs(config.DATA_DIR)
        logger.info(f"Created directory: {config.DATA_DIR}")
    init_db()


def init_db():
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        # Create records table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                display_name TEXT,
                name TEXT,
                pseudonym TEXT,
                bio TEXT,
                x_username TEXT,
                twitter_url TEXT,
                market_question TEXT,
                event_id TEXT,
                timestamp TEXT,
                comments_count INTEGER,
                source TEXT
            )
        ''')
        
        # Create processed_usernames table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_usernames (
                username TEXT PRIMARY KEY
            )
        ''')

        # Create processed_wallets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_wallets (
                wallet TEXT PRIMARY KEY
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {config.DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")


def get_cache_file_path():
    return os.path.join(config.DATA_DIR, CACHE_FILE_NAME)


def load_processed_entities():
    usernames = set()
    wallets = set()
    
    # Load from TXT file (legacy usernames)
    if os.path.exists(config.PROCESSED_USERNAMES_FILE):
        try:
            with open(config.PROCESSED_USERNAMES_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    u = line.strip().lower()
                    if u:
                        usernames.add(u)
        except Exception as e:
            logger.warning(f"Error loading processed usernames from file: {e}")

    # Load from Database
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('SELECT username FROM processed_usernames')
        for row in cursor.fetchall():
            usernames.add(row[0].lower())
            
        cursor.execute('SELECT wallet FROM processed_wallets')
        for row in cursor.fetchall():
            wallets.add(row[0].lower())
            
        conn.close()
    except Exception as e:
        logger.error(f"Error loading processed entities from database: {e}")
        
    return usernames, wallets


def save_processed_entity(username: str, wallet: str):
    username = username.lower()
    wallet = wallet.lower()
    
    # Save username to TXT file
    try:
        with open(config.PROCESSED_USERNAMES_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{username}\n")
    except Exception as e:
        logger.warning(f"Error saving processed username to file: {e}")

    # Save to Database
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO processed_usernames (username) VALUES (?)', (username,))
        cursor.execute('INSERT OR IGNORE INTO processed_wallets (wallet) VALUES (?)', (wallet,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error saving processed entity to database: {e}")


def save_data(record):
    # Save to TXT file
    try:
        txt_line = (
            f"{record['wallet']}|{record['x_username']}|{record['twitter_url']}|"
            f"{record['market_question']}|{record['event_id']}|"
            f"{record['timestamp']}|{record['comments_count']}\n"
        )
        with open(config.TXT_FILE, 'a', encoding='utf-8') as f:
            f.write(txt_line)
    except Exception as e:
        logger.warning(f"Error saving to TXT: {e}")

    # Save to JSON file
    try:
        data = []
        if os.path.exists(config.JSON_FILE):
            try:
                with open(config.JSON_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                data = []
        data.append(record)
        with open(config.JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Error saving to JSON: {e}")

    # Save to Database
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO records (
                wallet, display_name, name, pseudonym, bio, 
                x_username, twitter_url, market_question, 
                event_id, timestamp, comments_count, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            record.get('wallet'),
            record.get('display_name'),
            record.get('name'),
            record.get('pseudonym'),
            record.get('bio'),
            record.get('x_username'),
            record.get('twitter_url'),
            record.get('market_question'),
            record.get('event_id'),
            record.get('timestamp'),
            record.get('comments_count'),
            record.get('source')
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error saving to Database: {e}")


def load_cache():
    global api_cache
    cache_path = get_cache_file_path()

    if not os.path.exists(cache_path):
        api_cache = {}
        return

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
            if isinstance(raw, dict):
                api_cache = raw
            else:
                api_cache = {}
        logger.info(f"Loaded API cache: {len(api_cache)} entries")
    except Exception as e:
        logger.warning(f"Failed to load cache: {type(e).__name__}: {e}")
        api_cache = {}


def save_cache():
    cache_path = get_cache_file_path()
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(api_cache, f, indent=2, ensure_ascii=False)
        logger.debug(f"Cache saved: {len(api_cache)} entries")
    except Exception as e:
        logger.warning(f"Failed to save cache: {type(e).__name__}: {e}")


def cleanup_cache():
    now = datetime.now()
    to_delete = []

    for key, value in api_cache.items():
        if not isinstance(value, dict):
            to_delete.append(key)
            continue

        expires_at = value.get("expires_at")
        if not expires_at:
            to_delete.append(key)
            continue

        try:
            expires_dt = datetime.fromisoformat(expires_at)
            if now >= expires_dt:
                to_delete.append(key)
        except Exception:
            to_delete.append(key)

    for key in to_delete:
        api_cache.pop(key, None)

    if to_delete:
        logger.debug(f"Cache cleanup removed {len(to_delete)} expired entries")


def build_cache_key(method: str, url: str, params=None, json_data=None):
    payload = {
        "method": method.upper(),
        "url": url,
        "params": params or {},
        "json": json_data or {}
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.md5(raw.encode('utf-8')).hexdigest()
    return f"{method.upper()}:{digest}"


def get_cached_value(cache_key: str):
    item = api_cache.get(cache_key)
    if not item or not isinstance(item, dict):
        return None

    expires_at = item.get("expires_at")
    if not expires_at:
        api_cache.pop(cache_key, None)
        return None

    try:
        expires_dt = datetime.fromisoformat(expires_at)
        if datetime.now() >= expires_dt:
            api_cache.pop(cache_key, None)
            return None
    except Exception:
        api_cache.pop(cache_key, None)
        return None

    logger.debug(f"CACHE HIT: {cache_key}")
    return item.get("data")


def set_cached_value(cache_key: str, data, ttl_minutes: int):
    expires_at = (datetime.now() + timedelta(minutes=ttl_minutes)).isoformat()
    api_cache[cache_key] = {
        "expires_at": expires_at,
        "data": data
    }


def fetch_json(url, params=None, ttl_minutes=DEFAULT_CACHE_TTL_MINUTES, use_cache=True):
    cache_key = build_cache_key("GET", url, params=params)

    if use_cache:
        cached = get_cached_value(cache_key)
        if cached is not None:
            return cached

    # Prepare retry/backoff configuration
    max_retries = getattr(config, 'POLYMARKET_MAX_RETRIES', 5)
    backoff_base = getattr(config, 'POLYMARKET_BACKOFF_BASE', 0.5)

    try:
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
        except Exception:
            host = ""

        attempt = 0
        while True:
            if 'polymarket.com' in host:
                rps = getattr(config, 'POLYMARKET_RATE_LIMIT_RPS', None)
                if rps:
                    rate_limit_for_host(host, rps)

            logger.debug(f"REQUEST: GET {url} params={params} attempt={attempt}")
            t0 = time.time()
            try:
                response = session.get(url, params=params, timeout=15)
                latency = time.time() - t0
                metrics['requests'] += 1
                # Successful
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except Exception:
                        data = None
                    metrics['success'] += 1
                    metrics['latencies'].append(latency)
                    logger.debug(f"RESPONSE: {response.status_code} latency={latency:.3f}s - {len(str(data))} bytes")
                    if use_cache:
                        set_cached_value(cache_key, data, ttl_minutes)
                    return data

                # Rate limited
                if response.status_code == 429:
                    metrics['status_429'] += 1
                    ra = response.headers.get('Retry-After') or response.headers.get('retry-after')
                    if ra:
                        try:
                            sleep_sec = int(float(ra))
                        except Exception:
                            sleep_sec = backoff_base * (2 ** attempt) + random.uniform(0, backoff_base)
                    else:
                        sleep_sec = backoff_base * (2 ** attempt) + random.uniform(0, backoff_base)
                    logger.warning(f"Received 429 from {host}; sleeping {sleep_sec:.2f}s (attempt {attempt})")
                    time.sleep(sleep_sec)
                    attempt += 1
                    if attempt > max_retries:
                        logger.error(f"Exceeded max retries ({max_retries}) for {url}")
                        return None
                    continue

                # Server error - retry
                if response.status_code >= 500:
                    metrics['status_5xx'] += 1
                    sleep_sec = backoff_base * (2 ** attempt) + random.uniform(0, backoff_base)
                    logger.warning(f"Server error {response.status_code} from {host}; sleeping {sleep_sec:.2f}s (attempt {attempt})")
                    time.sleep(sleep_sec)
                    attempt += 1
                    if attempt > max_retries:
                        logger.error(f"Exceeded max retries ({max_retries}) for {url}")
                        return None
                    continue

                # Other non-success status
                logger.debug(f"Non-200 response {response.status_code} from {url}")
                return None

            except requests.RequestException as e:
                latency = time.time() - t0
                metrics['errors'] += 1
                logger.warning(f"RequestException for {url}: {type(e).__name__}: {e}; latency={latency:.3f}s")
                attempt += 1
                if attempt > max_retries:
                    logger.error(f"Exceeded max retries ({max_retries}) for {url} after exceptions")
                    return None
                sleep_sec = backoff_base * (2 ** (attempt-1)) + random.uniform(0, backoff_base)
                time.sleep(sleep_sec)
                continue

    except Exception as e:
        logger.error(f"Error fetching {url}: {type(e).__name__}: {e}")
        return None


def fetch_json_post(url, json_data=None):
    try:
        logger.debug(f"REQUEST: POST {url} json={json_data}")
        response = session.post(url, json=json_data, timeout=15)
        response.raise_for_status()
        data = response.json()
        logger.debug(f"RESPONSE: {response.status_code} - {len(str(data))} bytes")
        return data
    except Exception as e:
        logger.error(f"Error posting to {url}: {type(e).__name__}: {e}")
        return None


def get_profile(wallet):
    cache_key = f"profile:{wallet.lower()}"
    cached = get_cached_value(cache_key)
    if cached is not None:
        return cached

    urls_to_try = [
        f"https://polymarket.com/api/profile/userData?address={wallet}",
        f"{config.GAMMA_API_URL}/users/{wallet}",
        f"{config.GAMMA_API_URL}/user/{wallet}",
    ]

    for url in urls_to_try:
        logger.debug(f"Trying GET {url}")
        data = fetch_json(url, params=None, ttl_minutes=PROFILE_CACHE_TTL_MINUTES, use_cache=True)
        if data:
            set_cached_value(cache_key, data, PROFILE_CACHE_TTL_MINUTES)
            logger.debug(f"Success from {url}")
            return data

    logger.debug(f"All profile endpoints failed for {wallet}")
    return None


def get_comments(event_id):
    event_cache_key = f"comments:event:{event_id}"
    cached = get_cached_value(event_cache_key)
    if cached is not None:
        return cached

    url = f"{config.GAMMA_API_URL}/comments"
    all_comments = []
    offset = 0
    limit = 100

    logger.debug(f"Fetching all comments for event_id: {event_id}")

    while True:
        params = {
            'get_positions': 'true',
            'get_reports': 'true',
            'parent_entity_type': 'Event',
            'parent_entity_id': event_id,
            'ascending': 'false',
            'holders_only': 'false',
            'order': 'createdAt',
            'limit': str(limit),
            'offset': str(offset)
        }

        logger.debug(f"Fetching comments offset={offset} limit={limit}")
        response = fetch_json(
            url,
            params=params,
            ttl_minutes=COMMENTS_CACHE_TTL_MINUTES,
            use_cache=True
        )

        if not response:
            logger.debug(f"No response for offset={offset}")
            break

        if isinstance(response, list):
            batch = response
        elif isinstance(response, dict):
            batch = response.get('comments', response.get('data', []))
        else:
            batch = []

        if not batch:
            logger.debug(f"No more comments at offset={offset}")
            break

        all_comments.extend(batch)
        logger.debug(f"Got {len(batch)} comments, total: {len(all_comments)}")

        if len(batch) < limit:
            logger.debug(f"Batch size ({len(batch)}) < limit ({limit}), stopping pagination")
            break

        offset += limit

    set_cached_value(event_cache_key, all_comments, COMMENTS_CACHE_TTL_MINUTES)
    logger.debug(f"Retrieved total {len(all_comments)} comments for event {event_id}")
    return all_comments


def get_latest_events():
    cache_key = "events:all_active"
    cached = get_cached_value(cache_key)
    if cached is not None:
        return cached

    url = f"{config.GAMMA_API_URL}/events"
    all_events = []
    offset = 0
    limit = 100  # Максимальный размер батча для API

    logger.info("Fetching ALL active events from Polymarket...")

    while True:
        params = {
            'active': 'true',
            'closed': 'false',
            'limit': str(limit),
            'offset': str(offset)
        }
        
        logger.debug(f"Fetching events offset={offset} limit={limit}")
        events = fetch_json(
            url,
            params=params,
            ttl_minutes=EVENTS_CACHE_TTL_MINUTES,
            use_cache=True
        )

        if not events or not isinstance(events, list):
            logger.debug(f"No more events at offset={offset}")
            break

        all_events.extend(events)
        logger.debug(f"Got {len(events)} events, total: {len(all_events)}")

        if len(events) < limit:
            break

        offset += limit

    if all_events:
        logger.info(f"Retrieved TOTAL {len(all_events)} active events")
        set_cached_value(cache_key, all_events, EVENTS_CACHE_TTL_MINUTES)
    
    return all_events


def normalize_username(username: str) -> str:
    if not username:
        return ""
    username = username.strip()
    if username.startswith("@"):
        username = username[1:]
    return username.strip().lower()


def extract_twitter_data(profile, wallet):
    twitter_url = None
    x_username = None

    if profile.get('twitter'):
        twitter_url = str(profile.get('twitter')).strip()
        x_username = twitter_url.rstrip('/').split('/')[-1]
        if not x_username.startswith('@'):
            x_username = f"@{x_username}"

    elif profile.get('xUsername'):
        x_username = str(profile.get('xUsername')).strip()
        if not x_username.startswith('@'):
            x_username = f"@{x_username}"
        twitter_url = f"https://x.com/{x_username.lstrip('@')}"

    else:
        profile_api = get_profile(wallet)
        if profile_api and isinstance(profile_api, dict):
            api_twitter = profile_api.get('xUsername') or profile_api.get('twitter')
            if api_twitter and isinstance(api_twitter, str):
                api_twitter = api_twitter.strip()
                if api_twitter.startswith('http'):
                    twitter_url = api_twitter
                    x_username = api_twitter.rstrip('/').split('/')[-1]
                    if not x_username.startswith('@'):
                        x_username = f"@{x_username}"
                else:
                    if not api_twitter.startswith('@'):
                        x_username = f"@{api_twitter}"
                    else:
                        x_username = api_twitter
                    twitter_url = f"https://x.com/{x_username.lstrip('@')}"

    if x_username:
        username_key = normalize_username(x_username)
        x_username = f"@{username_key}"
        twitter_url = f"https://x.com/{username_key}"
        return x_username, twitter_url

    return None, None


def process_event(event, processed_usernames, processed_wallets):
    event_id = event.get('id')
    title = event.get('title', '')

    logger.debug(f"Processing event: {event_id} - {title}")

    if config.KEYWORDS:
        if not any(kw.lower() in title.lower() for kw in config.KEYWORDS):
            logger.debug(f"Event {event_id} skipped - no matching keywords")
            return

    comments = get_comments(event_id)
    if not comments:
        logger.debug(f"Event {event_id} skipped - no comments")
        return

    comments_count = len(comments)
    if not (config.MIN_COMMENTS <= comments_count <= config.MAX_COMMENTS):
        logger.debug(
            f"Event {event_id} skipped - {comments_count} comments "
            f"(min={config.MIN_COMMENTS}, max={config.MAX_COMMENTS})"
        )
        return

    logger.info(f"Processing event: {title} (ID: {event_id}, Comments: {comments_count})")

    saved_count = 0
    no_x_count = 0
    skipped_count = 0
    seen_usernames_in_event = set()
    seen_wallets_in_event = set()

    for idx, comment in enumerate(comments):
        if idx == 0:
            logger.info("Processing first comment...")

        wallet = comment.get('userAddress') if isinstance(comment, dict) else None
        if not wallet:
            logger.debug("Comment has no userAddress")
            skipped_count += 1
            continue

        wallet_key = wallet.lower()
        
        # 1. Local check (within this event)
        if wallet_key in seen_wallets_in_event:
            skipped_count += 1
            logger.debug(f"SKIP | Wallet {wallet[:8]} already seen in this event")
            continue
        seen_wallets_in_event.add(wallet_key)

        # 2. Global check (across all events)
        if wallet_key in processed_wallets:
            skipped_count += 1
            logger.debug(f"SKIP | Wallet {wallet[:8]} already processed globally")
            continue

        profile = comment.get('profile', {}) or {}
        # ... (rest of profile extraction)
        name = profile.get('name', '')
        pseudonym = profile.get('pseudonym', '')
        bio = profile.get('bio', '')
        display_name = pseudonym or name or wallet[:10]

        x_username, twitter_url = extract_twitter_data(profile, wallet)

        if not (x_username and twitter_url):
            no_x_count += 1
            logger.warning(f"NO X | {display_name} | {wallet[:8]}...")
            continue

        username_key = normalize_username(x_username)

        if username_key in seen_usernames_in_event:
            skipped_count += 1
            logger.debug(f"SKIP | Username {x_username} duplicated inside event")
            continue

        seen_usernames_in_event.add(username_key)

        if username_key in processed_usernames:
            skipped_count += 1
            logger.debug(f"SKIP | Username {x_username} already processed globally")
            continue

        record = {
            "wallet": wallet,
            "display_name": display_name,
            "name": name,
            "pseudonym": pseudonym,
            "bio": bio,
            "x_username": x_username,
            "twitter_url": twitter_url,
            "market_question": title,
            "event_id": event_id,
            "timestamp": datetime.now().isoformat(),
            "comments_count": comments_count,
            "source": "polymarket"
        }

        save_data(record)
        save_processed_entity(username_key, wallet_key)
        processed_usernames.add(username_key)
        processed_wallets.add(wallet_key)
        saved_count += 1

        logger.info(
            f"{Fore.GREEN}{Style.BRIGHT}VALID{Style.RESET_ALL} | "
            f"{display_name} | {x_username} | {wallet[:8]}..."
        )

        if saved_count == 1:
            logger.info(f"{Fore.GREEN}{Style.BRIGHT}FIRST VALID RECORD SAVED{Style.RESET_ALL}")

    logger.info(
        f"Event done | VALID: {saved_count} | NO_X: {no_x_count} | SKIPPED: {skipped_count}"
    )


def main():
    setup_data_dir()
    load_cache()
    cleanup_cache()

    processed_usernames, processed_wallets = load_processed_entities()
    logger.info(f"Loaded {len(processed_usernames)} processed usernames and {len(processed_wallets)} processed wallets.")

    if config.TEST_MODE:
        logger.info("TEST MODE enabled: getting first event from top 50...")
        events = get_latest_events()
        if events:
            config.TEST_EVENT_ID = events[0].get('id')
            logger.info(
                f"TEST MODE event: {config.TEST_EVENT_ID} - "
                f"{events[0].get('title', 'Unknown')}"
            )
        else:
            logger.error("Could not fetch events for test mode")
            return
    else:
        logger.info("Starting Polymarket Twitter Grabber 24/7...")

    iteration = 0
    while True:
        try:
            iteration += 1
            logger.info(f"--- Cycle #{iteration} ---")
            cleanup_cache()

            if config.TEST_MODE:
                url = f"{config.GAMMA_API_URL}/events"
                params = {'id': config.TEST_EVENT_ID}
                logger.debug(f"TEST MODE: Fetching event {config.TEST_EVENT_ID}")
                events = fetch_json(url, params=params, ttl_minutes=EVENTS_CACHE_TTL_MINUTES, use_cache=True)
                if events:
                    events = [events] if isinstance(events, dict) else events
            else:
                events = get_latest_events()

            if events:
                for event in events:
                    process_event(event, processed_usernames, processed_wallets)
            else:
                logger.warning("No events fetched this cycle.")

            save_cache()

            if config.TEST_MODE:
                logger.info("TEST MODE cycle complete, exiting...")
                break

            logger.info(f"Cycle complete. Waiting {config.CHECK_INTERVAL_MINUTES} minutes...")
            time.sleep(config.CHECK_INTERVAL_MINUTES * 60)

        except KeyboardInterrupt:
            save_cache()
            logger.info("Script stopped by user.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {type(e).__name__}: {e}")
            save_cache()
            time.sleep(60)


if __name__ == "__main__":
    main()
