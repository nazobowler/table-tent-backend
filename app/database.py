import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Defaults to a local SQLite file. Point DATABASE_URL at a real Postgres
# connection string later (e.g. if you add a Postgres plugin on Railway)
# without touching any other code - see the note in state.py about why the
# SQLite path needs a small timezone workaround that Postgres wouldn't.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./table_tent.db")

# Pin the Postgres driver explicitly rather than leaving it to whatever
# SQLAlchemy's default happens to be for a bare "postgresql://" URL. That
# default isn't guaranteed stable across SQLAlchemy versions - it quietly
# changed from psycopg2 to psycopg (v3) in SQLAlchemy 2.1, which took the
# live deploy down (ModuleNotFoundError: No module named 'psycopg') the
# moment a fresh build picked up the new release, since requirements.txt
# pins sqlalchemy>=2.0 with no upper bound. psycopg2-binary is the driver
# actually installed and tested here, so make sure that's the one used
# regardless of which SQLAlchemy version ends up installed. Also handles
# "postgres://" (the scheme some hosted Postgres providers still hand out),
# which SQLAlchemy 1.4+ refuses outright without a rewrite.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgresql://"):]

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
