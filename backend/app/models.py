"""Data models for the checker application."""

import threading
from typing import List


class CheckerState:
    """Manage the state of the current domain checking job."""

    def __init__(self):
        self.lock = threading.Lock()
        self._stop_event = threading.Event()
        self._reset_locked()

    def _reset_locked(self):
        self.running = False
        self.stop_requested = False
        self.total = 0
        self.checked = 0
        self.stage = "idle"
        self.final_total = 0
        self.final_checked = 0
        self.final_errors = 0
        self.available: List[str] = []
        self.taken: List[str] = []
        self.invalid: List[str] = []
        self.errors: List[str] = []
        self.current_domain = ""
        self.message = ""
        self._stop_event.clear()

    def begin_run(self, total: int, message: str = "Started (DNS prefilter)") -> bool:
        """Atomically reserve the state for a new job."""
        with self.lock:
            if self.running:
                return False

            self._reset_locked()
            self.running = True
            self.stage = "dns"
            self.total = max(0, int(total))
            self.message = message
            return True

    def request_stop(self) -> bool:
        """Request cancellation for the active job."""
        with self.lock:
            if not self.running:
                return False

            self.stop_requested = True
            self.stage = "stopping"
            self.message = "Stopping after current in-flight requests..."
            self._stop_event.set()
            return True

    def is_stop_requested(self) -> bool:
        """Return True when the current job should stop."""
        return self._stop_event.is_set()

    def finish(self, stage: str = "done", message: str = "Done!"):
        """Mark the current job as finished."""
        with self.lock:
            self.running = False
            self.stop_requested = False
            self.stage = stage
            self.message = message
            self._stop_event.clear()

    def fail(self, message: str):
        """Mark the current job as failed."""
        self.finish(stage="error", message=message)

    def to_dict(self):
        """Convert state to a JSON-safe dictionary."""
        with self.lock:
            if self.stage in ("final", "done", "stopping", "stopped"):
                denom = self.total + self.final_total
                numer = self.checked + self.final_checked
            else:
                denom = self.total
                numer = self.checked

            return {
                "running": self.running,
                "stop_requested": self.stop_requested,
                "stage": self.stage,
                "total": self.total,
                "checked": self.checked,
                "final_total": self.final_total,
                "final_checked": self.final_checked,
                "final_errors": self.final_errors,
                "available": len(self.available),
                "taken": len(self.taken),
                "invalid": len(self.invalid),
                "errors": len(self.errors),
                "current_domain": self.current_domain,
                "message": self.message,
                "progress_pct": int((numer / denom * 100) if denom > 0 else 0),
            }

    def reset(self):
        """Reset state to its initial values."""
        with self.lock:
            self._reset_locked()
