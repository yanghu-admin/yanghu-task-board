# 养护任务跟踪看板

> 南平联络线高速土建养护 · 任务跟踪看板（微信群任务自动跟踪）
> 当前维护方：WorkBuddy ｜ 看板地址：https://yanghu-admin.github.io/yanghu-task-board/

本文件为**真实架构说明**（替代早期 Marvis 写的旧版 README）。早期 README 描述的是「金数据表单 + 转发给 Marvis」的旧方案，已不适用，请勿照此操作。

---

## 一、系统总览

纯前端看板（HTML + `data.json`）+ 飞书多维表格作为数据中台 + GitHub Pages 部署。

```
企业微信群消息
   └─ wework_bot_relay.py (Flask :5001) ──► 飞书主表（自动建单）
经办人用飞书填写「经办人提交表」
核验人用飞书填写「核验提交表」
   └─ feishu_sync_form.py   （手动运行，经办人→主表+data.json）
   └─ feishu_sync_review.py （手动运行，核验→主表+data.json）
            │
            ▼
      本地 data.json  ◄── index.html 通过 fetch('data.json') 读取
            │
            ▼  git push（master）
      GitHub Pages 自动发布看板
```

> 注意：两个 `feishu_sync_*.py` 目前是**手动执行**的（每次有人提交表单后由人跑一次），
> 通过飞书表的「已同步」布尔字段做幂等标记，可重复运行。

---

## 二、关键文件

| 文件 | 说明 |
|------|------|
| `index.html` | 看板主页面（141,329 字节）。由旧 `index_live.html` 提升而来，与线上部署版一致。**这是唯一正确的基线**。 |
| `archive/index_20260723_old.html` | 归档的旧版 `index.html`（122,652 字节，工单模块合并前的版本），仅供回滚参考。 |
| `data.json` | 看板数据源，`index.html` 通过 `fetch` 读取。由同步脚本更新。 |
| `images/` | 维修前/中/后照片，按 `task-XXX_阶段_序号.jpg` 命名。 |
| `feishu_sync_form.py` | 经办人提交同步脚本（飞书→主表→data.json）。 |
| `feishu_sync_review.py` | 核验提交同步脚本（飞书→主表→data.json）。 |
| `wework_bot_relay.py` | 企业微信机器人回调中继（Flask，端口 5001）。 |
| `wework_to_feishu_relay.py` | 企业微信消息 → 飞书多维表格的中转服务（Flask）。 |
| `photo_watermark.py` | 照片水印工具（Pillow）。 |
| `env_loader.py` / `.env` / `.env.example` | 凭据统一加载与配置（见第四节）。 |
| `requirements.txt` | Python 依赖清单。 |

---

## 三、飞书多维表格结构

应用 App ID：`cli_aab0cfc248b9dcfa`（应用令牌 app_token 见 `.env.example`）

| 表 | table_id | 用途 |
|----|----------|------|
| 主表（养护任务跟踪看板） | `tbljdl99HjJrwiSs` | 工单主数据 |
| 经办人提交表 | `tblI0tKOoMy4fyX6` | 经办人维修反馈 |
| 核验提交表 | `tblBh8mjI3BiDLmh` | 一/二级核验结果 |

字段映射（节选）：

- 经办人提交 → 主表：`工单号(自动)` 匹配；`维修前/中/后照片` → `照片`；`备注` → `问题描述`；状态 `处理中/待核验`。
- 核验提交 → 主表：`核验级别` 路由到 `一级核验`/`二级核验`；`核验结果` → `已通过/退回`；状态 `需整改/已完成`。

---

## 四、凭据与安全管理（重要）

所有密钥**只**通过环境变量或 `.env` 文件提供，**严禁硬编码、严禁提交到 git**。

```bash
cp .env.example .env      # 然后填入真实值
```

- `.env` 已被 `.gitignore` 排除，不会进仓库。
- `feishu_sync_form.py` / `feishu_sync_review.py` 已改造为 `from env_loader import ...`，不再含明文密钥。
- `.env.example` 是模板（占位符 + 必要的非敏感标识），可安全提交。
- **如任一密钥泄露，立即在对应平台轮换**，并同步更新 `.env`。

> 已知待整改项：看板 `index.html` 的 admin 密钥仍硬编码在源码中（如 `ADMIN_KEY='yh2026'`），
> 且看板为公开 GitHub Pages，任何看源码者均可获取。这属于弱鉴权，生产环境应改为服务端校验。

---

## 五、本地运行同步脚本

```bash
pip install -r requirements.txt
# 确保 .env 已配置 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_APP_TOKEN
python feishu_sync_form.py    # 同步经办人提交
python feishu_sync_review.py  # 同步核验提交
```

中继服务（需公网可达 + 企业微信回调配置）：

```bash
python wework_bot_relay.py          # 端口 5001
python wework_to_feishu_relay.py    # 企业微信→飞书中转
```

---

## 六、部署（统一用 git push）

1. 本机需安装 Git。
2. 修改 `index.html` / `data.json` / `images/` 后：

```bash
git add -A
git commit -m "描述"
git push origin master
```

3. GitHub Pages 自动刷新看板。

> 历史上曾用「PowerShell 读二进制 + REST API PUT」方式部署（因当时机器无 Git）。
> 现已统一为 `git push`，API PUT 仅作应急。推送前务必先 `git fetch` 对齐远端，
> 避免覆盖线上版本。

---

## 七、相关文档

- `交接补遗_真实版_20260723.md` —— 纠正原移交文档失真的部分（数据流、线上版本、鉴权、密钥）。
- `移交缺口核查报告_20260723.md` —— 移交资料核查发现的问题清单。
- `马维斯解释核验与下一步计划_20260723.md` —— 对 Marvis 8 问答复的实测核验。

---

*最后更新：2026-07-23（WorkBuddy 接管后校准）*
