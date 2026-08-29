from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError

import db
from tests.utils import fast_retries
import tests.variables as var


# --------------------
# _prepare_block_data
# --------------------
def test__prepare_block_data_is_cleaned_up_correctly():
    raw_block = var.block_b
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


# --------------------
# _insert_from_dict
# --------------------
def test__insert_from_dict_executes_with_correct_table_and_params():
    mock_session = MagicMock(spec=Session)
    list_dict = [{"hash": "000abc", "height": 1}]
    db.insert_from_dict(list_dict, db.Blocks, mock_session)

    mock_session.execute.assert_called_once()
    stmt, params = mock_session.execute.call_args.args
    assert stmt.table.name == db.Blocks.__tablename__
    assert params == list_dict


def test__insert_from_dict_rejects_non_base_subclass():
    mock_session = MagicMock(spec=Session)
    with pytest.raises(TypeError):
        db.insert_from_dict([{"a": 1}], dict, mock_session)  # pyright: ignore


def test__insert_from_dict_not_calling_execute_when_list_none_or_empty():
    mock_session = MagicMock(spec=Session)
    db.insert_from_dict(None, db.Blocks, mock_session)  # pyright: ignore
    db.insert_from_dict([], db.Blocks, mock_session)
    mock_session.execute.assert_not_called()


def test__insert_from_dict_retries_and_call_begin_nested_after_all_retries_failed():
    mock_session = MagicMock(spec=Session)
    mock_session.execute.side_effect = OperationalError("stmt", {}, Exception("connectoin failedduplicate key"))
    retries = 3
    with fast_retries(db.insert_from_dict, retries):
        with pytest.raises(OperationalError):
            db.insert_from_dict([{"hash": "00abc"}], db.Blocks, mock_session)

    assert mock_session.execute.call_count == retries
    mock_session.begin_nested.assert_called()


@pytest.mark.integration
def test__insert_from_dict_db_insertion(db_url):
    engine = db.set_up_db()
    block = var.block_a
    block_hash = block["hash"]
    block_info, cb, txs, inputs, outputs = db._prepare_block_data(block)

    with Session(engine) as s:
        db.insert_from_dict([block_info], db.Blocks, s)
        pk = s.get(db.Blocks, block_hash)
        s.commit()

    with Session(engine) as s2:
        pk = s2.get(db.Blocks, block_hash)
        # commit changes are visible to another session
        assert pk is not None


@pytest.mark.integration
def test__insert_from_dict_is_not_committing_changes_to_db(db_url):
    engine = db.set_up_db()
    block = var.block_a
    block_hash = block["hash"]
    block_info, cb, txs, inputs, outputs = db._prepare_block_data(block)

    with Session(engine) as s:
        db.insert_from_dict([block_info], db.Blocks, s)
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
@patch("db.Session")
@patch("db._prepare_block_data")
def test_insert_block_handles_execute_5_commit_once(mock_prepare, mock_session_cls):
    mock_prepare.return_value = (
        {"hash": "000abc", "height": 1},  # block_info
        {"blockhash": "000abc", "spending_txid": "tx0"},  # coinbase
        [{"txid": "tx0"}, {"txid": "tx1"}],  # txs
        [{"spending_txid": "tx1", "n": 0}],  # inputs
        [{"spending_txid": "tx0", "n": 0}],  # outputs
    )
    mock_session = MagicMock(spec=Session)
    # with Session(engine) -> returns Session.__enter__
    mock_session_cls.return_value.__enter__.return_value = mock_session

    fake_block = {"height": 1}
    db.insert_block(fake_block, engine=MagicMock())

    mock_prepare.assert_called_once_with(fake_block)
    assert mock_session.execute.call_count == 5
    mock_session.commit.assert_called_once()


@patch("db.Session")
@patch("db._prepare_block_data")
def test_insert_block_handles__prepare_block_data_failures(mock_prepare, mock_session_cls):
    mock_prepare.return_value = None
    mock_session = MagicMock(spec=Session)
    mock_session_cls.return_value.__enter__.return_value = mock_session

    with pytest.raises(TypeError):
        db.insert_block({"height": 1}, engine=MagicMock())


# @patch("db.Session")
# @patch("db._prepare_block_data")
# def test_insert_block_handles__prepare_block_data_failures(mock_prepare, mock_session_cls):
#    mock_session = MagicMock(spec=Session)
#    block = var.block_b
#    # mock_session.execute.side_effect = IntegrityError("stmt", "params", Exception("duplicate key"))
#
#    with patch.object(db._prepare_block_data, 'method', return_value=None) as mock_method
#        with pytest.raises(TypeError):
#            db.insert_block(block, mock_session)


@pytest.mark.integration
def test_insert_block_insert_data_correctly(db_url):
    engine = db.set_up_db()
    block = var.block_b
    db.insert_block(block, engine)

    block_hash = block["hash"]
    txs = block["tx"]
    first_tx_inputs = txs[0]
    first_tx_id = first_tx_inputs["txid"]
    second_tx = txs[1]
    second_tx_id = second_tx["txid"]

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
    blocks = [var.block_a, var.block_b]
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
