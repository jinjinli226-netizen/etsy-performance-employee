<script setup lang="ts">
import { Download, FilePlus2, RotateCcw } from "lucide-vue-next";
import type { ExcelJob } from "../../api/excel";

defineProps<{ job: ExcelJob; downloading: boolean; hasLocalFile: boolean }>();
defineEmits<{ download: []; retry: []; reselect: [] }>();
</script>

<template>
  <footer class="result-actions">
    <div v-if="job.status === 'completed' && job.artifact" class="result-actions__ready">
      <div><strong>输出文件已就绪</strong><span>下载的是新副本，原工作簿保持不变</span></div>
      <button data-testid="download-result" type="button" :disabled="downloading" @click="$emit('download')">
        <Download :size="17" aria-hidden="true" />{{ downloading ? "正在下载" : "下载新表格" }}
      </button>
    </div>
    <div v-else-if="job.status === 'failed'" class="result-actions__retry">
      <div><strong>可以重新发起任务</strong><span>{{ hasLocalFile ? "浏览器仍保留本次选择的原文件" : "刷新后需要从电脑重新选择原文件" }}</span></div>
      <button v-if="hasLocalFile" data-testid="retry-job" type="button" @click="$emit('retry')"><RotateCcw :size="17" aria-hidden="true" />使用原文件重试</button>
      <button v-else data-testid="reselect-job" type="button" @click="$emit('reselect')"><FilePlus2 :size="17" aria-hidden="true" />重新选择文件</button>
    </div>
  </footer>
</template>

<style scoped>
.result-actions { margin-top: auto; padding-top: 24px; border-top: 1px solid var(--border); }
.result-actions:empty { display: none; }
.result-actions__ready, .result-actions__retry { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.result-actions__ready > div, .result-actions__retry > div { display: grid; gap: 3px; }.result-actions strong { font-size: 13px; font-weight: 500; }.result-actions span { color: var(--text-muted); font-size: 11px; }
.result-actions button { display: inline-flex; min-height: 40px; align-items: center; gap: 8px; padding: 0 15px; border: 1px solid var(--accent); border-radius: var(--ds-radius-control); background: var(--accent); color: #16100c; font-weight: 600; cursor: pointer; }
.result-actions__retry button { border-color: var(--border-strong); background: var(--surface-raised); color: var(--text); }
@media (max-width: 640px) { .result-actions__ready, .result-actions__retry { align-items: stretch; flex-direction: column; } .result-actions button { min-height: 44px; justify-content: center; } }
</style>
