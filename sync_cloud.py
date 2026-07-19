"""
云端同步脚本 - 将本地养护看板数据同步到 CloudStudio
Marvis 每次修改 data.json 后运行此脚本
"""
import os, json, shutil, subquote, sys

LOCAL_DIR = r"D:\workbuddy\养护看板"
SYNC_MARKER = os.path.join(LOCAL_DIR, ".sync_marker.json")

def do_sync():
    """执行同步到云端"""
    print("正在同步养护看板数据到云端...")
    
    # 这里由 WorkBuddy 部署工具接管，
    # Marvis 只需调用此脚本，WorkBuddy 会检测到同步请求
    # 实际同步逻辑由云端部署工具完成
    
    # 记录同步时间
    marker = {
        "last_sync": datetime.now().isoformat(),
        "data_version": "1.0",
        "task_count": 0
    }
    
    # 读取当前任务数
    data_path = os.path.join(LOCAL_DIR, "data.json")
    if os.path.exists(data_path):
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            marker["task_count"] = len(data.get("tasks", []))
    
    with open(SYNC_MARKER, 'w', encoding='utf-8') as f:
        json.dump(marker, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 同步标记已写入，等待 WorkBuddy 检测并部署")
    print(f"   任务数：{marker['task_count']}")
    print(f"   同步时间：{marker['last_sync']}")

if __name__ == "__main__":
    from datetime import datetime
    do_sync()
