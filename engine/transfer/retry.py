"""Retry policy for the NearShare transfer engine.

Implements an exponential backoff strategy with a configurable maximum
number of attempts.  Used by the transfer loop to decide when and how
long to wait before retransmitting chunks or reconnecting.
"""

from __future__ import annotations

import random

from engine.types import MAX_RETRY_ATTEMPTS


class RetryExhaustedError(Exception):
    """Raised when all retry attempts have been used up."""


class RetryPolicy:
    """Exponential backoff retry policy.

    Attributes:
        max_attempts:  Maximum number of retry attempts before giving up.
        base_delay_s:  Initial delay in seconds before the first retry.
        max_delay_s:   Cap on the backoff delay.
        jitter:        If True, add random jitter to the delay.
    """

    def __init__(
        self,
        max_attempts: int = MAX_RETRY_ATTEMPTS,
        base_delay_s: float = 0.5,
        max_delay_s: float = 30.0,
        jitter: bool = True,
        rng_seed: int | None = None,
    ) -> None:
        self._max_attempts = max_attempts
        self._base_delay_s = base_delay_s
        self._max_delay_s = max_delay_s
        self._jitter = jitter
        self._rng = random.Random(rng_seed)
        self._attempt: int = 0

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def attempt(self) -> int:
        """Current attempt number (0-based)."""
        return self._attempt

    @property
    def exhausted(self) -> bool:
        """True if all retry attempts have been used."""
        return self._attempt >= self._max_attempts

    def next_delay(self) -> float:
        """Compute the delay for the next retry and advance the attempt counter.

        Returns:
            Delay in seconds before the next retry.

        Raises:
            RetryExhaustedError: If max_attempts has been reached.
        """
        if self.exhausted:
            raise RetryExhaustedError(
                f"All {self._max_attempts} retry attempts exhausted"
            )

        # exponential backoff: base * 2^attempt
        delay = self._base_delay_s * (2 ** self._attempt)
        delay = min(delay, self._max_delay_s)

        if self._jitter:
            # add jitter: uniform between 0 and delay
            delay = self._rng.uniform(0, delay)

        self._attempt += 1
        return delay

    def reset(self) -> None:
        """Reset the attempt counter (e.g. after a successful operation)."""
        self._attempt = 0

    def remaining(self) -> int:
        """Number of retry attempts remaining."""
        return max(0, self._max_attempts - self._attempt)
