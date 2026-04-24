import contextlib
import logging
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import ContextManager, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


@dataclass
class _OperationStats:
    count: int = 0
    total_s: float = 0.0
    max_s: float = 0.0

    def add(self, duration_s: float) -> None:
        self.count += 1
        self.total_s += duration_s
        self.max_s = max(self.max_s, duration_s)


class SamplingProfiler:
    def __init__(self) -> None:
        self.enabled = False
        self.sample_interval_s = 0.25
        self.report_interval_s = 60.0
        self.max_stack_depth = 8
        self.max_entries = 10
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._samples: Counter[str] = Counter()
        self._operations: Dict[str, _OperationStats] = {}

    def configure(self, config: Dict) -> None:
        self.enabled = bool(config.get("enabled", False))
        self.sample_interval_s = float(config.get("sample_interval_s", 0.25))
        self.report_interval_s = float(config.get("report_interval_s", 60.0))
        self.max_stack_depth = int(config.get("max_stack_depth", 8))
        self.max_entries = int(config.get("max_entries", 10))

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="sampling_profiler"
        )
        self._thread.start()
        logger.info(
            "Sampling profiler started interval=%.3fs report_interval=%.1fs",
            self.sample_interval_s,
            self.report_interval_s,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.report()

    def record_operation(self, name: str, duration_s: float) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._operations.setdefault(name, _OperationStats()).add(duration_s)

    @contextlib.contextmanager
    def timed(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record_operation(name, time.perf_counter() - start)

    def _run(self) -> None:
        next_report = time.monotonic() + self.report_interval_s
        while not self._stop_event.wait(self.sample_interval_s):
            self._sample()
            now = time.monotonic()
            if now >= next_report:
                self.report()
                next_report = now + self.report_interval_s

    def _sample(self) -> None:
        frames = sys._current_frames()
        current_thread_id = threading.get_ident()
        thread_names = {thread.ident: thread.name for thread in threading.enumerate()}
        local_samples = Counter()

        for thread_id, frame in frames.items():
            if thread_id == current_thread_id:
                continue
            thread_name = thread_names.get(thread_id, str(thread_id))
            stack = []
            while frame is not None and len(stack) < self.max_stack_depth:
                code = frame.f_code
                stack.append(f"{code.co_name}({code.co_filename}:{frame.f_lineno})")
                frame = frame.f_back
            local_samples[f"{thread_name}: " + " <- ".join(stack)] += 1

        if local_samples:
            with self._lock:
                self._samples.update(local_samples)

    def report(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            samples = self._samples
            operations = self._operations
            self._samples = Counter()
            self._operations = {}

        sample_count = sum(samples.values())
        if sample_count:
            logger.info("Profiler top sampled stacks over %d samples:", sample_count)
            for stack, count in samples.most_common(self.max_entries):
                logger.info(
                    "Profiler stack %.1f%% %d/%d %s",
                    100.0 * count / sample_count,
                    count,
                    sample_count,
                    stack,
                )

        if operations:
            logger.info("Profiler operation timings:")
            sorted_operations = sorted(
                operations.items(), key=lambda item: item[1].total_s, reverse=True
            )
            for name, stats in sorted_operations[: self.max_entries]:
                avg_s = stats.total_s / stats.count if stats.count else 0.0
                logger.info(
                    "Profiler op %s count=%d total=%.3fs avg=%.3fs max=%.3fs",
                    name,
                    stats.count,
                    stats.total_s,
                    avg_s,
                    stats.max_s,
                )


profiler = SamplingProfiler()


def configure(config: Dict) -> None:
    was_running = profiler._thread is not None
    profiler.configure(config or {})
    if was_running and not profiler.enabled:
        profiler.stop()


def start() -> None:
    profiler.start()


def stop() -> None:
    profiler.stop()


def timed(name: str) -> ContextManager[None]:
    return profiler.timed(name)
