import asyncio
import sys
from pathlib import Path

from sqlalchemy import Engine


import db
from logger import setup_logging
import rpc
from context_manager import fail_on_error

# Recording wall clock and cpu time for the execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "benchmark"))
from cpu_io import Recorder

setup_logging(__name__)

START_HEIGHT = 878031
N_BLOCKS = 100

SEMAPHORE_INITIAL = 7
SEMPAHORE_INCREASE = 30
MAX_CONN = SEMAPHORE_INITIAL + SEMPAHORE_INCREASE
MAX_CONN_KEEPALIVE = MAX_CONN

HASH_RETRIEVED = 0
getblock_semaphore = asyncio.Semaphore(SEMAPHORE_INITIAL)
sqlite_lock = asyncio.Lock()


async def process_block(b: rpc.Blocks, height: int, engine: Engine):
    global HASH_RETRIEVED
    block_hash = None
    block = None
    with fail_on_error():
        block_hash = await b.get_block_hash(height)

        HASH_RETRIEVED += 1
        if HASH_RETRIEVED == N_BLOCKS:
            for _ in range(SEMPAHORE_INCREASE):
                getblock_semaphore.release()

        async with getblock_semaphore:
            if block_hash is not None:
                block = await b.get_block(block_hash)

    # TODO: sqlite can't handle concurent writing
    async with sqlite_lock:
        if block is not None:
            await asyncio.to_thread(db.insert_block, block, engine)


async def main():
    rec = Recorder(strategy="asyncio_semaphore", n_blocks=N_BLOCKS)
    e = db.set_up_db()
    async with rpc.Blocks(MAX_CONN, MAX_CONN_KEEPALIVE) as b:
        # fmt: off
        await asyncio.gather(*[
                process_block(b, h, e)
                for h in range(START_HEIGHT, START_HEIGHT + N_BLOCKS)
            ]
        )

    path = rec.save()
    print(f"Saved benchmark to {path}")


if __name__ == "__main__":
    asyncio.run(main())
