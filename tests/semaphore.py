# import asyncio
# import pytest
from unittest.mock import patch, AsyncMock

from rpc import Blocks
from semaphore_controller import SemaphoreController

BLOCK_HEIGHT = 878031
N_BLOCKS = 10
SEMAPHORE_INITIAL = 3
SEMPAHORE_INCREASE = 7
MAX_CONN = SEMAPHORE_INITIAL + SEMPAHORE_INCREASE
MAX_CONN_KEEPALIVE = MAX_CONN


async def test_getblockhash_runs_parent_method():
    sc = SemaphoreController(N_BLOCKS, SEMAPHORE_INITIAL, SEMPAHORE_INCREASE, MAX_CONN, MAX_CONN_KEEPALIVE)

    with patch.object(Blocks, "get_block_hash", new_callable=AsyncMock, return_value="foo") as mock_parent:
        result = await sc.get_block_hash(BLOCK_HEIGHT)

    mock_parent.assert_called_once_with(BLOCK_HEIGHT)
    assert result == "foo"


async def test_calling_getblockhash_increase_counter():
    loops = 3
    sc = SemaphoreController(N_BLOCKS, SEMAPHORE_INITIAL, SEMPAHORE_INCREASE, MAX_CONN, MAX_CONN_KEEPALIVE)
    if loops >= sc.n_blocks:
        assert True is False

    with patch.object(Blocks, "get_block_hash", new_callable=AsyncMock, return_value="foo"):
        for _ in range(loops):
            await sc.get_block_hash(BLOCK_HEIGHT)

    assert sc.getblockhash_count == loops


async def test_semaphore_increase_when_expected():
    sc = SemaphoreController(N_BLOCKS, SEMAPHORE_INITIAL, SEMPAHORE_INCREASE, MAX_CONN, MAX_CONN_KEEPALIVE)
    assert sc.getblock_semaphore._value == SEMAPHORE_INITIAL

    with patch.object(Blocks, "get_block_hash", new_callable=AsyncMock, return_value="foo"):
        for _ in range(N_BLOCKS - 1):
            await sc.get_block_hash(BLOCK_HEIGHT)
            assert sc.getblock_semaphore._value == SEMAPHORE_INITIAL
        # last call
        await sc.get_block_hash(BLOCK_HEIGHT)

    assert sc.getblockhash_count == N_BLOCKS
    assert sc.getblock_semaphore._value == (SEMAPHORE_INITIAL + SEMPAHORE_INCREASE)


async def test_two_semaphore_controllers_are_indenpendent():
    sc2_n_blocks = 8
    sc2_semaphore_initial = 2
    sc2_semaphore_increase = 3
    sc1 = SemaphoreController(N_BLOCKS, SEMAPHORE_INITIAL, SEMPAHORE_INCREASE, MAX_CONN, MAX_CONN_KEEPALIVE)
    sc2 = SemaphoreController(sc2_n_blocks, sc2_semaphore_initial, sc2_semaphore_increase, MAX_CONN, MAX_CONN_KEEPALIVE)
    assert sc1.getblock_semaphore._value != sc2.getblock_semaphore._value

    with patch.object(Blocks, "get_block_hash", new_callable=AsyncMock, return_value="foo"):
        # sc1 loop
        for _ in range(N_BLOCKS):
            await sc1.get_block_hash(BLOCK_HEIGHT)
        # sc2 loop
        for _ in range(sc2_n_blocks):
            await sc2.get_block_hash(BLOCK_HEIGHT)

    assert sc1.getblockhash_count == N_BLOCKS
    assert sc2.getblockhash_count == sc2_n_blocks
    assert sc1.getblock_semaphore._value != sc2.getblock_semaphore._value
    assert sc1.getblock_semaphore is not sc2.getblock_semaphore
