from unittest.mock import patch, MagicMock
from decimal import Decimal

import copy
import exceptions
import pytest
from sqlalchemy import Connection, Engine, select, func, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError, DisconnectionError, TimeoutError as SATimeoutError

import db
from tests.utils import fast_retries
import tests.variables as var


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


def test__prepare_block_data_convert_output_value_to_satoshis():
    b = copy.deepcopy(var.block_a)
    value_btc = Decimal(b["tx"][0]["vout"][0]["value"])
    value_stats = int(Decimal(value_btc * 10**8))
    block, cb, txs, inputs, outputs = db._prepare_block_data(b)
    assert outputs[0]["value"] == value_stats


def test__prepare_block_data_raise_on_errors():
    with pytest.raises(KeyError):
        b, cb, txs, i, o = db._prepare_block_data({"no_hash": "no"})
    with pytest.raises(TypeError):
        b, cb, txs, i, o = db._prepare_block_data(None)


# --------------------
# _insert_from_dict
# --------------------
def test__insert_from_dict_executes_with_correct_table_and_params():
    mock_session = MagicMock(spec=Session)
    list_dict = [{"hash": "000abc", "height": 1}]
    db._insert_from_dict(list_dict, db.Blocks, mock_session)

    mock_session.execute.assert_called_once()
    stmt, params = mock_session.execute.call_args.args
    assert stmt.table.name == db.Blocks.__tablename__
    assert params == list_dict


def test__insert_from_dict_rejects_non_base_subclass():
    mock_session = MagicMock(spec=Session)
    with pytest.raises(TypeError):
        db._insert_from_dict([{"a": 1}], dict, mock_session)  # pyright: ignore


def test__insert_from_dict_not_calling_execute_when_list_none_or_empty():
    mock_session = MagicMock(spec=Session)
    db._insert_from_dict(None, db.Blocks, mock_session)  # pyright: ignore
    db._insert_from_dict([], db.Blocks, mock_session)
    mock_session.execute.assert_not_called()


@pytest.mark.integration
def test__insert_from_dict_db_insertion(db_url):
    engine = db.set_up_db()
    block = copy.deepcopy(var.block_a)
    block_hash = block["hash"]
    block_info, cb, txs, inputs, outputs = db._prepare_block_data(block)

    with Session(engine) as s:
        conn = s.connection()
        db._insert_from_dict([block_info], db.Blocks, conn)
        pk = s.get(db.Blocks, block_hash)
        s.commit()

    with Session(engine) as s2:
        pk = s2.get(db.Blocks, block_hash)
        # commit changes are visible to another session
        assert pk is not None


@pytest.mark.integration
def test__insert_from_dict_is_not_committing_changes_to_db(db_url):
    engine = db.set_up_db()
    block = copy.deepcopy(var.block_a)
    block_hash = block["hash"]
    block_info, cb, txs, inputs, outputs = db._prepare_block_data(block)

    with Session(engine) as s:
        conn = s.connection()
        db._insert_from_dict([block_info], db.Blocks, conn)
        pk = s.get(db.Blocks, block_hash)
        # same session so uncommitted is visible
        assert pk is not None

    with Session(engine) as s2:
        pk2 = s2.get(db.Blocks, block_hash)
        # uncommitted, not visible
        assert pk2 is None


# --------------------
# insert_block
# --------------------
@patch("db._prepare_block_data")
def test_insert_block_handles_execute_5_times_and_commit_once(mock_prepare):
    mock_prepare.return_value = returned_mock__prepare_block_data()

    engine = MagicMock(spec=Engine)
    # with e.connect() as conn --> conn is the returned value of e.connect.__enter__()
    conn = engine.connect.return_value.__enter__.return_value

    fake_block = {"height": 1}
    db.insert_block(fake_block, e=engine)

    mock_prepare.assert_called_once_with(fake_block)
    assert conn.execute.call_count == 5
    conn.commit.assert_called_once()


@patch("db._prepare_block_data")
def test_insert_block_handles__prepare_block_data_failures(mock_prepare):
    mock_prepare.return_value = None
    engine = MagicMock(spec=Engine)

    with pytest.raises(TypeError):
        db.insert_block({"height": 1}, e=engine)


@patch("db._insert_from_dict")
@patch("db._prepare_block_data")
def test_insert_block_do_not_retry_on__prepare_block_errors(mock__prepare_block_data, mock__insert_from_dict):
    e = MagicMock(spec=Engine)
    block = copy.deepcopy(var.block_a)
    # use a different exc than KeyError: if prepare is retried, as it mutates block dict, it will throw a KeyError
    mock__prepare_block_data.side_effect = RuntimeError("error")

    with pytest.raises(RuntimeError):
        db.insert_block(block, e)
    assert mock__prepare_block_data.call_count == 1
    assert mock__insert_from_dict.call_count == 0


@pytest.mark.parametrize("exc_class", [OperationalError, SATimeoutError, DisconnectionError])
@patch("db._insert_from_dict")
@patch("db._prepare_block_data")
def test_insert_block_retries_on_sqlalchemy_network_errors(mock__prepare_block_data, mock__insert_from_dict, exc_class):
    e = MagicMock(spec=Engine)
    block = copy.deepcopy(var.block_a)
    attempts = 3
    mock__prepare_block_data.return_value = returned_mock__prepare_block_data()
    mock__insert_from_dict.side_effect = exc_class("INSERT ...", {}, Exception("connection lost"))

    with fast_retries(db._insert_prepared, attempts):
        with pytest.raises(exc_class):
            db.insert_block(block, e)

    assert mock__prepare_block_data.call_count == 1
    assert mock__insert_from_dict.call_count == attempts


@patch("db._insert_prepared")
@patch("db._prepare_block_data")
def test_insert_block_passes_prepared_data_to__insert_prepared(mock__prepare_block_data, mock__insert_prepared):
    e = MagicMock(spec=Engine)
    block = copy.deepcopy(var.block_a)
    prepared = returned_mock__prepare_block_data()
    mock__prepare_block_data.return_value = prepared

    db.insert_block(block, e)

    mock__prepare_block_data.assert_called_once_with(block)
    mock__insert_prepared.assert_called_once_with(*prepared, e)


@patch("db._insert_from_dict")
def test_insert_block_network_error_handling_with_real__insert_prepared(mock__insert_from_dict):
    b = copy.deepcopy(var.block_b)
    b_copy2 = copy.deepcopy(var.block_b)
    e = MagicMock(spec=Engine)
    attempts = 3
    mock__insert_from_dict.side_effect = OperationalError("INSERT ...", {}, Exception("connectin lost"))
    with fast_retries(db._insert_prepared, attempts):
        with pytest.raises(OperationalError):
            db.insert_block(b, e)
    assert mock__insert_from_dict.call_count == attempts
    prepared = db._prepare_block_data(b_copy2)
    assert mock__insert_from_dict.call_args(*prepared)


@pytest.mark.integration
def test_insert_block_insert_data_correctly(db_url):
    engine = db.set_up_db()
    block = copy.deepcopy(var.block_b)

    block_hash = block["hash"]
    txs = block["tx"]
    first_tx_inputs = txs[0]
    first_tx_id = first_tx_inputs["txid"]
    second_tx = txs[1]
    second_tx_id = second_tx["txid"]

    db.insert_block(block, engine)

    with Session(engine) as s:
        # block
        block_pk = s.get(db.Blocks, block_hash)
        assert block_pk is not None
        # transactions
        tx_pk = s.get(db.Transactions, first_tx_id)
        assert tx_pk is not None
        stmt = select(db.Transactions.txid).where(db.Transactions.blockhash == block_hash)
        tx_fk = s.scalar(stmt)
        assert tx_fk == first_tx_id
        # coinbase
        cb_pk = s.get(db.CoinbaseInputs, block_hash)
        assert cb_pk is not None
        stmt = select(db.CoinbaseInputs.spending_txid).where(db.CoinbaseInputs.blockhash == block_hash)
        cb_spending_txid = s.scalar(stmt)
        assert cb_spending_txid == first_tx_id
        # inputs
        first_tx_inputs = s.get(db.Inputs, (first_tx_id, 0))
        assert first_tx_inputs is None
        second_tx_inputs = s.get(db.Inputs, (second_tx_id, 0))
        assert second_tx_inputs is not None
        # outputs
        first_tx_outputs = s.get(db.Outputs, (first_tx_id, 0))
        assert first_tx_outputs is not None
        stmt = select(func.count()).select_from(db.Outputs).where(db.Outputs.spending_txid == second_tx_id)
        count = s.scalar(stmt)
        assert count == 2


# --------------------
# insert_blocks
# --------------------
@pytest.mark.integration
def test_insert_blocks_loops_correctly(db_url):
    engine = db.set_up_db()
    blocks = copy.deepcopy([var.block_a, var.block_b])
    db.insert_blocks(blocks, engine)

    with Session(engine) as s:
        # blocks
        count = s.scalar(select(func.count(db.Blocks.hash)))
        assert count == 2
        # transactions
        count = s.scalar(select(func.count(db.Transactions.txid)))
        assert count == 3
        # inputs
        count = s.scalar(select(func.count()).select_from(db.Inputs))
        assert count == 1
        # coinbase inputs
        count = s.scalar(select(func.count(db.CoinbaseInputs.blockhash)))
        assert count == 2
        # outputs
        count = s.scalar(select(func.count()).select_from(db.Outputs))
        assert count == 5


# --------------------
# add_foreign_keys
# --------------------
def test_foreign_keys_sanity_checks_fails_if_one_orphan_exist():
    conn = MagicMock(spec=Connection)
    conn.execute.return_value.scalar_one.return_value = 9
    res = db.foreign_keys_sanity_checks(conn)
    assert res is False


def test_adding_foreign_keys_fails_if_sanity_check_fails():
    engine = MagicMock(spec=Engine)
    conn = engine.connect.return_value.__enter__.return_value
    conn.execute.return_value.scalar_one.return_value = 9
    db.add_foreign_keys(engine)
    fk_checks = len(db.FK_ORPHAN_CHECKS)
    assert conn.execute.call_count == fk_checks


def test_adding_foreign_keys_calls_the_right_amount_of_execute():
    engine = MagicMock(spec=Engine)
    conn = engine.connect.return_value.__enter__.return_value
    conn.execute.return_value.scalar_one.return_value = 0
    db.add_foreign_keys(engine)
    fk_count = len(db.FOREIGN_KEYS)
    fk_checks = len(db.FK_ORPHAN_CHECKS)
    assert conn.execute.call_count == (fk_count + fk_checks)


def fk_exists(e: Engine, table: str, constraint_name: str) -> bool:
    with e.connect() as conn:
        return (
            conn.execute(
                text("""
                SELECT 1 FROM pg_constraint
                WHERE contype = 'f'
                  AND conname = :name
                  AND conrelid = CAST(:table as regclass)
            """),
                {"name": constraint_name, "table": table},
            ).scalar()
            is not None
        )


@pytest.mark.integration
def test_adding_foreign_keys_works(db_url):
    engine = db.set_up_db()
    block = copy.deepcopy(var.block_a)
    db.insert_block(block, engine)
    db.add_foreign_keys(engine)
    assert fk_exists(engine, "transactions", "fk_transactions_blockhash_blocks")
    assert fk_exists(engine, "inputs", "fk_inputs_spending_txid_transactions")
    assert fk_exists(engine, "outputs", "fk_outputs_spending_txid_transactions")
    assert fk_exists(engine, "coinbaseinputs", "fk_coinbaseinputs_blockhash_blocks")
    assert fk_exists(engine, "coinbaseinputs", "fk_coinbaseinputs_spending_txid_transactions")
