# -*- coding: utf-8 -*-
"""
企业微信群机器人 / 应用回调 → 飞书多维表格 中转服务
==================================================
功能：
  1. 接收企业微信回调（支持加密 / 明文兼容）
  2. 解析消息中的桩号 / 病害类型 / 紧急度 / 责任人 / 图片
  3. 自动刷新飞书 tenant_access_token（含重试）
  4. 写入飞书多维表格
  5. 提供 /api/health 与 /api/test 端点

运行：
  pip install flask requests pycryptodome
  python wework_to_feishu_relay.py

注意：
  - 企业微信要求回调在 5 秒内返回 "success"，否则会重试。
  - 本脚本同步写入飞书（通常 <1s）；若网络慢可改为线程异步。
"""

import os
import re
import time
import json
import logging
import random
import string
import hashlib
import base64
import socket
from datetime import datetime
from threading import Lock

import requests
from flask import Flask, request, jsonify, Response
from Crypto.Cipher import AES

# ============================================================
# 配置区（请替换为真实值，YOUR_XXX 为占位符）
# ============================================================
FEISHU_APP_ID = "YOUR_FEISHU_APP_ID"
FEISHU_APP_SECRET = "YOUR_FEISHU_APP_SECRET"
FEISHU_APP_TOKEN = "YOUR_FEISHU_APP_TOKEN"      # 多维表格 app_token，形如 bascnxxxx
FEISHU_TABLE_ID = "YOUR_FEISHU_TABLE_ID"        # 数据表 table_id，形如 tblxxxx

# 企业微信回调配置（接收消息需配置；仅用群机器人主动推送可留空）
WEWORK_TOKEN = "YOUR_WEWORK_TOKEN"              # 回调 Token
WEWORK_AES_KEY = "YOUR_WEWORK_ENCODING_AES_KEY" # EncodingAESKey（43位）

PORT = 5001
HOST = "0.0.0.0"

# 临时图片目录（脚本同目录下的 temp）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

LOG_FILE = os.path.join(BASE_DIR, "relay.log")

# 飞书 API 地址
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_RECORD_URL = "https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("relay")


# ============================================================
# 飞书 Token 管理（自动刷新 + 重试）
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
                    logger.info("飞书 token 刷新成功，过期时间 %s",
                                datetime.fromtimestamp(self._expire_at).strftime("%Y-%m-%d %H:%M:%S"))
                    return self._token
                else:
                    logger.error("飞书 token 获取失败：%s", data)
            except Exception as e:
                logger.error("飞书 token 请求异常（第 %d 次）：%s", attempt + 1, e)
            time.sleep(2 ** attempt)  # 指数退避
        raise RuntimeError("无法获取飞书 tenant_access_token")


token_mgr = FeishuTokenManager()


# ============================================================
# 企业微信消息加解密（WXBizMsgCrypt 逻辑内联）
# ============================================================
class WXBizMsgCrypt:
    """简化版企业微信消息加解密，兼容 AES-256-CBC + PKCS7。"""

    def __init__(self, token, encoding_aes_key):
        self.token = token
        self.aes_key = base64.b64decode(encoding_aes_key + "=")
        self.iv = self.aes_key[:16]

    def _pkcs7_pad(self, text):
        block_size = 32
        amount = block_size - (len(text) % block_size)
        return text + bytes([amount]) * amount

    def _pkcs7_unpad(self, text):
        pad = text[-1]
        if pad < 1 or pad > 32:
            pad = 0
        return text[:-pad] if pad else text

    def verify_signature(self, msg_signature, timestamp, nonce, encrypt):
        sha = hashlib.sha1()
        raw = "".join(sorted([self.token, timestamp, nonce, encrypt]))
        sha.update(raw.encode("utf-8"))
        return sha.hexdigest() == msg_signature

    def decrypt(self, encrypt):
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        plain = cipher.decrypt(base64.b64decode(encrypt))
        plain = self._pkcs7_unpad(plain)
        # 结构：16字节随机 + 4字节长度 + 消息 + corpid
        content = plain[16:]
        msg_len = int.from_bytes(content[:4], "big")
        msg = content[4:4 + msg_len]
        return msg.decode("utf-8")

    def encrypt(self, text, corpid=""):
        import struct
        rand = "".join(random.choices(string.ascii_letters + string.digits, k=16)).encode()
        msg = text.encode("utf-8")
        payload = rand + struct.pack(">I", len(msg)) + msg + corpid.encode()
        padded = self._pkcs7_pad(payload)
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        enc = cipher.encrypt(padded)
        return base64.b64encode(enc).decode()


def decrypt_webhook(body_dict):
    """解密企业微信回调，返回明文消息字符串。无 encrypt 字段则按明文处理。"""
    encrypt = body_dict.get("encrypt")
    if not encrypt:
        # 明文模式（测试或群机器人直接 POST）
        return json.dumps(body_dict, ensure_ascii=False)
    if not WEWORK_TOKEN or not WEWORK_AES_KEY or len(WEWORK_AES_KEY) != 43:
        raise ValueError("未配置有效的 WEWORK_TOKEN / WEWORK_AES_KEY，无法解密")
    crypt = WXBizMsgCrypt(WEWORK_TOKEN, WEWORK_AES_KEY)
    sig = body_dict.get("msg_signature", "")
    ts = body_dict.get("timestamp", "")
    nonce = body_dict.get("nonce", "")
    if not crypt.verify_signature(sig, ts, nonce, encrypt):
        raise ValueError("企业微信消息签名校验失败")
    return crypt.decrypt(encrypt)


# ============================================================
# 消息解析
# ============================================================
STAKE_PATTERN = re.compile(r"[A-Za-z]{0,3}\d+\+\d+")
DISEASE_MAP = {
    "坑槽": "坑槽",
    "裂缝": "裂缝",
    "沉降": "沉降",
    "积水": "积水",
    "护栏损坏": "护栏损坏",
    "护栏": "护栏损坏",
}
URGENCY_MAP = {
    "急": "急",
    "紧急": "急",
    "特急": "急",
    "一般": "一般",
    "慢": "慢",
    "缓": "慢",
}
OWNER_PATTERN = re.compile(r"@([^\s@，。,]+)")


def parse_message(content):
    """从消息文本解析结构化工单字段。"""
    result = {
        "description": content.strip() if content else "",
        "stake": None,
        "disease": None,
        "urgency": None,
        "owner": None,
        "image_local": None,
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


def download_image(pic_url):
    """下载图片到本地 temp，返回本地路径；失败返回 None。"""
    if not pic_url:
        return None
    try:
        resp = requests.get(pic_url, timeout=15, stream=True)
        resp.raise_for_status()
        ext = ".jpg"
        ct = resp.headers.get("Content-Type", "")
        if "png" in ct:
            ext = ".png"
        elif "gif" in ct:
            ext = ".gif"
        fname = "img_%d_%s%s" % (
            int(time.time() * 1000),
            "".join(random.choices(string.ascii_lowercase + string.digits, k=6)),
            ext,
        )
        path = os.path.join(TEMP_DIR, fname)
        with open(path, "wb") as f:
            for chunk in resp.iter_content(1024):
                f.write(chunk)
        logger.info("图片已下载：%s", path)
        return path
    except Exception as e:
        logger.error("图片下载失败：%s", e)
        return None


# ============================================================
# 飞书写入
# ============================================================
def build_feishu_fields(parsed, sender, create_time):
    """构造飞书多维表格记录字段。"""
    fields = {
        "描述": parsed["description"],
        "桩号": parsed["stake"] or "未识别",
        "类型": parsed["disease"] or "其他",
        "紧急度": parsed["urgency"] or "一般",
        "来源": sender or "未知",
        "时间": datetime.fromtimestamp(int(create_time)).strftime("%Y-%m-%d %H:%M:%S") if create_time else "",
    }
    if parsed["owner"]:
        fields["责任人"] = parsed["owner"]
    if parsed["image_local"]:
        # 飞书附件字段需传 file_token，此处简化为记录本地路径文本
        # 如需真实附件，需先调用文件上传 API 获取 file_token
        fields["附件"] = parsed["image_local"]
    return fields


def write_to_feishu(fields):
    """写入飞书多维表格，含 token 重试。"""
    url = FEISHU_RECORD_URL.format(app_token=FEISHU_APP_TOKEN, table_id=FEISHU_TABLE_ID)
    last_err = None
    for attempt in range(3):
        token = token_mgr.get_token(force=(attempt > 0))
        headers = {"Authorization": "Bearer %s" % token, "Content-Type": "application/json"}
        payload = {"fields": fields}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                logger.info("飞书写入成功，record_id=%s", data.get("data", {}).get("record", {}).get("id"))
                return True
            elif data.get("code") == 99991663:  # token 失效
                logger.warning("飞书 token 失效，准备刷新重试")
                last_err = data
                continue
            else:
                logger.error("飞书写入失败：%s", data)
                last_err = data
        except Exception as e:
            logger.error("飞书写入请求异常（第 %d 次）：%s", attempt + 1, e)
            last_err = str(e)
        time.sleep(2 ** attempt)
    logger.error("飞书写入最终失败：%s", last_err)
    return False


# ============================================================
# Flask 应用
# ============================================================
app = Flask(__name__)


def process_message(raw_msg):
    """处理一条消息（dict），解析并写入飞书。"""
    try:
        msg_type = raw_msg.get("MsgType", "text")
        content = raw_msg.get("Content", "")
        sender = raw_msg.get("FromUserName", "")
        create_time = raw_msg.get("CreateTime", int(time.time()))

        parsed = parse_message(content)
        image_local = None
        if msg_type == "image":
            image_local = download_image(raw_msg.get("PicUrl", ""))
            parsed["image_local"] = image_local
            if not parsed["description"]:
                parsed["description"] = "[图片工单] %s" % (image_local or "")

        fields = build_feishu_fields(parsed, sender, create_time)
        ok = write_to_feishu(fields)
        return {"parsed": parsed, "feishu_ok": ok}
    except Exception as e:
        logger.exception("处理消息异常：%s", e)
        return {"error": str(e)}


@app.route("/api/webhook", methods=["POST"])
def webhook():
    try:
        body = request.get_json(force=True, silent=True) or {}
        plain = decrypt_webhook(body)
        # 明文可能是 JSON 字符串或 XML；优先按 JSON 解析
        try:
            raw_msg = json.loads(plain)
        except Exception:
            # XML 兜底：简单提取 Content
            content_m = re.search(r"<Content><!\[CDATA\[(.*?)\]\]></Content>", plain)
            raw_msg = {
                "MsgType": "text",
                "Content": content_m.group(1) if content_m else plain,
                "FromUserName": "",
                "CreateTime": int(time.time()),
            }
        logger.info("收到消息：%s", json.dumps(raw_msg, ensure_ascii=False)[:500])
        process_message(raw_msg)
    except Exception as e:
        logger.exception("webhook 处理失败：%s", e)
        # 仍返回 success 避免企业微信无限重试，失败已记录日志
    return Response("success", mimetype="text/plain")


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "wework_to_feishu_relay",
        "feishu_app_token_set": bool(FEISHU_APP_TOKEN and FEISHU_APP_TOKEN != "YOUR_FEISHU_APP_TOKEN"),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/test", methods=["POST"])
def test_endpoint():
    """测试端点：接收明文 JSON，模拟完整解析 + 飞书写入流程。"""
    try:
        raw_msg = request.get_json(force=True, silent=True) or {}
        logger.info("测试消息：%s", json.dumps(raw_msg, ensure_ascii=False)[:500])
        result = process_message(raw_msg)
        return jsonify({"status": "processed", "result": result})
    except Exception as e:
        logger.exception("测试端点异常：%s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ============================================================
# 主入口
# ============================================================
def print_banner():
    logger.info("=" * 50)
    logger.info("企业微信 → 飞书多维表格 中转服务启动")
    logger.info("监听地址: http://%s:%d", get_local_ip(), PORT)
    logger.info("健康检查: http://localhost:%d/api/health", PORT)
    logger.info("测试端点: POST http://localhost:%d/api/test", PORT)
    logger.info("飞书 app_token: %s", "已配置" if FEISHU_APP_TOKEN != "YOUR_FEISHU_APP_TOKEN" else "未配置(占位符)")
    logger.info("飞书 table_id : %s", "已配置" if FEISHU_TABLE_ID != "YOUR_FEISHU_TABLE_ID" else "未配置(占位符)")
    logger.info("企业微信 Token : %s", "已配置" if WEWORK_TOKEN != "YOUR_WEWORK_TOKEN" else "未配置(占位符)")
    logger.info("临时图片目录: %s", TEMP_DIR)
    logger.info("日志文件: %s", LOG_FILE)
    logger.info("=" * 50)


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
    print_banner()
    app.run(host=HOST, port=PORT, threaded=True)
