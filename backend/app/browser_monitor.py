"""Browser heartbeat monitor for the desktop-style local app."""

import os
import threading
import time
import psutil


class BrowserMonitor:
    """Track browser heartbeats and exit when the last page is closed."""

    def __init__(self, enabled: bool = True, timeout: int = 60, startup_grace: int = 30, shutdown_delay: int = 3):
        self.enabled = bool(enabled)
        self.timeout = timeout
        self.startup_grace = startup_grace
        self.shutdown_delay = shutdown_delay
        self.started_at = time.time()
        self.running = False
        self._thread = None
        self._lock = threading.Lock()
        self._sessions = {}
        self._shutdown_requested_at = None

    def ping(self, session_id: str):
        """Record an active browser session."""
        if not self.enabled or not session_id:
            return

        with self._lock:
            self._sessions[session_id] = time.time()
            self._shutdown_requested_at = None

    def disconnect(self, session_id: str):
        """Remove a browser session after page close/unload."""
        if not self.enabled or not session_id:
            return

        with self._lock:
            self._sessions.pop(session_id, None)
            if not self._sessions:
                self._shutdown_requested_at = time.time()

    def _prune_stale_sessions(self, now: float):
        stale_sessions = [
            session_id
            for session_id, last_seen in self._sessions.items()
            if now - last_seen > self.timeout
        ]
        for session_id in stale_sessions:
            self._sessions.pop(session_id, None)

        if stale_sessions and not self._sessions:
            self._shutdown_requested_at = now

    def should_shutdown(self) -> bool:
        """Return True when the app should shut down."""
        if not self.enabled:
            return False

        now = time.time()
        with self._lock:
            self._prune_stale_sessions(now)

            if self._sessions:
                self._shutdown_requested_at = None
                return False

            if now - self.started_at < self.startup_grace:
                return False

            if self._shutdown_requested_at is None:
                self._shutdown_requested_at = now
                return False

            return now - self._shutdown_requested_at >= self.shutdown_delay

    def check_browser(self):
        """Terminate the process after the browser fully disconnects."""
        if not self.running or not self.enabled:
            return

        if self.should_shutdown():
            print("\nBrowser closed. Shutting down...")
            self.running = False
            # Kill all child processes to clean up any lingering consoles
            try:
                current_process = psutil.Process(os.getpid())
                for child in current_process.children(recursive=True):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                # Wait a bit for termination
                time.sleep(0.5)
                for child in current_process.children(recursive=True):
                    try:
                        child.kill()  # Force kill if still alive
                    except psutil.NoSuchProcess:
                        pass
            except Exception as e:
                print(f"Warning: Failed to clean up child processes: {e}")
            os._exit(0)

    def start(self):
        """Start the background watchdog thread."""
        if not self.enabled or self.running:
            return

        self.running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def _monitor_loop(self):
        while self.running:
            time.sleep(2)
            self.check_browser()
