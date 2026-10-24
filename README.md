# Polymarket Intelligence & Social Graph Crawler

A resilient, rate-limited data pipeline that extracts active prediction market participants from **Polymarket** and cross-references on-chain crypto wallets with Web2 social identities (Twitter/X).

## Key Engineering Features

- **Polymarket Gamma API Ingestion:** Crawls active prediction markets with cursor pagination (`limit` & `offset`) and fetches full comment threads.
- **Social Graph Entity Resolution:** Cross-references wallet addresses (`/users/{wallet}` and profile endpoints) to discover linked Twitter/X usernames, bios, and pseudonyms.
- **Production Resilience & Rate Limiting:**
  - Dynamic host-level RPS limiter (`rate_limit_for_host`).
  - **HTTP 429 Backoff:** Parses `Retry-After` headers with exponential backoff and randomized jitter to prevent rate-limit penalties.
  - Multi-tier TTL caching (MD5 request signature hashing) saving up to 90% of redundant API queries.
- **Multi-Sink Storage:** Simultaneously persists records to SQLite (`polymarket.db`), JSON, and flat pipe-delimited files.

## Data Pipeline Flow

```
Polymarket Gamma API -> Event Crawler -> Comments Parser -> Wallet Extraction -> Profile Resolver (Twitter/X) -> Multi-Tier Cache -> SQLite / JSON
```

## Setup & Usage

```bash
pip install -r requirements.txt
python main.py
```

Sample extracted records can be found in `data/sample_records.json`.
