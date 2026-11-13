"""验证脚本：初始化数据库并打印所有已创建的表"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.makedirs("data", exist_ok=True)

from sqlalchemy import inspect

from app.database import Base, engine
import app.models  # noqa: F401  确保模型已注册

# 建表（已存在则跳过）
Base.metadata.create_all(bind=engine)

tables = inspect(engine).get_table_names()
print("已创建的数据表:", tables)

# 验证生成列 duration
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models import Device, DeviceType, DeviceUsage, User

db = SessionLocal()
try:
    u = User(username="测试用户", email="test@example.com", house_size=89.5)
    db.add(u)
    db.commit()

    d = Device(user_id=u.id, name="客厅灯", type=DeviceType.LIGHT, room="客厅")
    db.add(d)
    db.commit()

    start = datetime(2026, 11, 11, 9, 0, 0)
    usage = DeviceUsage(
        device_id=d.id, start_time=start, end_time=start + timedelta(minutes=30),
        energy_consumption=0.5,
    )
    db.add(usage)
    db.commit()
    db.refresh(usage)
    print("生成列 duration 自动计算 =", usage.duration, "秒")  # 应为 1800
finally:
    db.close()
    import sqlalchemy
    with engine.begin() as conn:
        for t in ["device_usages", "security_events", "user_feedbacks",
                  "device_co_occurrences", "devices", "users"]:
            conn.execute(sqlalchemy.text(f"DELETE FROM {t}"))
print("验证完成，测试数据已清理")
