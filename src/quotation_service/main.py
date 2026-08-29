from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Union
from datetime import datetime
from sqlalchemy import text

from .config import DB_TYPE, DatabaseType
from .repository import QuotationRepository
from .repository_factory import get_repository, close_connections
from .schemas import (
    QuotationCreate,
    QuotationUpdate,
    QuotationResponse,
    QuotationAdvanceStatus,
    ORDER_STATUSES,
    ORDER_STATUS_TRANSITIONS,
)
from .auth_helper import get_current_user, require_role
from .database.main_db import ensure_indexes
from .database.connection_manager import close_all
from .routers.admin_database import router as admin_database_router
from .routers.chat import router as chat_router
from .services import synchronization_service


import logging
import os
import sentry_sdk

# Configure logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Known production frontend origins. Used as a safe default so the API is never
# wide-open ("*") yet never fully locked down (which would break the live app if
# the ALLOWED_ORIGINS env var is not injected by the platform).
DEFAULT_ALLOWED_ORIGINS = [
    "https://lovelaundry-manager.vercel.app",
    "http://localhost:5173",
    "http://localhost:3000",
]

# CORS configuration - prefer the platform-provided allowlist; otherwise use the
# known-good production origins. Never fall back to "*".
try:
    ALLOWED_ORIGINS_ENV = os.getenv("ALLOWED_ORIGINS", "")
    # Always keep the known-good production origins in the allowlist, and never
    # accept a literal "*" (Starlette refuses "*" when credentials are enabled).
    # This way a missing or misconfigured ALLOWED_ORIGINS env can never lock out
    # the live frontend.
    configured = [
        o.strip()
        for o in ALLOWED_ORIGINS_ENV.split(",")
        if o.strip() and o.strip() != "*"
    ]
    ALLOWED_ORIGINS = list(dict.fromkeys(configured + list(DEFAULT_ALLOWED_ORIGINS)))
    ALLOW_CREDENTIALS = True

    logger.info(f"CORS configured with origins: {ALLOWED_ORIGINS}, credentials: {ALLOW_CREDENTIALS}")
except Exception as e:
    logger.warning(f"CORS configuration failed, using safe default: {e}")
    ALLOWED_ORIGINS = list(DEFAULT_ALLOWED_ORIGINS)
    ALLOW_CREDENTIALS = True

ON_VERCEL = os.getenv("VERCEL") == "1"

SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.1,
    )

app = FastAPI(title="Quotation Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(admin_database_router)
app.include_router(chat_router)


@app.on_event("startup")
def startup_event():
    """Initialize database schema on startup"""
    try:
        if DB_TYPE in (DatabaseType.POSTGRESQL, DatabaseType.SQLITE):
            from .database import Base, engine

            if engine:
                Base.metadata.create_all(bind=engine)
            _migrate_quotation_schema()
        else:
            ensure_indexes()
    except Exception:
        logger.exception("Failed to initialize database schema on startup")


def _migrate_quotation_schema():
    """Add status_history to existing quotation tables (create_all won't alter)."""
    if DB_TYPE not in (DatabaseType.POSTGRESQL, DatabaseType.SQLITE):
        return
    try:
        from .database import engine

        if engine is None:
            return
        with engine.begin() as conn:
            if DB_TYPE == DatabaseType.POSTGRESQL:
                conn.execute(
                    text(
                        "ALTER TABLE quotations ADD COLUMN IF NOT EXISTS "
                        "status_history JSON NOT NULL DEFAULT '[]'::json"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE quotations ADD COLUMN IF NOT EXISTS "
                        "status_history JSON NOT NULL DEFAULT '[]'"
                    )
                )
        logger.info("Quotation schema migration check completed")
    except Exception:
        logger.exception("Quotation schema migration failed")

    if not ON_VERCEL:
        try:
            synchronization_service.start_worker()
        except Exception:
            logger.exception("Failed to start sync worker")


@app.on_event("shutdown")
def shutdown_event():
    """Close database connections on shutdown"""
    try:
        synchronization_service.stop_worker()
    except Exception:
        pass
    try:
        close_connections()
    except Exception:
        pass
    try:
        close_all()
    except Exception:
        pass


@app.get("/")
def root():
    return {
        "message": "Quotation Service API",
        "version": "1.0.0",
        "database": DB_TYPE.value,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get(
    "/quotations",
    response_model=list[QuotationResponse],
    dependencies=[Depends(require_role(["ADMIN", "MANAGER", "STAFF"]))],
)
def get_all_quotations(
    tag: str | None = None,
    repo: QuotationRepository = Depends(get_repository)
):
    """Get all quotations with optional tag filtering"""
    if tag:
        quotations = repo.get_by_tag(tag)
    else:
        quotations = repo.get_all()
    return quotations


@app.get(
    "/quotations/guest/shop",
    response_model=list[QuotationResponse],
)
def get_shop_quotations_guest(repo: QuotationRepository = Depends(get_repository)):
    """Public endpoint for guest users to view shop quotations only"""
    quotations = repo.get_by_tag("shop")
    return quotations


@app.get(
    "/quotations/{quotation_id}",
    response_model=QuotationResponse,
    dependencies=[Depends(require_role(["ADMIN", "MANAGER", "STAFF"]))],
)
def get_quotation(
    quotation_id: Union[int, str],
    repo: QuotationRepository = Depends(get_repository),
):
    # MongoDB uses string IDs, PostgreSQL uses integers
    if DB_TYPE == DatabaseType.MONGODB:
        quotation = repo.get_by_id(str(quotation_id))
    else:
        quotation = repo.get_by_id(int(quotation_id))

    if not quotation:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return quotation


@app.post(
    "/quotations",
    response_model=QuotationResponse,
    status_code=201,
    dependencies=[Depends(require_role(["ADMIN", "MANAGER"]))],
)
def create_quotation(
    payload: QuotationCreate,
    repo: QuotationRepository = Depends(get_repository),
    current_user: dict = Depends(get_current_user),
):
    line_items_data = [item.model_dump() for item in payload.line_items]

    username = current_user.get("username") or current_user.get("sub")
    if payload.status not in ORDER_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Status must be one of {ORDER_STATUSES}",
        )

    quotation_data = {
        "client_name": payload.client_name,
        "quotation_title": payload.quotation_title,
        "line_items": line_items_data,
        "status": payload.status,
        "status_history": [
            {
                "status": payload.status,
                "changed_at": datetime.utcnow().isoformat() + "Z",
                "changed_by": username,
                "note": "Created",
            }
        ],
        "tag": payload.tag,
    }

    new_quotation = repo.create(quotation_data)

    # Perform simple audit log trigger if needed
    # We will implement audit logs in the bill-service / dashboard API directly
    return new_quotation


@app.put(
    "/quotations/{quotation_id}",
    response_model=QuotationResponse,
    dependencies=[Depends(require_role(["ADMIN", "MANAGER"]))],
)
def update_quotation(
    quotation_id: Union[int, str],
    payload: QuotationUpdate,
    repo: QuotationRepository = Depends(get_repository),
    current_user: dict = Depends(get_current_user),
):
    # Convert quotation_id based on database type
    if DB_TYPE == DatabaseType.MONGODB:
        quotation_id = str(quotation_id)
    else:
        quotation_id = int(quotation_id)

    current = repo.get_by_id(quotation_id)
    if not current:
        raise HTTPException(status_code=404, detail="Quotation not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "line_items" in update_data and update_data["line_items"]:
        update_data["line_items"] = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in update_data["line_items"]
        ]

    # Record a status transition in history when the status actually changes.
    if "status" in update_data and update_data["status"] is not None:
        if update_data["status"] not in ORDER_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"Status must be one of {ORDER_STATUSES}",
            )
        if update_data["status"] != current.get("status"):
            hist = list(current.get("status_history") or [])
            hist.append(
                {
                    "status": update_data["status"],
                    "changed_at": datetime.utcnow().isoformat() + "Z",
                    "changed_by": current_user.get("username") or current_user.get("sub"),
                    "note": "Status updated",
                }
            )
            update_data["status_history"] = hist

    updated_quotation = repo.update(quotation_id, update_data)

    if not updated_quotation:
        raise HTTPException(status_code=404, detail="Quotation not found")

    return updated_quotation


@app.post(
    "/quotations/{quotation_id}/status",
    response_model=QuotationResponse,
    dependencies=[Depends(require_role(["ADMIN", "MANAGER", "STAFF"]))],
)
def advance_quotation_status(
    quotation_id: Union[int, str],
    payload: QuotationAdvanceStatus,
    repo: QuotationRepository = Depends(get_repository),
    current_user: dict = Depends(get_current_user),
):
    """Advance an order through its lifecycle, recording the transition."""
    if payload.status not in ORDER_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Status must be one of {ORDER_STATUSES}",
        )

    if DB_TYPE == DatabaseType.MONGODB:
        qid = str(quotation_id)
    else:
        qid = int(quotation_id)

    current = repo.get_by_id(qid)
    if not current:
        raise HTTPException(status_code=404, detail="Quotation not found")

    current_status = current.get("status")
    allowed = ORDER_STATUS_TRANSITIONS.get(current_status, [])
    if payload.status != current_status and payload.status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot move from '{current_status}' to '{payload.status}'. "
            f"Allowed next: {allowed or ['none (terminal)']}",
        )

    hist = list(current.get("status_history") or [])
    hist.append(
        {
            "status": payload.status,
            "changed_at": datetime.utcnow().isoformat() + "Z",
            "changed_by": current_user.get("username") or current_user.get("sub"),
            "note": payload.note,
        }
    )

    updated = repo.update(qid, {"status": payload.status, "status_history": hist})
    if not updated:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return updated


@app.get(
    "/quotations/{quotation_id}/tracking",
    dependencies=[Depends(require_role(["ADMIN", "MANAGER", "STAFF"]))],
)
def track_quotation(
    quotation_id: Union[int, str],
    repo: QuotationRepository = Depends(get_repository),
):
    """Customer/operator-facing order timeline (status + history, no PII)."""
    if DB_TYPE == DatabaseType.MONGODB:
        qid = str(quotation_id)
    else:
        qid = int(quotation_id)

    current = repo.get_by_id(qid)
    if not current:
        raise HTTPException(status_code=404, detail="Quotation not found")

    return {
        "id": current.get("id"),
        "quotation_title": current.get("quotation_title"),
        "tag": current.get("tag"),
        "status": current.get("status"),
        "status_history": current.get("status_history") or [],
        "created_at": current.get("created_at"),
        "updated_at": current.get("updated_at"),
    }


@app.delete(
    "/quotations/{quotation_id}",
    dependencies=[Depends(require_role(["ADMIN", "MANAGER"]))],
)
def delete_quotation(
    quotation_id: Union[int, str],
    repo: QuotationRepository = Depends(get_repository),
):
    # Convert quotation_id based on database type
    if DB_TYPE == DatabaseType.MONGODB:
        quotation_id = str(quotation_id)
    else:
        quotation_id = int(quotation_id)

    success = repo.delete(quotation_id)

    if not success:
        raise HTTPException(status_code=404, detail="Quotation not found")

    return {"message": "Quotation deleted successfully"}