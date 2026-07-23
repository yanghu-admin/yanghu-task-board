"""
飞书核验同步脚本 v1
扫描「核验提交」表中未同步的记录 → 按工单号匹配主表 → 更新核验状态
同时同步到本地 data.json
"""
import requests, json, os
from datetime import datetime

# 凭据统一从环境变量 / .env 读取（见 env_loader.py），不再硬编码
from env_loader import (
    FEISHU_APP_ID as APP_ID,
    FEISHU_APP_SECRET as APP_SECRET,
    FEISHU_APP_TOKEN as APP_TOKEN,
)
REVIEW_TABLE = "tblBh8mjI3BiDLmh"
MAIN_TABLE = "tbljdl99HjJrwiSs"
DATA_JSON = r"D:\workbuddy\养护看板\data.json"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": APP_ID, "app_secret": APP_SECRET})
TOKEN = r.json()["tenant_access_token"]
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

review_base = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{REVIEW_TABLE}"
main_base = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{MAIN_TABLE}"

# 1. 查核验表中未同步记录
r = requests.get(f"{review_base}/records?page_size=50", headers=headers)
review_records = r.json().get("data", {}).get("items", [])
log(f"核验提交表: {len(review_records)} 条")

unsynced = []
for rec in review_records:
    flds = rec.get("fields", {})
    if not flds.get("已同步", False) and flds.get("工单号", "").strip():
        unsynced.append(rec)

if not unsynced:
    log("无待同步核验记录")
    exit()

log(f"待同步核验: {len(unsynced)} 条")

# 2. 加载本地 data.json
with open(DATA_JSON, "r", encoding="utf-8") as f:
    data = json.load(f)

now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000000+08:00")
updated = 0

for rec in unsynced:
    flds = rec.get("fields", {})
    gdh = flds.get("工单号", "").strip()
    level = flds.get("核验级别", "")
    result = flds.get("核验结果", "")
    comment = flds.get("备注", "")
    photos = flds.get("验收照片", [])
    review_rid = rec["record_id"]

    log(f"  {gdh} | {level} | {result}")

    # 3a. 更新飞书主表
    r = requests.get(f"{main_base}/records?page_size=50", headers=headers)
    main_recs = r.json().get("data", {}).get("items", [])
    main_rid = None
    for mr in main_recs:
        if mr.get("fields", {}).get("工单号") == gdh:
            main_rid = mr["record_id"]
            break

    if not main_rid:
        log(f"  {gdh}: 主表无匹配，跳过")
        continue

    update = {}
    if level == "一级核验":
        if result == "通过":
            update["一级核验"] = "已通过"
            # 状态仍为待核验，等二级
        else:
            update["状态"] = "需整改"
            update["一级核验"] = "退回"
            if comment:
                update["问题描述"] = comment
    elif level == "二级核验":
        if result == "通过":
            update["二级核验"] = "已通过"
            update["状态"] = "已完成"
        else:
            update["状态"] = "需整改"
            update["二级核验"] = "退回"
            if comment:
                update["问题描述"] = comment

    r = requests.put(f"{main_base}/records/{main_rid}", headers=headers, json={"fields": update})
    code = r.json().get("code")
    log(f"  {gdh}: 主表更新 code={code}" + (" (失败)" if code != 0 else ""))

    # 3b. 更新本地 data.json
    for task in data.get("tasks", []):
        if task["id"] == gdh or task.get("number") == gdh:
            if not task.get("reviews"):
                task["reviews"] = {}
            if level == "一级核验":
                task["reviews"]["level1"] = {
                    "result": "approved" if result == "通过" else "rejected",
                    "comment": comment or "",
                    "images": photos if photos else []
                }
                if result == "不通过":
                    task["status"] = "rejected"
            elif level == "二级核验":
                task["reviews"]["level2"] = {
                    "result": "approved" if result == "通过" else "rejected",
                    "comment": comment or "",
                    "images": photos if photos else []
                }
                if result == "通过":
                    task["status"] = "completed"
                else:
                    task["status"] = "rejected"
            log(f"  {gdh}: data.json → 核验已更新")
            break

    # 3c. 标记已同步
    r = requests.put(f"{review_base}/records/{review_rid}", headers=headers,
        json={"fields": {"已同步": True}})
    updated += 1

# 4. 写回 data.json
data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000000+08:00")
stats = {"total": 0, "pending": 0, "processing": 0, "pending_review": 0, "completed": 0, "overdue": 0, "rejected": 0}
for t in data.get("tasks", []):
    stats["total"] += 1
    s = t.get("status", "pending")
    stats[s] = stats.get(s, 0) + 1
    if t.get("is_overdue"):
        stats["overdue"] += 1
data["statistics"] = stats

with open(DATA_JSON, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

log(f"核验同步完成: {updated}/{len(unsynced)}，data.json 已更新")
