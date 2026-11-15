"""Единственная строка реквизитов и текстов документов."""

from sqlalchemy.orm import Session

from .models import SiteSettings


def get_site(db: Session) -> SiteSettings:
    row = db.get(SiteSettings, 1)
    if row is not None:
        return row
    row = SiteSettings(
        id=1,
        legal_name="LotAi",
        recipient_name="",
        inn="",
        privacy_policy="",
        public_offer="",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def site_view(row: SiteSettings) -> dict:
    return {
        "legal_name": (row.legal_name or "").strip() or "LotAi",
        "recipient_name": (row.recipient_name or "").strip(),
        "inn": (row.inn or "").strip(),
        "privacy_policy": row.privacy_policy or "",
        "public_offer": row.public_offer or "",
    }
