# 养护任务跟踪看板

> 南平联络线高速土建养护 · 任务跟踪看板（微信群任务自动跟踪）
> 当前维护方：WorkBuddy ｜ 看板地址（新）：EdgeOne Pages（部署后获得 `*.edgeone.site`）｜ 旧：https://yanghu-admin.github.io/yanghu-task-board/

本文件为**真实架构说明**（替代早期 Marvis 写的旧版 README）。早期 README 描述的是「金数据表单 + 转发给 Marvis」的旧方案，已不适用，请勿照此操作。

---

## 一、系统总览（2026-07-23 架构升级）

**HTML 前端 + 边缘函数自动发布 + GitHub 仓库当数据源**。飞书多维表已退出用户流程（停用）。

```
一线人员网页填表（选照片 + 填桩号 + 提交）
   └─ POST /api/task  ──►  EdgeOne 边缘函数（functions/api/task.js）
                                │ 上传照片 → GitHub 仓库 images/
                                │ 写入/合并 → GitHub 仓库 data.json（经 GitHub API 自动提交）
                                ▼
       GitHub 仓库（data.json + images/）◄── 唯一数据源（带版本历史）
                                │
       GET /api/data ──► 边缘函数读取实时 data.json ──► 网页展示
```

- 数据链路：**0 人工脚本、0 常驻服务器、0 git 操作**（一线人员填完即上线）。
- 仓库 `data.json` / `images/` 是唯一真相源，Git 提交即天然审计。
- 飞书、企业微信中转、本地 Flask 全部移除（从未稳定跑通，且一线人员反感飞书表格）。

---

## 二、关键文件

| 文件 | 说明 |
|------|------|
| `index.html` | 看板主页面（录入 + 看板一体）。数据走 `/api/data`，图片走 GitHub 实时地址，提交走 `/api/task`。 |
| `archive/index_20260723_old.html` | 归档的旧版 `index.html`（122,652 字节，工单模块合并前的版本），仅供回滚参考。 |
| `data.json` | 看板数据源（GitHub 仓库内）。网页经 `/api/data` 读取，边缘函数经 GitHub API 写入。 |
| `images/` | 维修前/中/后照片，由边缘函数上传到仓库 `images/`。 |
| `functions/api/task.js` | **EdgeOne 边缘函数**：接收提交（新建/更新任务 + 上传照片），写入 GitHub。 |
| `functions/api/data.js` | **EdgeOne 边缘函数**：返回实时 `data.json`。 |
| `package.json` | 构建配置（`npm run build` → 输出 `dist`）。 |
| `feishu_sync_form.py` | 历史脚本（飞书→主表→data.json），**待停用**，已被边缘函数取代。 |
| `feishu_sync_review.py` | 历史脚本（飞书→主表→data.json），**待停用**。 |
| `wework_bot_relay.py` | 企业微信机器人回调中继（Flask），**从未生产部署，待停用**。 |
| `wework_to_feishu_relay.py` | 企业微信→飞书中转（Flask），**待停用**。 |
| `photo_watermark.py` | 照片水印工具（Pillow），后续 AI 水印功能复用。 |
| `env_loader.py` / `.env` / `.env.example` | 历史脚本的凭据加载（边缘函数改读 EdgeOne 环境变量）。 |
| `requirements.txt` | 历史 Python 脚本依赖清单。 |

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

## 六、部署（EdgeOne Pages，全自动）

详见《EdgeOne部署与授权_手把手教程_20260723.md》。要点：

1. 仓库推到 GitHub（WorkBuddy 用轮换后的 PAT 推送，含 `index.html` / `functions/` / `package.json`）。
2. EdgeOne Pages 连接该仓库（分支 `master`，构建 `npm run build`，输出 `dist`）。
3. 设环境变量 `GITHUB_PAT` / `GH_OWNER` / `GH_REPO` / `GH_BRANCH`（见教程）。
4. 点「部署」→ 获得 `*.edgeone.site` 网址，填报即自动发布。

> 底层仍是「写 GitHub 仓库 + 边缘函数实时读取」，但 git push 由边缘函数在云端完成，
> 一线人员与老板都**无需碰 git、无需维护服务器**。

---

## 七、相关文档

- `交接补遗_真实版_20260723.md` —— 纠正原移交文档失真的部分（数据流、线上版本、鉴权、密钥）。
- `移交缺口核查报告_20260723.md` —— 移交资料核查发现的问题清单。
- `马维斯解释核验与下一步计划_20260723.md` —— 对 Marvis 8 问答复的实测核验。

---

*最后更新：2026-07-23（WorkBuddy 接管后校准）*
