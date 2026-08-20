import os
from unittest.mock import patch

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

import db
import tests.variables as var


def test_prepare_block_data():
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


@pytest.mark.integration
@patch.dict(os.environ, {"DB_URL": "sqlite:///:memory:"}, clear=True)
def test_insert_from_dict():
    engine = db.set_up_db()
    block = var.block_a
    block_hash = block["hash"]
    block_info, cb, txs, inputs, outputs = db._prepare_block_data(block)

    with Session(engine) as s:
        db.insert_from_dict([block_info], db.Blocks, s)

    pk = s.get(db.Blocks, block_hash)
    assert pk is not None


@pytest.mark.integration
@patch.dict(os.environ, {"DB_URL": "sqlite:///:memory:"}, clear=True)
def test_insert_block():
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


@pytest.mark.integration
@patch.dict(os.environ, {"DB_URL": "sqlite:///:memory:"}, clear=True)
def test_insert_blocks():
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
