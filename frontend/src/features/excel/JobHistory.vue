<script setup lang="ts">
import { ChevronDown, FileSpreadsheet } from "lucide-vue-next";
import { ref } from "vue";
import type { ExcelJob } from "../../api/excel";

defineProps<{ jobs: ExcelJob[]; currentId: string | null; hasMore: boolean; loadingMore: boolean }>();
defineEmits<{ select: [id: string]; loadMore: [] }>();
const mobileOpen = ref(false);
const stateCopy = { queued: "排队", running: "处理中", completed: "完成", failed: "失败", cancelled: "取消" } as const;
const dateCopy = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
};
</script>

<template>
  <aside class="job-history" :class="{ 'is-open': mobileOpen }" aria-label="历史任务">
    <button class="job-history__toggle" type="button" :aria-expanded="mobileOpen" @click="mobileOpen = !mobileOpen">
      <span>历史任务 · {{ jobs.length }}</span><ChevronDown :size="17" aria-hidden="true" />
    </button>
    <div class="job-history__body">
      <header><span>历史任务</span><b>{{ jobs.length }}</b></header>
      <div v-if="!jobs.length" class="job-history__empty">暂无历史记录</div>
      <ol v-else>
        <li v-for="job in jobs" :key="job.id">
          <button type="button" :class="{ 'is-current': job.id === currentId }" @click="$emit('select', job.id)">
            <FileSpreadsheet :size="15" aria-hidden="true" />
            <span><strong>{{ job.source_filename }}</strong><small>{{ dateCopy(job.updated_at) }}</small></span>
            <em :class="`is-${job.status}`">{{ stateCopy[job.status] }}</em>
          </button>
        </li>
      </ol>
      <button v-if="hasMore" data-testid="load-more-jobs" class="job-history__more" type="button" :disabled="loadingMore" @click="$emit('loadMore')">
        {{ loadingMore ? "正在加载" : "加载更多" }}
      </button>
    </div>
  </aside>
</template>

<style scoped>
.job-history { min-width: 0; border-left: 1px solid var(--border); background: var(--canvas-soft); }
.job-history__toggle { display: none; }
.job-history__body > header { display: flex; min-height: 48px; align-items: center; justify-content: space-between; padding: 0 16px; border-bottom: 1px solid var(--border); color: var(--text-secondary); font-size: 12px; }
.job-history__body > header b { color: var(--text-muted); font-weight: 500; font-variant-numeric: tabular-nums; }
.job-history ol { display: grid; margin: 0; padding: 8px; list-style: none; }
.job-history li button { display: grid; width: 100%; min-height: 58px; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 9px; padding: 7px 8px; border: 1px solid transparent; border-radius: var(--ds-radius-control); background: transparent; color: var(--text-muted); text-align: left; cursor: pointer; }
.job-history li button:hover { background: rgba(255, 255, 255, .03); color: var(--text-secondary); }
.job-history li button.is-current { border-color: var(--border); background: var(--surface); color: var(--text-secondary); }
.job-history li span { display: grid; min-width: 0; gap: 2px; }
.job-history li strong { overflow: hidden; color: var(--text-secondary); font-size: 12px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.job-history li small { color: var(--text-muted); font-size: 10px; }
.job-history li em { padding: 2px 5px; border-radius: var(--ds-radius-label); background: var(--surface-raised); color: var(--text-muted); font-size: 10px; font-style: normal; white-space: nowrap; }
.job-history li em.is-running { color: var(--accent); }.job-history li em.is-completed { color: var(--success); }.job-history li em.is-failed { color: var(--danger); }
.job-history__empty { padding: 26px 16px; color: var(--text-muted); font-size: 12px; text-align: center; }
.job-history__more { width: calc(100% - 16px); min-height: 44px; margin: 0 8px 12px; border: 1px solid var(--border); border-radius: var(--ds-radius-control); background: transparent; color: var(--text-secondary); cursor: pointer; }
@media (max-width: 840px) { .job-history { border: 0; border-bottom: 1px solid var(--border); } .job-history__toggle { display: flex; width: 100%; min-height: 44px; align-items: center; justify-content: space-between; padding: 0 14px; border: 0; background: var(--canvas-soft); color: var(--text-secondary); } .job-history__toggle svg { transition: transform 180ms var(--ds-ease); } .job-history.is-open .job-history__toggle svg { transform: rotate(180deg); } .job-history__body { display: none; max-height: 264px; overflow: auto; border-top: 1px solid var(--border); } .job-history.is-open .job-history__body { display: block; } .job-history__body > header { display: none; } }
</style>
