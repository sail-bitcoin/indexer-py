import asyncio
import os
import sys

from pathlib import Path
from dotenv import load_dotenv

import uvloop
from sqlalchemy import Engine

import db
from logger import setup_logging
from context_manager import fail_on_error, log_on_db_insert_error
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


async def process_block(sc: SemaphoreController, height: int, engine: Engine):
    block_hash = None
    block = None
    with fail_on_error():
        block_hash = await sc.get_block_hash(height)

        async with sc.getblock_semaphore:
            if block_hash is not None:
                block = await sc.get_block(block_hash)

    with log_on_db_insert_error():
        if block is not None:
            await asyncio.to_thread(db.insert_block, block, engine)


async def main():
    load_dotenv()
    rec_prefix = os.getenv("RECORDER_PREFIX")
    rec = None
    if rec_prefix is not None:
        rec = Recorder(strategy=rec_prefix, n_blocks=N_BLOCKS)
    e = db.set_up_db()
    sc = SemaphoreController(N_BLOCKS, SEMAPHORE_INITIAL, SEMPAHORE_INCREASE, MAX_CONN, MAX_CONN_KEEPALIVE)
    async with sc:
        # fmt: off
        await asyncio.gather(*[
                process_block(sc, h, e)
                for h in range(START_HEIGHT, START_HEIGHT + N_BLOCKS)
            ]
        )

    if rec is not None:
        path = rec.save()
        print(f"Saved benchmark to {path}")


if __name__ == "__main__":
    # import yappi

    # yappi.set_clock_type("cpu")
    # yappi.start()
    asyncio.run(main(), loop_factory=uvloop.new_event_loop)
    # yappi.stop()
    #
    # profile_dir = Path("./var/profiles")
    # profile_dir.mkdir(parents=True, exist_ok=True)
    # timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # prof_path = profile_dir / f"cpu_profile_{timestamp}.prof"
    # txt_path = profile_dir / f"cpu_profile_{timestamp}.txt"
    #
    # stats = yappi.get_func_stats()
    # stats.sort("tsub", "desc")  # self CPU time, excluding time spent in calls
    # stats.save(str(prof_path), type="pstat")  # keep full paths for the .prof (e.g. loading via pstats)
    # stats.strip_dirs()  # .txt only: drop venv path noise so names fit the "name" column
    # with open(txt_path, "w") as f:
    #     stats.print_all(
    #         out=f,
    #         limit=200,
    #         columns={
    #             0: ("name", 100),
    #             1: ("ncall", 18),
    #             2: ("tsub", 10),
    #             3: ("ttot", 10),
    #             4: ("tavg", 10),
    #         },
    #     )
    #
    # print(f"Saved profiler results to {prof_path}/.txt")
