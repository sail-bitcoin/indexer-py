import unittest
from unittest.mock import MagicMock

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base, Session

import bitcoin_indexer.db as db
import tests.variables as var

Base = declarative_base()


class TableTest(Base):
    __tablename__ = "tabletest"
    hash = Column(String, primary_key=True)
    height = Column(Integer)
    size = Column(Integer)


class TestDb(unittest.TestCase):
    # TODO
    # def test_insert_from_dict(self):
    #    mock_session = MagicMock(spec=Session)
    #    res = [var.block_a, var.block_b]
    #    db.insert_from_dict(res, TableTest, mock_session)

    def test_prepare_block_data(self):
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


if __name__ == "__main__":
    unittest.main()
