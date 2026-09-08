// EdgeOne Pages Function — GET /api/img?token=xxx
// 飞书图片代理：收到图片请求→查KV缓存（24小时内复用）→无缓存则调飞书API获取临时URL→302重定向
// 解决飞书附件临时URL只有24小时有效期的问题

const CACHE_TTL = 23 * 60 * 60 * 1000; // 23小时（比飞书24小时少1小时，留安全余量）

async function getFeishuToken(env) {
  // 先查缓存
  const cachedToken = await env.KV.get('feishu:tenant_token');
  if (cachedToken) return cachedToken;

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

  const token = data.tenant_access_token;
  // 缓存110分钟（飞书token有效期约2小时，留安全余量）
  await env.KV.put('feishu:tenant_token', token, { expirationTtl: 6600 });
  return token;
}

async function getTmpUrl(env, fileToken) {
  // 先查缓存
  const cacheKey = 'img:' + fileToken;
  const cached = await env.KV.get(cacheKey, { type: 'json' });
  if (cached && cached.url && (Date.now() - cached.time < CACHE_TTL)) {
    return cached.url;
  }

  // 调用飞书API获取临时下载链接
  const token = await getFeishuToken(env);
  const url = `https://open.feishu.cn/open-apis/drive/v1/medias/batch_get_tmp_download_url?file_tokens=${encodeURIComponent(fileToken)}`;
  const res = await fetch(url, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  const data = await res.json();
  if (data.code !== 0) throw new Error('Feishu tmp url error: ' + data.msg);

  const tmpUrls = data.data?.tmp_download_urls || [];
  if (!tmpUrls.length) throw new Error('No tmp url returned');

  const tmpUrl = tmpUrls[0].tmp_download_url;

  // 缓存23小时
  await env.KV.put(cacheKey, JSON.stringify({ url: tmpUrl, time: Date.now() }), { expirationTtl: 82800 });

  return tmpUrl;
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
        'Cache-Control': 'public, max-age=82800', // 23小时
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
