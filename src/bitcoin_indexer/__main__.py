import asyncio
import sys
from pathlib import Path

import uvloop
from sqlalchemy import Engine

import db
from logger import setup_logging
from context_manager import fail_on_error
from semaphore_controller import SemaphoreController

# Recording wall clock and cpu time for the execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "benchmark"))
from cpu_io import Recorder  # pylint: disable=wrong-import-position

setup_logging(__name__)

START_HEIGHT = 878031
N_BLOCKS = 100

SEMAPHORE_INITIAL = 7
SEMPAHORE_INCREASE = 30
MAX_CONN = SEMAPHORE_INITIAL + SEMPAHORE_INCREASE
MAX_CONN_KEEPALIVE = MAX_CONN

sqlite_lock = asyncio.Lock()


async def process_block(sc: SemaphoreController, height: int, engine: Engine):
    block_hash = None
    block = None
    with fail_on_error():
        block_hash = await sc.get_block_hash(height)

        async with sc.getblock_semaphore:
            if block_hash is not None:
                block = await sc.get_block(block_hash)

    # TODO: sqlite can't handle concurent writing
    async with sqlite_lock:
        if block is not None:
            await asyncio.to_thread(db.insert_block, block, engine)


async def main():
    rec = Recorder(strategy="asyncio_uvloop", n_blocks=N_BLOCKS)
    e = db.set_up_db()
    sc = SemaphoreController(N_BLOCKS, SEMAPHORE_INITIAL, SEMPAHORE_INCREASE, MAX_CONN, MAX_CONN_KEEPALIVE)
    async with sc:
        # fmt: off
        await asyncio.gather(*[
                process_block(sc, h, e)
                for h in range(START_HEIGHT, START_HEIGHT + N_BLOCKS)
            ]
        )

    path = rec.save()
    print(f"Saved benchmark to {path}")


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=uvloop.new_event_loop)
