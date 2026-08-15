"""Tests for RetryPolicy — backoff, max-attempts cutoff, and jitter."""

import pytest

from engine.transfer.retry import RetryExhaustedError, RetryPolicy
from engine.types import MAX_RETRY_ATTEMPTS


# backoff behavior

class TestBackoff:
    """Exponential backoff delay computation."""

    def test_delays_increase_exponentially(self) -> None:
        """Without jitter, delays should double each attempt."""
        rp = RetryPolicy(max_attempts=5, base_delay_s=1.0, jitter=False)
        delays = [rp.next_delay() for _ in range(5)]
        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]

    def test_delay_capped_at_max(self) -> None:
        rp = RetryPolicy(
            max_attempts=10, base_delay_s=1.0, max_delay_s=5.0, jitter=False
        )
        delays = [rp.next_delay() for _ in range(6)]
        # 1, 2, 4, 5, 5, 5 (capped at 5)
        assert delays == [1.0, 2.0, 4.0, 5.0, 5.0, 5.0]

    def test_first_delay_equals_base(self) -> None:
        rp = RetryPolicy(base_delay_s=0.25, jitter=False)
        assert rp.next_delay() == 0.25

    def test_jitter_reduces_delay(self) -> None:
        """With jitter, delay should be between 0 and the computed backoff."""
        rp = RetryPolicy(
            max_attempts=5, base_delay_s=1.0, jitter=True, rng_seed=42
        )
        for i in range(5):
            max_expected = min(1.0 * (2 ** i), 30.0)
            delay = rp.next_delay()
            assert 0 <= delay <= max_expected

    def test_jitter_is_deterministic_with_seed(self) -> None:
        delays_a = []
        delays_b = []
        for _ in range(2):
            rp = RetryPolicy(max_attempts=4, jitter=True, rng_seed=99)
            if not delays_a:
                delays_a = [rp.next_delay() for _ in range(4)]
            else:
                delays_b = [rp.next_delay() for _ in range(4)]
        assert delays_a == delays_b


# max-attempts cutoff

class TestMaxAttempts:
    """RetryPolicy stops after max_attempts."""

    def test_exhausted_after_max(self) -> None:
        rp = RetryPolicy(max_attempts=3, jitter=False)
        for _ in range(3):
            rp.next_delay()
        assert rp.exhausted is True

    def test_raises_when_exhausted(self) -> None:
        rp = RetryPolicy(max_attempts=2, jitter=False)
        rp.next_delay()
        rp.next_delay()
        with pytest.raises(RetryExhaustedError, match="exhausted"):
            rp.next_delay()

    def test_default_max_attempts(self) -> None:
        rp = RetryPolicy()
        assert rp.max_attempts == MAX_RETRY_ATTEMPTS

    def test_remaining_decreases(self) -> None:
        rp = RetryPolicy(max_attempts=3, jitter=False)
        assert rp.remaining() == 3
        rp.next_delay()
        assert rp.remaining() == 2
        rp.next_delay()
        assert rp.remaining() == 1
        rp.next_delay()
        assert rp.remaining() == 0


# reset

class TestReset:
    """Reset allows reuse after success."""

    def test_reset_clears_attempt_counter(self) -> None:
        rp = RetryPolicy(max_attempts=2, jitter=False)
        rp.next_delay()
        rp.next_delay()
        assert rp.exhausted is True
        rp.reset()
        assert rp.exhausted is False
        assert rp.attempt == 0
        assert rp.remaining() == 2

    def test_reset_restarts_backoff(self) -> None:
        rp = RetryPolicy(max_attempts=3, base_delay_s=1.0, jitter=False)
        rp.next_delay()  # 1.0
        rp.next_delay()  # 2.0
        rp.reset()
        assert rp.next_delay() == 1.0  # back to base


# attempt tracking

class TestAttemptTracking:
    """Attempt counter increments correctly."""

    def test_starts_at_zero(self) -> None:
        rp = RetryPolicy()
        assert rp.attempt == 0

    def test_increments_on_next_delay(self) -> None:
        rp = RetryPolicy(max_attempts=3, jitter=False)
        rp.next_delay()
        assert rp.attempt == 1
        rp.next_delay()
        assert rp.attempt == 2
