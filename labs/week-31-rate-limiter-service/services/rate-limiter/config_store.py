"""
Configuration store for rate limit configurations.
Stores per-client and per-resource rate limit settings in Redis.
"""
import json
import logging
from typing import Dict, Optional
from dataclasses import dataclass, asdict
import redis

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limit configuration for a client/resource."""
    client_id: str
    resource: str = "*"  # Wildcard for all resources
    algorithm: str = "token_bucket"  # "token_bucket" or "sliding_window"
    limit: int = 100  # Requests per window or bucket capacity
    window_seconds: int = 60  # For sliding window
    refill_rate: float = 10.0  # Tokens per second for token bucket
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RateLimitConfig":
        return cls(**data)


class ConfigStore:
    """Store and retrieve rate limit configurations from Redis."""

    CONFIG_PREFIX = "ratelimit:config:"

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self._default_config = RateLimitConfig(
            client_id="default",
            resource="*",
            algorithm="token_bucket",
            limit=100,
            window_seconds=60,
            refill_rate=10.0,
            enabled=True
        )

    def _make_key(self, client_id: str, resource: str) -> str:
        """Create Redis key for a configuration."""
        # Normalize resource path
        resource = resource.strip("/") if resource != "*" else "*"
        return f"{self.CONFIG_PREFIX}{client_id}:{resource}"

    def get_config(self, client_id: str, resource: str = "*") -> RateLimitConfig:
        """
        Get rate limit configuration for a client/resource.

        Lookup order:
        1. Exact client + resource match
        2. Client + wildcard resource
        3. Default configuration
        """
        # Try exact match
        key = self._make_key(client_id, resource)
        data = self.redis.get(key)
        if data:
            config = RateLimitConfig.from_dict(json.loads(data))
            logger.debug(f"Found exact config for {client_id}/{resource}")
            return config

        # Try wildcard resource
        if resource != "*":
            key = self._make_key(client_id, "*")
            data = self.redis.get(key)
            if data:
                config = RateLimitConfig.from_dict(json.loads(data))
                logger.debug(f"Found wildcard config for {client_id}/*")
                return config

        # Try default client
        key = self._make_key("default", "*")
        data = self.redis.get(key)
        if data:
            config = RateLimitConfig.from_dict(json.loads(data))
            logger.debug(f"Found default config")
            return config

        logger.debug(f"Using built-in default config")
        return self._default_config

    def set_config(self, config: RateLimitConfig) -> RateLimitConfig:
        """Store rate limit configuration."""
        key = self._make_key(config.client_id, config.resource)
        self.redis.set(key, json.dumps(config.to_dict()))
        logger.info(f"Updated config for {config.client_id}/{config.resource}")
        return config

    def delete_config(self, client_id: str, resource: str = "*") -> bool:
        """Delete a rate limit configuration."""
        key = self._make_key(client_id, resource)
        result = self.redis.delete(key)
        logger.info(f"Deleted config for {client_id}/{resource}")
        return result > 0

    def list_configs(self, client_id: Optional[str] = None) -> list[RateLimitConfig]:
        """List all configurations, optionally filtered by client_id."""
        pattern = f"{self.CONFIG_PREFIX}{client_id}:*" if client_id else f"{self.CONFIG_PREFIX}*"
        configs = []

        for key in self.redis.scan_iter(match=pattern):
            data = self.redis.get(key)
            if data:
                configs.append(RateLimitConfig.from_dict(json.loads(data)))

        return configs

    def set_default_config(
        self,
        algorithm: str = "token_bucket",
        limit: int = 100,
        window_seconds: int = 60,
        refill_rate: float = 10.0
    ):
        """Set the default configuration."""
        config = RateLimitConfig(
            client_id="default",
            resource="*",
            algorithm=algorithm,
            limit=limit,
            window_seconds=window_seconds,
            refill_rate=refill_rate,
            enabled=True
        )
        self.set_config(config)
        self._default_config = config
