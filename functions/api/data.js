// EdgeOne Pages Function — GET /api/data
// 从飞书多维表格获取养护任务数据，返回前端需要的JSON格式
// 替换原来的GitHub data.json方案

const FEISHU_BASE_TOKEN = 'PG1NbgKG7ae8HGsXKVFcGp1MnBh'; // 新表格（应用创建，有完全控制权）
const FEISHU_TABLE_ID = 'tbl7w9jy83w5rJUS'; // 养护任务跟踪看板

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
  // 缓存110分钟（飞书token有效期约2小时）
  await env.KV.put('feishu:tenant_token', token, { expirationTtl: 6600 });
  return token;
}

// 飞书富文本字段转纯文本
function richTextToText(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    return value.map(item => {
      if (typeof item === 'string') return item;
      if (item && item.text) return item.text;
      return '';
    }).join('');
  }
  return String(value);
}

// 飞书附件字段转图片URL列表
function attachmentsToUrls(value) {
  if (!value || !Array.isArray(value)) return [];
  return value.map(att => ({
    file_token: att.file_token,
    name: att.name,
    size: att.size,
    type: att.mime_type || att.type,
    url: `/api/img?token=${att.file_token}` // 通过边缘函数代理访问
  }));
}

// 飞书人员字段转姓名
function personToName(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    return value.map(p => {
      if (typeof p === 'string') return p;
      if (p && p.name) return p.name;
      if (p && p.text) return p.text;
      return '';
    }).join(', ');
  }
  return String(value);
}

// 飞书单选字段转文本
function selectToText(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.join(', ');
  return String(value);
}

// 飞书日期字段转ISO字符串
function dateToISO(value) {
  if (!value) return null;
  if (typeof value === 'number') return new Date(value).toISOString();
  if (typeof value === 'string') return value;
  return null;
}

// 转换飞书记录为前端任务格式
function convertRecord(record) {
  const f = record.fields || {};
  return {
    id: record.record_id,
    work_order: richTextToText(f['工单号']),
    pile_number: richTextToText(f['桩号']),
    description: richTextToText(f['问题描述']),
    photos: attachmentsToUrls(f['照片']),
    responsible: richTextToText(f['责任人']),
    status: selectToText(f['状态']) || 'pending',
    level1_reviewer: richTextToText(f['一级核验人']),
    level1_result: selectToText(f['一级核验']),
    level2_reviewer: richTextToText(f['二级核验人']),
    level2_result: selectToText(f['二级核验']),
    dispatch_time: dateToISO(f['派发时间']),
    complete_time: dateToISO(f['完成时间']),
    deadline: dateToISO(f['截止时间']),
    disease_category: selectToText(f['病害分类']),
    accept_time: dateToISO(f['受理时间']),
    created_time: record.created_time ? new Date(record.created_time).toISOString() : null,
    updated_time: record.last_modified_time ? new Date(record.last_modified_time).toISOString() : null
  };
}

// 计算统计信息
function calculateStats(tasks) {
  const now = new Date();
  const stats = {
    total: tasks.length,
    pending: 0,
    processing: 0,
    pending_review: 0,
    completed: 0,
    overdue: 0
  };
  for (const t of tasks) {
    const status = (t.status || '').toLowerCase();
    if (status.includes('待处理') || status === 'pending') stats.pending++;
    else if (status.includes('处理中') || status.includes('进行中') || status === 'processing') stats.processing++;
    else if (status.includes('待核验') || status.includes('核验') || status === 'review') stats.pending_review++;
    else if (status.includes('已完成') || status.includes('完成') || status === 'completed' || status === 'done') stats.completed++;

    if (t.deadline && !status.includes('已完成') && !status.includes('完成')) {
      if (new Date(t.deadline) < now) {
        stats.overdue++;
        t.is_overdue = true;
      }
    }
  }
  return stats;
}

export async function onRequestGet({ request, env }) {
  try {
    const token = await getFeishuToken(env);
    const baseToken = env.FEISHU_BASE_TOKEN || FEISHU_BASE_TOKEN;
    const tableId = env.FEISHU_TABLE_ID || FEISHU_TABLE_ID;

    // 从飞书获取所有记录（分页）
    let allRecords = [];
    let pageToken = null;
    do {
      let url = `https://open.feishu.cn/open-apis/bitable/v1/apps/${baseToken}/tables/${tableId}/records?page_size=100`;
      if (pageToken) url += `&page_token=${encodeURIComponent(pageToken)}`;

      const res = await fetch(url, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const data = await res.json();
      if (data.code !== 0) throw new Error('Feishu records error: ' + data.msg);

      allRecords = allRecords.concat(data.data?.items || []);
      pageToken = data.data?.page_token;
    } while (pageToken);

    // 转换为前端格式
    const tasks = allRecords.map(convertRecord);

    // 按更新时间倒序
    tasks.sort((a, b) => {
      const aTime = a.updated_time || a.created_time || '';
      const bTime = b.updated_time || b.created_time || '';
      return bTime.localeCompare(aTime);
    });

    // 计算统计
    const statistics = calculateStats(tasks);

    const result = {
      tasks,
      statistics,
      last_updated: new Date().toISOString(),
      source: 'feishu_bitable',
      total: tasks.length
    };

    return new Response(JSON.stringify(result), {
      headers: {
        'content-type': 'application/json; charset=utf-8',
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'no-store'
      }
    });
  } catch (e) {
    return new Response(JSON.stringify({
      tasks: [],
      statistics: { total: 0, pending: 0, processing: 0, pending_review: 0, completed: 0, overdue: 0 },
      error: String(e.message)
    }), {
      status: 200,
      headers: { 'content-type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
    });
  }
}
