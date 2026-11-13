"""Pydantic 模型：定义每个接口"返回什么字段"（数据的出栈格式）"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # 允许直接从 ORM 对象读取属性

    id: int
    username: str
    email: str
    house_size: float | None = None
    created_at: datetime
    updated_at: datetime


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    type: str          # 设备类型名（如 Light、Air_conditioner）
    room: str


class UsageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    start_time: datetime
    end_time: datetime
    duration: int          # 秒（数据库生成列自动算的）
    energy_consumption: float


class SecurityEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    event_type: str
    timestamp: datetime
    severity: str
    details: str | None = None


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    device_id: int | None = None
    feedback_text: str
    rating: int
    generated_by: str     # 生成方式：template_v1 / llm:模型名
    created_at: datetime


class EnergySummary(BaseModel):
    """能耗汇总：每台设备的累计使用时长与累计能耗"""

    device_id: int
    device_name: str
    device_type: str
    usage_count: int          # 使用次数
    total_hours: float        # 累计使用时长（小时）
    total_energy: float       # 累计能耗（千瓦时）
