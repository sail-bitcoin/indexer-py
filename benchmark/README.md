# Benchmark scripts
This folder is here to help benchmark external rpc provider and help to optmize the indexer.

## Scripts
- [cpu_io.py](./cpu_io.py) -> calculate the wall clock and cpu times, to help optimize cpu-bound and io-bound processes
- [rpc.py](./rpc.py) -> calculate the total time for `getblockhash` and `getblock` methods for a particular RPC url

`rpc.py` can be executed as standalone: `python benchmark/rpc.py`

To set up `cpu_io.py` benchmark in `__main__.py`:
```python
# Recording wall clock and cpu time for the execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "benchmark"))
from cpu_io import Recorder

[...]

async def main():
    # Create a Recorder instance
    rec = Recorder(strategy="asyncio_semaphore", n_blocks=N_BLOCKS)

    # wathever your main logic
    e = db.set_up_db()
    async with rpc.Blocks(MAX_CONN, MAX_CONN_KEEPALIVE) as b:
        # fmt: off
        await asyncio.gather(*[
                process_block(b, h, e)
                for h in range(START_HEIGHT, START_HEIGHT + N_BLOCKS)
            ]
        )

    # Save the record as a json file 
    path = rec.save()
    print(f"Saved benchmark to {path}")
```

## Estimations
- [concurent_rpc_estimation.md](./concurent_rpc_estimation.md) -> quick mathematics to guide an estimated values to set our semaphore concurent limit for `getblock` method and for the `AsyncLimiter` for `getblockhash` for a particular rate limitation (as their latency are at different scale)
