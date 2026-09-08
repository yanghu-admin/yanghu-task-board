// EdgeOne Pages Function — POST /api/task
// 新建或更新任务：上传照片到飞书附件、写入飞书多维表格
// 替换原来的GitHub data.json方案
// 前端以 multipart 提交：字段 task_json（任务对象 JSON）+ before/during/after（照片文件）

const FEISHU_BASE_TOKEN = 'PG1NbgKG7ae8HGsXKVFcGp1MnBh';
const FEISHU_TABLE_ID = 'tbl7w9jy83w5rJUS';

async function getFeishuToken(env) {
  const cachedToken = await env.KV.get('feishu:tenant_token');
  if (cachedToken) return cachedToken;

  const res = await fetch('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app_id: env.FEISHU_APP_ID, app_secret: env.FEISHU_APP_SECRET })
  });
  const data = await res.json();
  if (data.code !== 0) throw new Error('Feishu token error: ' + data.msg);

  const token = data.tenant_access_token;
  await env.KV.put('feishu:tenant_token', token, { expirationTtl: 6600 });
  return token;
}

// 上传文件到飞书附件
async function uploadAttachment(env, token, file, fileName) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const formData = new FormData();
  formData.append('file_name', fileName);
  formData.append('parent_type', 'bitable');
  formData.append('parent_node', env.FEISHU_BASE_TOKEN || FEISHU_BASE_TOKEN);
  formData.append('size', String(bytes.length));
  formData.append('file', new Blob([bytes], { type: file.type || 'application/octet-stream' }), fileName);

  const res = await fetch('https://open.feishu.cn/open-apis/drive/v1/medias/upload_all', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` },
    body: formData
  });
  const data = await res.json();
  if (data.code !== 0) throw new Error('Upload attachment error: ' + data.msg);
  return data.data.file_token;
}

// 富文本字段转换
function toRichText(value) {
  if (!value) return null;
  if (typeof value === 'string') return [{ type: 'text', text: value }];
  return value;
}

// 转换任务对象为飞书字段格式
function taskToFields(task, photoFileTokens) {
  const fields = {};

  if (task.work_order !== undefined) fields['工单号'] = task.work_order;
  if (task.pile_number !== undefined) fields['桩号'] = task.pile_number;
  if (task.description !== undefined) fields['问题描述'] = task.description;
  if (task.responsible !== undefined) fields['责任人'] = task.responsible;
  if (task.status !== undefined) fields['状态'] = task.status;
  if (task.level1_reviewer !== undefined) fields['一级核验人'] = task.level1_reviewer;
  if (task.level1_result !== undefined) fields['一级核验'] = task.level1_result;
  if (task.level2_reviewer !== undefined) fields['二级核验人'] = task.level2_reviewer;
  if (task.level2_result !== undefined) fields['二级核验'] = task.level2_result;
  if (task.disease_category !== undefined) fields['病害分类'] = task.disease_category;

  // 日期字段（毫秒时间戳）
  if (task.dispatch_time) fields['派发时间'] = new Date(task.dispatch_time).getTime();
  if (task.complete_time) fields['完成时间'] = new Date(task.complete_time).getTime();
  if (task.deadline) fields['截止时间'] = new Date(task.deadline).getTime();
  if (task.accept_time) fields['受理时间'] = new Date(task.accept_time).getTime();

  // 照片附件字段
  if (photoFileTokens && photoFileTokens.length > 0) {
    fields['照片'] = photoFileTokens.map(token => ({ file_token: token }));
  }

  return fields;
}

export async function onRequestPost({ request, env }) {
  try {
    const ct = request.headers.get('content-type') || '';
    let task = {};
    const photoFiles = [];

    if (ct.includes('multipart')) {
      const fd = await request.formData();
      task = JSON.parse(fd.get('task_json') || '{}');

      // 收集所有照片文件
      for (const key of ['before', 'during', 'after', 'photos', 'photo']) {
        const files = fd.getAll(key);
        if (files && files.length) {
          for (const file of files) {
            if (file && file.size > 0) {
              photoFiles.push({ file, name: `${key}_${Date.now()}_${photoFiles.length}.jpg` });
            }
          }
        }
      }
    } else {
      task = await request.json();
    }

    // 服务端鉴权：检查 X-Admin-Key 请求头
    // 前端硬编码的 ADMIN_KEY 仅用于UI显示控制，真正的写入鉴权在这里
    const adminKey = request.headers.get('X-Admin-Key') || request.headers.get('x-admin-key');
    const expectedKey = env.ADMIN_KEY || 'yh2026'; // 默认值兼容旧版，部署时应配置环境变量
    if (!adminKey || adminKey !== expectedKey) {
      return new Response(JSON.stringify({ ok: false, error: 'Unauthorized: 无效的管理员密钥' }), {
        status: 401,
        headers: { 'content-type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
      });
    }

    const token = await getFeishuToken(env);
    const baseToken = env.FEISHU_BASE_TOKEN || FEISHU_BASE_TOKEN;
    const tableId = env.FEISHU_TABLE_ID || FEISHU_TABLE_ID;

    // 上传照片到飞书附件
    const photoFileTokens = [];
    for (const { file, name } of photoFiles) {
      try {
        const fileToken = await uploadAttachment(env, token, file, name);
        photoFileTokens.push(fileToken);
      } catch (e) {
        console.error('Upload photo failed:', e.message);
      }
    }

    // 转换为飞书字段格式
    const fields = taskToFields(task, photoFileTokens);

    // 如果有 task.id，更新记录；否则新建记录
    let recordId = task.id;
    if (recordId) {
      // 尝试更新记录
      const updateUrl = `https://open.feishu.cn/open-apis/bitable/v1/apps/${baseToken}/tables/${tableId}/records/${recordId}`;
      const res = await fetch(updateUrl, {
        method: 'PUT',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields })
      });
      const data = await res.json();
      if (data.code !== 0) {
        // 更新失败，可能是record_id不存在，改为新建
        recordId = null;
      }
    }

    if (!recordId) {
      // 新建记录
      const createUrl = `https://open.feishu.cn/open-apis/bitable/v1/apps/${baseToken}/tables/${tableId}/records`;
      const res = await fetch(createUrl, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields })
      });
      const data = await res.json();
      if (data.code !== 0) throw new Error('Create record error: ' + data.msg + ' ' + JSON.stringify(data.error || {}));
      recordId = data.data.record.record_id;
    }

    return new Response(JSON.stringify({ ok: true, id: recordId, photos_uploaded: photoFileTokens.length }), {
      headers: { 'content-type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
    });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e.message) }), {
      status: 500,
      headers: { 'content-type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
    });
  }
}
