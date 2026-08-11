import os
import unittest
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from bitcoin_indexer import db
import tests.variables as var


class TestDb(unittest.TestCase):
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

    @pytest.mark.integration
    @patch.dict(os.environ, {"DB_URL": "sqlite:///:memory:"}, clear=True)
    def test_insert_from_dict(self):
        engine = db.set_up_db()
        raw_block = var.block_a
        block_hash = raw_block["hash"]
        block_info, cb, txs, inputs, outputs = db._prepare_block_data(raw_block)

        with Session(engine) as s:
            db.insert_from_dict([block_info], db.Blocks, s)

        pk = s.get(db.Blocks, block_hash)
        assert pk is not None


if __name__ == "__main__":
    unittest.main()
