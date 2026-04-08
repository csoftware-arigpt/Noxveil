import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "c2.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}")

os.makedirs(DATA_DIR, exist_ok=True)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)


if "sqlite" in DATABASE_URL:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in DATABASE_URL:
            result = await conn.exec_driver_sql("PRAGMA table_info(users)")
            existing_columns = {row[1] for row in result.fetchall()}
            required_columns = {
                "failed_login_attempts": "INTEGER NOT NULL DEFAULT 0",
                "locked_until": "DATETIME",
                "mfa_secret": "TEXT",
                "mfa_enabled": "BOOLEAN NOT NULL DEFAULT 0",
                "last_login_at": "DATETIME",
            }
            for column_name, column_spec in required_columns.items():
                if column_name not in existing_columns:
                    await conn.exec_driver_sql(f"ALTER TABLE users ADD COLUMN {column_name} {column_spec}")


async def close_db() -> None:
    await engine.dispose()
