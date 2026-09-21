import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Defaults to a local SQLite file. Point DATABASE_URL at a real Postgres
# connection string later (e.g. if you add a Postgres plugin on Railway)
# without touching any other code - see the note in state.py about why the
# SQLite path needs a small timezone workaround that Postgres wouldn't.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./table_tent.db")

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
