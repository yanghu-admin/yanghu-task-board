"""
养护看板凭据加载器
优先从环境变量读取，其次从同级目录 .env 文件读取
用法: from env_loader import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_APP_TOKEN
"""
import os
import sys

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

def _load_dotenv():
    """加载 .env 文件到 os.environ（不覆盖已有环境变量）"""
    if not os.path.exists(_ENV_PATH):
        return
    with open(_ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

_load_dotenv()

FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_APP_TOKEN  = os.environ.get("FEISHU_APP_TOKEN", "")
WEWORK_BOT_KEY    = os.environ.get("WEWORK_BOT_KEY", "")
GITHUB_PAT        = os.environ.get("GITHUB_PAT", "")
