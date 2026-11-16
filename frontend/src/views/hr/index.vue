<template>
  <section class="page" data-module="hr">
    <header class="page-head">
      <div>
        <h2>人力效能评估管理</h2>
        <p class="page-desc">
          读取员工任职区间、工时与部门产出，按自然日切分有效期并折算投入，生成部门人效偏差数据集；
          人效分母为零、未上报工时、产出未上报与真实零产出分别标记。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportBatch">导出当前批次明细</button>
        <button class="btn primary" type="button" @click="runCalculation" :disabled="running">
          {{ running ? '核算中…' : '执行人效核算' }}
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <nav class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <p v-if="message" class="notice" :class="{ warn: messageWarn }">{{ message }}</p>

    <!-- ============================ 核算结果 ============================ -->
    <template v-if="activeTab === 'result'">
      <form class="filter-bar" @submit.prevent="runCalculation">
        <label class="filter-item">
          <span>核算开始日期</span>
          <input v-model="period.start" type="date" />
        </label>
        <label class="filter-item">
          <span>核算结束日期</span>
          <input v-model="period.end" type="date" />
        </label>
        <label class="filter-item">
          <span>切片窗口开始（可选）</span>
          <input v-model="period.windowStart" type="date" />
        </label>
        <label class="filter-item">
          <span>切片窗口结束（可选）</span>
          <input v-model="period.windowEnd" type="date" />
        </label>
        <button class="btn" type="submit" :disabled="running">按此周期核算</button>
      </form>

      <template v-if="batch">
        <div class="conclusion" :class="{ blocked: !batch.可形成完整评估 }">
          <strong>{{ batch.批次号 }}</strong>
          <span>{{ batch.完整性结论 }}</span>
          <span v-if="batch.headcount">
            覆盖 {{ batch.headcount.覆盖员工数 }} 名员工 · 折合投入 {{ batch.headcount.折合投入人数 }} 人 ·
            投入工时 {{ batch.headcount.投入工时合计 }}
          </span>
          <span v-if="!batch.区间一致" class="warn-text">
            切片窗口与核算周期不一致，聚合区间：{{ batch.聚合区间.start }}~{{ batch.聚合区间.end }}
          </span>
          <span v-if="batch.closure?.无任职工时员工日数" class="warn-text">
            有 {{ batch.closure.无任职工时员工日数 }} 个员工日报了工时却无任职归属，
            共 {{ batch.closure.无任职归属工时 }} 工时未摊入部门
          </span>
        </div>

        <table class="data-table">
          <thead>
            <tr>
              <th>部门</th>
              <th>核算状态</th>
              <th>完整性</th>
              <th>人数</th>
              <th>投入工时</th>
              <th>折合人数</th>
              <th>产出数量</th>
              <th>人效</th>
              <th>行业均值</th>
              <th>偏差</th>
              <th>偏差结论</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in batch.departments" :key="row.部门">
              <td>{{ row.部门 }}</td>
              <td>{{ row.核算状态 }}</td>
              <td>
                <span class="badge" :class="integrityClass(row.评估完整性)">{{ row.评估完整性 }}</span>
              </td>
              <td>{{ row.人数 }}</td>
              <td>{{ row.投入工时 }}</td>
              <td>{{ row.折合人数 }}</td>
              <td>{{ row.产出数量 }} {{ row.产出单位 }}</td>
              <td>{{ row.人效 === null ? '—' : `${row.人效} ${row.人效单位}` }}</td>
              <td>
                <template v-if="row.行业均值 !== null">
                  {{ row.行业均值 }} {{ row.行业均值单位 }}
                  <small class="muted">{{ row.行业均值适用周期 }}</small>
                </template>
                <span v-else class="muted">{{ row.行业均值匹配状态 }}</span>
              </td>
              <td>{{ row.偏差 === null ? '—' : row.偏差 }}</td>
              <td>{{ row.偏差结论 }}</td>
              <td>
                <span v-if="!row.明细说明.length" class="muted">—</span>
                <ul v-else class="note-list">
                  <li v-for="note in row.明细说明" :key="note">{{ note }}</li>
                </ul>
              </td>
            </tr>
            <tr v-if="!batch.departments.length">
              <td colspan="12" class="empty-state">当前周期没有可核算的部门数据</td>
            </tr>
          </tbody>
        </table>

        <div class="grid-2">
          <section class="panel">
            <h3>投入闭合核对</h3>
            <dl class="kv">
              <template v-for="(value, key) in closureRows" :key="key">
                <dt>{{ key }}</dt>
                <dd :class="{ 'warn-text': closureWarn(String(key), value) }">{{ value }}</dd>
              </template>
            </dl>
          </section>

          <section class="panel">
            <h3>区间交集</h3>
            <table class="mini-table">
              <thead>
                <tr><th>员工</th><th>部门</th><th>交集</th><th>比例合计</th><th>判定</th></tr>
              </thead>
              <tbody>
                <tr v-for="item in batch.interval_intersections" :key="`${item.员工号}-${item.交集开始}-${item.部门A}-${item.部门B}`">
                  <td>{{ item.员工号 }} {{ item.姓名 }}</td>
                  <td>{{ item.部门A }} / {{ item.部门B }}</td>
                  <td>{{ item.交集开始 }}~{{ item.交集结束 }}（{{ item.交集天数 }} 天）</td>
                  <td>{{ item.比例合计 }}</td>
                  <td>{{ item.判定 }}</td>
                </tr>
                <tr v-if="!batch.interval_intersections.length">
                  <td colspan="5" class="empty-state">没有并行任职</td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>

        <section class="panel">
          <h3>被隔离的矛盾记录</h3>
          <table class="mini-table">
            <thead>
              <tr><th>类型</th><th>对象</th><th>区间</th><th>天数</th><th>涉及部门</th><th>隔离工时</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="(item, index) in batch.conflicts" :key="index">
                <td>{{ item.类型 }}</td>
                <td>{{ item.员工号 ? `${item.员工号} ${item.姓名 || ''}` : item.部门 }}</td>
                <td>{{ item.开始日期 || item.日期 }}<template v-if="item.结束日期 && item.结束日期 !== item.开始日期">~{{ item.结束日期 }}</template></td>
                <td>{{ item.天数 ?? '—' }}</td>
                <td>{{ (item.涉及部门 || []).join('、') || '—' }}</td>
                <td>{{ item.隔离工时 ?? '—' }}</td>
                <td>{{ item.说明 }}</td>
              </tr>
              <tr v-if="!batch.conflicts.length">
                <td colspan="7" class="empty-state">没有需要隔离的矛盾记录</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="panel">
          <h3>核算问题清单</h3>
          <table class="mini-table">
            <thead><tr><th>级别</th><th>类型</th><th>部门</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="(item, index) in batch.issues" :key="index">
                <td><span class="badge" :class="item.级别 === '阻断' ? 'bad' : 'warn'">{{ item.级别 }}</span></td>
                <td>{{ item.code }}</td>
                <td>{{ item.部门 || '—' }}</td>
                <td>{{ item.说明 }}</td>
              </tr>
              <tr v-if="!batch.issues.length">
                <td colspan="4" class="empty-state">本次核算没有发现问题</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="panel">
          <h3>
            原始依据快照
            <button class="link" type="button" @click="loadSource">查看落库批次引用的台账</button>
          </h3>
          <p v-if="!source" class="muted">已落库的人效批次保存自己的台账快照，核对时不必依赖当前台账。</p>
          <template v-else>
            <dl class="kv">
              <dt>批次号</dt><dd>{{ source.批次号 }}</dd>
              <dt>依据摘要</dt><dd>{{ source.依据摘要 }}</dd>
              <dt>记录数</dt>
              <dd>
                任职 {{ source.records.assignments }} · 工时 {{ source.records.timesheets }} ·
                产出 {{ source.records.outputs }} · 行业均值 {{ source.records.industry }}
              </dd>
            </dl>
          </template>
        </section>

        <section class="panel">
          <h3>投入折算口径</h3>
          <dl class="kv">
            <template v-for="(value, key) in batch.conversion" :key="key">
              <dt>{{ key }}</dt>
              <dd>{{ value }}</dd>
            </template>
          </dl>
        </section>
      </template>
      <p v-else class="empty-state panel">尚未核算。请选择周期后执行一次人效核算。</p>
    </template>

    <!-- ============================ 日期切片 ============================ -->
    <template v-else-if="activeTab === 'slices'">
      <form class="filter-bar" @submit.prevent="loadSlices(1)">
        <label class="filter-item">
          <span>员工号</span>
          <input v-model="sliceFilters.employee" placeholder="如 E001" />
        </label>
        <label class="filter-item">
          <span>部门</span>
          <input v-model="sliceFilters.department" placeholder="如 销售一部" />
        </label>
        <label class="filter-item">
          <span>日期</span>
          <input v-model="sliceFilters.day" type="date" />
        </label>
        <label class="filter-item">
          <span>状态</span>
          <select v-model="sliceFilters.status">
            <option value="">全部</option>
            <option value="正常">正常</option>
            <option value="工时未上报">工时未上报</option>
            <option value="区间交叠已折算">区间交叠已折算</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetSliceFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>员工</th>
            <th>部门</th>
            <th>日期</th>
            <th>任职区间</th>
            <th>任职节点</th>
            <th>投入比例</th>
            <th>当日比例合计</th>
            <th>折算系数</th>
            <th>可用工时</th>
            <th>当日报工</th>
            <th>分配工时</th>
            <th>未分配</th>
            <th>归一化差额</th>
            <th>状态</th>
            <th>来源依据</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in slices" :key="row.切片编号">
            <td>{{ row.员工号 }} {{ row.姓名 }}</td>
            <td>{{ row.部门 }}</td>
            <td>{{ row.日期 }}</td>
            <td>{{ row.任职区间 || `${row.任职开始}~${row.任职结束 || '至今'}` }}</td>
            <td>{{ row.任职节点 }}</td>
            <td>{{ row.投入比例 }}</td>
            <td>{{ row.当日比例合计 }}</td>
            <td>{{ row.折算系数 }}</td>
            <td>{{ row.可用工时 ?? '—' }}</td>
            <td>{{ row.员工当日报工时 ?? '—' }}</td>
            <td>{{ row.分配工时 ?? '—' }}</td>
            <td>{{ row.未分配工时 }}</td>
            <td>{{ row.折算差额工时 }}</td>
            <td>{{ row.状态 }}</td>
            <td class="muted">
              任职 {{ row.来源依据?.任职记录 }} · 工时 {{ row.来源依据?.工时记录 ?? '无' }}
            </td>
          </tr>
          <tr v-if="!slices.length">
            <td colspan="15" class="empty-state">没有符合条件的日期切片</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ sliceTotal }} 条切片 · 第 {{ slicePage }} 页</span>
        <span class="pager">
          <button class="link" type="button" :disabled="slicePage <= 1" @click="loadSlices(slicePage - 1)">上一页</button>
          <button class="link" type="button" :disabled="slicePage * sliceSize >= sliceTotal" @click="loadSlices(slicePage + 1)">下一页</button>
        </span>
      </footer>
    </template>

    <!-- ============================ 人员台账 ============================ -->
    <template v-else-if="activeTab === 'ledger'">
      <section class="panel">
        <h3>员工任职区间</h3>
        <p class="muted">更正任职只改台账；已生成的核算批次保留原快照，重新核算才使用新值。</p>
        <table class="data-table">
          <thead>
            <tr><th>记录</th><th>员工</th><th>部门</th><th>任职开始</th><th>任职结束</th><th>投入比例</th><th>变更次数</th><th>更新时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in assignments" :key="row.id">
              <td>{{ row.id }}</td>
              <td>{{ row.员工号 }} {{ row.姓名 }}</td>
              <td>{{ row.部门 }}</td>
              <td>{{ row.任职开始 }}</td>
              <td>{{ row.任职结束 || '至今' }}</td>
              <td>{{ row.投入比例 }}</td>
              <td>{{ row.变更次数 ?? 0 }}</td>
              <td class="muted">{{ row.更新时间 ?? '—' }}</td>
              <td class="row-actions">
                <button class="link" type="button" @click="startEdit(row)">更正</button>
              </td>
            </tr>
            <tr v-if="!assignments.length">
              <td colspan="9" class="empty-state">暂无任职记录</td>
            </tr>
          </tbody>
        </table>

        <form v-if="editing" class="inline-form" @submit.prevent="saveAssignment">
          <strong>更正 {{ editing.id }}（{{ editing.员工号 }} {{ editing.姓名 }}）</strong>
          <label class="filter-item"><span>部门</span><input v-model="editing.部门" /></label>
          <label class="filter-item"><span>任职开始</span><input v-model="editing.任职开始" type="date" /></label>
          <label class="filter-item"><span>任职结束</span><input v-model="editing.任职结束" type="date" /></label>
          <label class="filter-item"><span>投入比例</span><input v-model.number="editing.投入比例" type="number" step="0.1" min="0.1" max="1" /></label>
          <button class="btn primary" type="submit">保存更正</button>
          <button class="btn ghost" type="button" @click="editing = null">取消</button>
        </form>
      </section>

      <div class="grid-2">
        <section class="panel">
          <h3>补报工时</h3>
          <p class="muted">同一员工同一天只允许一条工时，重复补报会被拒绝，避免继续核算时重复累计。</p>
          <form class="inline-form" @submit.prevent="submitTimesheet">
            <label class="filter-item"><span>员工号</span><input v-model="timesheetForm.员工号" placeholder="E006" /></label>
            <label class="filter-item"><span>日期</span><input v-model="timesheetForm.日期" type="date" /></label>
            <label class="filter-item"><span>可用工时</span><input v-model.number="timesheetForm.可用工时" type="number" step="0.5" /></label>
            <label class="filter-item"><span>实际上报工时</span><input v-model.number="timesheetForm.实际上报工时" type="number" step="0.5" /></label>
            <button class="btn" type="submit">补报</button>
          </form>
        </section>

        <section class="panel">
          <h3>补报部门产出</h3>
          <p class="muted">产出与投入使用同一日期边界；数量填 0 表示真实零产出，不填记录表示产出未上报。</p>
          <form class="inline-form" @submit.prevent="submitOutput">
            <label class="filter-item"><span>部门</span><input v-model="outputForm.部门" placeholder="运营部" /></label>
            <label class="filter-item"><span>日期</span><input v-model="outputForm.日期" type="date" /></label>
            <label class="filter-item"><span>数量</span><input v-model.number="outputForm.数量" type="number" step="1" min="0" /></label>
            <label class="filter-item"><span>单位</span><input v-model="outputForm.单位" placeholder="件" /></label>
            <button class="btn" type="submit">补报</button>
          </form>
        </section>
      </div>

      <div class="grid-2">
        <section class="panel">
          <h3>工时明细（最近 20 条）</h3>
          <table class="mini-table">
            <thead><tr><th>记录</th><th>员工</th><th>日期</th><th>可用</th><th>上报</th><th>补报</th></tr></thead>
            <tbody>
              <tr v-for="row in recentTimesheets" :key="row.id">
                <td>{{ row.id }}</td><td>{{ row.员工号 }}</td><td>{{ row.日期 }}</td>
                <td>{{ row.可用工时 }}</td><td>{{ row.实际上报工时 }}</td><td>{{ row.补报 ? '是' : '否' }}</td>
              </tr>
            </tbody>
          </table>
        </section>
        <section class="panel">
          <h3>台账变更日志</h3>
          <table class="mini-table">
            <thead><tr><th>时间</th><th>类型</th><th>对象</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="row in events" :key="row.id">
                <td>{{ row.发生时间 }}</td>
                <td>{{ row.类型 }}</td>
                <td>{{ row.对象 }}</td>
                <td>{{ row.说明 }}</td>
              </tr>
              <tr v-if="!events.length">
                <td colspan="4" class="empty-state">台账尚未发生更正或补报</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <section class="panel">
        <h3>行业均值口径</h3>
        <table class="mini-table">
          <thead><tr><th>部门</th><th>行业均值</th><th>单位</th><th>适用周期</th><th>口径</th><th>来源</th></tr></thead>
          <tbody>
            <tr v-for="row in industryAverages" :key="row.id">
              <td>{{ row.部门 }}</td>
              <td>{{ row.行业均值 }}</td>
              <td>{{ row.单位 }}</td>
              <td>{{ row.适用开始 }}~{{ row.适用结束 }}</td>
              <td>{{ row.口径 }}</td>
              <td>{{ row.来源 }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- ============================ 核算批次 ============================ -->
    <template v-else>
      <table class="data-table">
        <thead>
          <tr>
            <th>批次号</th>
            <th>核算周期</th>
            <th>聚合区间</th>
            <th>状态</th>
            <th>完整性结论</th>
            <th>部门数</th>
            <th>切片数</th>
            <th>覆盖人数</th>
            <th>依据摘要</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in batches" :key="row.批次号">
            <td>{{ row.批次号 }}<small v-if="batch?.批次号 === row.批次号" class="muted">（当前）</small></td>
            <td>{{ row.核算周期.start }}~{{ row.核算周期.end }}</td>
            <td>{{ row.聚合区间?.start }}~{{ row.聚合区间?.end }}</td>
            <td>{{ row.状态 }}</td>
            <td>{{ row.完整性结论 }}</td>
            <td>{{ row.部门数 }}</td>
            <td>{{ row.切片数 }}</td>
            <td>{{ row.headcount?.覆盖员工数 ?? '—' }}</td>
            <td class="muted">{{ row.依据摘要 }}</td>
            <td class="muted">{{ row.创建时间 }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openBatch(row.批次号)">查看</button>
            </td>
          </tr>
          <tr v-if="!batches.length">
            <td colspan="11" class="empty-state">尚未生成核算批次</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Batch = Record<string, any>
type Row = Record<string, any>

const tabs = [
  { key: 'result', label: '人效核算' },
  { key: 'slices', label: '人员日期切片' },
  { key: 'ledger', label: '人员台账' },
  { key: 'batches', label: '核算批次' },
]

const activeTab = ref('result')
const running = ref(false)
const message = ref('')
const messageWarn = ref(false)
const period = ref({
  start: '2026-06-01',
  end: '2026-06-30',
  windowStart: '',
  windowEnd: '',
})

const batch = ref<Batch | null>(null)
const batches = ref<Batch[]>([])
const slices = ref<Row[]>([])
const sliceTotal = ref(0)
const slicePage = ref(1)
const sliceSize = 30
const sliceFilters = ref({ employee: '', department: '', day: '', status: '' })

const assignments = ref<Row[]>([])
const recentTimesheets = ref<Row[]>([])
const events = ref<Row[]>([])
const industryAverages = ref<Row[]>([])
const source = ref<Row | null>(null)
const editing = ref<Row | null>(null)
const timesheetForm = ref({ 员工号: '', 日期: '', 可用工时: 8, 实际上报工时: 8 })
const outputForm = ref({ 部门: '', 日期: '', 数量: 0, 单位: '' })

const statCards = computed(() => [
  { label: '覆盖员工数', value: batch.value?.headcount?.覆盖员工数 ?? 0 },
  { label: '投入工时合计', value: batch.value?.headcount?.投入工时合计 ?? 0 },
  { label: '部门人效记录', value: batch.value?.departments?.length ?? 0 },
  { label: '矛盾记录', value: batch.value?.conflicts?.length ?? 0 },
])

const closureRows = computed<Record<string, unknown>>(() => batch.value?.closure ?? {})

function closureWarn(key: string, value: unknown) {
  if (key === '闭合') return value !== true
  if (key === '无任职工时员工日数') return Number(value) > 0
  return false
}

function setMessage(text: string, warn = false) {
  message.value = text
  messageWarn.value = warn
}

function integrityClass(value: string) {
  if (value === '完整') return 'good'
  if (value === '不可核算') return 'bad'
  return 'warn'
}

async function postJson(path: string, body: unknown) {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  return response.json()
}

async function runCalculation() {
  running.value = true
  message.value = ''
  try {
    const values: Record<string, string> = {
      period_start: period.value.start,
      period_end: period.value.end,
    }
    if (period.value.windowStart) values.window_start = period.value.windowStart
    if (period.value.windowEnd) values.window_end = period.value.windowEnd
    const payload = await postJson('/api/hr/efficiency/runs', { values })
    if (!payload.ok) throw new Error(payload.message)
    batch.value = payload.entry
    source.value = null
    setMessage(payload.message, !payload.entry.可形成完整评估)
    await Promise.all([loadBatches(), loadSlices(1)])
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '人效核算失败', true)
  } finally {
    running.value = false
  }
}

async function loadBatches() {
  const response = await request('/api/hr/efficiency/batches?size=50')
  const payload = await response.json()
  batches.value = payload.items ?? []
}

async function loadSlices(page = 1) {
  const batchId = batch.value?.批次号 ?? batches.value[0]?.批次号
  if (!batchId) {
    slices.value = []
    sliceTotal.value = 0
    return
  }
  const query = new URLSearchParams()
  Object.entries(sliceFilters.value).forEach(([key, value]) => {
    if (value) query.set(key, value)
  })
  query.set('page', String(page))
  query.set('size', String(sliceSize))
  const response = await request(`/api/hr/efficiency/batches/${batchId}/slices?${query.toString()}`)
  const payload = await response.json()
  slices.value = payload.items ?? []
  sliceTotal.value = payload.total ?? 0
  slicePage.value = page
}

function resetSliceFilters() {
  sliceFilters.value = { employee: '', department: '', day: '', status: '' }
  void loadSlices(1)
}

async function loadSource() {
  const batchId = batch.value?.批次号
  if (!batchId) return
  const response = await request(`/api/hr/efficiency/batches/${batchId}/source`)
  if (!response.ok) {
    setMessage('原始依据快照读取失败', true)
    return
  }
  source.value = await response.json()
}

async function openBatch(batchId: string) {
  const response = await request(`/api/hr/efficiency/batches/${batchId}`)
  batch.value = await response.json()
  source.value = null
  activeTab.value = 'result'
  await loadSlices(1)
}

async function loadLedger() {
  const [assignmentRes, timesheetRes, eventRes, industryRes] = await Promise.all([
    request('/api/hr/source/assignments?size=200'),
    request('/api/hr/source/timesheets?size=20'),
    request('/api/hr/source/events?size=20'),
    request('/api/hr/source/industry'),
  ])
  assignments.value = (await assignmentRes.json()).items ?? []
  recentTimesheets.value = (await timesheetRes.json()).items ?? []
  events.value = (await eventRes.json()).items ?? []
  industryAverages.value = (await industryRes.json()).items ?? []
}

function startEdit(row: Row) {
  editing.value = { ...row, 任职结束: row.任职结束 || '' }
}

async function saveAssignment() {
  if (!editing.value) return
  const values: Record<string, unknown> = {
    部门: editing.value.部门,
    任职开始: editing.value.任职开始,
    任职结束: editing.value.任职结束 || null,
    投入比例: Number(editing.value.投入比例),
  }
  const response = await request(`/api/hr/source/assignments/${editing.value.id}`, {
    method: 'PUT',
    body: JSON.stringify({ values, remark: '页面更正任职' }),
  })
  const payload = await response.json()
  setMessage(payload.message, !payload.ok)
  if (payload.ok) {
    editing.value = null
    await loadLedger()
  }
}

async function submitTimesheet() {
  const payload = await postJson('/api/hr/source/timesheets', { values: timesheetForm.value })
  setMessage(payload.message, !payload.ok)
  if (payload.ok) await loadLedger()
}

async function submitOutput() {
  const payload = await postJson('/api/hr/source/outputs', { values: outputForm.value })
  setMessage(payload.message, !payload.ok)
  if (payload.ok) await loadLedger()
}

function switchTab(key: string) {
  activeTab.value = key
  if (key === 'slices') void loadSlices(1)
  if (key === 'ledger') void loadLedger()
  if (key === 'batches') void loadBatches()
}

function exportBatch() {
  const batchId = batch.value?.批次号
  if (!batchId) {
    setMessage('请先完成一次核算再导出', true)
    return
  }
  window.open(`/api/hr/efficiency/batches/${batchId}/export`, '_blank')
}

onMounted(async () => {
  try {
    await Promise.all([loadBatches(), loadLedger()])
    if (batches.value.length) {
      await openBatch(batches.value[0].批次号)
    }
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '人力效能评估数据读取失败', true)
  }
})
</script>

<style scoped>
.tabs { display: flex; gap: 6px; margin: 4px 0 12px; }
.tab {
  border: 1px solid var(--border);
  background: #fff;
  border-radius: 6px 6px 0 0;
  padding: 6px 14px;
  cursor: pointer;
  font-size: 13px;
}
.tab.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.notice {
  background: #eef4ff;
  border: 1px solid #c7d7fe;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
  margin: 0 0 12px;
}
.notice.warn { background: #fff7ed; border-color: #fed7aa; color: #9a3412; }
.conclusion {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: baseline;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.conclusion.blocked { background: #fff7ed; border-color: #fed7aa; }
.muted { color: var(--muted); font-size: 12px; }
.warn-text { color: #b45309; }
.badge { display: inline-block; border-radius: 10px; padding: 1px 8px; font-size: 12px; }
.badge.good { background: #dcfce7; color: #166534; }
.badge.warn { background: #fef3c7; color: #92400e; }
.badge.bad { background: #fee2e2; color: #991b1b; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.panel h3 { margin: 0 0 8px; font-size: 14px; }
.panel p { margin: 0 0 8px; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; margin: 0; font-size: 13px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mini-table th, .mini-table td { border: 1px solid var(--border); padding: 5px 7px; text-align: left; vertical-align: top; }
.note-list { margin: 0; padding-left: 16px; }
.inline-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-top: 10px; }
.pager { display: flex; gap: 12px; }
.pager .link:disabled { color: #cbd5e1; cursor: default; }
select { border: 1px solid var(--border); border-radius: 4px; padding: 4px 6px; }
input { border: 1px solid var(--border); border-radius: 4px; padding: 4px 6px; }
</style>
