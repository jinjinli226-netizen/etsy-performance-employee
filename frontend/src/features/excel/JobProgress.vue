<script setup lang="ts">
import { Ban, Check, Clock3, LoaderCircle, TriangleAlert, X } from "lucide-vue-next";
import { computed } from "vue";
import type { ExcelJob } from "../../api/excel";

const props = defineProps<{ job: ExcelJob; warnings: string[]; skippedCount: number; cancelling: boolean }>();
defineEmits<{ cancel: [] }>();

const copy = {
  queued: { label: "排队中", detail: "数字员工正在准备工作环境", icon: Clock3 },
  running: { label: "生成中", detail: "正在逐行生成 Listing 内容", icon: LoaderCircle },
  needs_review: { label: "待复核（历史任务）", detail: "这是早期任务状态，请检查员工提醒", icon: TriangleAlert },
  completed: { label: "已完成", detail: "新工作簿已通过校验", icon: Check },
  failed: { label: "生成失败", detail: "本次任务未生成可下载文件", icon: X },
  cancelled: { label: "已取消", detail: "数字员工已停止处理", icon: Ban },
} as const;
const state = computed(() => copy[props.job.status]);
const percent = computed(() => Math.min(100, Math.max(0, props.job.progress_percent)));
const active = computed(() => ["queued", "running"].includes(props.job.status));
const displayStatus = computed(() => props.job.status === "completed" && props.warnings.length ? "完成（有提醒）" : state.value.label);
const size = computed(() => props.job.source_size_bytes < 1024 * 1024
  ? `${Math.max(1, Math.round(props.job.source_size_bytes / 1024))} KB`
  : `${(props.job.source_size_bytes / 1024 / 1024).toFixed(1)} MB`);
</script>

<template>
  <section class="job-progress" aria-labelledby="job-progress-title">
    <header class="job-progress__header">
      <div class="job-progress__identity">
        <span class="job-progress__state" :class="`is-${job.status}`">
          <component :is="state.icon" :class="{ spin: job.status === 'running' }" :size="18" aria-hidden="true" />
        </span>
        <div>
          <span class="job-progress__eyebrow">当前工作簿 · {{ size }}</span>
          <h2 id="job-progress-title">{{ job.source_filename }}</h2>
        </div>
      </div>
      <button v-if="active" class="job-progress__cancel" type="button" :disabled="cancelling" @click="$emit('cancel')">
        {{ cancelling ? "正在取消" : "取消任务" }}
      </button>
    </header>

    <div class="job-progress__meter">
      <div class="job-progress__status">
        <strong>{{ displayStatus }}</strong>
        <span>{{ state.detail }}</span>
        <b>{{ percent }}%</b>
      </div>
      <div
        class="job-progress__track"
        role="progressbar"
        :aria-valuenow="percent"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuetext="`${displayStatus}，${percent}%`"
      ><span :style="{ width: `${percent}%` }" /></div>
      <div class="job-progress__rows"><span>逐行处理</span><span>{{ percent === 100 ? "全部完成" : "总行数由员工自动识别" }}</span></div>
    </div>

    <div v-if="warnings.length" class="job-progress__warnings" aria-label="员工提醒">
      <strong><TriangleAlert :size="15" aria-hidden="true" />员工提醒</strong>
      <ul><li v-for="warning in warnings" :key="warning">{{ warning }}</li></ul>
    </div>
    <div v-if="skippedCount > 0" class="job-progress__skipped" data-testid="skipped-rows">
      <strong>已跳过 {{ skippedCount }} 行</strong>
      <span>已跳过：缺少商品图片</span>
    </div>
  </section>
</template>

<style scoped>
.job-progress { min-width: 0; }
.job-progress__header { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 26px 0; border-bottom: 1px solid var(--border); }
.job-progress__identity { display: flex; min-width: 0; align-items: center; gap: 14px; }
.job-progress__state { display: grid; width: 42px; height: 42px; flex: 0 0 auto; place-items: center; border: 1px solid var(--border-strong); border-radius: var(--ds-radius-control); background: var(--surface); color: var(--text-secondary); }
.job-progress__state.is-completed { color: var(--success); }.job-progress__state.is-failed { color: var(--danger); }.job-progress__state.is-needs_review { color: var(--warning); }.job-progress__state.is-running { color: var(--accent); }
.job-progress__identity > div { min-width: 0; }
.job-progress__eyebrow { display: block; margin-bottom: 3px; color: var(--text-muted); font-size: 11px; }
.job-progress h2 { overflow: hidden; margin: 0; font-size: 16px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.job-progress__cancel { min-height: 38px; flex: 0 0 auto; padding: 0 13px; border: 1px solid var(--border-strong); border-radius: var(--ds-radius-control); background: transparent; color: var(--text-secondary); cursor: pointer; }
.job-progress__cancel:hover { border-color: rgba(240, 100, 100, .55); color: var(--danger); }
.job-progress__meter { padding: 38px 0 32px; }
.job-progress__status { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: baseline; gap: 12px; }
.job-progress__status strong { font-size: 24px; font-weight: 600; }.job-progress__status span { color: var(--text-muted); font-size: 13px; }.job-progress__status b { color: var(--text-secondary); font-size: 13px; font-variant-numeric: tabular-nums; }
.job-progress__track { height: 4px; margin-top: 18px; overflow: hidden; background: var(--surface-raised); }
.job-progress__track span { display: block; height: 100%; background: var(--accent); transition: width 200ms var(--ds-ease); }
.job-progress__rows { display: flex; justify-content: space-between; margin-top: 9px; color: var(--text-muted); font-size: 11px; }
.job-progress__warnings { padding: 18px 0 2px; border-top: 1px solid var(--border); }
.job-progress__warnings strong { display: flex; align-items: center; gap: 7px; color: var(--warning); font-size: 12px; font-weight: 500; }
.job-progress__warnings ul { display: grid; gap: 6px; margin: 10px 0 0; padding-left: 18px; color: var(--text-secondary); font-size: 12px; }
.job-progress__skipped { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 0 0; color: var(--text-secondary); font-size: 12px; }
.job-progress__skipped strong { color: var(--warning); font-weight: 600; }
.spin { animation: spin .8s linear infinite; } @keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 640px) { .job-progress__header { align-items: flex-start; } .job-progress__identity { width: 100%; } .job-progress__identity > div { max-width: calc(100% - 56px); } .job-progress h2 { max-width: 100%; } .job-progress__cancel { min-height: 44px; } .job-progress__status { grid-template-columns: minmax(0, 1fr) auto; } .job-progress__status span { grid-column: 1 / -1; grid-row: 2; } .job-progress__status strong { font-size: 21px; } .job-progress__status b { justify-self: end; } .job-progress__rows { gap: 12px; } .job-progress__rows span { min-width: 0; } }
</style>
