"""用户反馈生成器（scripts/feedback_generator.py）

教程要求的两层设计：
  1. 模板生成器（generate_feedback）：不依赖任何外部 API，文本与设备类型、
     评分、场景强一致——评分高选正面模板、低选负面模板，特征词从设备类型的
     特征库抽取，保证"文本与设备一致"，可重复（受随机种子控制）。
  2. LLM 生成入口（generate_feedback_llm）：只有配置了 API Key 才会真正调用
     （OpenAI 兼容接口：DEEPSEEK_API_KEY / KIMI_API_KEY / OPENAI_API_KEY 均可），
     未配置时返回 None，调用方应回退到模板生成——绝不把模板产物冒充 LLM 生成。

每条反馈都会带 generated_by 字段（template_v1 或 llm:模型名），写入
user_feedbacks 表，方便追溯数据来源。
"""
import os
import random

# 每种设备类型的"特征库"：反馈文本只围绕这些特征展开，保证与设备一致
TYPE_FEATURES = {
    "Light":            ["亮度调节", "定时开关", "色温切换", "语音控制"],
    "Thermostat":       ["温度控制精度", "升温速度", "app 远程调温", "节能模式"],
    "Security_camera":  ["夜视清晰度", "移动侦测灵敏度", "画面传输流畅度", "云台转动"],
    "Door_lock":        ["指纹识别速度", "临时密码功能", "开锁反应", "门铃联动"],
    "Smart_plug":       ["电量统计", "定时断电", "远程开关", "插拔手感"],
    "Air_conditioner":  ["制冷效果", "噪音水平", "风向调节", "能耗表现"],
    "Window_sensor":    ["门窗感应灵敏度", "掉线情况", "报警及时性", "安装牢固度"],
    "Motion_sensor":    ["人体感应灵敏度", "误报频率", "覆盖范围", "响应速度"],
}

# 通用系统级反馈（device_id 为 NULL 时使用）
SYSTEM_POSITIVE = ["整体联动很流畅", "app 界面清爽好用", "设备添加流程简单", "推送及时不吵"]
SYSTEM_NEGATIVE = ["app 偶尔闪退", "设备偶尔离线要重连", "固件升级后设置被重置"]
SYSTEM_NEUTRAL = ["希望增加场景自动化", "希望能支持更多设备品牌", "希望增加能耗周报"]

POS_TAILS = ["，很满意", "，超出预期", "，值得推荐", "，家人都说好用"]
NEG_TAILS = ["，有点失望", "，需要改进", "，不太理想", "，希望优化"]
NEU_TAILS = ["，中规中矩", "，一般般", "，还有提升空间", "，凑合能用"]


def generate_feedback(device_type: str | None, rating: int, rng: random.Random) -> str:
    """模板生成一条反馈文本。

    device_type：设备类型英文名（None 表示系统级反馈）
    rating     ：1~5 分，决定正面/中性/负面语气
    rng        ：random.Random 实例（受种子控制 → 可复现）
    """
    if rating >= 4:
        mood, tails = "pos", POS_TAILS
    elif rating <= 2:
        mood, tails = "neg", NEG_TAILS
    else:
        mood, tails = "neu", NEU_TAILS

    if device_type is None:                       # 系统级反馈
        pool = {"pos": SYSTEM_POSITIVE, "neg": SYSTEM_NEGATIVE, "neu": SYSTEM_NEUTRAL}[mood]
        return rng.choice(pool) + rng.choice(tails)

    feature = rng.choice(TYPE_FEATURES.get(device_type, ["整体体验"]))
    phrase = {
        "pos": f"{feature}表现不错",
        "neg": f"{feature}不太行",
        "neu": f"{feature}尚可",
    }[mood]
    return phrase + rng.choice(tails)


def generate_feedback_llm(device_type: str | None, rating: int) -> tuple[str, str] | None:
    """LLM 生成入口（可选，教程第 2 步）。

    返回 (文本, 生成方式标记)；未配置 API Key 时返回 None，调用方回退模板。
    兼容 DeepSeek / Kimi(Moonshot) / OpenAI 等.OpenAI 兼容接口。
    """
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("KIMI_API_KEY")
               or os.environ.get("OPENAI_API_KEY"))
    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    if not api_key:
        return None

    import json as _json

    import requests

    scene = f"设备类型：{device_type}" if device_type else "智能家居系统整体"
    prompt = (
        f"为一条智能家居用户反馈写一句话评语（20字以内，中文，口语化）。"
        f"{scene}；评分 {rating}/5 分（{'正面' if rating >= 4 else '负面' if rating <= 2 else '中性'}语气）。"
        f"只输出评语本身。"
    )
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 1.2, "max_tokens": 50},
            timeout=15,
        )
        resp.raise_for_status()
        text = _json.loads(resp.json()["choices"][0]["message"]["content"]).strip()
        return text, f"llm:{model}"
    except Exception as e:                        # 网络/配额等任何失败 → 回退模板
        print(f"    LLM 生成失败（{e}），该条回退模板生成")
        return None


def sample_check(rows: list[dict], n: int = 10, out_dir: str = "outputs") -> None:
    """教程第 4 步：抽样检查——随机抽 n 条人工核对，保存检查记录 CSV。"""
    import csv

    rng = random.Random(7)
    picked = rng.sample(rows, min(n, len(rows)))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "06_反馈抽样人工检查记录.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["用户ID", "设备类型", "评分", "反馈文本", "生成方式", "检查结论(人工填写)"])
        for r in picked:
            w.writerow([r["user_id"], r["device_type"] or "系统级",
                        r["rating"], r["text"], r["generated_by"], ""])
    print(f"    抽样 {len(picked)} 条已保存：outputs/06_反馈抽样人工检查记录.csv（人工核对'检查结论'列）")
