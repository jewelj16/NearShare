"""UI utilities for displaying transfer progress."""

import sys
import time


class ProgressBar:
    """A terminal progress bar with a flight animation and live speed."""

    def __init__(self, width: int = 40):
        self.width = width
        self.start_time: float | None = None

    def start(self) -> None:
        """Start the progress bar timer."""
        self.start_time = time.monotonic()
        self.update(0, 1)

    def update(self, bytes_done: int, total_bytes: int) -> None:
        """Update the progress bar with current progress."""
        if self.start_time is None:
            self.start_time = time.monotonic()

        if total_bytes == 0:
            percent = 100.0
            fraction = 1.0
        else:
            percent = min(100.0, (bytes_done / total_bytes) * 100.0)
            fraction = min(1.0, bytes_done / total_bytes)

        elapsed = time.monotonic() - self.start_time
        if elapsed > 0 and bytes_done > 0:
            speed_bps = bytes_done / elapsed
            speed_mibs = speed_bps / (1024 * 1024)
            speed_str = f"{speed_mibs:.1f} MiB/s"
        else:
            speed_str = "0.0 MiB/s"

        pos = int(fraction * self.width)

        if pos == 0:
            bar = "✈️" + " " * (self.width - 1)
        elif pos >= self.width:
            bar = "=" * (self.width - 1) + "✈️"
        else:
            bar = "=" * pos + "✈️" + " " * (self.width - pos - 1)

        sys.stdout.write(f"\r[{bar}] {percent:5.1f}% | {speed_str}")
        sys.stdout.flush()

    def finish(self) -> None:
        """Complete the progress bar and print a newline."""
        sys.stdout.write("\n")
        sys.stdout.flush()
