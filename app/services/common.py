from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


def paginate(db: Session, query: Select, limit: int, offset: int) -> dict:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = db.scalars(query.limit(limit).offset(offset)).all()
    return {"items": items, "total": total or 0, "limit": limit, "offset": offset}


def like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
