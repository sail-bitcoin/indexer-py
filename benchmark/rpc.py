"""
Sequential (no-concurrency) latency probe for getblockhash / getblock.

Reuses rpc.Blocks directly — driven one call at a time so there's no
rate-limiter/connection-pool contention to distort the measured latency.
"""

import sys
import asyncio
import json
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "bitcoin_indexer"))
import rpc

START_HEIGHT = 878031
N_BLOCKS = 100
RESULTS_DIR = Path("var/benchmarks/rpc")


def _public_rpc_host(url: str) -> str:
    """Registered domain only (e.g. 'quiknode.pro') — drops subdomain, path, and any access key."""
    hostname = urlparse(url).hostname or ""
    parts = hostname.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else hostname


def _stats(times: list[float]) -> dict:
    stats = {
        "min": min(times),
        "avg": statistics.mean(times),
        "median": statistics.median(times),
        "max": max(times),
    }
    if len(times) >= 2:
        deciles = statistics.quantiles(times, n=10)
        stats["p10"] = deciles[0]
        stats["p90"] = deciles[8]
    return stats


async def run() -> tuple[list[float], list[float]]:
    b = rpc.Blocks()
    hash_times, block_times = [], []
    async with b:
        for height in range(START_HEIGHT, START_HEIGHT + N_BLOCKS):
            print(f"height={height} calling get_block_hash...", flush=True)
            t0 = time.perf_counter()
            block_hash = await b.get_block_hash(height)
            t1 = time.perf_counter()
            print(f"height={height} get_block_hash={t1 - t0:.3f}s, calling get_block...", flush=True)

            await b.get_block(block_hash)
            t2 = time.perf_counter()

            hash_times.append(t1 - t0)
            block_times.append(t2 - t1)
            print(f"height={height} get_block_hash={t1 - t0:.3f}s get_block={t2 - t1:.3f}s", flush=True)
    return hash_times, block_times


def main():
    with tempfile.TemporaryDirectory() as tmp_cache_dir:
        rpc.RPC_CACHE_DIR = Path(tmp_cache_dir)  # force real network calls, no cache hits
        hash_times, block_times = asyncio.run(run())

    rpc_host = _public_rpc_host(rpc.RpcClient().rpc_url)
    summary = {
        "rpc_host": rpc_host,
        "start_height": START_HEIGHT,
        "n_blocks": N_BLOCKS,
        "get_block_hash": _stats(hash_times),
        "get_block": _stats(block_times),
    }
    print(json.dumps(summary, indent=2))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y_%m_%d_T%H%M%SZ")
    path = RESULTS_DIR / f"{rpc_host}_{N_BLOCKS}_{timestamp}.json"
    path.write_text(json.dumps(summary, indent=2))
    print(f"Saved to {path}")


if __name__ == "__main__":
    main()
