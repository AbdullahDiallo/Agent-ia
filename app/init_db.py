from uuid import UUID

from .db import engine
from .db import SessionLocal
from .models import Base, Tenant
from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        tenant_id = UUID(str(settings.default_tenant_id))
        existing = db.get(Tenant, tenant_id)
        if not existing:
            db.add(Tenant(id=tenant_id, slug="default", name="Default Tenant", is_active=True))
            db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
    logger.info("Database initialized (tables created if not existing)")
