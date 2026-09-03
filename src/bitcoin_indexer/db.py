import os
from logging import WARNING
from typing import cast

import orjson
from dotenv import load_dotenv
from sqlalchemy import JSON, Boolean, Column, Float, ForeignKey, BigInteger, Integer, String, Table, create_engine, inspect, insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DisconnectionError, OperationalError, TimeoutError as SATimeoutError
from sqlalchemy.orm import DeclarativeBase, Session
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

import context_manager
from logger import logger
from utils import raise_outside_of_retry


class Base(DeclarativeBase):
    pass


load_dotenv()


def should_retry(exc: BaseException) -> bool:
    return isinstance(exc, (OperationalError, SATimeoutError, DisconnectionError))


# ------------------------------------------------------------
# DB Tables
# ------------------------------------------------------------
BLOCK_FIELDS_TO_EXCLUDE = ["tx", "nextblockhash", "target", "coinbase_tx"]
TRANSACTION_FIELDS_TO_EXCLUDE = ["vin", "vout"]
COINBASETX_FIELDS_TO_EXCLUDE = ["witness"]

STALE_BLOCK_FIELDS = {"confirmations"}

# what about "in_active_chain"?
STALE_TRANSACTION_FIELDS = {"confirmations"}

INSERTION_RETRIES = 4


class Blocks(Base):
    __tablename__ = "blocks"
    hash = Column(String, primary_key=True)
    height = Column(Integer)
    size = Column(Integer)
    strippedsize = Column(Integer)
    weight = Column(Integer)
    version = Column(Integer)
    versionHex = Column(String)
    merkleroot = Column(String)
    time = Column(Integer)
    mediantime = Column(Integer)
    confirmations = Column(Integer)
    nonce = Column(BigInteger)
    bits = Column(String)
    difficulty = Column(Float)
    chainwork = Column(String)
    nTx = Column(Integer)
    previousblockhash = Column(String)


class Transactions(Base):
    __tablename__ = "transactions"
    txid = Column(String, primary_key=True)
    n = Column(Integer)
    hash = Column(String)
    in_active_chain = Column(Boolean)
    hex = Column(String)
    size = Column(Integer)
    vsize = Column(Integer)
    weight = Column(Integer)
    version = Column(Integer)
    locktime = Column(BigInteger)
    fee = Column(Integer)
    blockhash = Column(String, ForeignKey("blocks.hash"))


class Inputs(Base):
    __tablename__ = "inputs"
    spending_txid = Column(String, ForeignKey("transactions.txid"), primary_key=True)
    n = Column(Integer, primary_key=True)
    txid = Column(String)
    vout = Column(Integer)
    scriptSig = Column(JSON)
    sequence = Column(BigInteger)
    txinwitness = Column(JSON)


class Outputs(Base):
    __tablename__ = "outputs"
    spending_txid = Column(String, ForeignKey("transactions.txid"), primary_key=True)
    n = Column(Integer, primary_key=True)
    value = Column(BigInteger)
    scriptPubKey = Column(JSON)


class CoinbaseInputs(Base):
    __tablename__ = "coinbaseinputs"
    blockhash = Column(String, ForeignKey("blocks.hash"), primary_key=True)
    spending_txid = Column(String, ForeignKey("transactions.txid"))
    version = Column(Integer)
    locktime = Column(BigInteger)
    sequence = Column(BigInteger)
    coinbase = Column(String)


# --------------
# DB Set Up
# --------------
def get_database_url() -> str:
    load_dotenv()
    url = os.getenv("DB_URL")
    if url is None:
        raise ValueError("Database URL is not set.")
    return url


def create_db_engine(url: str | None = None):
    with context_manager.fail_on_error():
        logger.info("Creating Database Engine at %s", url)
        url = url or get_database_url()
        connect_args = {}
        logger.info("Database Engine created.")
        return create_engine(
            url,
            echo=False,
            hide_parameters=True,
            connect_args=connect_args,
            json_serializer=lambda v: orjson.dumps(v).decode(),
        )


def create_tables(engine: Engine) -> None:
    # TODO: for later use Alembic instead
    with context_manager.fail_on_error():
        logger.info("Creating Tables...")
        Base.metadata.create_all(engine)
        table_names = inspect(engine).get_table_names()
        logger.info("Tables created: %s", table_names)


def set_up_db() -> Engine:
    db_url = get_database_url()
    engine = create_db_engine(db_url)
    create_tables(engine)
    return engine


# --------------
# Insertion
# --------------
@retry(
    stop=stop_after_attempt(INSERTION_RETRIES),
    wait=wait_exponential_jitter(initial=1, jitter=1.5, max=10),
    retry_error_callback=raise_outside_of_retry,
    retry=retry_if_exception(should_retry),
    before_sleep=before_sleep_log(logger, WARNING),
)
def insert_from_dict(list_dict: list[dict], table_class: type[Base], s: Session):
    if not list_dict:
        logger.info("No rows to insert for %s, skipping.", table_class.__name__)
        return
    with context_manager.rollback_on_error(s):
        if not issubclass(table_class, Base):
            raise TypeError("table_class arg must be a subclass of Base.")
        logger.info("Inserting %s representations of the resource %s...", len(list_dict), table_class.__name__)
        s.execute(insert(cast(Table, table_class.__table__)), list_dict)


def _prepare_block_data(block: dict) -> tuple[dict, dict, list, list, list]:
    with context_manager.fail_on_error():
        block_hash = block["hash"]
        txs = []
        inputs = []
        outputs = []
        cb = block["coinbase_tx"]
        for field in COINBASETX_FIELDS_TO_EXCLUDE:
            cb.pop(field, None)

        for k, tx in enumerate(block["tx"]):
            # 1. Transactions
            txid = tx["txid"]
            vin = tx.pop("vin")
            vout = tx.pop("vout")
            tx["blockhash"] = block_hash
            tx["n"] = k

            # 1. Inputs
            for n, i in enumerate(vin):
                # 2. Coinbase
                if k == 0 and n == 0 and "coinbase" in i:
                    cb["blockhash"] = block_hash
                    cb["spending_txid"] = txid
                    break  # first input of first block's tx is COINBASE not INPUTS

                i["spending_txid"] = txid
                i["n"] = n
                # txinwitness only present in Segwit inputs
                if "txinwitness" not in i:
                    i["txinwitness"] = None
                inputs.append(i)

            # 3. Outputs
            for o in vout:
                o["spending_txid"] = txid
                outputs.append(o)

            txs.append(tx)

        for field in BLOCK_FIELDS_TO_EXCLUDE:
            block.pop(field, None)
        return block, cb, txs, inputs, outputs


def insert_block(block: dict, engine: Engine):
    if not block:
        logger.error("Block dict empty, nothing to insert.")
        return
    block_info, coinbase, txs, inputs, outputs = _prepare_block_data(block)
    logger.info("Adding Blocks height: %s and all it's transactions...", block["height"])
    with Session(engine) as s:
        insert_from_dict([block_info], Blocks, s)
        insert_from_dict(txs, Transactions, s)
        insert_from_dict([coinbase], CoinbaseInputs, s)
        insert_from_dict(inputs, Inputs, s)
        insert_from_dict(outputs, Outputs, s)
        s.commit()
        logger.info("Finished processing block %s.", block["height"])


def insert_blocks(blocks: list[dict], engine: Engine):
    if not blocks:
        logger.error("Block list empty, nothing to insert.")
        return
    for block in blocks:
        insert_block(block, engine)
