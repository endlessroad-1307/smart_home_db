"""智能家居数据分析（analysis/analyze.py）

按教程要求实现 5 个分析，结果（CSV + PNG 图表）保存到项目根目录的 outputs/：
  1. 数据质量检查  ：各表记录数、外键完整性、异常时长/能耗、数据时间范围
  2. 能耗分析      ：按设备类型统计总能耗、平均单次能耗，区分
                     "设备多所以总量高" 与 "单台设备能耗高"，输出 Top10 高能耗设备
  3. 面积分组分析  ：按房屋面积区间比较户均设备数、户均能耗、日均使用时长
  4. 时段分析      ：按小时统计各类型设备使用次数（热力图），验证模拟时段规则
  5. 共现分析      ：高频设备对排行 + 设备共现网络图

⚠️ 说明：面积与使用模式的结果只能表述为"模拟规则生成的数据中观察到的关联"，
   不能当作真实用户行为结论；关联不等于因果。

运行方式（在项目根目录下）：
    python -m analysis.analyze
或 PyCharm 里右键 Run 本文件。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")                       # 无窗口环境也能出图；PyCharm 里同样适用
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import seaborn as sns
from sqlalchemy import func

from app.database import BASE_DIR, SessionLocal
from app.models import (
    Device,
    DeviceCoOccurrence,
    DeviceType,
    DeviceUsage,
    SecurityEvent,
    User,
)

# ---------- 输出目录与中文显示 ----------
OUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(OUT_DIR, exist_ok=True)

# 字体回退列表：Windows 命中微软雅黑，Linux/Mac 依次尝试后面的
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "PingFang SC",
    "Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei",
]
plt.rcParams["axes.unicode_minus"] = False   # 负号正常显示
sns.set_style("whitegrid", rc={
    "font.sans-serif": plt.rcParams["font.sans-serif"],
    "axes.unicode_minus": False,
})

db = SessionLocal()


def save_fig(fig, name):
    path = os.path.join(OUT_DIR, name)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"    图表已保存：outputs/{name}")


def save_csv(df, name):
    path = os.path.join(OUT_DIR, name)
    df.to_csv(path, index=False, encoding="utf-8-sig")   # utf-8-sig：Excel 打开不乱码
    print(f"    表格已保存：outputs/{name}")


# ============================================================
# 分析 1：数据质量检查
# ============================================================
def analyze_quality():
    print("\n===== 分析 1：数据质量检查 =====")
    report = {}

    # 1a. 各表记录数
    tables = {
        "users": User, "devices": Device, "device_usages": DeviceUsage,
        "security_events": SecurityEvent, "device_co_occurrences": DeviceCoOccurrence,
    }
    for name, model in tables.items():
        report[f"记录数_{name}"] = db.query(func.count(model.id)).scalar()

    # 1b. 外键完整性（孤儿记录应为 0）
    orphan_checks = {
        "孤儿设备(指向不存在用户)": (
            db.query(func.count(Device.id))
            .outerjoin(User, Device.user_id == User.id)
            .filter(User.id.is_(None)).scalar()),
        "孤儿使用记录(指向不存在设备)": (
            db.query(func.count(DeviceUsage.id))
            .outerjoin(Device, DeviceUsage.device_id == Device.id)
            .filter(Device.id.is_(None)).scalar()),
        "孤儿安防事件(指向不存在设备)": (
            db.query(func.count(SecurityEvent.id))
            .outerjoin(Device, SecurityEvent.device_id == Device.id)
            .filter(Device.id.is_(None)).scalar()),
    }

    # 1c. 异常值
    n_neg_duration = db.query(func.count(DeviceUsage.id)).filter(
        DeviceUsage.duration < 0).scalar()
    n_zero_duration = db.query(func.count(DeviceUsage.id)).filter(
        DeviceUsage.duration == 0).scalar()
    n_neg_energy = db.query(func.count(DeviceUsage.id)).filter(
        DeviceUsage.energy_consumption < 0).scalar()
    max_energy = db.query(func.max(DeviceUsage.energy_consumption)).scalar()

    # 1d. 数据时间范围
    t_min, t_max = db.query(func.min(DeviceUsage.start_time),
                            func.max(DeviceUsage.start_time)).one()

    for k, v in orphan_checks.items():
        report[k] = v
    report["负时长记录"] = n_neg_duration
    report["零时长记录"] = n_zero_duration
    report["负能耗记录"] = n_neg_energy
    report["最大单次能耗(千瓦时)"] = round(float(max_energy), 3)
    report["使用记录时间范围"] = f"{t_min} ~ {t_max}"

    df = pd.DataFrame({"检查项": list(report.keys()), "结果": list(report.values())})
    print(df.to_string(index=False))
    save_csv(df, "01_数据质量报告.csv")


# ============================================================
# 分析 2：能耗分析（教程第一项交付物："高能耗设备识别"）
# ============================================================
def analyze_energy():
    print("\n===== 分析 2：能耗分析 =====")

    # 2a. 按设备类型汇总：总能耗 / 使用次数 / 平均单次能耗 / 总时长 / 设备数 / 户均单台能耗
    rows = (
        db.query(
            Device.type,
            func.count(func.distinct(Device.id)).label("device_count"),
            func.count(DeviceUsage.id).label("usage_count"),
            func.sum(DeviceUsage.energy_consumption).label("total_energy"),
            func.avg(DeviceUsage.energy_consumption).label("avg_per_use"),
            func.sum(DeviceUsage.duration).label("total_seconds"),
        )
        .join(DeviceUsage, DeviceUsage.device_id == Device.id)
        .group_by(Device.type)
        .all()
    )
    df = pd.DataFrame([{
        "设备类型": r[0].value,
        "设备数": r.device_count,
        "使用次数": r.usage_count,
        "总能耗(千瓦时)": round(float(r.total_energy), 2),
        "平均单次能耗(千瓦时)": round(float(r.avg_per_use), 4),
        "累计时长(小时)": round(float(r.total_seconds) / 3600, 1),
        # 单台平均能耗 = 总能耗 / 设备数：区分"设备多所以总量高"和"单台能耗高"
        "单台平均能耗(千瓦时)": round(float(r.total_energy) / r.device_count, 2),
    } for r in rows]).sort_values("总能耗(千瓦时)", ascending=False)

    total = df["总能耗(千瓦时)"].sum()
    df["能耗占比"] = (df["总能耗(千瓦时)"] / total * 100).round(1).astype(str) + "%"
    print(df.to_string(index=False))
    save_csv(df, "02_设备类型能耗汇总.csv")

    top_type = df.iloc[0]
    print(f"    → 总量冠军：{top_type['设备类型']}（{top_type['总能耗(千瓦时)']} 千瓦时，"
          f"占 {top_type['能耗占比']}）；单台冠军："
          f"{df.sort_values('单台平均能耗(千瓦时)', ascending=False).iloc[0]['设备类型']}")

    # 2b. Top10 高能耗单台设备
    dev_rows = (
        db.query(
            Device.id, Device.name, Device.type, Device.room,
            func.count(DeviceUsage.id).label("usage_count"),
            func.sum(DeviceUsage.energy_consumption).label("total_energy"),
            func.sum(DeviceUsage.duration).label("total_seconds"),
        )
        .join(DeviceUsage, DeviceUsage.device_id == Device.id)
        .group_by(Device.id)
        .order_by(func.sum(DeviceUsage.energy_consumption).desc())
        .limit(10)
        .all()
    )
    df_dev = pd.DataFrame([{
        "设备ID": r.id, "设备名": r.name, "类型": r.type.value, "房间": r.room,
        "使用次数": r.usage_count,
        "总能耗(千瓦时)": round(float(r.total_energy), 2),
        "累计时长(小时)": round(float(r.total_seconds) / 3600, 1),
    } for r in dev_rows])
    print("\nTop10 高能耗设备：")
    print(df_dev.to_string(index=False))
    save_csv(df_dev, "02b_Top10高能耗设备.csv")

    # 2c. 图：类型总能耗（左）+ Top10 设备（右）
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    palette = sns.color_palette("Reds_r", len(df))
    sns.barplot(data=df, x="总能耗(千瓦时)", y="设备类型", ax=axes[0],
                hue="设备类型", palette=palette, legend=False)
    axes[0].set_title("各设备类型总能耗（模拟数据）")

    palette2 = sns.color_palette("Oranges_r", len(df_dev))
    sns.barplot(data=df_dev, x="总能耗(千瓦时)", y="设备名", ax=axes[1],
                hue="设备名", palette=palette2, legend=False)
    axes[1].set_title("Top 10 高能耗设备")
    save_fig(fig, "02_能耗分析.png")


# ============================================================
# 分析 3：面积分组分析（关联分析，非因果）
# ============================================================
def analyze_house_size():
    print("\n===== 分析 3：面积分组分析（模拟数据的关联分析） =====")
    bins = [0, 80, 120, 999]
    labels = ["小户型(<80㎡)", "中户型(80-120㎡)", "大户型(>120㎡)"]

    rows = (
        db.query(User.id, User.house_size,
                 func.count(func.distinct(Device.id)).label("n_dev"),
                 func.coalesce(func.sum(DeviceUsage.energy_consumption), 0).label("energy"),
                 func.coalesce(func.sum(DeviceUsage.duration), 0).label("seconds"))
        .select_from(User)
        .outerjoin(Device, Device.user_id == User.id)
        .outerjoin(DeviceUsage, DeviceUsage.device_id == Device.id)
        .group_by(User.id)
        .all()
    )
    df = pd.DataFrame([{
        "用户ID": r.id, "面积": float(r.house_size), "设备数": r.n_dev,
        "总能耗": float(r.energy), "总时长(小时)": float(r.seconds) / 3600,
    } for r in rows])
    df["面积区间"] = pd.cut(df["面积"], bins=bins, labels=labels)

    grouped = df.groupby("面积区间", observed=True).agg(
        户数=("用户ID", "count"),
        户均设备数=("设备数", "mean"),
        户均总能耗_千瓦时=("总能耗", "mean"),
        户均日均使用时长_小时=("总时长(小时)", lambda s: s.mean() / 30),  # 30 天窗口
    ).round(2).reset_index()
    print(grouped.to_string(index=False))

    r = np.corrcoef(df["面积"], df["设备数"])[0, 1]
    print(f"    → 面积 vs 设备数 相关系数 r = {r:.3f}（模拟规则：每 15㎡ 约 1 台）")
    save_csv(grouped, "03_面积分组分析.csv")

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    palette = sns.color_palette("Blues", 3)
    for ax, col, title in zip(
        axes,
        ["户均设备数", "户均总能耗_千瓦时", "户均日均使用时长_小时"],
        ["户均设备数", "户均总能耗(千瓦时)", "户均日均使用时长(小时)"],
    ):
        sns.barplot(data=grouped, x="面积区间", y=col, ax=ax,
                    hue="面积区间", palette=palette, legend=False)
        ax.set_title(title)
        ax.tick_params(axis="x", rotation=15)
    fig.suptitle("房屋面积与设备使用行为（模拟数据关联分析，不构成因果结论）", y=1.02)
    save_fig(fig, "03_面积分组分析.png")


# ============================================================
# 分析 4：时段分析（验证模拟时段规则确实体现在数据里）
# ============================================================
def analyze_hourly():
    print("\n===== 分析 4：时段分析 =====")
    rows = (
        db.query(Device.type,
                 func.strftime("%H", DeviceUsage.start_time).label("hour"),
                 func.count(DeviceUsage.id).label("cnt"))
        .join(DeviceUsage, DeviceUsage.device_id == Device.id)
        .group_by(Device.type, "hour")
        .all()
    )
    df = pd.DataFrame([{
        "设备类型": r[0].value, "小时": int(r.hour), "使用次数": r.cnt,
    } for r in rows])
    pivot = df.pivot_table(index="设备类型", columns="小时",
                           values="使用次数", fill_value=0)
    pivot = pivot.reindex(columns=range(24), fill_value=0)

    # 抽查两条规则是否"可被发现"
    light = pivot.loc["Light"]
    print(f"    Light 晚间(17-23点)占比 = {light[17:24].sum() / light.sum():.0%}（模拟规则：晚间双峰）")
    lock = pivot.loc["Door_lock"]
    peak = lock[6:9].sum() + lock[17:20].sum()
    print(f"    Door_lock 通勤时段(6-9,17-20)占比 = {peak / lock.sum():.0%}（模拟规则：早晚通勤峰）")
    save_csv(pivot.reset_index(), "04_时段分布.csv")

    fig, ax = plt.subplots(figsize=(13, 5))
    sns.heatmap(pivot, cmap="YlOrRd", ax=ax,
                cbar_kws={"label": "使用次数"})
    ax.set_title("各类型设备 24 小时使用分布（模拟数据，0-23 点）")
    ax.set_xlabel("小时")
    ax.set_ylabel("设备类型")
    save_fig(fig, "04_时段热力图.png")


# ============================================================
# 分析 5：共现分析（高频设备对 + 网络图）
# ============================================================
def analyze_cooccurrence():
    print("\n===== 分析 5：共现分析 =====")
    d1 = Device.__table__.alias("d1")
    d2 = Device.__table__.alias("d2")
    rows = (
        db.query(
            DeviceCoOccurrence.occurrence_count,
            d1.c.name, d1.c.type,
            d2.c.name, d2.c.type,
        )
        .join(d1, DeviceCoOccurrence.device1_id == d1.c.id)
        .join(d2, DeviceCoOccurrence.device2_id == d2.c.id)
        .order_by(DeviceCoOccurrence.occurrence_count.desc())
        .limit(15)
        .all()
    )
    df = pd.DataFrame([{
        "设备1": f"{r[1]}({r[2].value})", "设备2": f"{r[3]}({r[4].value})",
        "共同出现天数": r[0],
    } for r in rows])
    print("高频设备对 Top15：")
    print(df.to_string(index=False))
    save_csv(df, "05_高频设备对Top15.csv")

    # 网络图：取 Top50 共现对
    pairs = (
        db.query(DeviceCoOccurrence.device1_id, DeviceCoOccurrence.device2_id,
                 DeviceCoOccurrence.occurrence_count)
        .order_by(DeviceCoOccurrence.occurrence_count.desc())
        .limit(50)
        .all()
    )
    types = {d.id: d.type.value for d in db.query(Device).all()}
    G = nx.Graph()
    for a, b, w in pairs:
        G.add_edge(a, b, weight=w)

    fig, ax = plt.subplots(figsize=(11, 9))
    pos = nx.spring_layout(G, seed=42, k=0.5)
    node_types = [types.get(n, "Unknown") for n in G.nodes]
    unique_types = sorted(set(node_types))
    cmap = {t: sns.color_palette("tab10", len(unique_types))[i]
            for i, t in enumerate(unique_types)}
    weights = [G[u][v]["weight"] for u, v in G.edges]
    nx.draw_networkx_edges(G, pos, ax=ax, alpha=0.3,
                           width=[w / max(weights) * 4 + 0.3 for w in weights])
    nx.draw_networkx_nodes(G, pos, ax=ax, node_size=120,
                           node_color=[cmap[t] for t in node_types])
    # 图例（按设备类型）
    for t, c in cmap.items():
        ax.scatter([], [], c=[c], label=t, s=60)
    ax.legend(loc="upper right", fontsize=9, title="设备类型")
    ax.set_title("设备共现网络 Top50（节点=设备，边粗细=共同出现天数，模拟数据）")
    ax.axis("off")
    save_fig(fig, "05_共现网络图.png")

    # ---- 5b. 设备类型级共现网络（教程要求：节点代表设备或设备类型）----
    rows_t = (
        db.query(d1.c.type, d2.c.type, DeviceCoOccurrence.occurrence_count)
        .join(d1, DeviceCoOccurrence.device1_id == d1.c.id)
        .join(d2, DeviceCoOccurrence.device2_id == d2.c.id)
        .all()
    )
    agg = {}  # (type1, type2) -> 总共现天数
    for t1, t2, w in rows_t:
        key = tuple(sorted((t1.value, t2.value)))
        agg[key] = agg.get(key, 0) + w

    GT = nx.Graph()
    for (t1, t2), w in agg.items():
        GT.add_edge(t1, t2, weight=w)
    # 节点大小 = 该类型参与的所有共现总次数
    size = {n: sum(d["weight"] for _, _, d in GT.edges(n, data=True)) for n in GT.nodes}

    fig, ax = plt.subplots(figsize=(10, 8))
    pos = nx.spring_layout(GT, seed=42, k=1.2)
    ws = [GT[u][v]["weight"] for u, v in GT.edges]
    nx.draw_networkx_edges(GT, pos, ax=ax, alpha=0.35,
                           width=[w / max(ws) * 10 + 0.5 for w in ws])
    nx.draw_networkx_nodes(GT, pos, ax=ax,
                           node_size=[size[n] / max(size.values()) * 3000 + 200
                                      for n in GT.nodes],
                           node_color=sns.color_palette("Set2", GT.number_of_nodes()))
    nx.draw_networkx_labels(GT, pos, ax=ax, font_size=10,
                            font_family=plt.rcParams["font.sans-serif"][0])
    ax.set_title("设备类型级共现网络（节点大小=共现总量，边粗细=共现天数，模拟数据）")
    ax.axis("off")
    save_fig(fig, "05b_类型共现网络.png")

    # ---- 5c. 可读结论（教程要求"把高频组合写成几条可读结论"）----
    rank = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
    total_all = sum(agg.values())
    lines = ["设备类型共现分析结论（模拟数据，共现=同一天使用时间重叠）：", ""]
    for i, ((t1, t2), w) in enumerate(rank[:5], 1):
        lines.append(f"{i}. {t1} 与 {t2} 最常一起使用，累计共同出现 {w} 天，"
                     f"占全部共现的 {w / total_all:.1%}")
    top = rank[0]
    lines += [
        "",
        f"总体看：「{top[0][0]} + {top[0][1]}」是最典型的组合，符合晚间回家开灯、"
        f"用插座的场景；空调类因运行时间长，与灯光的共现也排在前列。",
        "注：以上为模拟生成数据中的关联，不代表真实用户行为。",
    ]
    with open(os.path.join(OUT_DIR, "05c_共现分析结论.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("    结论已保存：outputs/05c_共现分析结论.txt")


if __name__ == "__main__":
    analyze_quality()
    analyze_energy()
    analyze_house_size()
    analyze_hourly()
    analyze_cooccurrence()
    db.close()
    print("\n全部分析完成，结果见 outputs/ 目录")
