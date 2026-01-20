"""
Rate limiting algorithms implemented with Redis backend.
"""
import time
from abc import ABC, abstractmethod
from typing import Optional, Tuple
import redis


class RateLimitResult:
    """Result of a rate limit check."""
    def __init__(
        self,
        allowed: bool,
        limit: int,
        remaining: int,
        reset_at: int,
        algorithm: str,
        retry_after: int = 0
    ):
        self.allowed = allowed
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at
        self.algorithm = algorithm
        self.retry_after = retry_after


class RateLimiter(ABC):
    """Base class for rate limiters."""

    @abstractmethod
    def check(self, key: str, cost: int = 1) -> RateLimitResult:
        """Check if a request should be allowed."""
        pass

    @abstractmethod
    def get_usage(self, key: str) -> Tuple[int, int]:
        """Get current usage and limit for a key."""
        pass


class TokenBucketLimiter(RateLimiter):
    """
    Token Bucket Algorithm.

    - Bucket has a maximum capacity
    - Tokens are added at a constant refill rate
    - Each request consumes tokens
    - Allows burst traffic up to bucket capacity
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        capacity: int = 100,
        refill_rate: float = 10.0  # tokens per second
    ):
        self.redis = redis_client
        self.capacity = capacity
        self.refill_rate = refill_rate

        # Lua script for atomic token bucket operation
        self.lua_script = self.redis.register_script("""
            local key = KEYS[1]
            local capacity = tonumber(ARGV[1])
            local refill_rate = tonumber(ARGV[2])
            local now = tonumber(ARGV[3])
            local cost = tonumber(ARGV[4])

            -- Get current bucket state
            local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
            local tokens = tonumber(bucket[1]) or capacity
            local last_refill = tonumber(bucket[2]) or now

            -- Calculate tokens to add based on time elapsed
            local elapsed = now - last_refill
            local refill = elapsed * refill_rate
            tokens = math.min(capacity, tokens + refill)

            -- Check if request can be fulfilled
            local allowed = tokens >= cost
            local remaining = tokens
            local retry_after = 0

            if allowed then
                tokens = tokens - cost
                remaining = tokens
            else
                retry_after = math.ceil((cost - tokens) / refill_rate)
            end

            -- Update bucket state
            redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
            redis.call('EXPIRE', key, 3600)  -- 1 hour TTL

            return {allowed and 1 or 0, math.floor(remaining), retry_after}
        """)

    def check(self, key: str, cost: int = 1) -> RateLimitResult:
        now = time.time()
        result = self.lua_script(
            keys=[f"tb:{key}"],
            args=[self.capacity, self.refill_rate, now, cost]
        )

        allowed = bool(result[0])
        remaining = max(0, int(result[1]))
        retry_after = int(result[2])

        # Reset time is when bucket would be full again
        time_to_full = (self.capacity - remaining) / self.refill_rate
        reset_at = int(now + time_to_full)

        return RateLimitResult(
            allowed=allowed,
            limit=self.capacity,
            remaining=remaining,
            reset_at=reset_at,
            algorithm="token_bucket",
            retry_after=retry_after
        )

    def get_usage(self, key: str) -> Tuple[int, int]:
        bucket = self.redis.hmget(f"tb:{key}", "tokens", "last_refill")
        tokens = float(bucket[0] or self.capacity)
        used = self.capacity - int(tokens)
        return max(0, used), self.capacity


class SlidingWindowLimiter(RateLimiter):
    """
    Sliding Window Log Algorithm.

    - Maintains a log of request timestamps
    - Counts requests in the sliding window
    - More accurate than fixed windows
    - Higher memory usage for high-volume clients
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        limit: int = 100,
        window_seconds: int = 60
    ):
        self.redis = redis_client
        self.limit = limit
        self.window_seconds = window_seconds

        # Lua script for atomic sliding window operation
        self.lua_script = self.redis.register_script("""
            local key = KEYS[1]
            local limit = tonumber(ARGV[1])
            local window = tonumber(ARGV[2])
            local now = tonumber(ARGV[3])
            local cost = tonumber(ARGV[4])

            -- Remove old entries outside the window
            local window_start = now - window
            redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

            -- Count current requests in window
            local current_count = redis.call('ZCARD', key)

            -- Check if request can be allowed
            local allowed = (current_count + cost) <= limit
            local remaining = limit - current_count
            local retry_after = 0

            if allowed then
                -- Add request(s) to the window
                for i = 1, cost do
                    redis.call('ZADD', key, now, now .. ':' .. i .. ':' .. math.random())
                end
                remaining = remaining - cost
            else
                -- Calculate when oldest request will expire
                local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
                if oldest and #oldest > 0 then
                    retry_after = math.ceil((tonumber(oldest[2]) + window) - now)
                else
                    retry_after = 1
                end
            end

            -- Set TTL
            redis.call('EXPIRE', key, window + 10)

            return {allowed and 1 or 0, math.max(0, remaining), retry_after, current_count}
        """)

    def check(self, key: str, cost: int = 1) -> RateLimitResult:
        now = time.time()
        result = self.lua_script(
            keys=[f"sw:{key}"],
            args=[self.limit, self.window_seconds, now, cost]
        )

        allowed = bool(result[0])
        remaining = max(0, int(result[1]))
        retry_after = int(result[2])

        # Reset time is end of current window
        reset_at = int(now + self.window_seconds)

        return RateLimitResult(
            allowed=allowed,
            limit=self.limit,
            remaining=remaining,
            reset_at=reset_at,
            algorithm="sliding_window",
            retry_after=retry_after
        )

    def get_usage(self, key: str) -> Tuple[int, int]:
        now = time.time()
        window_start = now - self.window_seconds

        # Remove expired entries and count
        self.redis.zremrangebyscore(f"sw:{key}", "-inf", window_start)
        count = self.redis.zcard(f"sw:{key}")

        return int(count), self.limit


class SlidingWindowCounterLimiter(RateLimiter):
    """
    Sliding Window Counter Algorithm.

    - Hybrid of fixed window and sliding window
    - Lower memory than sliding log
    - Weighted average of current and previous window
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        limit: int = 100,
        window_seconds: int = 60
    ):
        self.redis = redis_client
        self.limit = limit
        self.window_seconds = window_seconds

        # Lua script for atomic sliding window counter operation
        self.lua_script = self.redis.register_script("""
            local key = KEYS[1]
            local limit = tonumber(ARGV[1])
            local window = tonumber(ARGV[2])
            local now = tonumber(ARGV[3])
            local cost = tonumber(ARGV[4])

            -- Calculate current and previous window
            local current_window = math.floor(now / window)
            local current_key = key .. ':' .. current_window
            local prev_key = key .. ':' .. (current_window - 1)

            -- Get counts
            local current_count = tonumber(redis.call('GET', current_key)) or 0
            local prev_count = tonumber(redis.call('GET', prev_key)) or 0

            -- Calculate weighted count (sliding window approximation)
            local window_progress = (now % window) / window
            local weighted_count = prev_count * (1 - window_progress) + current_count

            -- Check if request can be allowed
            local allowed = (weighted_count + cost) <= limit
            local remaining = limit - weighted_count
            local retry_after = 0

            if allowed then
                redis.call('INCRBY', current_key, cost)
                redis.call('EXPIRE', current_key, window * 2)
                remaining = remaining - cost
            else
                retry_after = math.ceil(window * (1 - window_progress))
            end

            return {allowed and 1 or 0, math.max(0, math.floor(remaining)), retry_after, math.floor(weighted_count)}
        """)

    def check(self, key: str, cost: int = 1) -> RateLimitResult:
        now = time.time()
        result = self.lua_script(
            keys=[f"swc:{key}"],
            args=[self.limit, self.window_seconds, now, cost]
        )

        allowed = bool(result[0])
        remaining = max(0, int(result[1]))
        retry_after = int(result[2])

        # Reset time is end of current window
        current_window = int(now / self.window_seconds)
        reset_at = (current_window + 1) * self.window_seconds

        return RateLimitResult(
            allowed=allowed,
            limit=self.limit,
            remaining=remaining,
            reset_at=int(reset_at),
            algorithm="sliding_window_counter",
            retry_after=retry_after
        )

    def get_usage(self, key: str) -> Tuple[int, int]:
        now = time.time()
        current_window = int(now / self.window_seconds)

        current_count = int(self.redis.get(f"swc:{key}:{current_window}") or 0)
        prev_count = int(self.redis.get(f"swc:{key}:{current_window - 1}") or 0)

        window_progress = (now % self.window_seconds) / self.window_seconds
        weighted_count = prev_count * (1 - window_progress) + current_count

        return int(weighted_count), self.limit
