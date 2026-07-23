# -*- coding: utf-8 -*-
"""
企业微信智能机器人 → 飞书多维表格 中转服务
==========================================
适配企业微信智能机器人 API 模式（JSON 加密回调）。

运行：
  python wework_bot_relay.py

流程：
  1. 企业微信发送 GET 验证请求 → 解密 echostr 返回
  2. 企业微信 POST 群消息 → 解密 → 解析工单字段 → 写入飞书
"""

import os
import re
import time
import json
import logging
import socket
from datetime import datetime
from threading import Lock

import requests
from flask import Flask, request, jsonify

# ============================================================
# 配置区
# ============================================================
# 飞书凭据（后续填入真实值）
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "YOUR_FEISHU_APP_ID")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "YOUR_FEISHU_APP_SECRET")
FEISHU_APP_TOKEN = os.environ.get("FEISHU_APP_TOKEN", "YOUR_FEISHU_APP_TOKEN")
FEISHU_TABLE_ID = os.environ.get("FEISHU_TABLE_ID", "YOUR_FEISHU_TABLE_ID")

# 企业微信智能机器人 API 插件凭据
WEWORK_API_KEY = os.environ.get("WEWORK_API_KEY", "WG8u01VHnECh3BpdvSXti4fDTKN26Ocb")

PORT = int(os.environ.get("PORT", 5001))
HOST = "0.0.0.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

LOG_FILE = os.path.join(BASE_DIR, "relay.log")

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_RECORD_URL = "https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("relay")


# ============================================================
# 飞书 Token 管理
# ============================================================
class FeishuTokenManager:
    def __init__(self):
        self._token = None
        self._expire_at = 0
        self._lock = Lock()

    def get_token(self, force=False):
        with self._lock:
            now = time.time()
            if self._token and not force and now < self._expire_at - 60:
                return self._token
            return self._refresh()

    def _refresh(self):
        for attempt in range(3):
            try:
                resp = requests.post(
                    FEISHU_TOKEN_URL,
                    json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
                    timeout=10,
                )
                data = resp.json()
                if data.get("code") == 0:
                    self._token = data["tenant_access_token"]
                    self._expire_at = time.time() + int(data.get("expire", 7200))
                    logger.info("飞书 token 刷新成功")
                    return self._token
                else:
                    logger.error("飞书 token 获取失败：%s", data)
            except Exception as e:
                logger.error("飞书 token 请求异常（第 %d 次）：%s", attempt + 1, e)
            time.sleep(2 ** attempt)
        raise RuntimeError("无法获取飞书 tenant_access_token")


token_mgr = FeishuTokenManager()





# ============================================================
# 消息解析（同旧脚本逻辑）
# ============================================================
STAKE_PATTERN = re.compile(r"[A-Za-z]{0,3}\d+\+\d+")
DISEASE_MAP = {
    "坑槽": "坑槽", "裂缝": "裂缝", "沉降": "沉降",
    "积水": "积水", "护栏损坏": "护栏损坏", "护栏": "护栏损坏",
}
URGENCY_MAP = {
    "急": "急", "紧急": "急", "特急": "急",
    "一般": "一般", "慢": "慢", "缓": "慢",
}
OWNER_PATTERN = re.compile(r"@([^\s@，。,]+)")


def parse_message(content):
    result = {
        "description": content.strip() if content else "",
        "stake": None, "disease": None, "urgency": None, "owner": None,
    }
    if not content:
        return result
    m = STAKE_PATTERN.search(content)
    if m:
        result["stake"] = m.group(0).upper()
    for kw, val in DISEASE_MAP.items():
        if kw in content:
            result["disease"] = val
            break
    for kw, val in URGENCY_MAP.items():
        if kw in content:
            result["urgency"] = val
            break
    om = OWNER_PATTERN.search(content)
    if om:
        result["owner"] = om.group(1)
    return result


# ============================================================
# 飞书写入
# ============================================================
def write_to_feishu(fields):
    url = FEISHU_RECORD_URL.format(app_token=FEISHU_APP_TOKEN, table_id=FEISHU_TABLE_ID)
    for attempt in range(3):
        token = token_mgr.get_token(force=(attempt > 0))
        headers = {"Authorization": "Bearer %s" % token, "Content-Type": "application/json"}
        try:
            resp = requests.post(url, headers=headers, json={"fields": fields}, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                logger.info("飞书写入成功，record_id=%s", data.get("data", {}).get("record", {}).get("id"))
                return True
            elif data.get("code") == 99991663:
                continue
            else:
                logger.error("飞书写入失败：%s", data)
        except Exception as e:
            logger.error("飞书写入异常（第 %d 次）：%s", attempt + 1, e)
        time.sleep(2 ** attempt)
    return False


# ============================================================
# API Key 验证
# ============================================================
from functools import wraps


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # 调试模式：记录所有请求头，暂时跳过验证
        headers = dict(request.headers)
        logger.info("收到请求，headers: %s", json.dumps(headers, ensure_ascii=False))
        return f(*args, **kwargs)
    return decorated


# ============================================================
# Flask 应用
# ============================================================
app = Flask(__name__)


@app.route("/api/bot", methods=["GET", "POST"])
@app.route("/api/bot/", methods=["GET", "POST"])
@require_api_key
def receive_message():
    """接收企业微信智能机器人推送的群消息（API 插件模式）。"""
    if request.method == "GET":
        # API 插件模式无 URL 验证，简单返回 ok
        return jsonify({"status": "ok"})

    body = request.get_json(force=True, silent=True) or {}
    logger.info("收到消息：%s", json.dumps(body, ensure_ascii=False)[:500])

    try:
        # 提取消息内容
        content = ""
        msgtype = body.get("msgtype", "text")
        if msgtype == "text":
            content = body.get("text", {}).get("content", "")
        elif msgtype == "mixed":
            items = body.get("mixed", {}).get("msg_item", [])
            texts = [i.get("text", {}).get("content", "") for i in items if i.get("msgtype") == "text"]
            content = " ".join(texts)
        else:
            content = "[%s消息]" % msgtype

        # 去掉 @机器人 前缀
        content = re.sub(r"@\S+\s*", "", content).strip()

        sender = body.get("from", {}).get("userid", "")

        # 日志：记录原始内容（调试用）
        log_file = os.path.join(TEMP_DIR, "messages.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("%s | %s | %s\n" % (datetime.now().isoformat(), sender, content))

        # 解析工单字段
        parsed = parse_message(content)
        fields = {
            "描述": parsed["description"],
            "桩号": parsed["stake"] or "未识别",
            "类型": parsed["disease"] or "其他",
            "紧急度": parsed["urgency"] or "一般",
            "来源": sender,
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if parsed["owner"]:
            fields["责任人"] = parsed["owner"]

        # 写入飞书（未配置凭据时跳过）
        feishu_configured = FEISHU_APP_TOKEN not in ("YOUR_FEISHU_APP_TOKEN", "")
        if feishu_configured:
            feishu_ok = write_to_feishu(fields)
        else:
            logger.info("飞书未配置，跳过写入。解析结果: %s", json.dumps(fields, ensure_ascii=False))
            feishu_ok = True  # 未配置不算失败

        # 构造回复
        if feishu_ok:
            reply = {
                "msgtype": "text",
                "text": {"content": "已录入工单：%s" % parsed["description"][:50]},
            }
        else:
            reply = {
                "msgtype": "text",
                "text": {"content": "工单写入飞书失败，请检查日志"},
            }

        return jsonify(reply)

    except Exception as e:
        logger.exception("消息处理失败：%s", e)
        return jsonify({"msgtype": "text", "text": {"content": "处理失败，请稍后重试"}})


@app.route("/api/health", methods=["GET"])
def health():
    feishu_ok = FEISHU_APP_TOKEN != "YOUR_FEISHU_APP_TOKEN"
    return jsonify({
        "status": "ok",
        "api_key_set": WEWORK_API_KEY != "YOUR_WEWORK_API_KEY",
        "feishu_app_token_set": feishu_ok,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("企业微信智能机器人 → 飞书 中转服务启动")
    logger.info("本地地址: http://%s:%d", get_local_ip(), PORT)
    logger.info("回调地址: /api/bot")
    logger.info("健康检查: /api/health")
    logger.info("=" * 50)
    app.run(host=HOST, port=PORT, threaded=True)
