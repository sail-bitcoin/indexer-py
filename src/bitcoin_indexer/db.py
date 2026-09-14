import os
from decimal import Decimal
from logging import WARNING
from typing import cast

import asyncpg
import orjson
from dotenv import load_dotenv
from sqlalchemy import JSON, Column, Float, BigInteger, Integer, String, Table, insert, text
from sqlalchemy.exc import DBAPIError, TimeoutError as SATimeoutError
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection, create_async_engine
from sqlalchemy.engine import make_url
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

import context_manager as cm
from logger import logger
from utils import raise_outside_of_retry


class Base(DeclarativeBase):
    pass


load_dotenv()

ASYNCPG_TRANSIENT_CONN_ERR = (asyncpg.InsufficientResourcesError, asyncpg.OperatorInterventionError)


def should_retry(exc: BaseException) -> bool:
    # pool exhausted, or Postgres unreachable (asyncpg raises OSError)
    if isinstance(exc, (SATimeoutError, OSError, *ASYNCPG_TRANSIENT_CONN_ERR)):
        return True
    # connection dropped mid-query
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


# ------------------------------------------------------------
# DB Tables
# ------------------------------------------------------------
BLOCK_FIELDS_TO_EXCLUDE = ["tx", "nextblockhash", "target", "coinbase_tx"]
TRANSACTION_FIELDS_TO_EXCLUDE = ["vin", "vout"]
COINBASETX_FIELDS_TO_EXCLUDE = ["witness"]
STALE_BLOCK_FIELDS = {"confirmations"}
STALE_TRANSACTION_FIELDS = {"confirmations"}
INSERTION_RETRIES = 4
SA_POOL_SIZE = 16
SA_POOL_MAX_OVERFLOW = 0

FOREIGN_KEYS = [
    "ALTER TABLE transactions ADD CONSTRAINT fk_transactions_blockhash_blocks FOREIGN KEY (blockhash) REFERENCES blocks (hash)",
    "ALTER TABLE inputs ADD CONSTRAINT fk_inputs_spending_txid_transactions FOREIGN KEY (spending_txid) REFERENCES transactions (txid)",
    "ALTER TABLE outputs ADD CONSTRAINT fk_outputs_spending_txid_transactions FOREIGN KEY (spending_txid) REFERENCES transactions (txid)",
    "ALTER TABLE coinbaseinputs ADD CONSTRAINT fk_coinbaseinputs_blockhash_blocks FOREIGN KEY (blockhash) REFERENCES blocks (hash)",
    "ALTER TABLE coinbaseinputs ADD CONSTRAINT fk_coinbaseinputs_spending_txid_transactions FOREIGN KEY (spending_txid) REFERENCES transactions (txid)",
]

FK_ORPHAN_CHECKS = [
    (
        "transactions.blockhash -> blocks.hash",
        """
        SELECT count(*) FROM transactions t
        LEFT JOIN blocks b ON t.blockhash = b.hash
        WHERE t.blockhash IS NOT NULL AND b.hash IS NULL
        """,
    ),
    (
        "inputs.spending_txid -> transactions.txid",
        """
        SELECT count(*) FROM inputs i
        LEFT JOIN transactions t ON i.spending_txid = t.txid
        WHERE i.spending_txid IS NOT NULL AND t.txid IS NULL
        """,
    ),
    (
        "outputs.spending_txid -> transactions.txid",
        """
        SELECT count(*) FROM outputs o
        LEFT JOIN transactions t ON o.spending_txid = t.txid
        WHERE o.spending_txid IS NOT NULL AND t.txid IS NULL
        """,
    ),
    (
        "coinbaseinputs.blockhash -> blocks.hash",
        """
        SELECT count(*) FROM coinbaseinputs c
        LEFT JOIN blocks b ON c.blockhash = b.hash
        WHERE c.blockhash IS NOT NULL AND b.hash IS NULL
        """,
    ),
    (
        "coinbaseinputs.spending_txid -> transactions.txid",
        """
        SELECT count(*) FROM coinbaseinputs c
        LEFT JOIN transactions t ON c.spending_txid = t.txid
        WHERE c.spending_txid IS NOT NULL AND t.txid IS NULL
        """,
    ),
]


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
    hex = Column(String)
    size = Column(Integer)
    vsize = Column(Integer)
    weight = Column(Integer)
    version = Column(Integer)
    locktime = Column(BigInteger)
    fee = Column(BigInteger)
    blockhash = Column(String)


class Inputs(Base):
    __tablename__ = "inputs"
    spending_txid = Column(String, primary_key=True)
    n = Column(Integer, primary_key=True)
    txid = Column(String)
    vout = Column(Integer)
    scriptSig = Column(JSON)
    sequence = Column(BigInteger)
    txinwitness = Column(JSON)


class Outputs(Base):
    __tablename__ = "outputs"
    spending_txid = Column(String, primary_key=True)
    n = Column(Integer, primary_key=True)
    value = Column(BigInteger)
    scriptPubKey = Column(JSON)


class CoinbaseInputs(Base):
    __tablename__ = "coinbaseinputs"
    blockhash = Column(String, primary_key=True)
    spending_txid = Column(String)
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
    url = url or get_database_url()
    logger.info("Creating Database AsyncEngine at %s", make_url(url).render_as_string(hide_password=True))
    return create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        connect_args={"server_settings": {"synchronous_commit": "off"}},
        pool_size=SA_POOL_SIZE,
        max_overflow=SA_POOL_MAX_OVERFLOW,
        json_serializer=lambda v: orjson.dumps(v).decode(),
    )


async def create_tables(engine: AsyncEngine) -> None:
    logger.info("Creating Tables...")
    async with engine.connect() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.commit()
    logger.info("Tables created.")


async def set_up_db() -> AsyncEngine:
    engine = create_db_engine(get_database_url())
    await create_tables(engine)
    return engine


async def foreign_keys_sanity_checks(conn: AsyncConnection) -> bool:
    """Check that there is no orphan columns before adding FKs"""
    total = 0
    for label, sql in FK_ORPHAN_CHECKS:
        count = await conn.scalar(text(sql))
        if count != 0:
            logger.error("%s orphans have been found in %s FK, skipping adding Foreign Keys.", count, label)
        total += count
    return total == 0


async def add_foreign_keys(e: AsyncEngine):
    """Add foreign keys after adding the data optimize the loading time"""
    logger.info("Adding Foreign Keys to tables..")
    with cm.catch_db_exceptions():
        async with e.connect() as conn:
            if await foreign_keys_sanity_checks(conn):
                for ddl in FOREIGN_KEYS:
                    await conn.execute(text(ddl))
                await conn.commit()
                logger.info("FKs added.")


# --------------
# Insertion
# --------------
async def _insert_from_dict(list_dict: list[dict], table_class: type[Base], conn: AsyncConnection):
    if not list_dict:
        logger.info("No rows to insert for %s, skipping.", table_class.__name__)
        return
    if not issubclass(table_class, Base):
        raise TypeError("table_class arg must be a subclass of Base.")
    logger.info("Inserting %s representations of the resource %s...", len(list_dict), table_class.__name__)
    await conn.execute(insert(cast(Table, table_class.__table__)), list_dict)


def _prepare_block_data(block: dict) -> tuple[dict, dict, list, list, list]:
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
        fee = int(Decimal(str(tx.get("fee", 0))) * 10**8)
        tx["fee"] = fee

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
            sats = int(Decimal(str(o["value"])) * 10**8)
            o["value"] = sats
            outputs.append(o)

        txs.append(tx)

    for field in BLOCK_FIELDS_TO_EXCLUDE:
        block.pop(field, None)
    return block, cb, txs, inputs, outputs


@retry(
    stop=stop_after_attempt(INSERTION_RETRIES),
    wait=wait_exponential_jitter(initial=1, jitter=1.5, max=10),
    retry_error_callback=raise_outside_of_retry,
    retry=retry_if_exception(should_retry),
    before_sleep=before_sleep_log(logger, WARNING),
)
async def _insert_prepared(block_info: dict, coinbase: dict, txs: list, inputs: list, outputs, e: AsyncEngine):
    logger.info("Adding Blocks height: %s and all it's transactions...", block_info["height"])
    async with e.connect() as conn:
        await _insert_from_dict([block_info], Blocks, conn)
        await _insert_from_dict(txs, Transactions, conn)
        await _insert_from_dict([coinbase], CoinbaseInputs, conn)
        await _insert_from_dict(inputs, Inputs, conn)
        await _insert_from_dict(outputs, Outputs, conn)
        await conn.commit()
    logger.info("Finished processing block %s.", block_info["height"])


async def insert_block(block: dict, e: AsyncEngine):
    if not block:
        logger.error("Block dict empty, nothing to insert.")
        return
    prepared = _prepare_block_data(block)
    await _insert_prepared(*prepared, e)


async def insert_blocks(blocks: list[dict], e: AsyncEngine):
    if not blocks:
        logger.error("Block list empty, nothing to insert.")
        return
    for block in blocks:
        await insert_block(block, e)
