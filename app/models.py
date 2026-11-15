from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


class SiteSettings(Base):
    """Реквизиты и тексты документов. Одна строка с id=1."""
    __tablename__ = "site_settings"
    id = Column(Integer, primary_key=True)
    legal_name = Column(String, default="LotAi")
    recipient_name = Column(String, default="")
    inn = Column(String, default="")
    privacy_policy = Column(Text, default="")
    public_offer = Column(Text, default="")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    subscriptions = relationship("Subscription", back_populates="user")


class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    days = Column(Integer, nullable=False)
    traffic_gb = Column(Integer, nullable=False)  # 0 = безлимит
    price = Column(Integer, nullable=False)        # в рублях
    is_active = Column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    inv_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=True)
    kind = Column(String, default="new")  # new | renew
    amount = Column(Integer, nullable=False)
    status = Column(String, default="pending")  # pending | paid | failed
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)
    user = relationship("User")
    plan = relationship("Plan")
    subscription = relationship("Subscription")


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)  # имя клиента в 3x-ui
    name = Column(String, default="")  # как показываем подписку в кабинете
    comment = Column(String, default="")  # подпись в панели и в шаблоне {{COMMENT}}
    sub_id = Column(String, index=True, nullable=False)
    sub_url = Column(String, nullable=False)
    client_uuid = Column(String, default="")
    expiry_time = Column(DateTime, nullable=False)
    traffic_gb = Column(Integer, default=0)
    total_bytes = Column(Integer, default=0)
    upload_bytes = Column(Integer, default=0)
    download_bytes = Column(Integer, default=0)
    panel_enable = Column(Boolean, default=True)
    active = Column(Boolean, default=True)
    last_sync_at = Column(DateTime, nullable=True)
    sync_error = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = relationship("User", back_populates="subscriptions")
