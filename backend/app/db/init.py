"""Create tables: python -m app.db.init  (use Alembic once the schema settles)."""
from sqlalchemy import create_engine

from app.settings import settings

from .models import Base

if __name__ == "__main__":
    if not settings.database_url:
        raise SystemExit("Set DATABASE_URL first")
    Base.metadata.create_all(create_engine(settings.database_url))
    print("Tables created")
