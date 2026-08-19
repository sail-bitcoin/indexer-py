from asyncio import Semaphore
from typing import Any

from rpc import Blocks
from logger import logger


class SemaphoreController(Blocks):
    """Semaphore Controller for RcpClient calls"""

    def __init__(self, n_blocks: int, concurrent_init: int, concurrent_increase: int, max_conn=15, max_conn_keepalive=15) -> None:
        super().__init__(max_conn, max_conn_keepalive)
        self.n_blocks = n_blocks
        self.concurrent_init = concurrent_init
        self.getblock_semaphore = Semaphore(self.concurrent_init)
        self.concurrent_increase = concurrent_increase
        self.getblockhash_count = 0

    async def get_block_hash(self, *args, **kwargs) -> Any:
        self.getblockhash_count += 1
        if self.getblockhash_count == self.n_blocks:
            self.increase_semaphore()
        return await super().get_block_hash(*args, **kwargs)

    def increase_semaphore(self) -> None:
        concurrent_final = self.concurrent_init + self.concurrent_increase
        logger.info("Last getblockhash request for the %s blocks batch is about to be proceed, increasing getblock's semaphore limit from %s to %s.", self.n_blocks, self.concurrent_init, concurrent_final)
        for _ in range(self.concurrent_increase):
            self.getblock_semaphore.release()
