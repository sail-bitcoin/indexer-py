import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

RESULTS_DIR = Path("var/benchmarks")


class Recorder:
    """Accumulates total/average wall & cpu time across a run and persists to var/benchmarks/"""

    def __init__(self, strategy: str, n_blocks: int):
        self.strategy = strategy
        self.n_blocks = n_blocks
        self._process = psutil.Process(os.getpid())
        self._start_wall = time.perf_counter()
        self._start_cpu = time.process_time()
        self._start_rss = self._process.memory_info().rss
        psutil.cpu_percent(interval=None)  # prime the baseline; next call reports avg since here

    def save(self) -> Path:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = RESULTS_DIR / f"{self.strategy}_{timestamp}.json"
        total_wall = time.perf_counter() - self._start_wall
        total_cpu = time.process_time() - self._start_cpu
        if total_wall != 0:
            total_cpu_ratio = total_cpu / total_wall
        else:
            total_cpu_ratio = "N/A"

        payload = {
            "strategy": self.strategy,
            "n_blocks": self.n_blocks,
            "total_wall": total_wall,
            "total_cpu": total_cpu,
            "total_cpu_ratio": total_cpu_ratio,
            "host": {
                "cpu_logical_count": psutil.cpu_count(logical=True),
                "cpu_physical_count": psutil.cpu_count(logical=False),
                "cpu_percent_avg_during_run": psutil.cpu_percent(interval=None),
                "ram_total_bytes": psutil.virtual_memory().total,
                "ram_available_bytes": psutil.virtual_memory().available,
                "process_rss_start_bytes": self._start_rss,
                "process_rss_end_bytes": self._process.memory_info().rss,
            },
        }
        path.write_text(json.dumps(payload, indent=2))
        return path
