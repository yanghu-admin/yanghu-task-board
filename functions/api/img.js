// EdgeOne Pages Function — GET /api/img?token=xxx
// 飞书图片代理：收到图片请求→调飞书API获取临时URL→302重定向
// 解决飞书附件临时URL只有24小时有效期的问题
// 注意：本版本不使用KV存储，用模块级变量缓存token（实例内有效）
// 环境变量：FEISHU_APP_ID、FEISHU_APP_SECRET（在EdgeOne项目设置中配置）

// 模块级缓存（边缘函数实例内有效，实例重启后失效）
let cachedToken = null;
let cachedTokenTime = 0;
const TOKEN_CACHE_TTL = 110 * 60 * 1000; // 110分钟（飞书token有效期约2小时）

async function getFeishuToken(env) {
  // 先查模块级缓存
  if (cachedToken && (Date.now() - cachedTokenTime < TOKEN_CACHE_TTL)) {
    return cachedToken;
  }

  // 调用飞书API获取token
  const appId = env.FEISHU_APP_ID;
  const appSecret = env.FEISHU_APP_SECRET;
  const res = await fetch('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret })
  });
  const data = await res.json();
  if (data.code !== 0) throw new Error('Feishu token error: ' + data.msg);

  cachedToken = data.tenant_access_token;
  cachedTokenTime = Date.now();
  return cachedToken;
}

async function getTmpUrl(env, fileToken) {
  // 调用飞书API获取临时下载链接（不缓存，每次都获取）
  const token = await getFeishuToken(env);
  const url = `https://open.feishu.cn/open-apis/drive/v1/medias/batch_get_tmp_download_url?file_tokens=${encodeURIComponent(fileToken)}`;
  const res = await fetch(url, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  const data = await res.json();
  if (data.code !== 0) throw new Error('Feishu tmp url error: ' + data.msg);

  const tmpUrls = data.data?.tmp_download_urls || [];
  if (!tmpUrls.length) throw new Error('No tmp url returned');

  return tmpUrls[0].tmp_download_url;
}

export async function onRequestGet({ request, env }) {
  try {
    const url = new URL(request.url);
    const fileToken = url.searchParams.get('token');

    if (!fileToken) {
      return new Response(JSON.stringify({ error: 'missing token parameter' }), {
        status: 400,
        headers: { 'content-type': 'application/json; charset=utf-8' }
      });
    }

    const tmpUrl = await getTmpUrl(env, fileToken);

    // 302重定向到飞书临时URL
    return new Response(null, {
      status: 302,
      headers: {
        'Location': tmpUrl,
        'Cache-Control': 'public, max-age=3600', // 缓存1小时（浏览器/CDN层）
        'Access-Control-Allow-Origin': '*'
      }
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e.message) }), {
      status: 500,
      headers: { 'content-type': 'application/json; charset=utf-8' }
    });
  }
}
