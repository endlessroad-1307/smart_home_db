"""FastAPI 入口：定义 5 个查询接口，把数据库数据暴露给浏览器

启动：在项目根目录运行  uvicorn app.main:app --reload
测试：浏览器打开 http://127.0.0.1:8000/docs
"""
from datetime import datetime

from fastapi import Depends, FastAPI, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models, schemas
from .database import get_db

app = FastAPI(
    title="智能家居数据服务",
    description="查询用户、设备、使用记录、能耗与安防事件",
    version="1.0",
)


@app.get("/users", response_model=list[schemas.UserOut], summary="查看用户")
def list_users(
    limit: int = Query(20, ge=1, le=200, description="每页条数"),
    offset: int = Query(0, ge=0, description="跳过条数（翻页用）"),
    db: Session = Depends(get_db),
):
    return db.query(models.User).order_by(models.User.id).offset(offset).limit(limit).all()


@app.get("/devices", response_model=list[schemas.DeviceOut], summary="按用户/类型/房间筛选设备")
def list_devices(
    user_id: int | None = Query(None, description="按所属用户筛选"),
    type: models.DeviceType | None = Query(None, description="按设备类型筛选，如 Light"),
    room: str | None = Query(None, description="按房间筛选，如 客厅"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(models.Device)
    if user_id is not None:
        q = q.filter(models.Device.user_id == user_id)
    if type is not None:
        q = q.filter(models.Device.type == type)
    if room:
        q = q.filter(models.Device.room == room)
    return q.order_by(models.Device.id).offset(offset).limit(limit).all()


@app.get("/usages", response_model=list[schemas.UsageOut], summary="按设备/日期范围查询使用记录")
def list_usages(
    device_id: int | None = Query(None, description="按设备筛选"),
    start: datetime | None = Query(None, description="使用开始时间下限，如 2026-11-01"),
    end: datetime | None = Query(None, description="使用开始时间上限，如 2026-11-10"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(models.DeviceUsage)
    if device_id is not None:
        q = q.filter(models.DeviceUsage.device_id == device_id)
    if start is not None:
        q = q.filter(models.DeviceUsage.start_time >= start)
    if end is not None:
        q = q.filter(models.DeviceUsage.start_time <= end)
    return q.order_by(models.DeviceUsage.start_time).offset(offset).limit(limit).all()


@app.get("/analytics/energy", response_model=list[schemas.EnergySummary], summary="设备能耗汇总")
def energy_summary(
    user_id: int | None = Query(None, description="只看某个用户的设备"),
    top: int = Query(20, ge=1, le=100, description="取能耗最高的前 N 台设备"),
    db: Session = Depends(get_db),
):
    # duration 是生成列（秒），直接求和换算成小时
    seconds = func.sum(models.DeviceUsage.duration)
    q = (
        db.query(
            models.Device.id.label("device_id"),
            models.Device.name.label("device_name"),
            models.Device.type.label("device_type"),
            func.count(models.DeviceUsage.id).label("usage_count"),
            (seconds / 3600.0).label("total_hours"),
            func.sum(models.DeviceUsage.energy_consumption).label("total_energy"),
        )
        .join(models.DeviceUsage, models.DeviceUsage.device_id == models.Device.id)
        .group_by(models.Device.id)
    )
    if user_id is not None:
        q = q.filter(models.Device.user_id == user_id)
    return (
        q.order_by(func.sum(models.DeviceUsage.energy_consumption).desc())
        .limit(top)
        .all()
    )


@app.get("/security-events", response_model=list[schemas.SecurityEventOut], summary="查询安防事件")
def list_security_events(
    device_id: int | None = Query(None, description="按设备筛选"),
    severity: models.EventSeverity | None = Query(None, description="按严重程度筛选：low/medium/high/critical"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(models.SecurityEvent)
    if device_id is not None:
        q = q.filter(models.SecurityEvent.device_id == device_id)
    if severity is not None:
        q = q.filter(models.SecurityEvent.severity == severity)
    # 严重程度高的排前面，同级按时间倒序
    return q.order_by(models.SecurityEvent.timestamp.desc()).offset(offset).limit(limit).all()


@app.get("/feedbacks", response_model=list[schemas.FeedbackOut], summary="查询用户反馈")
def list_feedbacks(
    user_id: int | None = Query(None, description="按用户筛选"),
    device_id: int | None = Query(None, description="按设备筛选（系统级反馈 device_id 为空）"),
    min_rating: int | None = Query(None, ge=1, le=5, description="最低评分，如填 4 只看 4~5 分"),
    max_rating: int | None = Query(None, ge=1, le=5, description="最高评分，如填 2 只看 1~2 分差评"),
    generated_by: str | None = Query(None, description="按生成方式筛选：template_v1 或 llm:模型名"),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(models.UserFeedback)
    if user_id is not None:
        q = q.filter(models.UserFeedback.user_id == user_id)
    if device_id is not None:
        q = q.filter(models.UserFeedback.device_id == device_id)
    if min_rating is not None:
        q = q.filter(models.UserFeedback.rating >= min_rating)
    if max_rating is not None:
        q = q.filter(models.UserFeedback.rating <= max_rating)
    if generated_by:
        q = q.filter(models.UserFeedback.generated_by == generated_by)
    return q.order_by(models.UserFeedback.id).offset(offset).limit(limit).all()


@app.get("/", summary="健康检查")
def root():
    return {"status": "ok", "docs": "http://127.0.0.1:8000/docs"}
