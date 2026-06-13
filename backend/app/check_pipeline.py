"""DNS + RDAP checking pipeline.

Orchestrates the two-stage domain-availability check:
  1. DNS prefilter (parallel, via dnspython)
  2. RDAP final check (parallel, on available/error candidates)
"""

import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .models import CheckerState
from .services import dns_check, rdap_check
from .utils import dedupe


def _get_domain_tld(domain: str) -> str:
    d = (domain or "").strip().lower().rstrip(".")
    if "." not in d:
        return ""
    return d.rsplit(".", 1)[-1]


def _run_thread_pool(items, worker, max_workers: int, max_in_flight: int = None, should_cancel=None):
    """Drive *worker* over *items* with bounded concurrency and backpressure."""
    max_workers = max(1, int(max_workers))
    if max_in_flight is None:
        max_in_flight = max_workers * 4
    max_in_flight = max(max_workers, int(max_in_flight))

    iterator = iter(items)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict = {}

        def _submit_next() -> bool:
            if should_cancel and should_cancel():
                return False
            try:
                item = next(iterator)
            except StopIteration:
                return False
            futures[executor.submit(worker, item)] = item
            return True

        while len(futures) < max_in_flight and _submit_next():
            pass

        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for fut in done:
                futures.pop(fut, None)
                try:
                    fut.result()
                except Exception:
                    pass
            while len(futures) < max_in_flight and _submit_next():
                pass


def _dedupe_results(state: CheckerState):
    with state.lock:
        state.available = dedupe(state.available)
        state.taken = dedupe(state.taken)
        state.invalid = dedupe(state.invalid)
        state.errors = dedupe(state.errors)


def run_check(
    state: CheckerState,
    domains_raw: list,
    threads: int,
    rdap_recheck_errors: bool,
    final_check_enabled: bool = True,
    final_check_workers: int = 12,
    dns_strict_tlds: list = None,
):
    """Main two-stage checking pipeline (runs in a background thread)."""
    try:
        strict_set = set(dns_strict_tlds or [])

        with state.lock:
            state.stage = "dns"
            state.total = len(domains_raw)
            state.checked = 0
            state.final_total = 0
            state.final_checked = 0
            state.final_errors = 0
            state.available = []
            state.taken = []
            state.invalid = []
            state.errors = []
            state.current_domain = ""
            state.message = "Started (DNS prefilter)"

        def dns_worker(domain: str):
            if state.is_stop_requested():
                return
            try:
                result = dns_check(domain)
            except Exception:
                result = "error"
            if state.is_stop_requested():
                return
            tld = _get_domain_tld(domain)
            is_strict = bool(strict_set) and tld in strict_set
            with state.lock:
                if state.stop_requested:
                    return
                if result == "available":
                    state.available.append(domain)
                elif result == "taken":
                    state.taken.append(domain)
                elif result == "invalid":
                    state.invalid.append(domain)
                elif result == "unknown":
                    if is_strict:
                        state.taken.append(domain)
                    else:
                        state.errors.append(domain)
                else:
                    state.errors.append(domain)
                state.checked += 1
                state.current_domain = domain
                state.message = f"Checked {state.checked}/{state.total} (DNS prefilter)"

        _run_thread_pool(
            domains_raw,
            dns_worker,
            max_workers=max(1, threads),
            should_cancel=state.is_stop_requested,
        )

        if state.is_stop_requested():
            _dedupe_results(state)
            state.finish(stage="stopped", message="Stopped by user.")
            return

        if not final_check_enabled:
            _dedupe_results(state)
            state.finish(stage="done", message="Done!")
            return

        with state.lock:
            available_candidates = dedupe(state.available)
            error_candidates = dedupe(state.errors)
            final_candidates = list(available_candidates)
            if rdap_recheck_errors:
                final_candidates.extend(error_candidates)
            final_candidates = dedupe(final_candidates)

            state.stage = "final"
            state.final_total = len(final_candidates)
            state.final_checked = 0
            state.final_errors = 0
            state.current_domain = ""
            state.message = f"Final check (RDAP): 0/{state.final_total}"
            state.available = []
            if rdap_recheck_errors:
                state.errors = []

        def final_worker(dom: str):
            if state.is_stop_requested():
                return
            try:
                res = rdap_check(dom)
            except Exception:
                res = "error"
            if state.is_stop_requested():
                return
            with state.lock:
                if state.stop_requested:
                    return
                if res == "available":
                    state.available.append(dom)
                elif res == "taken":
                    state.taken.append(dom)
                elif res == "invalid":
                    state.invalid.append(dom)
                else:
                    state.errors.append(dom)
                    state.final_errors += 1
                state.final_checked += 1
                state.current_domain = dom
                state.message = f"Final check (RDAP): {state.final_checked}/{state.final_total}"

        _run_thread_pool(
            final_candidates,
            final_worker,
            max_workers=max(1, final_check_workers),
            should_cancel=state.is_stop_requested,
        )

        _dedupe_results(state)
        if state.is_stop_requested():
            state.finish(stage="stopped", message="Stopped by user.")
        else:
            state.finish(stage="done", message="Done!")

    except Exception as e:
        print(f"ERROR in run_check: {e}")
        traceback.print_exc()
        _dedupe_results(state)
        state.fail(f"Error: {str(e)}")
