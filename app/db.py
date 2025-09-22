from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from .settings import settings

database_url = settings.database_url
if database_url is None:
    raise RuntimeError("DATABASE_URL is not configured")

# ``psycopg2`` struggles with raw DSN strings that contain non-UTF8 characters
# (for example, passwords that include symbols such as ``²``) because it tries
# to decode the DSN bytes as UTF-8 before establishing the connection. By
# parsing the URL up-front and passing the resulting ``URL`` object to SQLAlchemy,
# the driver receives the discrete connection arguments (user, password, host,
# etc.) directly instead of a single encoded string. This sidesteps the
# problematic decode step while keeping the configuration unchanged for callers.
engine = create_engine(make_url(database_url), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
