<template>
  <section class="page" data-module="report">
    <header class="page-head">
      <div>
        <h2>经营分析报告管理</h2>
        <p class="page-desc">
          维护经营分析的模板选择、关键发现与分发管理。生成报告可引用部门人效偏差数据集，
          附件固化人员日期切片、部门汇总与行业口径，组织调整后旧附件仍可用自身快照复算。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出经营分析报告清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="message" class="notice" :class="{ warn: messageWarn }">{{ message }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openAttachments(row)">查看人效附件</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无经营分析报告数据，可先登记分析报告</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条经营分析报告记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- ============================ 附件面板 ============================ -->
    <section v-if="activeReport" class="panel">
      <header class="panel-head">
        <h3>人效数据附件 · {{ activeReport.报告编号 }}</h3>
        <button class="link" type="button" @click="activeReport = null">收起</button>
      </header>

      <div class="filter-bar">
        <label class="filter-item">
          <span>引用核算批次</span>
          <select v-model="selectedBatch">
            <option value="">最近一次核算</option>
            <option v-for="item in hrDatasets" :key="item.批次号" :value="item.批次号">
              {{ item.批次号 }} · {{ item.核算周期.start }}~{{ item.核算周期.end }} · {{ item.状态 }}
            </option>
          </select>
        </label>
        <button class="btn primary" type="button" @click="generateAttachment">生成报告附件</button>
      </div>

      <table class="mini-table">
        <thead><tr><th>版本</th><th>批次号</th><th>核算周期</th><th>覆盖人数</th><th>部门数</th><th>状态</th><th>完整性</th><th>依据摘要</th><th>生成时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="item in attachments" :key="item.版本">
            <td>v{{ item.版本 }}</td>
            <td>{{ item.批次号 }}</td>
            <td>{{ item.核算周期.start }}~{{ item.核算周期.end }}</td>
            <td>{{ item.人数 }}</td>
            <td>{{ item.部门数 }}</td>
            <td>{{ item.状态 }}</td>
            <td>{{ item.完整性结论 }}</td>
            <td class="muted">{{ item.依据摘要 }}</td>
            <td class="muted">{{ item.生成时间 }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="viewAttachment(item.版本)">查看</button>
              <button class="link" type="button" @click="recompute(item.版本)">复算</button>
            </td>
          </tr>
          <tr v-if="!attachments.length">
            <td colspan="10" class="empty-state">该报告尚未生成人效附件</td>
          </tr>
        </tbody>
      </table>

      <template v-if="attachment">
        <h4>附件 v{{ attachment.版本 }} · 部门汇总</h4>
        <table class="mini-table">
          <thead><tr><th>部门</th><th>状态</th><th>人数</th><th>投入工时</th><th>产出</th><th>人效</th><th>行业均值</th><th>偏差结论</th></tr></thead>
          <tbody>
            <tr v-for="row in attachment.部门汇总" :key="row.部门">
              <td>{{ row.部门 }}</td>
              <td>{{ row.核算状态 }}</td>
              <td>{{ row.人数 }}</td>
              <td>{{ row.投入工时 }}</td>
              <td>{{ row.产出数量 }} {{ row.产出单位 }}</td>
              <td>{{ row.人效 === null ? '—' : `${row.人效} ${row.人效单位}` }}</td>
              <td>{{ row.行业均值 === null ? '—' : `${row.行业均值} ${row.行业均值单位}（${row.行业均值适用周期}）` }}</td>
              <td>{{ row.偏差结论 }}</td>
            </tr>
          </tbody>
        </table>

        <h4>行业口径</h4>
        <table class="mini-table">
          <thead><tr><th>部门</th><th>行业均值</th><th>单位</th><th>适用周期</th><th>口径</th><th>匹配状态</th></tr></thead>
          <tbody>
            <tr v-for="row in attachment.行业口径" :key="row.部门">
              <td>{{ row.部门 }}</td>
              <td>{{ row.行业均值 ?? '—' }}</td>
              <td>{{ row.单位 || '—' }}</td>
              <td>{{ row.适用周期 || '—' }}</td>
              <td>{{ row.口径 || '—' }}</td>
              <td>{{ row.匹配状态 }}</td>
            </tr>
          </tbody>
        </table>

        <h4>投入闭合核对</h4>
        <dl class="kv">
          <template v-for="(value, key) in attachment.投入闭合核对" :key="key">
            <dt>{{ key }}</dt>
            <dd :class="{ 'warn-text': closureWarn(String(key), value) }">{{ value }}</dd>
          </template>
        </dl>

        <h4>
          人员日期切片（{{ attachment.人员日期切片.length }} 条）
          <button class="link" type="button" @click="downloadAttachment">下载完整附件</button>
        </h4>
        <table class="mini-table">
          <thead><tr><th>员工</th><th>部门</th><th>日期</th><th>任职区间</th><th>节点</th><th>比例</th><th>当日报工</th><th>分配工时</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="row in attachment.人员日期切片.slice(0, 40)" :key="row.切片编号">
              <td>{{ row.员工号 }} {{ row.姓名 }}</td>
              <td>{{ row.部门 }}</td>
              <td>{{ row.日期 }}</td>
              <td>{{ row.任职开始 }}~{{ row.任职结束 || '至今' }}</td>
              <td>{{ row.任职节点 }}</td>
              <td>{{ row.投入比例 }}</td>
              <td>{{ row.员工当日报工时 ?? '—' }}</td>
              <td>{{ row.分配工时 ?? '—' }}</td>
              <td>{{ row.状态 }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted">仅展示前 40 条，下载附件可获取全部切片。</p>
      </template>

      <template v-if="recomputeResult">
        <h4>复算结果：{{ recomputeResult.结论 }}</h4>
        <table class="mini-table">
          <thead><tr><th>部门</th><th>附件人效</th><th>复算人效</th><th>附件投入</th><th>复算投入</th><th>一致</th></tr></thead>
          <tbody>
            <tr v-for="row in recomputeResult.部门比对" :key="row.部门">
              <td>{{ row.部门 }}</td>
              <td>{{ row.附件人效 ?? '—' }}</td>
              <td>{{ row.复算人效 ?? '—' }}</td>
              <td>{{ row.附件投入工时 ?? '—' }}</td>
              <td>{{ row.复算投入工时 ?? '—' }}</td>
              <td>{{ row.一致 ? '是' : '否' }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/report'
const columns = ["报告编号", "报告主题", "分析周期", "报告模板", "关键发现", "数据附件", "分发对象", "报告状态"]
const actions = ["生成报告", "分发报告", "归档报告"]

const rows = ref<Row[]>([])
const total = ref(0)
const message = ref('')
const messageWarn = ref(false)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const activeReport = ref<Row | null>(null)
const attachments = ref<Row[]>([])
const attachment = ref<Row | null>(null)
const recomputeResult = ref<Row | null>(null)
const hrDatasets = ref<Row[]>([])
const selectedBatch = ref('')

const stats = computed(() => [
  { label: '分析报告总数', value: total.value },
  { label: '已生成报告', value: rows.value.filter((row) => row.status === '已生成').length },
  { label: '已分发报告', value: rows.value.filter((row) => row.status === '已分发').length },
])

function closureWarn(key: string, value: unknown) {
  if (key === '闭合') return value !== true
  if (key === '无任职工时员工日数') return Number(value) > 0
  return false
}

function setMessage(text: string, warn = false) {
  message.value = text
  messageWarn.value = warn
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const values: Record<string, unknown> = { action }
    if (action === '生成报告' && selectedBatch.value) values.hr_batch = selectedBatch.value
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    setMessage(payload.message)
    await reload()
    if (action === '生成报告') {
      activeReport.value = row
      await loadAttachments(row.id)
    }
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '经营分析报告操作失败', true)
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) throw new Error('分析报告列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '经营分析报告列表读取失败'
  }
}

async function loadDatasets() {
  const response = await request(`${ENDPOINT}/hr-datasets`)
  const payload = await response.json()
  hrDatasets.value = payload.items ?? []
}

async function openAttachments(row: Row) {
  activeReport.value = row
  attachment.value = null
  recomputeResult.value = null
  await loadAttachments(row.id)
}

async function loadAttachments(reportId: number) {
  const response = await request(`${ENDPOINT}/${reportId}/attachments`)
  const payload = await response.json()
  attachments.value = payload.items ?? []
}

async function generateAttachment() {
  if (!activeReport.value) return
  await runAction('生成报告', activeReport.value)
}

async function viewAttachment(version: number) {
  if (!activeReport.value) return
  const response = await request(`${ENDPOINT}/${activeReport.value.id}/attachments/${version}`)
  if (!response.ok) {
    setMessage('附件读取失败', true)
    return
  }
  attachment.value = await response.json()
  recomputeResult.value = null
}

async function recompute(version: number) {
  if (!activeReport.value) return
  const response = await request(
    `${ENDPOINT}/${activeReport.value.id}/attachments/${version}/recompute`,
    { method: 'POST', body: '{}' },
  )
  const payload = await response.json()
  recomputeResult.value = payload.entry ?? null
  setMessage(payload.message, !payload.ok)
}

async function downloadAttachment() {
  if (!activeReport.value || !attachment.value) return
  const data = JSON.stringify(attachment.value, null, 2)
  const blob = new Blob([data], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${activeReport.value.报告编号}-人效附件v${attachment.value.版本}.json`
  link.click()
  URL.revokeObjectURL(url)
}

onMounted(async () => {
  await Promise.all([reload(), loadDatasets()])
})
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-top: 16px;
}
.panel-head { display: flex; justify-content: space-between; align-items: center; }
.panel h3 { margin: 0 0 8px; font-size: 15px; }
.panel h4 { margin: 14px 0 6px; font-size: 13px; display: flex; gap: 10px; align-items: baseline; }
.notice {
  background: #eef4ff;
  border: 1px solid #c7d7fe;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
  margin: 0 0 12px;
}
.notice.warn { background: #fff7ed; border-color: #fed7aa; color: #9a3412; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mini-table th, .mini-table td { border: 1px solid var(--border); padding: 5px 7px; text-align: left; }
.muted { color: var(--muted); font-size: 12px; }
.warn-text { color: #b45309; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; margin: 0; font-size: 13px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; }
select { border: 1px solid var(--border); border-radius: 4px; padding: 4px 6px; }
input { border: 1px solid var(--border); border-radius: 4px; padding: 4px 6px; }
</style>
