import cProfile
import pstats

from sqlalchemy.engine import Engine

import db
import logger
import rpc


logger = logger.setup_logging(__name__)


def insert_block_with_txs(block_number: int, engine: Engine):
    block = rpc.Blocks()
    block_hash = block.get_block_hash(block_number)
    b = block.get_block(block_hash, verbosity=2)
    db.insert_block(b, engine)


if __name__ == "__main__":
    # -----------
    #  MVP
    # -----------
    with cProfile.Profile() as profile:
        BLOCK_NUMBER = 957390
        e = db.set_up_db()
        insert_block_with_txs(BLOCK_NUMBER, e)

    results = pstats.Stats(profile)
    results.sort_stats(pstats.SortKey.TIME)
    results.dump_stats("./var/results.prof")
