"""Infrastructure adapters: PostgreSQL storage, Redis cache, Bloom filter and rate limiting.

Each implements a port the service already uses (Repository, RateLimiter), so the service code does not
change when they are enabled. Their tests need real servers and run in CI's infrastructure job.
"""
