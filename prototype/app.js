/* ============================================================
   ArtPM 工作台 · 三栏原型
   纯前端演示：数据在内存中，刷新即重置。
   交互覆盖 prototype/交互规格.md §4 的 F1–F4 与 §8 的 A1–A8。
   ============================================================ */
(() => {
  'use strict';

  // ── 常量 ────────────────────────────────────────────────
  const TYPE_TEMPLATES = {
    S0: {
      label: 'S0 月度人天汇总',
      steps: ['解析附件', '识别表结构', '按月裁剪', '生成 XLSX', '核验'],
      params: [{ key: 'month', label: '目标月份', value: '2026-11' }],
    },
    S1: {
      label: 'S1 报价核对',
      steps: ['读取报价单', '匹配费率表', '计算费用', '生成报价单', '核验'],
      params: [{ key: 'client', label: '客户简称', value: '腾讯' }],
    },
    S2: {
      label: 'S2 每日巡检',
      steps: ['拉取在途任务', '比对进度', '生成巡检报告', '推送企业微信'],
      params: [{ key: 'scope', label: '巡检范围', value: '全部在途' }],
    },
    S3: {
      label: 'S3 项目复盘',
      steps: ['汇总实际人天', '比对报价', '计算毛利', '生成复盘报告'],
      params: [{ key: 'project', label: '项目代号', value: '星穹-场景' }],
    },
  };

  const STATUS_META = {
    draft: { text: '草稿', pill: 'idle' },
    queued: { text: '排队中', pill: 'idle' },
    running: { text: '进行中', pill: 'running' },
    waiting: { text: '等待中', pill: 'waiting' },
    needs_input: { text: '待澄清', pill: 'needs' },
    succeeded: { text: '已完成', pill: 'done' },
    failed: { text: '失败', pill: 'failed' },
    cancelled: { text: '已取消', pill: 'idle' },
  };

  const STEP_MARK = {
    idle: '○',
    running: '◐',
    done: '✓',
    failed: '✕',
    waiting: '?',
  };

  const HOUR_MS = 3600 * 1000;
  const DAY_MS = 24 * HOUR_MS;

  // ── 状态 ────────────────────────────────────────────────
  const state = {
    projects: [
      { id: 'p-tx', name: '腾讯-王者外装', code: 'TX' },
      { id: 'p-mhy', name: '米哈游-星穹场景', code: 'MHY' },
      { id: 'p-in', name: '内部-产能治理', code: 'INT' },
    ],
    projectId: null,
    jobs: [],
    conversations: [
      { id: 'c1', title: '你好', at: Date.now() - 5 * HOUR_MS },
      { id: 'c2', title: '帮我看看合同里的账期', at: Date.now() - 26 * HOUR_MS },
      { id: 'c3', title: '人天费率怎么算的', at: Date.now() - 3 * DAY_MS },
    ],
    selectedJobId: null,
    expandedGroups: { 进行中: true, 计划中: true, 已完成: false, 会话: false },
    openRunsJobId: null,
    theme: 'light',
    parseCount: 0,
    picked: null,        // {anchor:[r,c], focus:[r,c]}
    activeVersion: {},   // jobId -> artifactId
    previewOpen: {},     // artifactId -> bool
    logOpen: {},         // `${jobId}:${stepId}` -> bool
    clarifyKey: null,    // 当前已渲染澄清卡片的标识，避免轮询重绘
    approvalKey: null,   // 同上，用于审批卡片
    fileDrafts: [],
    newJobType: 'S0',
    running: new Set(),
    timers: new Set(),
  };

  // ── 工具 ────────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const uid = (p) => p + '-' + Math.random().toString(36).slice(2, 9);

  const fmtTime = (ts) => {
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };

  const fmtDateTime = (ts) => {
    const d = new Date(ts);
    return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')} ${fmtTime(ts)}`;
  };

  const timeAgo = (ts) => {
    const diff = Date.now() - ts;
    if (diff < 60 * 1000) return '刚刚';
    if (diff < HOUR_MS) return `${Math.floor(diff / 60000)} 分钟前`;
    if (diff < DAY_MS) return `${Math.floor(diff / HOUR_MS)} 小时前`;
    if (diff < 7 * DAY_MS) return `${Math.floor(diff / DAY_MS)} 天前`;
    return fmtDateTime(ts);
  };

  const untilText = (ts) => {
    const diff = ts - Date.now();
    if (diff <= 0) return '即将触发';
    if (diff < HOUR_MS) return `${Math.ceil(diff / 60000)} 分钟后`;
    if (diff < DAY_MS) return `${Math.floor(diff / HOUR_MS)} 小时后`;
    return `${Math.floor(diff / DAY_MS)} 天后`;
  };

  const fmtSize = (bytes) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  };

  const fakeSha = (seed) => {
    let h = 0x811c9dc5;
    for (let i = 0; i < seed.length; i += 1) {
      h ^= seed.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    let out = '';
    for (let i = 0; i < 4; i += 1) {
      h = Math.imul(h ^ (h >>> 13), 0x5bd1e995) >>> 0;
      out += h.toString(16).padStart(8, '0');
    }
    return out;
  };

  const jobProgress = (job) => {
    const total = job.steps.length || 1;
    const done = job.steps.filter((s) => s.status === 'done').length;
    const active = job.steps.find((s) => s.status === 'running');
    const partial = active ? active.progress / 100 : 0;
    const ratio = Math.min(1, (done + partial) / total);
    return { ratio, done, total, pct: Math.round(ratio * 100) };
  };

  const groupOf = (job) => {
    if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') {
      return '已完成';
    }
    if (job.nextRunAt) return '计划中';
    return '进行中';
  };

  const visibleJobs = () =>
    state.jobs
      .filter((j) => !state.projectId || j.projectId === state.projectId)
      .sort((a, b) => b.createdAt - a.createdAt);

  const selectedJob = () =>
    state.jobs.find((j) => j.id === state.selectedJobId) || null;

  const projectName = (id) => {
    const p = state.projects.find((x) => x.id === id);
    return p ? p.name : '未归类';
  };

  // ── Toast ───────────────────────────────────────────────
  function toast(title, text, tone) {
    const node = el('div', 'toast');
    node.dataset.tone = tone || 'info';
    node.appendChild(el('strong', null, title));
    if (text) node.appendChild(el('span', null, text));
    $('#toasts').appendChild(node);
    const kill = () => {
      node.dataset.leaving = 'true';
      setTimeout(() => node.remove(), 220);
    };
    setTimeout(kill, 4200);
    node.addEventListener('click', kill);
  }

  // ── 种子数据 ────────────────────────────────────────────
  function seed() {
    const now = Date.now();

    state.jobs = [
      {
        id: 'j1',
        name: '11 月人天汇总',
        projectId: 'p-tx',
        type: 'S0',
        trigger: 'manual',
        status: 'needs_input',
        createdAt: now - 26 * 60 * 1000,
        nextRunAt: null,
        inputs: { month: '2026-11' },
        error: null,
        steps: [
          { id: 's1', name: '解析附件', status: 'done', progress: 100,
            detail: '角色组-任务及绩效分配.xlsx · 4 个工作表',
            log: 'openpyxl load_workbook(..., data_only=True)\n识别到表：人天分配 / 产能 / 排期 / 说明\n读取 128 行' },
          { id: 's2', name: '识别表结构', status: 'done', progress: 100,
            detail: 'document_type=人天分配表，月份列 = B',
            log: 'headers: ["任务", "月份", "商务人天", "已分配人天"]\nmonth_column = B\n未判定为报价单（无单价/总价列）' },
          { id: 's3', name: '按月裁剪', status: 'running', progress: 60,
            detail: '目标月份 2026-11，已匹配 2 行',
            log: 'mode=new_items\nmatched=2 uncovered=2\n等待口径确认后继续' },
          { id: 's4', name: '生成 XLSX', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's5', name: '核验', status: 'idle', progress: 0, detail: '', log: '' },
        ],
        clarify: {
          question: '11 月是否含结转人天？表格里 11 月有 2 行，另有 2 行未覆盖。',
          options: ['含结转', '不含结转', '只算新增'],
          answer: null,
        },
        approval: null,
        artifacts: [],
        runs: [],
      },
      {
        id: 'j2',
        name: '腾讯报价核对',
        projectId: 'p-tx',
        type: 'S1',
        trigger: 'manual',
        status: 'running',
        createdAt: now - 8 * 60 * 1000,
        nextRunAt: null,
        inputs: { client: '腾讯' },
        error: null,
        steps: [
          { id: 's1', name: '读取报价单', status: 'running', progress: 35,
            detail: '报价单-2026Q4.xlsx', log: '解析中…' },
          { id: 's2', name: '匹配费率表', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's3', name: '计算费用', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's4', name: '生成报价单', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's5', name: '核验', status: 'idle', progress: 0, detail: '', log: '' },
        ],
        clarify: null,
        approval: null,
        artifacts: [],
        runs: [],
      },
      {
        id: 'j3',
        name: '主城排期',
        projectId: 'p-mhy',
        type: 'S0',
        trigger: 'from_message',
        status: 'queued',
        createdAt: now - 3 * HOUR_MS,
        nextRunAt: null,
        inputs: { month: '2026-12' },
        error: null,
        steps: [
          { id: 's1', name: '解析附件', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's2', name: '识别表结构', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's3', name: '按月裁剪', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's4', name: '生成 XLSX', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's5', name: '核验', status: 'idle', progress: 0, detail: '', log: '' },
        ],
        clarify: null,
        approval: null,
        artifacts: [],
        runs: [],
      },
      {
        id: 'j4',
        name: '每日巡检',
        projectId: 'p-in',
        type: 'S2',
        trigger: 'scheduled',
        status: 'queued',
        createdAt: now - 9 * DAY_MS,
        nextRunAt: new Date(now + 9 * HOUR_MS).setMinutes(0, 0, 0),
        inputs: { scope: '全部在途' },
        error: null,
        steps: [
          { id: 's1', name: '拉取在途任务', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's2', name: '比对进度', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's3', name: '生成巡检报告', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's4', name: '推送企业微信', status: 'idle', progress: 0, detail: '', log: '' },
        ],
        clarify: null,
        approval: null,
        artifacts: [],
        runs: [
          { at: now - DAY_MS, status: 'succeeded', duration: '12s' },
          { at: now - 2 * DAY_MS, status: 'succeeded', duration: '11s' },
          { at: now - 3 * DAY_MS, status: 'failed', duration: '3s' },
        ],
      },
      {
        id: 'j5',
        name: '周五催办',
        projectId: 'p-in',
        type: 'S2',
        trigger: 'scheduled',
        status: 'queued',
        createdAt: now - 20 * DAY_MS,
        nextRunAt: now + 3 * DAY_MS,
        inputs: {},
        error: null,
        steps: [
          { id: 's1', name: '拉取在途任务', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's2', name: '比对进度', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's3', name: '生成巡检报告', status: 'idle', progress: 0, detail: '', log: '' },
          { id: 's4', name: '推送企业微信', status: 'idle', progress: 0, detail: '', log: '' },
        ],
        clarify: null,
        approval: null,
        artifacts: [],
        runs: [{ at: now - 4 * DAY_MS, status: 'succeeded', duration: '9s' }],
      },
      {
        id: 'j6',
        name: '星穹-场景资产交付',
        projectId: 'p-mhy',
        type: 'S0',
        trigger: 'manual',
        status: 'succeeded',
        createdAt: now - 2 * DAY_MS,
        nextRunAt: null,
        inputs: { month: '2026-10' },
        error: null,
        steps: [
          { id: 's1', name: '解析附件', status: 'done', progress: 100, detail: '场景资产表.xlsx', log: '读取 64 行' },
          { id: 's2', name: '识别表结构', status: 'done', progress: 100, detail: 'document_type=资产清单', log: 'headers 匹配 8 列' },
          { id: 's3', name: '按月裁剪', status: 'done', progress: 100, detail: '匹配 14 行', log: 'mode=new_items matched=14' },
          { id: 's4', name: '生成 XLSX', status: 'done', progress: 100, detail: '角色组-11月.xlsx', log: '工作表：人天分配 / 处理说明' },
          { id: 's5', name: '核验', status: 'done', progress: 100, detail: 'passed · SHA-256 一致', log: 'reopen ✓ 行数 ✓ 摘要 ✓ 口径说明 ✓' },
        ],
        clarify: null,
        approval: null,
        artifacts: [
          {
            id: 'a1', filename: '角色组-11月.xlsx', type: 'xlsx', size: 24678,
            sha256: fakeSha('a1'), version: 2, verification: 'passed',
            at: now - 2 * DAY_MS + 60 * 1000,
            stepId: 's4',
            columns: ['任务', '月份', '商务人天', '已分配人天'],
            rows: [
              ['麦迪', '2026-11', 18, 18],
              ['裸模优化', '2026-11', 15, 0],
              ['场景贴图', '2026-11', 12, 4],
              ['高模精修', '2026-11', 9, 9],
            ],
          },
          {
            id: 'a2', filename: '角色组-11月.xlsx', type: 'xlsx', size: 24310,
            sha256: fakeSha('a2'), version: 1, verification: 'passed',
            at: now - 2 * DAY_MS - 40 * 60 * 1000,
            stepId: 's4',
            columns: ['任务', '月份', '商务人天', '已分配人天'],
            rows: [
              ['麦迪', '2026-11', 18, 18],
              ['裸模优化', '2026-11', 15, 0],
            ],
          },
        ],
        runs: [],
      },
      {
        id: 'j7',
        name: '9 月人天汇总',
        projectId: 'p-tx',
        type: 'S0',
        trigger: 'manual',
        status: 'succeeded',
        createdAt: now - 6 * DAY_MS,
        nextRunAt: null,
        inputs: { month: '2026-09' },
        error: null,
        steps: [
          { id: 's1', name: '解析附件', status: 'done', progress: 100, detail: '9月分配表.xlsx', log: '读取 96 行' },
          { id: 's2', name: '识别表结构', status: 'done', progress: 100, detail: '月份列 = B', log: '' },
          { id: 's3', name: '按月裁剪', status: 'done', progress: 100, detail: '匹配 11 行', log: '' },
          { id: 's4', name: '生成 XLSX', status: 'done', progress: 100, detail: '9月汇总.xlsx', log: '' },
          { id: 's5', name: '核验', status: 'done', progress: 100, detail: 'passed', log: '' },
        ],
        clarify: null,
        approval: null,
        artifacts: [
          { id: 'a3', filename: '9月汇总.xlsx', type: 'xlsx', size: 18240,
            sha256: fakeSha('a3'), version: 1, verification: 'passed',
            at: now - 6 * DAY_MS, stepId: 's4',
            columns: ['任务', '月份', '商务人天'],
            rows: [['麦迪', '2026-09', 20], ['场景贴图', '2026-09', 14]] },
        ],
        runs: [],
      },
      {
        id: 'j8',
        name: '米哈游-周报生成',
        projectId: 'p-mhy',
        type: 'S2',
        trigger: 'manual',
        status: 'failed',
        createdAt: now - 30 * HOUR_MS,
        nextRunAt: null,
        inputs: {},
        error: 'PptxGenerator 缺少模板文件：templates/weekly.pptx',
        steps: [
          { id: 's1', name: '拉取在途任务', status: 'done', progress: 100, detail: '12 条', log: '' },
          { id: 's2', name: '比对进度', status: 'done', progress: 100, detail: '延期 2 条', log: '' },
          { id: 's3', name: '生成巡检报告', status: 'failed', progress: 42,
            detail: '缺少模板文件', log: 'FileNotFoundError: templates/weekly.pptx\n  at generator.py:974' },
          { id: 's4', name: '推送企业微信', status: 'idle', progress: 0, detail: '', log: '' },
        ],
        clarify: null,
        approval: null,
        artifacts: [],
        runs: [],
      },
    ];

    state.selectedJobId = 'j1';
  }

  // ── 渲染：项目选择器 ────────────────────────────────────
  function renderProjectMenu() {
    const menu = $('#project-menu');
    menu.textContent = '';

    const options = [{ id: null, name: '全部项目', code: 'ALL' }, ...state.projects];
    options.forEach((p) => {
      const li = el('li');
      const btn = el('button', 'project-select__item');
      btn.type = 'button';
      btn.setAttribute('role', 'option');
      btn.setAttribute('aria-selected', String(state.projectId === p.id));
      btn.appendChild(el('span', null, p.name));
      const count = state.jobs.filter((j) => !p.id || j.projectId === p.id).length;
      btn.appendChild(el('small', null, `${count} 个任务`));
      btn.addEventListener('click', () => {
        state.projectId = p.id;
        state.selectedJobId = null;
        state.picked = null;
        $('#project-label').textContent = p.name;
        closeProjectMenu();
        renderAll();
        toast('已切换项目', p.id ? `${p.name}，②③ 栏已重置` : '显示全部项目');
      });
      li.appendChild(btn);
      menu.appendChild(li);
    });

    const label = options.find((p) => p.id === state.projectId);
    $('#project-label').textContent = label ? label.name : '全部项目';
  }

  function openProjectMenu() {
    $('#project-menu').hidden = false;
    $('#project-btn').setAttribute('aria-expanded', 'true');
  }
  function closeProjectMenu() {
    $('#project-menu').hidden = true;
    $('#project-btn').setAttribute('aria-expanded', 'false');
  }

  // ── 渲染：① 任务栏 ──────────────────────────────────────
  function renderRail() {
    const scroll = $('#rail-scroll');
    scroll.textContent = '';

    const groups = { 进行中: [], 计划中: [], 已完成: [], 会话: [] };
    visibleJobs().forEach((job) => groups[groupOf(job)].push(job));
    groups.会话 = state.conversations;

    const total = groups.进行中.length + groups.计划中.length + groups.已完成.length;
    $('#rail-stat').textContent =
      `${total} 个任务 · ${groups.计划中.length} 个计划中 · ${groups.会话.length} 条会话`;

    if (total === 0) {
      const box = el('div', 'rail__empty');
      box.appendChild(el('strong', null, '这个项目还没有任务'));
      const text = el('p', null, '新建一个，或从下面的示例开始。');
      text.style.margin = '0';
      box.appendChild(text);

      const sugg = el('div', 'suggestions');
      [
        { t: '导出 11 月人天汇总版', type: 'S0' },
        { t: '核对腾讯这份报价单', type: 'S1' },
        { t: '每天 06:00 巡检在途任务', type: 'S2' },
      ].forEach((s) => {
        const b = el('button', 'suggestion', s.t);
        b.type = 'button';
        b.addEventListener('click', () => {
          createJob({ name: s.t, type: s.type, run: true });
        });
        sugg.appendChild(b);
      });
      box.appendChild(sugg);
      scroll.appendChild(box);
      renderConversations(scroll, groups.会话);
      return;
    }

    ['进行中', '计划中', '已完成'].forEach((name) => {
      scroll.appendChild(renderGroup(name, groups[name]));
    });

    renderConversations(scroll, groups.会话);
  }

  function renderGroup(name, jobs) {
    const open = state.expandedGroups[name] !== false;
    const wrap = el('section', 'group');
    wrap.dataset.open = String(open);

    const head = el('button', 'group__head');
    head.type = 'button';
    head.setAttribute('aria-expanded', String(open));
    head.appendChild(el('span', 'group__caret', '▾'));
    head.appendChild(el('span', null, name));
    head.appendChild(el('span', 'group__count', String(jobs.length)));
    head.addEventListener('click', () => {
      state.expandedGroups[name] = !open;
      renderRail();
    });
    wrap.appendChild(head);

    const body = el('div', 'group__body');
    if (jobs.length === 0) {
      const none = el('div', 'rail__empty', '暂无任务');
      none.style.padding = '10px';
      body.appendChild(none);
    } else {
      jobs.forEach((job) => body.appendChild(renderJobRow(job)));
    }
    wrap.appendChild(body);
    return wrap;
  }

  function renderJobRow(job) {
    const row = el('div');
    row.style.position = 'relative';

    const btn = el('button', 'job');
    btn.type = 'button';
    btn.setAttribute('aria-current', String(job.id === state.selectedJobId));

    const meta = STATUS_META[job.status] || STATUS_META.draft;
    const prog = jobProgress(job);

    const top = el('div', 'job__top');
    top.appendChild(el('span', 'job__name', job.name));
    const pill = el('span', `pill pill--${meta.pill}`, meta.text);
    top.appendChild(pill);
    btn.appendChild(top);

    const sub = el('div', 'job__sub');
    const bar = el('div', 'bar');
    const fill = el('i', 'bar__fill');
    let tone = '';
    if (job.status === 'succeeded') tone = 'done';
    else if (job.status === 'failed') tone = 'failed';
    else if (job.status === 'needs_input') tone = 'waiting';
    if (tone) fill.dataset.tone = tone;
    fill.style.width = `${prog.pct}%`;
    bar.appendChild(fill);
    sub.appendChild(bar);

    if (job.nextRunAt) {
      sub.appendChild(el('span', null, `下次 ${fmtTime(job.nextRunAt)}`));
      sub.appendChild(el('span', 'job__time', `创建于 ${timeAgo(job.createdAt)}`));
    } else {
      // 状态已在徽标里显示，这里给的是「卡在哪一步」而不是重复状态名
      if (job.status === 'failed') {
        sub.appendChild(el('span', null, '可重试'));
      } else if (job.status === 'needs_input') {
        sub.appendChild(el('span', null, '等待口径确认'));
      } else if (job.status === 'succeeded') {
        sub.appendChild(el('span', null, `${prog.total} 步全部完成`));
      } else {
        sub.appendChild(el('span', null, `${prog.done}/${prog.total} 步`));
      }
      sub.appendChild(el('span', 'job__time', timeAgo(job.createdAt)));
    }
    btn.appendChild(sub);

    btn.addEventListener('click', () => selectJob(job.id));
    row.appendChild(btn);

    if (job.trigger === 'scheduled' && job.runs.length) {
      const toggle = el('button', 'btn btn--ghost btn--sm');
      toggle.type = 'button';
      toggle.style.margin = '2px 0 6px 12px';
      const isOpen = state.openRunsJobId === job.id;
      toggle.textContent = isOpen ? '收起历史 run' : `历史 run (${job.runs.length})`;
      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        state.openRunsJobId = isOpen ? null : job.id;
        renderRail();
      });
      row.appendChild(toggle);

      if (isOpen) {
        const list = el('ul', 'runs');
        job.runs.forEach((run) => {
          const li = el('li');
          li.appendChild(el('span', null, fmtDateTime(run.at)));
          li.appendChild(el('strong', null, run.status === 'succeeded' ? '成功' : '失败'));
          li.appendChild(el('span', null, run.duration));
          list.appendChild(li);
        });
        row.appendChild(list);
      }
    }

    return row;
  }

  function renderConversations(scroll, items) {
    const open = state.expandedGroups['会话'] === true;
    const wrap = el('section', 'group');
    wrap.dataset.open = String(open);

    const head = el('button', 'group__head');
    head.type = 'button';
    head.setAttribute('aria-expanded', String(open));
    head.appendChild(el('span', 'group__caret', '▾'));
    head.appendChild(el('span', null, '会话'));
    head.appendChild(el('span', 'group__count', String(items.length)));
    head.addEventListener('click', () => {
      state.expandedGroups['会话'] = !open;
      renderRail();
    });
    wrap.appendChild(head);

    const body = el('div', 'group__body');
    if (items.length === 0) {
      const none = el('div', 'rail__empty', '暂无会话');
      none.style.padding = '10px';
      body.appendChild(none);
    } else {
      items.forEach((c) => {
        const row = el('div');
        row.style.cssText = 'display:flex;gap:8px;padding:7px 10px;font-size:12px;color:var(--pm-text-muted)';
        row.appendChild(el('span', null, '💬'));
        const t = el('span', null, c.title);
        t.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
        row.appendChild(t);
        row.appendChild(el('span', null, timeAgo(c.at)));
        body.appendChild(row);
      });
    }
    wrap.appendChild(body);
    scroll.appendChild(wrap);
  }

  // ── 渲染：② 过程栏 ──────────────────────────────────────
  function renderProcess() {
    const job = selectedJob();

    if (!job) {
      $('#proc-empty').hidden = false;
      $('#step-list').hidden = true;
      $('#proc-title').textContent = '未选择任务';
      $('#proc-meta').textContent = '';
      $('#btn-retry').hidden = true;
      $('#btn-save-job').hidden = true;
      clearClarify();
      clearApproval();
      return;
    }

    $('#proc-empty').hidden = true;
    $('#step-list').hidden = false;

    $('#proc-title').textContent = job.name;

    const metaBox = $('#proc-meta');
    metaBox.textContent = '';
    const meta = STATUS_META[job.status] || STATUS_META.draft;
    const triggerText = { manual: '手动创建', scheduled: '定时触发', from_message: '对话发起' };
    [
      projectName(job.projectId),
      `Job #${job.id}`,
      TYPE_TEMPLATES[job.type] ? TYPE_TEMPLATES[job.type].label : job.type,
      triggerText[job.trigger] || job.trigger,
    ].forEach((t) => metaBox.appendChild(el('span', null, t)));
    const pill = el('span', `pill pill--${meta.pill}`, meta.text);
    metaBox.appendChild(pill);
    if (job.nextRunAt) {
      metaBox.appendChild(el('span', null, `下次触发 ${fmtDateTime(job.nextRunAt)}（${untilText(job.nextRunAt)}）`));
    }

    const steps = $('#step-list');
    steps.textContent = '';
    job.steps.forEach((step) => steps.appendChild(renderStep(step, job)));

    $('#btn-retry').hidden = job.status !== 'failed';
    $('#btn-save-job').hidden = job.trigger !== 'from_message';

    renderClarify(job);
    renderApproval(job);
  }

  function renderStep(step, job) {
    const li = el('li', 'step');
    li.dataset.status = step.status;

    li.appendChild(el('span', 'step__mark', STEP_MARK[step.status] || '○'));

    const card = el('div', 'step__card');

    const row = el('div', 'step__row');
    row.appendChild(el('span', 'step__name', step.name));
    if (step.status === 'running') {
      const spin = el('span', 'spin', '◐');
      spin.style.color = 'var(--pm-step-running)';
      row.appendChild(spin);
    }
    row.appendChild(el('span', 'step__time',
      step.status === 'done' ? '完成' : step.status === 'failed' ? '失败' : ''));
    card.appendChild(row);

    if (step.status === 'running') {
      const bar = el('div', 'mini-bar');
      bar.style.marginTop = '8px';
      const i = el('i');
      i.style.width = `${step.progress}%`;
      bar.appendChild(i);
      card.appendChild(bar);
    }

    if (step.detail) {
      const body = el('div', 'step__body', step.detail);
      card.appendChild(body);
    }

    if (step.log) {
      const key = `${job.id}:${step.id}`;
      const toggle = el('button', 'step__expand', '展开中间产物');
      toggle.type = 'button';
      const log = el('div', 'step__log', step.log);
      const open = state.logOpen[key] === true;
      log.hidden = !open;
      toggle.textContent = open ? '收起中间产物' : '展开中间产物';
      toggle.addEventListener('click', () => {
        const next = !(state.logOpen[key] === true);
        state.logOpen[key] = next;
        log.hidden = !next;
        toggle.textContent = next ? '收起中间产物' : '展开中间产物';
      });
      card.appendChild(toggle);
      card.appendChild(log);
    }

    li.appendChild(card);
    return li;
  }

  function clarifyKeyOf(job) {
    return `${job.id}:${job.clarify.question}`;
  }

  function renderClarify(job) {
    const wrap = $('#clarify-wrap');
    if (!job || !job.clarify || job.clarify.answer) {
      state.clarifyKey = null;
      clearClarify();
      return;
    }

    // 轮询每 1.4s 调一次 renderAll；无条件重建会把用户正在输入的自定义口径
    // 抹掉，并让入场动画反复重放。同一张卡片只渲染一次。
    const key = clarifyKeyOf(job);
    if (state.clarifyKey === key && wrap.firstChild) {
      return;
    }
    state.clarifyKey = key;

    wrap.textContent = '';
    wrap.hidden = false;

    const card = el('div', 'clarify');
    const head = el('div', 'clarify__head');
    head.appendChild(el('span', null, '?'));
    head.appendChild(el('span', null, '需要澄清 · Job 已转 needs_input'));
    card.appendChild(head);

    card.appendChild(el('div', 'clarify__q', job.clarify.question));

    const opts = el('div', 'clarify__opts');
    let picked = null;
    job.clarify.options.forEach((opt, idx) => {
      const b = el('button', 'clarify__opt', opt);
      b.type = 'button';
      b.setAttribute('aria-pressed', 'false');
      if (idx === 0) {
        picked = opt;
        b.setAttribute('aria-pressed', 'true');
      }
      b.addEventListener('click', () => {
        opts.querySelectorAll('.clarify__opt').forEach((x) =>
          x.setAttribute('aria-pressed', 'false'));
        b.setAttribute('aria-pressed', 'true');
        picked = opt;
        custom.value = '';
      });
      opts.appendChild(b);
    });
    card.appendChild(opts);

    const customRow = el('div', 'clarify__custom');
    const custom = el('input', 'input');
    custom.type = 'text';
    custom.placeholder = '或输入自定义口径';
    customRow.appendChild(custom);
    card.appendChild(customRow);

    const foot = el('div', 'clarify__foot');
    const confirm = el('button', 'btn btn--primary', '确认并继续');
    confirm.type = 'button';
    confirm.addEventListener('click', () => {
      answerClarify(job, custom.value.trim() || picked, false);
    });
    const skip = el('button', 'clarify__skip', '跳过此项');
    skip.type = 'button';
    skip.addEventListener('click', () => answerClarify(job, null, true));
    foot.appendChild(confirm);
    foot.appendChild(skip);
    foot.appendChild(el('span', 'clarify__note', '答案写回 inputs，从断点续跑，不重跑已完成步骤'));
    card.appendChild(foot);

    wrap.appendChild(card);
  }

  function clearClarify() {
    const wrap = $('#clarify-wrap');
    wrap.textContent = '';
    wrap.hidden = true;
  }

  function approvalKeyOf(job) {
    return `${job.id}:${job.approval.text}`;
  }

  function renderApproval(job) {
    const wrap = $('#approval-wrap');
    if (!job || !job.approval) {
      state.approvalKey = null;
      clearApproval();
      return;
    }

    // 与澄清卡片同理：轮询重绘会让按钮在鼠标落下前移位，也会重放入场动画。
    const key = approvalKeyOf(job);
    if (state.approvalKey === key && wrap.firstChild) {
      return;
    }
    state.approvalKey = key;

    wrap.textContent = '';
    wrap.hidden = false;

    const card = el('div', 'approval');
    const head = el('div', 'approval__head');
    head.appendChild(el('span', null, '⚠'));
    head.appendChild(el('span', null, '需要审批 · 写入型操作'));
    card.appendChild(head);

    card.appendChild(el('div', 'approval__body', job.approval.text));

    const scope = el('ul', 'approval__scope');
    job.approval.scope.forEach((s) => scope.appendChild(el('li', null, s)));
    card.appendChild(scope);

    const actions = el('div', 'approval__actions');
    actions.style.marginTop = '11px';
    const ok = el('button', 'btn btn--primary', '批准并执行');
    ok.type = 'button';
    ok.addEventListener('click', () => {
      job.approval = null;
      if (job.status === 'waiting') job.status = 'running';
      toast('已批准', '继续执行写入步骤', 'success');
      renderAll();
      runJob(job, { fromStep: job.steps.findIndex((s) => s.status !== 'done') });
    });
    const no = el('button', 'btn btn--ghost', '拒绝');
    no.type = 'button';
    no.addEventListener('click', () => {
      job.approval = null;
      job.status = 'cancelled';
      job.steps.forEach((s) => { if (s.status !== 'done') s.status = 'idle'; });
      toast('已拒绝', '任务已取消，已完成步骤保留', 'warn');
      renderAll();
    });
    actions.appendChild(ok);
    actions.appendChild(no);
    card.appendChild(actions);

    wrap.appendChild(card);
  }

  function clearApproval() {
    const wrap = $('#approval-wrap');
    wrap.textContent = '';
    wrap.hidden = true;
  }

  // ── 渲染：③ 成果栏 ──────────────────────────────────────
  function renderArtifacts() {
    const job = selectedJob();
    const list = $('#artifact-list');
    const versionsPanel = $('#version-panel');
    const editPanel = $('#edit-panel');
    list.textContent = '';

    if (!job || job.artifacts.length === 0) {
      $('#artifact-empty').hidden = false;
      $('#artifact-count').textContent = '0';
      versionsPanel.hidden = true;
      editPanel.hidden = true;
      $('#artifact-empty-text').textContent = job && job.error
        ? `最近一次失败：${job.error}`
        : '任务跑完会自动生成交付物，通过核验后才可下载。';
      return;
    }

    $('#artifact-empty').hidden = true;

    // 每个文件名取最高版本作为当前版本
    const byName = new Map();
    job.artifacts.forEach((a) => {
      const cur = byName.get(a.filename);
      if (!cur || a.version > cur.version) byName.set(a.filename, a);
    });
    const current = [...byName.values()].sort((a, b) => b.at - a.at);

    $('#artifact-count').textContent = String(current.length);

    current.forEach((artifact) => list.appendChild(renderArtifactCard(job, artifact)));

    // 版本历史
    const focus = current[0];
    const history = job.artifacts
      .filter((a) => a.filename === focus.filename)
      .sort((a, b) => b.version - a.version);

    if (history.length > 1) {
      versionsPanel.hidden = false;
      const ul = $('#version-list');
      ul.textContent = '';
      history.forEach((h, idx) => {
        const li = el('li');
        li.dataset.active = String(idx === 0);
        li.appendChild(el('span', 'v-tag', `v${h.version}`));
        li.appendChild(el('span', null, idx === 0 ? '当前' : '已归档'));
        li.appendChild(el('span', 'v-time', timeAgo(h.at)));
        const use = el('button', 'v-use', '查看');
        use.type = 'button';
        use.addEventListener('click', () => {
          state.previewOpen = {};
          state.previewOpen[h.id] = true;
          renderArtifacts();
          toast(`v${h.version}`, '已打开该历史版本预览');
        });
        li.appendChild(use);
        ul.appendChild(li);
      });
    } else {
      versionsPanel.hidden = true;
    }

    editPanel.hidden = !(focus && focus.verification === 'passed');
    if (focus && focus.verification === 'passed') {
      const sel = state.picked;
      const label = sel
        ? `已选 ${describePicked(sel, focus)}`
        : '在上方预览中选中区域或单元格（点一下选一格，Shift 点选矩形）';
      $('#edit-selection').textContent = label;
      $('#edit-run').disabled = !sel || !$('#edit-opinion').value.trim();
      $('#parse-count').textContent = String(state.parseCount);
    }
  }

  function describePicked(sel, artifact) {
    const r1 = Math.min(sel.anchor[0], sel.focus[0]);
    const r2 = Math.max(sel.anchor[0], sel.focus[0]);
    const c1 = Math.min(sel.anchor[1], sel.focus[1]);
    const c2 = Math.max(sel.anchor[1], sel.focus[1]);
    const colName = (i) => String.fromCharCode(65 + i);
    const cols = colName(c1) + (c2 > c1 ? ':' + colName(c2) : '');
    return `${cols}${r1 + 1}:${colName(c2)}${r2 + 1}（${c2 - c1 + 1} 列 × ${r2 - r1 + 1} 行）`;
  }

  function renderArtifactCard(job, artifact) {
    const card = el('div', 'artifact');
    const isCurrent = artifact.version === Math.max(
      ...job.artifacts.filter((a) => a.filename === artifact.filename).map((a) => a.version));
    if (isCurrent) card.classList.add('artifact--current');

    const head = el('div', 'artifact__head');
    const name = el('div', 'artifact__name');
    name.appendChild(el('span', null, fileIcon(artifact.type)));
    name.appendChild(el('span', null, artifact.filename));
    head.appendChild(name);

    const meta = el('div', 'artifact__meta');
    meta.appendChild(el('span', null, fmtSize(artifact.size)));
    meta.appendChild(el('span', null, `v${artifact.version}`));

    const verify = el('span', 'verify');
    verify.dataset.state = artifact.verification;
    verify.textContent = artifact.verification === 'passed'
      ? '● SHA-256 一致'
      : artifact.verification === 'pending'
        ? '◐ 核验中'
        : '✕ 核验失败';
    meta.appendChild(verify);

    const code = el('code', null, artifact.sha256.slice(0, 12));
    code.title = artifact.sha256;
    meta.appendChild(code);
    meta.appendChild(el('span', null, timeAgo(artifact.at)));
    head.appendChild(meta);
    card.appendChild(head);

    const actions = el('div', 'artifact__actions');

    const preview = el('button', 'btn btn--ghost btn--sm', 
      state.previewOpen[artifact.id] ? '收起预览' : '预览');
    preview.type = 'button';
    preview.addEventListener('click', () => {
      state.previewOpen[artifact.id] = !state.previewOpen[artifact.id];
      renderArtifacts();
    });
    actions.appendChild(preview);

    const download = el('button', 'btn btn--primary btn--sm', '下载');
    download.type = 'button';
    const passed = artifact.verification === 'passed';
    download.disabled = !passed;
    download.title = passed ? '下载该版本' : '核验未通过，不可下载';
    download.addEventListener('click', () => {
      if (!passed) return;
      toast('已开始下载', `${artifact.filename} · v${artifact.version} · SHA 校验通过`, 'success');
    });
    actions.appendChild(download);

    const versionBtn = el('button', 'btn btn--ghost btn--sm', '版本');
    versionBtn.type = 'button';
    versionBtn.addEventListener('click', () => {
      const panel = $('#version-panel');
      panel.hidden = !panel.hidden;
      if (!panel.hidden) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
    actions.appendChild(versionBtn);

    card.appendChild(actions);

    // 预览
    if (state.previewOpen[artifact.id] && artifact.columns) {
      const previewBox = el('div', 'preview');
      const table = el('table');

      const thead = el('thead');
      const hrow = el('tr');
      artifact.columns.forEach((c, ci) => {
        const th = el('th', null, c);
        if (isPicked(artifact, -1, ci)) th.dataset.picked = 'true';
        hrow.appendChild(th);
      });
      thead.appendChild(hrow);
      table.appendChild(thead);

      const tbody = el('tbody');
      artifact.rows.forEach((row, ri) => {
        const tr = el('tr');
        row.forEach((cell, ci) => {
          const td = el('td', null, String(cell));
          if (isPicked(artifact, ri, ci)) td.dataset.picked = 'true';
          td.addEventListener('click', (event) => pickCell(artifact, ri, ci, event.shiftKey));
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);

      previewBox.appendChild(table);
      card.appendChild(previewBox);
    }

    return card;
  }

  function isPicked(artifact, r, c) {
    const sel = state.picked;
    if (!sel || sel.artifactId !== artifact.id) return false;
    const r1 = Math.min(sel.anchor[0], sel.focus[0]);
    const r2 = Math.max(sel.anchor[0], sel.focus[0]);
    const c1 = Math.min(sel.anchor[1], sel.focus[1]);
    const c2 = Math.max(sel.anchor[1], sel.focus[1]);
    return r >= r1 && r <= r2 && c >= c1 && c <= c2;
  }

  function pickCell(artifact, r, c, extend) {
    if (!state.picked || state.picked.artifactId !== artifact.id || !extend) {
      state.picked = { artifactId: artifact.id, anchor: [r, c], focus: [r, c] };
    } else {
      state.picked.focus = [r, c];
    }
    renderArtifacts();
  }

  function fileIcon(type) {
    return { xlsx: '▦', docx: '▤', pptx: '▧', pdf: '▥' }[type] || '▢';
  }

  // ── 执行模拟 ────────────────────────────────────────────
  function schedule(fn, ms) {
    const id = setTimeout(() => {
      state.timers.delete(id);
      fn();
    }, ms);
    state.timers.add(id);
    return id;
  }

  function runJob(job, opts) {
    if (state.running.has(job.id)) return;
    state.running.add(job.id);

    const from = opts && typeof opts.fromStep === 'number'
      ? opts.fromStep
      : job.steps.findIndex((s) => s.status !== 'done');
    const start = from < 0 ? 0 : from;

    if (job.status === 'draft' || job.status === 'queued' || job.status === 'failed') {
      job.status = 'running';
      job.error = null;
    }

    let index = start;
    renderAll();

    const advance = () => {
      if (index >= job.steps.length) {
        job.status = 'succeeded';
        state.running.delete(job.id);
        finishJob(job);
        renderAll();
        return;
      }

      const step = job.steps[index];
      step.status = 'running';
      step.progress = 0;
      renderAll();

      const tick = () => {
        step.progress = Math.min(100, step.progress + 20 + Math.random() * 20);
        if (step.progress >= 100) {
          step.progress = 100;
          step.status = 'done';
          step.detail = stepDetail(job, step);
          if (step.name.includes('解析附件')) {
            state.parseCount += 1;
            step.detail += ` · 解析计数 ${state.parseCount}`;
          }
          index += 1;
          renderAll();
          schedule(advance, 220);
          return;
        }
        renderAll();
        schedule(tick, 260);
      };
      schedule(tick, 220);
    };

    schedule(advance, 260);
  }

  function stepDetail(job, step) {
    const map = {
      '解析附件': '读取 128 行 · 4 个工作表',
      '识别表结构': 'document_type=人天分配表',
      '按月裁剪': `目标月份 ${(job.inputs && job.inputs.month) || '2026-11'} · 匹配 2 行`,
      '生成 XLSX': '产出人天分配 / 处理说明两个工作表',
      '核验': 'reopen ✓ 行数 ✓ SHA-256 ✓ 口径说明 ✓',
      '读取报价单': '报价单-2026Q4.xlsx · 12 个资产',
      '匹配费率表': 'team_members.daily_cost 命中 5 人',
      '计算费用': '人工 + 管理 + 税 三段合计',
      '生成报价单': '报价单模板填充完成',
      '拉取在途任务': '12 条在途',
      '比对进度': '延期 2 条',
      '生成巡检报告': '巡检报告.docx',
      '推送企业微信': '已推送 3 人',
      '汇总实际人天': '实际 168 人天',
      '比对报价': '报价 180 人天',
      '计算毛利': '毛利率 21.4%',
      '生成复盘报告': '复盘报告.docx',
    };
    return map[step.name] || '完成';
  }

  function finishJob(job) {
    if (job.artifacts.length && job.type === 'S0') {
      return;
    }
    if (job.type === 'S0') {
      job.artifacts.push(makeMonthlyArtifact(job));
    } else if (job.type === 'S1') {
      job.artifacts.push({
        id: uid('a'), filename: '报价单-腾讯-2026Q4.xlsx', type: 'xlsx',
        size: 31240, sha256: fakeSha(job.id + 'quote'), version: 1,
        verification: 'passed', at: Date.now(), stepId: 's4',
        columns: ['资产名称', '数量', '单价', '总价'],
        rows: [['主角模型', 2, 4500, 9000], ['场景贴图', 6, 1800, 10800],
               ['UI 图标', 24, 320, 7680]],
      });
    } else if (job.type === 'S2') {
      job.artifacts.push({
        id: uid('a'), filename: '巡检报告.xlsx', type: 'xlsx',
        size: 14220, sha256: fakeSha(job.id + 'inspect'), version: 1,
        verification: 'passed', at: Date.now(), stepId: 's3',
        columns: ['项目', '在途', '延期'],
        rows: [['腾讯-王者外装', 5, 1], ['米哈游-星穹场景', 7, 1]],
      });
    } else if (job.type === 'S3') {
      job.artifacts.push({
        id: uid('a'), filename: '复盘报告.docx', type: 'docx',
        size: 88240, sha256: fakeSha(job.id + 'retro'), version: 1,
        verification: 'passed', at: Date.now(), stepId: 's4',
        columns: null, rows: null,
      });
    }
    toast('任务完成', `${job.name} · 产出 ${job.artifacts.length} 个文件`, 'success');
  }

  function makeMonthlyArtifact(job) {
    const month = (job.inputs && job.inputs.month) || '2026-11';
    const existing = job.artifacts.filter((a) => a.filename.startsWith('角色组'));
    const version = existing.length
      ? Math.max(...existing.map((a) => a.version)) + 1
      : 1;
    return {
      id: uid('a'),
      filename: `角色组-${month.slice(5)}月.xlsx`,
      type: 'xlsx',
      size: 24678 + version * 37,
      sha256: fakeSha(job.id + version),
      version,
      verification: 'passed',
      at: Date.now(),
      stepId: 's4',
      columns: ['任务', '月份', '商务人天', '已分配人天'],
      rows: [
        ['麦迪', month, 18, 18],
        ['裸模优化', month, 15, 0],
        ['场景贴图', month, 12, 4],
      ],
    };
  }

  function answerClarify(job, answer, skipped) {
    job.clarify.answer = answer || '（跳过）';
    job.inputs = Object.assign({}, job.inputs, { clarify: job.clarify.answer });

    const waiting = job.steps.findIndex((s) => s.status === 'waiting');
    const runningIdx = job.steps.findIndex((s) => s.status === 'running');
    const resumeAt = waiting >= 0 ? waiting : runningIdx >= 0 ? runningIdx : 0;

    // 已完成步骤保持 done，不重跑
    job.status = 'running';
    toast(skipped ? '已跳过' : '已记录口径', `答案「${job.clarify.answer}」，从断点续跑`, skipped ? 'warn' : 'success');
    renderAll();
    runJob(job, { fromStep: resumeAt });
  }

  function createJob(opts) {
    const type = opts.type || state.newJobType;
    const template = TYPE_TEMPLATES[type];
    const job = {
      id: uid('j'),
      name: opts.name || template.label,
      projectId: opts.projectId || state.projectId || state.projects[0].id,
      type,
      trigger: opts.trigger || (opts.scheduled ? 'scheduled' : 'manual'),
      status: opts.scheduled ? 'queued' : 'queued',
      createdAt: Date.now(),
      nextRunAt: opts.scheduled ? tomorrowAt(6, 0) : null,
      inputs: opts.inputs || {},
      error: null,
      steps: template.steps.map((name, i) => ({
        id: `s${i + 1}`, name, status: 'idle', progress: 0, detail: '', log: '',
      })),
      clarify: null,
      approval: null,
      artifacts: [],
      runs: [],
    };

    state.jobs.unshift(job);
    state.selectedJobId = job.id;
    state.expandedGroups[groupOf(job)] = true;
    state.picked = null;
    renderAll();

    toast('任务已创建', `${job.name} · ${template.label}`, 'success');

    if (opts.run !== false) {
      runJob(job);
    }
    return job;
  }

  function tomorrowAt(h, m) {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(h, m, 0, 0);
    return d.getTime();
  }

  function retryFromFailure() {
    const job = selectedJob();
    if (!job || job.status !== 'failed') return;
    const at = job.steps.findIndex((s) => s.status === 'failed');
    const resume = at >= 0 ? at : job.steps.findIndex((s) => s.status !== 'done');
    // 失败步骤本身重跑；已完成步骤保留
    job.steps.forEach((s, i) => {
      if (i >= resume) { s.status = 'idle'; s.progress = 0; s.detail = ''; }
    });
    job.error = null;
    toast('从失败处重试', `保留 ${resume} 个已完成步骤`, 'info');
    runJob(job, { fromStep: resume });
  }

  // ── 事件绑定 ────────────────────────────────────────────
  function selectJob(id) {
    state.selectedJobId = id;
    state.picked = null;
    state.previewOpen = {};
    if (window.matchMedia('(max-width: 899px)').matches) {
      document.documentElement.dataset.drawer = '';
      $('#rail-scrim').hidden = true;
    }
    renderAll();
  }

  function renderAll() {
    renderRail();
    renderProcess();
    renderArtifacts();
  }

  function wire() {
    // 项目选择器
    $('#project-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      const menu = $('#project-menu');
      if (menu.hidden) openProjectMenu(); else closeProjectMenu();
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.project-select')) closeProjectMenu();
    });

    // 任务栏折叠（桌面）
    $('#rail-toggle').addEventListener('click', () => {
      const root = document.documentElement;
      const collapsed = root.dataset.rail === 'collapsed';
      root.dataset.rail = collapsed ? 'expanded' : 'collapsed';
      $('#rail-toggle').setAttribute('aria-expanded', String(collapsed));
      toast(collapsed ? '任务栏已展开' : '任务栏已折叠', null);
    });

    // 移动端抽屉
    $('#rail-scrim').addEventListener('click', () => {
      document.documentElement.dataset.drawer = '';
      $('#rail-scrim').hidden = true;
    });

    // 主题
    $('#theme-toggle').addEventListener('click', () => {
      const dark = document.documentElement.dataset.theme === 'dark';
      document.documentElement.dataset.theme = dark ? 'light' : 'dark';
      toast(dark ? '已切换到浅色' : '已切换到深色', null);
    });

    // 通知
    $('#notify-toggle').addEventListener('click', () => {
      $('#notify-dot').hidden = true;
      toast('每日巡检', '计划任务将于次日 06:00 触发', 'info');
      toast('待澄清', '11 月人天汇总等待口径确认', 'warn');
    });

    // 新建任务弹窗
    $('#new-job-btn').addEventListener('click', openNewJobModal);
    $('#new-job-modal').addEventListener('click', (e) => {
      if (e.target.dataset.close) closeNewJobModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeNewJobModal();
        closeProjectMenu();
      }
    });

    renderTypeChips();

    // 拖放
    const drop = $('#drop-zone');
    drop.addEventListener('click', () => $('#file-input').click());
    drop.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        $('#file-input').click();
      }
    });
    ['dragenter', 'dragover'].forEach((ev) =>
      drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.dataset.over = 'true';
      }));
    ['dragleave', 'drop'].forEach((ev) =>
      drop.addEventListener(ev, () => { drop.dataset.over = 'false'; }));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      addFiles([...e.dataTransfer.files]);
    });
    $('#file-input').addEventListener('change', (e) => {
      addFiles([...e.target.files]);
      e.target.value = '';
    });

    $('#job-create').addEventListener('click', submitNewJob);

    // 输入区
    const input = $('#composer-input');
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(132, input.scrollHeight) + 'px';
      $('#edit-run').disabled = !state.picked || !$('#edit-opinion').value.trim();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
    $('#composer-send').addEventListener('click', sendMessage);
    $('#composer-attach').addEventListener('click', () => {
      toast('添加附件', '原型中请在「新建任务」里拖入文件', 'info');
    });

    // 过程栏操作
    $('#btn-retry').addEventListener('click', retryFromFailure);
    $('#btn-save-job').addEventListener('click', () => {
      const job = selectedJob();
      if (!job) return;
      job.trigger = 'manual';
      toast('已另存为任务', `${job.name} 已进入任务列表`, 'success');
      renderAll();
    });

    // 定点改
    $('#edit-opinion').addEventListener('input', () => {
      $('#edit-run').disabled = !state.picked || !$('#edit-opinion').value.trim();
    });
    $('#edit-run').addEventListener('click', runTargetedEdit);

    // 移动端底部切换
    document.querySelectorAll('[data-mobile-target]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.mobileTarget;
        const root = document.documentElement;
        if (root.dataset.drawer === target) {
          root.dataset.drawer = '';
          $('#rail-scrim').hidden = true;
          return;
        }
        root.dataset.drawer = target;
        $('#rail-scrim').hidden = target !== 'rail';
      });
    });
  }

  function renderTypeChips() {
    const box = $('#type-chips');
    box.textContent = '';
    Object.entries(TYPE_TEMPLATES).forEach(([key, t]) => {
      const chip = el('button', 'chip', t.label);
      chip.type = 'button';
      chip.setAttribute('role', 'radio');
      chip.setAttribute('aria-checked', String(state.newJobType === key));
      chip.addEventListener('click', () => {
        state.newJobType = key;
        renderTypeChips();
        renderParams();
        if (!$('#job-name').value.trim()) {
          $('#job-name').placeholder = `例如：${t.label.replace(/^S\d\s/, '')}`;
        }
      });
      box.appendChild(chip);
    });
    renderParams();
  }

  function renderParams() {
    const box = $('#job-params');
    box.textContent = '';
    TYPE_TEMPLATES[state.newJobType].params.forEach((p) => {
      const wrap = el('div');
      const label = el('label', null, p.label);
      label.setAttribute('for', `param-${p.key}`);
      const input = el('input', 'input');
      input.id = `param-${p.key}`;
      input.dataset.paramKey = p.key;
      input.type = 'text';
      input.value = p.value;
      wrap.appendChild(label);
      wrap.appendChild(input);
      box.appendChild(wrap);
    });
  }

  function openNewJobModal() {
    const select = $('#job-project');
    select.textContent = '';
    state.projects.forEach((p) => {
      const opt = el('option', null, p.name);
      opt.value = p.id;
      if (p.id === state.projectId) opt.selected = true;
      select.appendChild(opt);
    });
    $('#job-name').value = '';
    $('#job-scheduled').checked = false;
    state.fileDrafts = [];
    renderFileList();
    $('#new-job-modal').hidden = false;
    setTimeout(() => $('#job-name').focus(), 30);
  }

  function closeNewJobModal() {
    $('#new-job-modal').hidden = true;
  }

  function addFiles(files) {
    files.forEach((f) => {
      const name = f.name || `附件-${state.fileDrafts.length + 1}.xlsx`;
      if (!state.fileDrafts.some((x) => x.name === name)) {
        state.fileDrafts.push({ name, size: f.size || 18432 + Math.floor(Math.random() * 9000) });
      }
    });
    renderFileList();
  }

  function renderFileList() {
    const list = $('#file-list');
    list.textContent = '';
    state.fileDrafts.forEach((f, i) => {
      const li = el('li');
      li.appendChild(el('span', null, '▦'));
      li.appendChild(el('span', null, f.name));
      li.appendChild(el('span', null, fmtSize(f.size)));
      const rm = el('button', 'f-remove', '✕');
      rm.type = 'button';
      rm.title = '移除';
      rm.addEventListener('click', () => {
        state.fileDrafts.splice(i, 1);
        renderFileList();
      });
      li.appendChild(rm);
      list.appendChild(li);
    });
  }

  function submitNewJob() {
    const name = $('#job-name').value.trim();
    if (!name) {
      toast('缺少任务名称', '请填写任务名称后再创建', 'error');
      $('#job-name').focus();
      return;
    }
    const inputs = {};
    document.querySelectorAll('#job-params [data-param-key]').forEach((input) => {
      inputs[input.dataset.paramKey] = input.value.trim();
    });
    if (state.fileDrafts.length) {
      inputs.attachments = state.fileDrafts.map((f) => f.name);
    }

    const scheduled = $('#job-scheduled').checked;
    closeNewJobModal();

    const job = createJob({
      name,
      type: state.newJobType,
      projectId: $('#job-project').value,
      inputs,
      scheduled,
      run: !scheduled,
    });

    if (scheduled) {
      toast('已设为计划任务', `${job.name} · 每天 06:00 触发`, 'info');
    }
  }

  function sendMessage() {
    const input = $('#composer-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    input.style.height = 'auto';

    // F2：从对话升级为任务
    const job = createJob({
      name: text.length > 18 ? text.slice(0, 18) + '…' : text,
      type: 'S0',
      trigger: 'from_message',
      inputs: { request: text },
      run: true,
    });

    toast('已从对话发起任务', '可在任务详情中「另存为任务」固化', 'info');
    return job;
  }

  function runTargetedEdit() {
    const job = selectedJob();
    if (!job || !state.picked) return;
    const artifact = job.artifacts.find((a) => a.id === state.picked.artifactId);
    if (!artifact) return;

    const opinion = $('#edit-opinion').value.trim();
    if (!opinion) return;

    const targetStep = job.steps.find((s) => s.id === artifact.stepId)
      || job.steps[job.steps.length - 2];

    const before = state.parseCount;

    // 只重跑对应 step 及其下游
    const at = job.steps.indexOf(targetStep);
    job.steps.forEach((s, i) => {
      if (i >= at) { s.status = 'idle'; s.progress = 0; }
    });
    job.status = 'running';

    const newVersion = artifact.version + 1;
    const newArtifact = Object.assign({}, artifact, {
      id: uid('a'),
      version: newVersion,
      sha256: fakeSha(artifact.id + newVersion + opinion),
      size: artifact.size + 41,
      at: Date.now(),
      verification: 'pending',
      rows: applyOpinion(artifact.rows, opinion),
    });

    $('#edit-opinion').value = '';
    state.picked = null;

    toast('定点改已提交', `只重跑「${targetStep.name}」，附件不重新解析`, 'info');
    renderAll();

    schedule(() => {
      job.steps.forEach((s, i) => {
        if (i >= at) { s.status = 'done'; s.progress = 100; s.detail = stepDetail(job, s); }
      });
      job.status = 'succeeded';
      newArtifact.verification = 'passed';
      job.artifacts.push(newArtifact);

      const after = state.parseCount;
      if (after !== before) {
        toast('异常', '定点改不应改变附件解析计数', 'error');
      }
      renderAll();
      toast('定点改完成', `${newArtifact.filename} 已追加 v${newVersion}`, 'success');
    }, 900);
  }

  function applyOpinion(rows, opinion) {
    if (!rows) return rows;
    // 演示：意见里提到「万元」就把数字列换算
    if (opinion.includes('万元')) {
      return rows.map((r) => r.map((cell, i) =>
        i >= 2 && typeof cell === 'number' ? Number((cell / 10000).toFixed(2)) : cell));
    }
    return rows;
  }

  // ── 启动 ────────────────────────────────────────────────
  function boot() {
    document.documentElement.dataset.theme = 'light';
    document.documentElement.dataset.rail = 'expanded';
    document.documentElement.dataset.drawer = '';

    seed();
    renderProjectMenu();
    renderAll();
    wire();
    $('#parse-count').textContent = '0';

    // j2 的进行中步骤持续推进，让 ② 栏保持活的
    const live = state.jobs.find((j) => j.id === 'j2');
    if (live) {
      const step = live.steps[0];
      const pump = () => {
        if (step.status !== 'running') return;
        step.progress = Math.min(100, step.progress + 12);
        if (step.progress >= 100) {
          step.status = 'done';
          step.detail = '读取 12 个资产';
          live.status = 'needs_input';
          live.approval = null;
          live.clarify = {
            question: '这份报价单里的「主角模型」是否按 3D 建模计价？列表里标的是「原画」。',
            options: ['按 3D 建模', '按原画', '拆成两项'],
            answer: null,
          };
          live.steps[1].status = 'waiting';
          toast('需要澄清', '腾讯报价核对等待确认资产类型', 'warn');
          renderAll();
          return;
        }
        renderProcess();
        schedule(pump, 1400);
      };
      schedule(pump, 1600);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
