import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


# ---------- 枚举类型 ----------
class DeviceType(str, enum.Enum):
    """设备类型（8 种）"""
    LIGHT = "Light"
    THERMOSTAT = "Thermostat"
    SECURITY_CAMERA = "Security_camera"
    DOOR_LOCK = "Door_lock"
    SMART_PLUG = "Smart_plug"
    AIR_CONDITIONER = "Air_conditioner"
    WINDOW_SENSOR = "Window_sensor"
    MOTION_SENSOR = "Motion_sensor"


class EventSeverity(str, enum.Enum):
    """安防事件严重程度（4 级）"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------- 用户表 ----------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    house_size: Mapped[float | None] = mapped_column(Float, nullable=True)  # 房屋面积（㎡）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    devices = relationship("Device", back_populates="owner", cascade="all, delete-orphan")
    feedbacks = relationship("UserFeedback", back_populates="user", cascade="all, delete-orphan")


# ---------- 设备表 ----------
class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[DeviceType] = mapped_column(Enum(DeviceType), nullable=False)
    room: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    owner = relationship("User", back_populates="devices")
    usages = relationship("DeviceUsage", back_populates="device", cascade="all, delete-orphan")
    security_events = relationship(
        "SecurityEvent", back_populates="device", cascade="all, delete-orphan"
    )
    feedbacks = relationship("UserFeedback", back_populates="device")  # 设备删除时 SET NULL


# ---------- 设备使用记录表 ----------
class DeviceUsage(Base):
    __tablename__ = "device_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 生成列：数据库自动计算时长（秒），插入时无需手动赋值
    duration: Mapped[int] = mapped_column(
        Integer,
        Computed("CAST((julianday(end_time) - julianday(start_time)) * 86400 AS INTEGER)"),
    )
    energy_consumption: Mapped[float] = mapped_column(Float, default=0.0)  # 千瓦时

    device = relationship("Device", back_populates="usages")

    __table_args__ = (
        Index("ix_device_usages_device_time", "device_id", "start_time"),
    )


# ---------- 安防事件表 ----------
class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    severity: Mapped[EventSeverity] = mapped_column(Enum(EventSeverity), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    device = relationship("Device", back_populates="security_events")

    __table_args__ = (
        Index("ix_security_events_device_time", "device_id", "timestamp"),
    )


# ---------- 用户反馈表 ----------
class UserFeedback(Base):
    __tablename__ = "user_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True  # 设备删除后保留反馈
    )
    feedback_text: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    # 生成方式：template_v1（模板生成）/ llm:模型名（大模型生成），便于追溯数据来源
    generated_by: Mapped[str] = mapped_column(String(50), nullable=False, server_default="template_v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="feedbacks")
    device = relationship("Device", back_populates="feedbacks")

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_rating_range"),
        Index("ix_user_feedbacks_user_device", "user_id", "device_id"),
    )


# ---------- 设备共现表（分析辅助表） ----------
class DeviceCoOccurrence(Base):
    __tablename__ = "device_co_occurrences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device1_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    device2_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    last_occurred: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    device1 = relationship("Device", foreign_keys=[device1_id])
    device2 = relationship("Device", foreign_keys=[device2_id])

    __table_args__ = (
        UniqueConstraint("user_id", "device1_id", "device2_id", name="uq_co_occurrence"),
    )
