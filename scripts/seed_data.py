"""生成一批模拟数据（可重复运行：先清空旧数据再生成，不会重复累加）

与旧版的区别：数据生成内置了明确的业务规则，让分析结果"有据可查"——
1. 房屋面积影响设备数量：面积越大平均设备越多（允许随机例外）
2. 设备类型影响使用频率与时长能耗：空调高能耗、门锁/传感器短时长高频次
3. 时间影响使用模式：灯光晚间双峰、门锁早晚通勤峰、空调午后+夜间
4. 设备共现从使用记录计算：同一用户同一天内时间区间重叠的设备对，
   不再单独随机生成

运行方式（在项目根目录下）：
    python -m scripts.seed_data
或直接右键 Run 本文件（PyCharm）
"""
import os
import random
import sys
from datetime import datetime, timedelta

# 保证无论从哪里运行都能找到项目根目录下的 app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.database import DATA_DIR, Base, SessionLocal, engine
from app.models import (
    Device,
    DeviceCoOccurrence,
    DeviceType,
    DeviceUsage,
    EventSeverity,
    SecurityEvent,
    User,
    UserFeedback,
)

# ---------- 可调参数 ----------
RANDOM_SEED = 42          # 固定随机种子 → 每次生成的数据完全一样，可复现
N_USERS = 200             # 用户数（几百个即可）
NOW = datetime(2026, 11, 11, 12, 0, 0)  # 以固定"当前时间"生成，保证可复现

rng = random.Random(RANDOM_SEED)

ROOMS = ["客厅", "卧室", "厨房", "书房", "卫生间", "阳台", "玄关"]
EVENT_TYPES = ["移动侦测", "门窗开启", "门铃触发", "烟雾报警", "摄像头离线", "异常闯入"]
SEVERITIES = list(EventSeverity)
FEEDBACK_TEXTS = [
    "响应灵敏，很好用", "App 连接偶尔不稳定", "夜视效果清晰",
    "待机功耗有点高", "安装简单，家人都会用", "提醒推送及时",
    "误报有点多", "续航表现不错", "外观和家装很搭", "联动场景很实用",
]
# 各设备类型的典型功率（千瓦），能耗 = 功率 × 时长
POWER_KW = {
    DeviceType.LIGHT: 0.01,
    DeviceType.THERMOSTAT: 0.5,
    DeviceType.SECURITY_CAMERA: 0.006,
    DeviceType.DOOR_LOCK: 0.002,
    DeviceType.SMART_PLUG: 0.05,
    DeviceType.AIR_CONDITIONER: 1.2,
    DeviceType.WINDOW_SENSOR: 0.001,
    DeviceType.MOTION_SENSOR: 0.001,
}
# 业务规则 2：设备类型决定使用频率（30 天内次数区间）与单次时长（小时区间）
DEVICE_PROFILE = {
    #                           次数区间       单次时长(小时)     时间模式
    DeviceType.LIGHT:           ((18, 30), (0.5, 4.0),   "evening"),
    DeviceType.THERMOSTAT:      ((8, 15),  (1.0, 5.0),   "all_day"),
    DeviceType.SECURITY_CAMERA: ((25, 35), (0.02, 0.15), "all_day"),
    DeviceType.DOOR_LOCK:       ((20, 30), (0.01, 0.08), "commute"),
    DeviceType.SMART_PLUG:      ((15, 25), (0.5, 3.0),   "all_day"),
    DeviceType.AIR_CONDITIONER: ((6, 14),  (1.0, 6.0),   "afternoon_night"),
    DeviceType.WINDOW_SENSOR:   ((4, 10),  (0.01, 0.05), "commute"),
    DeviceType.MOTION_SENSOR:   ((10, 20), (0.01, 0.06), "evening"),
}
# 业务规则 3：不同时间模式的"一天内小时分布"权重（24 个小时）
HOUR_WEIGHTS = {
    # 均匀使用：全天都有，白天略高
    "all_day":          [2, 2, 2, 2, 2, 3, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 4, 4, 3, 3, 2],
    # 晚间双峰：早 6-8 点、晚 17-23 点集中
    "evening":          [1, 1, 1, 1, 1, 2, 6, 5, 3, 2, 2, 2, 2, 2, 2, 3, 5, 8, 9, 9, 8, 7, 5, 3],
    # 通勤双峰：早 6-8 点出门、晚 17-19 点归家
    "commute":          [0, 0, 0, 0, 0, 3, 9, 8, 4, 1, 1, 1, 1, 1, 1, 2, 5, 9, 8, 4, 2, 1, 1, 0],
    # 午后+夜间：空调制冷/制热时段
    "afternoon_night":  [2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4, 5, 6, 7, 7, 6, 5, 5, 5, 5, 6, 7, 7, 5],
}

TABLES_IN_DELETE_ORDER = [
    "device_usages", "security_events", "user_feedbacks",
    "device_co_occurrences", "devices", "users",
]


def clear_all(db):
    """按外键依赖顺序清空所有表 → 脚本可重复运行，不会累加"""
    for t in TABLES_IN_DELETE_ORDER:
        db.execute(text(f"DELETE FROM {t}"))
    db.commit()
    print("已清空旧数据")


def gen_users(db):
    users = []
    for i in range(1, N_USERS + 1):
        users.append(User(
            username=f"用户{i:03d}",
            email=f"user{i:03d}@example.com",
            house_size=round(rng.uniform(45, 180), 1),   # 房屋面积 45~180 ㎡
        ))
    db.add_all(users)
    db.commit()
    print(f"1/6 用户表       ：{len(users)} 条")
    return users


def n_devices_for(house_size):
    """业务规则 1：面积越大设备越多（每 15㎡ 一台为基准，允许 ±2 的随机例外）"""
    return max(3, min(14, round(house_size / 15) + rng.randint(-1, 2)))


def gen_devices(db, users):
    device_types = list(DeviceType)
    devices = []
    for u in users:
        for _ in range(n_devices_for(u.house_size)):
            d_type = rng.choice(device_types)   # 先抽类型，名称和 type 用同一个值，保证一致
            devices.append(Device(
                user_id=u.id,
                name=f"{rng.choice(['智能', '无线', '简约'])}{d_type.value}{rng.randint(1, 99):02d}",
                type=d_type,
                room=rng.choice(ROOMS),
            ))
    db.add_all(devices)
    db.commit()
    print(f"2/6 设备表       ：{len(devices)} 条")
    return devices


def sample_start_time(profile):
    """业务规则 3：按设备类型的时间模式采样"30 天内的某一天 + 优先小时" """
    day_offset = rng.uniform(0, 30)
    base = NOW - timedelta(days=day_offset)
    hour = rng.choices(range(24), weights=HOUR_WEIGHTS[profile], k=1)[0]
    return base.replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59))


def gen_usages(db, devices):
    usages = []
    for d in devices:
        (n_lo, n_hi), (h_lo, h_hi), profile = DEVICE_PROFILE[d.type]
        for _ in range(rng.randint(n_lo, n_hi)):
            start = sample_start_time(profile)
            hours = rng.uniform(h_lo, h_hi)
            end = start + timedelta(hours=hours)
            usages.append(DeviceUsage(
                device_id=d.id,
                start_time=start,
                end_time=end,
                # 能耗 = 功率 × 时长；duration 由数据库生成列自动计算，不用手动赋值
                energy_consumption=round(hours * POWER_KW[d.type], 4),
            ))
    db.add_all(usages)
    db.commit()
    print(f"3/6 使用记录表   ：{len(usages)} 条")
    return usages


def gen_cooccurrences_from_usages(db, devices_by_user):
    """业务规则 4：共现 = 同一用户、同一天内使用时间区间重叠的设备对。
    统计口径：同一设备对在同一天内无论重叠多少次，只记 1 次；
    occurrence_count = 发生过重叠的"天数"；跨午夜的使用记录同时归入
    起始日和结束日两天的分组，避免漏算。"""
    by_user_and_day = {}  # (user_id, 日期) -> [(start, end, device_id), ...]
    for uid, devs in devices_by_user.items():
        dev_ids = [d.id for d in devs]
        if not dev_ids:
            continue
        for u in db.query(DeviceUsage).filter(DeviceUsage.device_id.in_(dev_ids)).all():
            by_user_and_day.setdefault((uid, u.start_time.date()), []).append(
                (u.start_time, u.end_time, u.device_id)
            )
            if u.end_time.date() != u.start_time.date():   # 跨午夜：两天的分组都放一份
                by_user_and_day.setdefault((uid, u.end_time.date()), []).append(
                    (u.start_time, u.end_time, u.device_id)
                )

    days = {}   # (uid, d1, d2) -> 发生过重叠的天数
    last_end = {}  # (uid, d1, d2) -> 最后一次重叠的结束时间
    for (uid, _day), records in by_user_and_day.items():
        records.sort(key=lambda r: r[0])
        day_pairs = set()          # 当天发生过重叠的设备对（每天去重）
        day_max_end = None
        for i in range(len(records)):
            s1, e1, d1 = records[i]
            for j in range(i + 1, len(records)):
                s2, e2, d2 = records[j]
                if d1 == d2:
                    continue
                if s2 >= e1:            # 按 start 排序后，后面的不可能再与当前重叠
                    break
                lo, hi = sorted((d1, d2))
                day_pairs.add((lo, hi))
                me = max(e1, e2)
                if day_max_end is None or me > day_max_end:
                    day_max_end = me
        for pair in day_pairs:
            key = (uid, pair[0], pair[1])
            days[key] = days.get(key, 0) + 1
            if day_max_end is not None:
                last_end[key] = max(last_end.get(key, day_max_end), day_max_end)

    co = [
        DeviceCoOccurrence(
            user_id=uid, device1_id=d1, device2_id=d2,
            occurrence_count=cnt, last_occurred=last_end[key],
        )
        for (uid, d1, d2), cnt in days.items()
        for key in [(uid, d1, d2)]
    ]
    db.add_all(co)
    db.commit()
    print(f"4/6 设备共现表   ：{len(co)} 条（由使用记录重叠推导）")
    return co


def gen_security_events(db, devices):
    events = []
    for d in devices:
        if rng.random() < 0.3:                           # 30% 的设备有过安防事件
            for _ in range(rng.randint(1, 5)):
                events.append(SecurityEvent(
                    device_id=d.id,
                    event_type=rng.choice(EVENT_TYPES),
                    timestamp=NOW - timedelta(days=rng.uniform(0, 30)),
                    severity=rng.choice(SEVERITIES),
                    details=rng.choice(["无人时段触发", "夜间触发", "用户在家触发", None]),
                ))
    db.add_all(events)
    db.commit()
    print(f"5/6 安防事件表   ：{len(events)} 条")


def gen_feedbacks(db, users, devices_by_user):
    """反馈生成：默认模板生成（generated_by=template_v1）；
    配置了 LLM_API_KEY 环境变量时可切换 LLM 生成（generated_by=llm:模型名）。
    文本与设备类型、评分强一致，支持人工抽样检查。"""
    from scripts.feedback_generator import generate_feedback, generate_feedback_llm, sample_check

    use_llm = bool(os.environ.get("LLM_API_KEY"))
    feedbacks = []
    records = []                                   # 供抽样检查用
    for u in users:
        for _ in range(rng.randint(1, 3)):               # 每位用户 1~3 条反馈
            user_devices = devices_by_user.get(u.id, [])
            # 80% 针对自己家的设备，20% 是对系统整体反馈（device_id 为 NULL）
            target = rng.choice(user_devices) if user_devices and rng.random() < 0.8 else None
            rating = rng.randint(1, 5)
            if use_llm:
                llm = generate_feedback_llm(target.type.value if target else None, rating)
                text, generated_by = llm if llm else (generate_feedback(
                    target.type.value if target else None, rating, rng), "template_v1")
            else:
                text = generate_feedback(target.type.value if target else None, rating, rng)
                generated_by = "template_v1"
            feedbacks.append(UserFeedback(
                user_id=u.id,
                device_id=target.id if target else None,
                feedback_text=text,
                rating=rating,
                generated_by=generated_by,
            ))
            records.append({"user_id": u.id,
                            "device_type": target.type.value if target else None,
                            "rating": rating, "text": text, "generated_by": generated_by})
    db.add_all(feedbacks)
    db.commit()
    print(f"6/6 用户反馈表   ：{len(feedbacks)} 条（模板生成，文本与设备/评分一致）")
    os.makedirs(DATA_DIR, exist_ok=True)
    sample_check(records, out_dir=os.path.join(os.path.dirname(DATA_DIR), "outputs"))


def main():
    os.makedirs(DATA_DIR, exist_ok=True)                 # 数据库所在目录（项目根目录下的 data）
    Base.metadata.create_all(bind=engine)                # 表不存在则先建

    db = SessionLocal()
    try:
        clear_all(db)
        users = gen_users(db)
        devices = gen_devices(db, users)
        gen_usages(db, devices)
        gen_cooccurrences_from_usages(db, {u.id: [d for d in devices if d.user_id == u.id] for u in users})
        gen_security_events(db, devices)
        gen_feedbacks(db, users, {u.id: [d for d in devices if d.user_id == u.id] for u in users})

        # 汇总
        print("\n===== 数据库当前总量 =====")
        for t in TABLES_IN_DELETE_ORDER:
            n = db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            print(f"{t:24s}{n:>8d} 条")
    finally:
        db.close()


if __name__ == "__main__":
    main()
