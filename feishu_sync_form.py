"""
飞书表单同步脚本 v3
扫描「经办人提交」表中未同步的记录 → 按工单号匹配主表 → 更新
规则：
  - 无照片提交(维修中+维修后均为空) = 受理确认 → 受理时间 + 状态→处理中
  - 有照片提交 = 处置完成 → 照片 + 状态→待核验
同时同步到本地 data.json，确保看板 index.html 实时反映
"""
import requests, json, os
from datetime import datetime

# 凭据统一从环境变量 / .env 读取（见 env_loader.py），不再硬编码
from env_loader import (
    FEISHU_APP_ID as APP_ID,
    FEISHU_APP_SECRET as APP_SECRET,
    FEISHU_APP_TOKEN as APP_TOKEN,
)
FORM_TABLE = "tblI0tKOoMy4fyX6"
MAIN_TABLE = "tbljdl99HjJrwiSs"
DATA_JSON = r"D:\workbuddy\养护看板\data.json"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": APP_ID, "app_secret": APP_SECRET})
TOKEN = r.json()["tenant_access_token"]
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

form_base = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{FORM_TABLE}"
main_base = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{MAIN_TABLE}"

# 1. 查未同步记录
r = requests.get(f"{form_base}/records?page_size=50", headers=headers)
form_records = r.json().get("data", {}).get("items", [])
log(f"经办人提交表: {len(form_records)} 条")

unsynced = []
for rec in form_records:
    flds = rec.get("fields", {})
    if not flds.get("已同步", False) and flds.get("工单号(自动)", "").strip():
        unsynced.append(rec)

if not unsynced:
    log("无待同步记录")
    exit()

log(f"待同步: {len(unsynced)} 条")

# 2. 加载本地 data.json
with open(DATA_JSON, "r", encoding="utf-8") as f:
    data = json.load(f)

now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000000+08:00")
updated = 0

for rec in unsynced:
    flds = rec.get("fields", {})
    gdh = flds.get("工单号(自动)", "").strip()
    photos_before = flds.get("维修前照片", [])
    photos_mid = flds.get("维修中照片", [])
    photos_after = flds.get("维修后照片", [])
    notes = flds.get("备注", "")
    form_rid = rec["record_id"]

    # 合并照片（三张照片模式: 维修前/维修中/维修后）
    has_before = bool(photos_before)
    has_mid = bool(photos_mid)
    has_after = bool(photos_after)

    if not has_before and not has_mid and not has_after:
        log(f"  {gdh}: 无照片，跳过")
        continue

    all_photos = []
    if photos_before:
        all_photos.extend(photos_before if isinstance(photos_before, list) else [photos_before])
    if photos_mid:
        all_photos.extend(photos_mid if isinstance(photos_mid, list) else [photos_mid])
    if photos_after:
        all_photos.extend(photos_after if isinstance(photos_after, list) else [photos_after])

    # 3a. 更新飞书主表
    r = requests.get(f"{main_base}/records?page_size=50", headers=headers)
    main_recs = r.json().get("data", {}).get("items", [])
    main_rid = None
    for mr in main_recs:
        if mr.get("fields", {}).get("工单号") == gdh:
            main_rid = mr["record_id"]
            break

    if main_rid:
        ms = int(datetime.now().timestamp() * 1000)
        update = {}

        if has_mid:
            # 维修中照片 → 受理时间 = 此时
            update["状态"] = "处理中" if not has_after else "待核验"
            update["受理时间"] = ms
            if has_after:
                update["照片"] = all_photos
            log(f"  {gdh}: 维修中 → 处理中" + ("（维修后已传→待核验）" if has_after else ""))
        elif has_after:
            # 仅维修后照片（补充提交）
            update["状态"] = "待核验"
            update["照片"] = all_photos
            log(f"  {gdh}: 维修后 → 待核验")

        if notes:
            update["问题描述"] = notes

        r = requests.put(f"{main_base}/records/{main_rid}", headers=headers, json={"fields": update})
        code = r.json().get("code")
        if code != 0:
            log(f"  {gdh}: 主表更新失败 code={code}")
    else:
        log(f"  {gdh}: 主表无匹配，跳过")

    # 3b. 更新本地 data.json
    for task in data.get("tasks", []):
        if task["id"] == gdh or task.get("number") == gdh:
            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000000+08:00")
            if has_mid:
                task["accepted_at"] = now
                if has_after:
                    task["status"] = "pending_review"
                    task["images"]["after"] = all_photos
                    task["completed_at"] = now
                else:
                    task["status"] = "processing"
            elif has_after:
                task["status"] = "pending_review"
                task["images"]["after"] = all_photos
                task["completed_at"] = now
            # 维修前照片
            if has_before:
                task["images"]["before"] = (photos_before if isinstance(photos_before, list) else [photos_before])
            if notes:
                task["notes"] = notes
            log(f"  {gdh}: data.json → {task['status']}")
            break

    # 3c. 标记已同步
    r = requests.put(f"{form_base}/records/{form_rid}", headers=headers,
        json={"fields": {"已同步": True}})
    updated += 1

# 4. 写回 data.json
data["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000000+08:00")
stats = {"total": 0, "pending": 0, "processing": 0, "pending_review": 0, "completed": 0, "overdue": 0}
for t in data.get("tasks", []):
    stats["total"] += 1
    s = t.get("status", "pending")
    stats[s] = stats.get(s, 0) + 1
    if t.get("is_overdue"):
        stats["overdue"] += 1
data["statistics"] = stats

with open(DATA_JSON, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

log(f"同步完成: {updated}/{len(unsynced)}，data.json 已更新")
