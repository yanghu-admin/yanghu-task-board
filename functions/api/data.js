// EdgeOne Pages Function — GET /api/data
// 返回 GitHub 仓库中 data.json 的实时内容（不依赖站点重建），实现"填报即更新"。
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
  const norm = b64.replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(norm);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}
async function getDecodedFile(env, path) {
  const url = `https://api.github.com/repos/${env.GH_OWNER || OWNER}/${env.GH_REPO || REPO}/contents/${path}?ref=${env.GH_BRANCH || BRANCH}`;
  const res = await fetch(url, { headers: ghHeaders(env.GITHUB_PAT) });
  if (!res.ok) throw new Error('GitHub GET ' + res.status + ' ' + path);
  const data = await res.json();
  return b64decode(data.content);
}

export async function onRequestGet({ env }) {
  try {
    const raw = await getDecodedFile(env, 'data.json');
    const obj = JSON.parse(raw);
    return new Response(JSON.stringify(obj), {
      headers: { 'content-type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' }
    });
  } catch (e) {
    return new Response(JSON.stringify({ tasks: [], circular_tasks: [], statistics: {}, error: String(e.message) }), {
      status: 200,
      headers: { 'content-type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
    });
  }
}
