from string.templatelib import convert
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

import copy

from sqlalchemy import exc

from asyncpg import InvalidPasswordError as PGInvalidPasswordError, Connection as AsyncPGConnection, UniqueViolationError
import exceptions
import pytest
from sqlalchemy import Connection, Engine, PoolProxiedConnection, select, func, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError, OperationalError, DisconnectionError, TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

import db
from tests.utils import fast_retries
import tests.variables as var


def mock_engine():
    engine = MagicMock(spec=AsyncEngine)
    conn = AsyncMock(spec=AsyncConnection)
    # async with e.begin()/e.connect() as conn --> conn is the returned value of __aenter__()
    engine.begin.return_value.__aenter__.return_value = conn
    engine.connect.return_value.__aenter__.return_value = conn
    return engine, conn


def mock_engine_with_pg():
    """Mock the chain engine.connect() -> conn.get_raw_connection() -> raw.driver_connection (asyncpg)."""
    engine, conn = mock_engine()
    # SQLAlchemy pool wrapper
    raw = MagicMock(spec=PoolProxiedConnection)
    conn.get_raw_connection.return_value = raw
    # asyncpg connection: spec restricts attributes to real asyncpg.Connection ones
    pg = MagicMock(spec=AsyncPGConnection)
    pg.is_closed.return_value = False
    raw.driver_connection = pg
    return engine, raw, pg


def returned_mock__prepare_block_data():
    return (
        {"hash": "000abc", "height": 1},  # block_info
        {"blockhash": "000abc", "spending_txid": "tx0"},  # coinbase
        [{"txid": "tx0"}, {"txid": "tx1"}],  # txs
        [{"spending_txid": "tx1", "n": 0}],  # inputs
        [{"spending_txid": "tx0", "n": 0}],  # outputs
    )


# --------------------
# _prepare_block_data
# --------------------
def test__prepare_block_data_is_cleaned_up_correctly():
    raw_block = copy.deepcopy(var.block_b)
    # first, check if field to exclude exists
    for field in db.BLOCK_FIELDS_TO_EXCLUDE:
        assert field in raw_block
    for tx in raw_block["tx"]:
        for field in db.TRANSACTION_FIELDS_TO_EXCLUDE:
            assert field in tx
    for field in db.COINBASETX_FIELDS_TO_EXCLUDE:
        assert field in raw_block["coinbase_tx"]
    # prepare block data
    block, cb, txs, inputs, outputs = db._prepare_block_data(raw_block)
    # check if field to exclude has been removed
    for field in db.BLOCK_FIELDS_TO_EXCLUDE:
        assert field not in block
    for tx in txs:
        for field in db.TRANSACTION_FIELDS_TO_EXCLUDE:
            assert field not in tx
    for field in db.COINBASETX_FIELDS_TO_EXCLUDE:
        assert field not in cb
    # check looping mecanism
    assert len(txs) == 2
    assert len(inputs) == (len(txs) - 1)  # tx[0]["vin"] is coinbase not input
    assert len(outputs) == 4


def convert_to_satoshis(s):
    return int(Decimal(str(s)) * 10**8)


def test_that_convert_to_satoshis_method_works_correctly():
    float = 0.3
    decimal = Decimal(0.3)
    assert float is not decimal

    a = 0.3
    b = {"fee": a}
    res = convert_to_satoshis(b["fee"])
    float_to_sats = int(Decimal(a) * 10**8)
    assert res != float_to_sats

    decimal_to_sats = int(Decimal(str(a)) * 10**8)
    assert res == decimal_to_sats


def test__prepare_block_data_convert_output_value_to_satoshis():
    b = copy.deepcopy(var.block_a)
    value_sats = convert_to_satoshis(b["tx"][0]["vout"][0]["value"])
    block, cb, txs, inputs, outputs = db._prepare_block_data(b)
    assert outputs[0]["value"] == value_sats


def test__prepare_block_data_convert_fee_to_satoshis():
    b = copy.deepcopy(var.block_b)
    fee_sats = convert_to_satoshis(b["tx"][1].get("fee", 0))
    block, cb, txs, inputs, outputs = db._prepare_block_data(b)
    assert txs[1]["fee"] == fee_sats


def test__prepare_block_data_raise_on_errors():
    with pytest.raises(KeyError):
        b, cb, txs, i, o = db._prepare_block_data({"no_hash": "no"})
    with pytest.raises(TypeError):
        b, cb, txs, i, o = db._prepare_block_data(None)


# --------------------
# _copy_from_dict
# --------------------
async def test__copy_from_dict_executes_with_correct_table_and_params():
    pg = AsyncMock(AsyncPGConnection)
    list_dict = [{"hash": "000abc", "height": 1}]
    await db._copy_from_dict(list_dict, db.Blocks, pg)

    params = pg.copy_records_to_table.call_args
    table_name = params[0]
    args = params[1]

    pg.copy_records_to_table.assert_awaited_once()
    assert table_name[0] == db.Blocks.__tablename__
    assert args["records"][0][0] == list_dict[0]["hash"]
    assert args["records"][0][1] == list_dict[0]["height"]


async def test__copy_from_dict_rejects_non_base_subclass():
    pg = MagicMock(spec=AsyncPGConnection)
    with pytest.raises(TypeError):
        await db._copy_from_dict([{"a": 1}], dict, pg)  # pyright: ignore
    pg.copy_records_to_table.assert_not_awaited()


async def test__copy_from_dict_not_copying_when_list_none_or_empty():
    pg = MagicMock(spec=AsyncPGConnection)
    await db._copy_from_dict(None, db.Blocks, pg)  # pyright: ignore
    await db._copy_from_dict([], db.Blocks, pg)
    pg.copy_records_to_table.assert_not_awaited()


@pytest.mark.integration
async def test__copy_from_dict_in_transaction_is_committed(engine):
    block = copy.deepcopy(var.block_a)
    block_info, *_ = db._prepare_block_data(block)

    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        pg = raw.driver_connection
        assert pg is not None
        async with pg.transaction():
            await db._copy_from_dict([block_info], db.Blocks, pg)

    # committed changes are visible to another session
    async with AsyncSession(engine) as s:
        assert await s.get(db.Blocks, block["hash"]) is not None


@pytest.mark.integration
async def test__copy_from_dict_in_failed_transaction_is_rolled_back(engine):
    block = copy.deepcopy(var.block_a)
    block_info, *_ = db._prepare_block_data(block)

    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        pg = raw.driver_connection
        assert pg is not None
        with pytest.raises(UniqueViolationError):
            async with pg.transaction():
                await db._copy_from_dict([block_info], db.Blocks, pg)
                # same PK again: second COPY fails and must roll back the first one
                await db._copy_from_dict([block_info], db.Blocks, pg)

    async with AsyncSession(engine) as s:
        assert await s.get(db.Blocks, block["hash"]) is None


# --------------------
# _insert_prepared
# --------------------
@pytest.mark.parametrize("is_closed", [True, False])
@patch("db._copy_from_dict")
async def test__insert_prepared_invalidates_only_dead_connections(mock__copy_from_dict, is_closed):
    e, raw, pg = mock_engine_with_pg()
    pg.is_closed.return_value = is_closed
    mock__copy_from_dict.side_effect = PGInvalidPasswordError("error")  # non transient: no retry

    with pytest.raises(PGInvalidPasswordError):
        await db._insert_prepared(*returned_mock__prepare_block_data(), e)

    assert raw.invalidate.call_count == int(is_closed)


@pytest.mark.parametrize("exc_class", db.ASYNCPG_TRANSIENT_CONN_ERR)
@patch("db._copy_from_dict")
async def test__insert_prepared_retries_on_asyncpg_transient_errors(mock__copy_from_dict, exc_class):
    e, _, _ = mock_engine_with_pg()
    attempts = 3
    prepared = returned_mock__prepare_block_data()
    mock__copy_from_dict.side_effect = exc_class("error")

    with fast_retries(db._insert_prepared, attempts):
        with pytest.raises(exc_class):
            await db._insert_prepared(*prepared, e)

    assert mock__copy_from_dict.await_count == attempts
    assert mock__copy_from_dict.await_args.args[:2] == ([prepared[0]], db.Blocks)


@patch("db._copy_from_dict")
async def test__insert_prepared_do_not_retry_on_asyncpg_non_transient_errors(mock__copy_from_dict):
    e, _, _ = mock_engine_with_pg()
    attempts = 3
    prepared = returned_mock__prepare_block_data()
    mock__copy_from_dict.side_effect = PGInvalidPasswordError("error")

    with fast_retries(db._insert_prepared, attempts):
        with pytest.raises(PGInvalidPasswordError):
            await db._insert_prepared(*prepared, e)

    assert mock__copy_from_dict.await_count == 1
    assert mock__copy_from_dict.await_args.args[:2] == ([prepared[0]], db.Blocks)


@pytest.mark.parametrize("exc_class", [SATimeoutError, OSError, DBAPIError])
@patch("db._copy_from_dict")
async def test__insert_prepared_retries_on_some_exceptions(mock__copy_from_dict, exc_class):
    e, _, _ = mock_engine_with_pg()
    attempts = 3
    prepared = returned_mock__prepare_block_data()
    if issubclass(exc_class, DBAPIError):
        mock__copy_from_dict.side_effect = exc_class("INSERT ...", {}, Exception("connection lost"), connection_invalidated=True)
    else:
        mock__copy_from_dict.side_effect = exc_class("INSERT ...", {}, Exception("connection lost"))

    with fast_retries(db._insert_prepared, attempts):
        with pytest.raises(exc_class):
            await db._insert_prepared(*prepared, e)

    assert mock__copy_from_dict.await_count == attempts
    assert mock__copy_from_dict.await_args.args[:2] == ([prepared[0]], db.Blocks)


# --------------------
# insert_block
# --------------------


@patch("db._prepare_block_data")
async def test_insert_block_copies_5_times_in_one_transaction(mock_prepare):
    mock_prepare.return_value = returned_mock__prepare_block_data()

    engine, raw, pg = mock_engine_with_pg()

    fake_block = {"height": 1}
    await db.insert_block(fake_block, e=engine)

    mock_prepare.assert_called_once_with(fake_block)
    assert pg.copy_records_to_table.await_count == 5
    pg.transaction.assert_called_once()
    raw.invalidate.assert_not_called()


@patch("db._prepare_block_data")
async def test_insert_block_handles__prepare_block_data_failures(mock_prepare):
    mock_prepare.return_value = None
    engine = AsyncMock(spec=AsyncEngine)

    with pytest.raises(TypeError):
        await db.insert_block({"height": 1}, e=engine)


@patch("db._copy_from_dict")
@patch("db._prepare_block_data")
async def test_insert_block_do_not_retry_on__prepare_block_errors(mock__prepare_block_data, mock__copy_from_dict):
    e = AsyncMock(spec=AsyncEngine)
    block = copy.deepcopy(var.block_a)
    # use a different exc than KeyError: if prepare is retried, as it mutates block dict, it will throw a KeyError
    mock__prepare_block_data.side_effect = RuntimeError("error")

    with pytest.raises(RuntimeError):
        await db.insert_block(block, e)
    assert mock__prepare_block_data.call_count == 1
    assert mock__copy_from_dict.call_count == 0


@patch("db._insert_prepared")
@patch("db._prepare_block_data")
async def test_insert_block_passes_prepared_data_to__insert_prepared(mock__prepare_block_data, mock__insert_prepared):
    e = AsyncMock(spec=AsyncEngine)
    block = copy.deepcopy(var.block_a)
    prepared = returned_mock__prepare_block_data()
    mock__prepare_block_data.return_value = prepared

    await db.insert_block(block, e)

    mock__prepare_block_data.assert_called_once_with(block)
    mock__insert_prepared.assert_called_once_with(*prepared, e)


@patch("db._copy_from_dict")
async def test_insert_block_network_error_handling_with_real__insert_prepared(mock__copy_from_dict):
    b = copy.deepcopy(var.block_b)
    b_copy2 = copy.deepcopy(var.block_b)
    e, _, _ = mock_engine_with_pg()
    attempts = 3
    mock__copy_from_dict.side_effect = OSError
    with fast_retries(db._insert_prepared, attempts):
        with pytest.raises(OSError):
            await db.insert_block(b, e)
    assert mock__copy_from_dict.call_count == attempts
    prepared = db._prepare_block_data(b_copy2)
    assert mock__copy_from_dict.await_args.args[:2] == ([prepared[0]], db.Blocks)


@pytest.mark.integration
async def test_insert_block_insert_data_correctly(engine):
    block = copy.deepcopy(var.block_b)

    block_hash = block["hash"]
    txs = block["tx"]
    first_tx_inputs = txs[0]
    first_tx_id = first_tx_inputs["txid"]
    second_tx = txs[1]
    second_tx_id = second_tx["txid"]

    await db.insert_block(block, engine)

    async with AsyncSession(engine) as s:
        # block
        block_pk = await s.get(db.Blocks, block_hash)
        assert block_pk is not None
        # transactions
        tx_pk = await s.get(db.Transactions, first_tx_id)
        assert tx_pk is not None
        stmt = select(db.Transactions.txid).where(db.Transactions.blockhash == block_hash)
        tx_fk = await s.scalar(stmt)
        assert tx_fk == first_tx_id
        # coinbase
        cb_pk = await s.get(db.CoinbaseInputs, block_hash)
        assert cb_pk is not None
        stmt = select(db.CoinbaseInputs.spending_txid).where(db.CoinbaseInputs.blockhash == block_hash)
        cb_spending_txid = await s.scalar(stmt)
        assert cb_spending_txid == first_tx_id
        # inputs
        first_tx_inputs = await s.get(db.Inputs, (first_tx_id, 0))
        assert first_tx_inputs is None
        second_tx_inputs = await s.get(db.Inputs, (second_tx_id, 0))
        assert second_tx_inputs is not None
        # outputs
        first_tx_outputs = await s.get(db.Outputs, (first_tx_id, 0))
        assert first_tx_outputs is not None
        stmt = select(func.count()).select_from(db.Outputs).where(db.Outputs.spending_txid == second_tx_id)
        count = await s.scalar(stmt)
        assert count == 2


# --------------------
# insert_blocks
# --------------------
@pytest.mark.integration
async def test_insert_blocks_loops_correctly(engine):
    blocks = copy.deepcopy([var.block_a, var.block_b])
    await db.insert_blocks(blocks, engine)

    async with engine.connect() as conn:
        # blocks
        count = await conn.scalar(select(func.count(db.Blocks.hash)))
        assert count == 2
        # transactions
        count = await conn.scalar(select(func.count(db.Transactions.txid)))
        assert count == 3
        # inputs
        count = await conn.scalar(select(func.count()).select_from(db.Inputs))
        assert count == 1
        # coinbase inputs
        count = await conn.scalar(select(func.count(db.CoinbaseInputs.blockhash)))
        assert count == 2
        # outputs
        count = await conn.scalar(select(func.count()).select_from(db.Outputs))
        assert count == 5


# --------------------
# add_foreign_keys
# --------------------
async def test_foreign_keys_sanity_checks_fails_if_one_orphan_exist():
    conn = AsyncMock(spec=AsyncConnection)
    conn.scalar.return_value = 9
    res = await db.foreign_keys_sanity_checks(conn)
    assert res is False


async def test_foreign_keys_sanity_checks_success_if_no_orphan():
    conn = AsyncMock(spec=AsyncConnection)
    conn.scalar.return_value = 0
    res = await db.foreign_keys_sanity_checks(conn)
    assert res is True


async def test_adding_foreign_keys_fails_if_sanity_check_fails():
    engine, conn = mock_engine()
    conn.scalar.return_value = 9
    await db.add_foreign_keys(engine)
    fk_checks = len(db.FK_ORPHAN_CHECKS)
    assert conn.scalar.await_count == fk_checks


async def test_adding_foreign_keys_calls_the_right_amount_of_connect_methods():
    engine, conn = mock_engine()
    conn.scalar.return_value = 0
    await db.add_foreign_keys(engine)
    fk_count = len(db.FOREIGN_KEYS)
    fk_checks = len(db.FK_ORPHAN_CHECKS)
    assert conn.scalar.await_count == fk_checks
    assert conn.execute.await_count == fk_count
    conn.commit.assert_awaited_once()


async def fk_exists(e: AsyncEngine, table: str, constraint_name: str) -> bool:
    async with e.connect() as conn:
        return (
            await conn.scalar(
                text("""
                SELECT 1 FROM pg_constraint
                WHERE contype = 'f'
                  AND conname = :name
                  AND conrelid = CAST(:table as regclass)
            """),
                {"name": constraint_name, "table": table},
            )
            is not None
        )


@pytest.mark.integration
async def test_adding_foreign_keys_works(engine):
    block = copy.deepcopy(var.block_a)
    await db.insert_block(block, engine)
    await db.add_foreign_keys(engine)
    assert await fk_exists(engine, "transactions", "fk_transactions_blockhash_blocks")
    assert await fk_exists(engine, "inputs", "fk_inputs_spending_txid_transactions")
    assert await fk_exists(engine, "outputs", "fk_outputs_spending_txid_transactions")
    assert await fk_exists(engine, "coinbaseinputs", "fk_coinbaseinputs_blockhash_blocks")
    assert await fk_exists(engine, "coinbaseinputs", "fk_coinbaseinputs_spending_txid_transactions")
