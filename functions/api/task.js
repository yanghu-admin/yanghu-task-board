// EdgeOne Pages Function — POST /api/task
// 新建或更新任务：上传照片到 images/、写入 GitHub 仓库 data.json，实现"填报即自动发布"。
// 前端以 multipart 提交：字段 task_json（任务对象 JSON）+ before/during/after（照片文件）。
const OWNER = 'yanghu-admin';
const REPO = 'yanghu-task-board';
const BRANCH = 'master';

function ghHeaders(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'User-Agent': 'edgeone-yanghu',
    'Content-Type': 'application/json'
  };
}
function b64decode(b64) {
  // GitHub 返回的 base64 含换行与（可能的）URL-safe 变体，必须全部清理，否则 atob 抛 InvalidCharacterError
  const norm = String(b64).replace(/[^A-Za-z0-9+/]/g, '');
  const bin = atob(norm);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}
function b64encodeBytes(bytes) {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}
function b64encodeStr(str) {
  return b64encodeBytes(new TextEncoder().encode(str));
}
async function getSha(env, path) {
  const url = `https://api.github.com/repos/${env.GH_OWNER || OWNER}/${env.GH_REPO || REPO}/contents/${path}?ref=${env.GH_BRANCH || BRANCH}`;
  const res = await fetch(url, { headers: ghHeaders(env.GITHUB_PAT) });
  if (res.ok) { const d = await res.json(); return d.sha; }
  return null;
}
async function putContent(env, path, contentB64, message) {
  const url = `https://api.github.com/repos/${env.GH_OWNER || OWNER}/${env.GH_REPO || REPO}/contents/${path}`;
  const sha = await getSha(env, path);
  const body = { message, content: contentB64, branch: env.GH_BRANCH || BRANCH };
  if (sha) body.sha = sha;
  const res = await fetch(url, { method: 'PUT', headers: ghHeaders(env.GITHUB_PAT), body: JSON.stringify(body) });
  if (!res.ok) { const t = await res.text(); throw new Error('GitHub PUT ' + res.status + ' ' + path + ' ' + t.slice(0, 200)); }
}
async function getDecodedFile(env, path) {
  const url = `https://api.github.com/repos/${env.GH_OWNER || OWNER}/${env.GH_REPO || REPO}/contents/${path}?ref=${env.GH_BRANCH || BRANCH}`;
  const res = await fetch(url, { headers: ghHeaders(env.GITHUB_PAT) });
  if (!res.ok) throw new Error('GitHub GET ' + res.status + ' ' + path);
  const data = await res.json();
  return b64decode(data.content);
}

export async function onRequestPost({ request, env }) {
  try {
    const ct = request.headers.get('content-type') || '';
    let task = {};
    const groups = {};
    if (ct.includes('multipart')) {
      const fd = await request.formData();
      task = JSON.parse(fd.get('task_json') || '{}');
      for (const g of ['before', 'during', 'after']) {
        const fs = fd.getAll(g);
        if (fs && fs.length) groups[g] = fs;
      }
    } else {
      task = await request.json();
    }

    // 上传照片到仓库 images/
    for (const g of Object.keys(groups)) {
      if (!task.images) task.images = {};
      if (!task.images[g]) task.images[g] = [];
      for (let i = 0; i < groups[g].length; i++) {
        const file = groups[g][i];
        const bytes = new Uint8Array(await file.arrayBuffer());
        const path = (task.images[g][i]) || `${g}_${Date.now()}_${i}.jpg`;
        await putContent(env, path, b64encodeBytes(bytes), 'add image ' + path);
        if (!task.images[g].includes(path)) task.images[g].push(path);
      }
    }

    // 读取仓库当前数据并合并
    const raw = await getDecodedFile(env, 'data.json');
    const db = JSON.parse(raw);
    if (!db.tasks) db.tasks = [];
    const idx = task.id ? db.tasks.findIndex(t => t.id === task.id) : -1;
    if (idx >= 0) {
      const existing = db.tasks[idx];
      if (task.work_orders === undefined && existing.work_orders !== undefined) task.work_orders = existing.work_orders;
      db.tasks[idx] = Object.assign({}, existing, task);
    } else {
      task.id = task.id || ('TASK-' + Date.now());
      task.status = task.status || 'pending';
      if (!task.images) task.images = { before: [], during: [], after: [] };
      if (!task.reviews) task.reviews = {
        level1: { reviewer: '黄瑾文', result: null, reviewed_at: null, comment: '', images: [] },
        level2: { reviewer: '邹佳飞', result: null, reviewed_at: null, comment: '', images: [] }
      };
      if (!task.number) task.number = 'IMPORT-' + Date.now();
      db.tasks.unshift(task);
    }
    await putContent(env, 'data.json', b64encodeStr(JSON.stringify(db, null, 2)), 'update data.json via edge function');
    return new Response(JSON.stringify({ ok: true, id: task.id }), {
      headers: { 'content-type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
    });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e.message) }), {
      status: 500,
      headers: { 'content-type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
    });
  }
}
