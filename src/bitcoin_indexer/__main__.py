import asyncio

from sqlalchemy.engine import Engine

import benchmark
import db
import logger
import rpc


logger = logger.setup_logging(__name__)

START_HEIGHT = 862050
N_BLOCKS = 10

if __name__ == "__main__":
    # -----------
    #  MVP
    # -----------
    rec = benchmark.Recorder(strategy="sequential", n_blocks=N_BLOCKS)
    b = rpc.Blocks()
    e = db.set_up_db()

    for h in range(START_HEIGHT, START_HEIGHT + N_BLOCKS):
        block_hash = b.get_block_hash(h)
        block = b.get_block(block_hash)
        db.insert_block(block, e)

    path = rec.save()
    print(f"Saved benchmark to {path}")
