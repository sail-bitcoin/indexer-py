import os

from dotenv import load_dotenv
from sqlalchemy import JSON, Boolean, Column, Float, ForeignKey, Integer, String, create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session

import context_manager
from logger import logger


class Base(DeclarativeBase):
    pass


load_dotenv()

# ------------------------------------------------------------
# DB Tables
# ------------------------------------------------------------
BLOCK_FIELDS_TO_EXCLUDE = ["tx", "nextblockhash", "target", "coinbase_tx"]
TRANSACTION_FIELDS_TO_EXCLUDE = ["vin", "vout"]
COINBASETX_FIELDS_TO_EXCLUDE = ["witness"]

STALE_BLOCK_FIELDS = {"confirmations"}

# what about "in_active_chain"?
STALE_TRANSACTION_FIELDS = {"confirmations"}


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
    nonce = Column(Integer)
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
    locktime = Column(Integer)
    fee = Column(Integer)
    blockhash = Column(String, ForeignKey("blocks.hash"))


class Inputs(Base):
    __tablename__ = "inputs"
    spending_txid = Column(String, ForeignKey("transactions.txid"), primary_key=True)
    n = Column(Integer, primary_key=True)
    txid = Column(String)
    vout = Column(Integer)
    scriptSig = Column(JSON)
    sequence = Column(Integer)
    txinwitness = Column(JSON)


class Outputs(Base):
    __tablename__ = "outputs"
    spending_txid = Column(String, ForeignKey("transactions.txid"), primary_key=True)
    n = Column(Integer, primary_key=True)
    value = Column(Integer)
    scriptPubKey = Column(JSON)


class CoinbaseInputs(Base):
    __tablename__ = "coinbaseinputs"
    blockhash = Column(String, ForeignKey("blocks.hash"), primary_key=True)
    spending_txid = Column(String, ForeignKey("transactions.txid"))
    version = Column(Integer)
    locktime = Column(Integer)
    sequence = Column(Integer)
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
        if url.startswith("sqlite"):
            # TODO: Required when the engine is shared across threads?
            connect_args["check_same_thread"] = False
        logger.info("Database Engine created.")
        return create_engine(url, echo=False, hide_parameters=True, connect_args=connect_args)


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
def insert_from_dict(list_dict: list[dict], table_class: type[Base], s: Session):
    with context_manager.fail_on_db_insert_error(s):
        if not issubclass(table_class, Base):
            raise TypeError("table_class arg must be a subclass of Base.")
        logger.info("Inserting %s representations of the resource %s...", len(list_dict), table_class.__name__)
        pk_name = inspect(table_class).primary_key[0].name
        objects = []
        for data in list_dict:
            model = table_class(**data)
            objects.append(model)
            logger.debug("Inserting a representation of %s with PK %s={getattr(model, pk_name)}", table_class.__name__, pk_name)
        s.add_all(objects)
        s.commit()
        logger.info("Insertion done.")


def _prepare_block_data(block: dict) -> tuple[dict, dict, list, list, list]:
    with context_manager.fail_on_error():
        block_hash = block["hash"]
        txs = []
        inputs = []
        outputs = []
        cb = {k: v for k, v in block["coinbase_tx"].items() if k not in COINBASETX_FIELDS_TO_EXCLUDE}

        for k, tx in enumerate(block["tx"]):
            # 1. Transactions
            txid = tx["txid"]
            new_tx = {field: value for field, value in tx.items() if field not in TRANSACTION_FIELDS_TO_EXCLUDE}
            new_tx["blockhash"] = block_hash
            new_tx["n"] = k

            # 1. Inputs
            for n, i in enumerate(tx["vin"]):
                # 2. Coinbase
                if k == 0 and n == 0 and "coinbase" in i:
                    cb = {**cb, "blockhash": block_hash, "spending_txid": txid}
                    break  # first input of first block's tx is COINBASE not INPUTS

                inputs.append({**i, "spending_txid": txid, "n": n})

            # 3. Outputs
            for o in tx["vout"]:
                outputs.append({**o, "spending_txid": txid})

            txs.append(new_tx)

        new_block = {k: v for k, v in block.items() if k not in BLOCK_FIELDS_TO_EXCLUDE}
        return new_block, cb, txs, inputs, outputs


def insert_all(block: dict, engine: Engine):
    block_info, coinbase, txs, inputs, outputs = _prepare_block_data(block)
    logger.info("Adding Blocks height: %s and all it's transactions...", block["height"])
    with Session(engine) as s:
        insert_from_dict([block_info], Blocks, s)
        insert_from_dict([coinbase], CoinbaseInputs, s)
        insert_from_dict(txs, Transactions, s)
        insert_from_dict(inputs, Inputs, s)
        insert_from_dict(outputs, Outputs, s)
        logger.info("Finished processing block %s.", block["height"])
