<script setup lang="ts">
import { CircleAlert, FileSpreadsheet } from "lucide-vue-next";
import { computed, nextTick, onMounted, ref } from "vue";
import type { HttpErrorCode } from "../api/client";
import ExcelDropzone from "../features/excel/ExcelDropzone.vue";
import JobHistory from "../features/excel/JobHistory.vue";
import JobProgress from "../features/excel/JobProgress.vue";
import ResultDownload from "../features/excel/ResultDownload.vue";
import { defaultExcelStore, type ExcelStore } from "../features/excel/excel.store";

const props = withDefaults(defineProps<{ store?: ExcelStore }>(), { store: () => defaultExcelStore });
const store = props.store;
const dropzone = ref<InstanceType<typeof ExcelDropzone> | null>(null);
const anotherDropzone = ref<InstanceType<typeof ExcelDropzone> | null>(null);
const another = ref<HTMLDetailsElement | null>(null);
const alertRef = ref<HTMLElement | null>(null);
const liveRef = ref<HTMLElement | null>(null);
const errorCopy: Record<HttpErrorCode | "capacity" | "unsupported" | "empty" | "too_large", string> = {
  bad_request: "工作簿无法处理，请确认文件完整后重试。",
  not_found: "这条任务已不存在，请从历史任务重新选择。",
  conflict: "文件还未生成完成，暂时不能下载。",
  invalid_input: "仅支持有效的 .xlsx 工作簿。",
  employee_unavailable: "数字员工暂时不可用，请稍后重试。",
  timeout: "连接数字员工超时，请稍后重试。",
  network: "无法连接本地服务，请确认服务已启动。",
  server_error: "本地服务未能完成操作，请稍后重试。",
  capacity: "知识库容量需要先处理，当前无法开始新的生成任务。",
  unsupported: "仅支持 .xlsx 文件，不支持 .xlsm、.xls 或 .csv。",
  empty: "这个工作簿是空文件，请重新选择。",
  too_large: "工作簿超过 50 MB，请缩小文件后重试。",
};
const errorMessage = computed(() => store.errorCode ? errorCopy[store.errorCode] : "");

const upload = async (file: File) => {
  const succeeded = await store.upload(file);
  await nextTick();
  if (succeeded) liveRef.value?.focus();
  else if (errorMessage.value) alertRef.value?.focus();
  else dropzone.value?.focus();
};
const retry = async () => { await store.retryCurrent(); await nextTick(); liveRef.value?.focus(); };
const download = async () => { await store.downloadCurrent(); await nextTick(); liveRef.value?.focus(); };
const reselect = async () => {
  if (another.value) another.value.open = true;
  await nextTick();
  anotherDropzone.value?.openPicker();
};

onMounted(() => { if (!store.jobs.length && !store.loading) void store.initialize(); });
</script>

<template>
  <section class="excel-workspace" aria-labelledby="excel-title">
    <div class="excel-workspace__main">
      <header class="excel-workspace__intro">
        <div>
          <span class="excel-workspace__eyebrow">Excel 自动化</span>
          <h2 id="excel-title">生成 Listing 表格</h2>
          <p>交给数字员工逐行处理，只补齐固定 Listing 字段并输出一份新文件。</p>
        </div>
        <FileSpreadsheet :size="23" aria-hidden="true" />
      </header>

      <div v-if="errorMessage" ref="alertRef" class="excel-workspace__notice" role="alert" tabindex="-1">
        <CircleAlert :size="16" aria-hidden="true" /><span>{{ errorMessage }}</span>
        <button type="button" aria-label="关闭错误提醒" @click="store.clearError()">关闭</button>
      </div>
      <div ref="liveRef" class="sr-only" aria-live="polite" tabindex="-1">
        {{ store.currentJob ? `${store.currentJob.source_filename}：${store.currentJob.status}，进度 ${store.currentJob.progress_percent}%` : "" }}
      </div>

      <div v-if="store.loading && !store.jobs.length" class="excel-workspace__loading">正在读取历史任务…</div>
      <template v-else-if="!store.currentJob">
        <ExcelDropzone ref="dropzone" :uploading="store.uploading" @select="upload" />
      </template>
      <template v-else>
        <JobProgress :job="store.currentJob" :warnings="store.currentWarnings" :cancelling="store.cancelling" @cancel="store.cancelCurrent()" />
        <ResultDownload
          :job="store.currentJob"
          :downloading="store.downloading"
          :has-local-file="store.hasLocalFile(store.currentJob.id)"
          @download="download"
          @retry="retry"
          @reselect="reselect"
        />
        <details ref="another" class="excel-workspace__another">
          <summary>上传另一个工作簿</summary>
          <ExcelDropzone ref="anotherDropzone" :uploading="store.uploading" @select="upload" />
        </details>
      </template>
    </div>

    <JobHistory
      :jobs="store.jobs"
      :current-id="store.currentJobId"
      :has-more="store.hasMore"
      :loading-more="store.loadingMore"
      @select="store.selectJob"
      @load-more="store.loadMore"
    />
  </section>
</template>

<style scoped>
.excel-workspace { display: grid; min-height: calc(100dvh - var(--topbar-height)); grid-template-columns: minmax(0, 1fr) 268px; background-image: linear-gradient(var(--border) 1px, transparent 1px); background-size: 100% 48px; }
.excel-workspace__main { display: flex; width: min(920px, 100%); min-height: calc(100dvh - var(--topbar-height)); flex-direction: column; justify-self: center; padding: 28px 34px 34px; background: var(--canvas); }
.excel-workspace__intro { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding-bottom: 22px; border-bottom: 1px solid var(--border); }
.excel-workspace__intro > svg { color: var(--text-muted); }.excel-workspace__eyebrow { display: block; margin-bottom: 5px; color: var(--accent); font-size: 11px; font-weight: 600; }
.excel-workspace h2 { margin: 0 0 7px; font-size: 22px; font-weight: 600; }.excel-workspace p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.excel-workspace__intro + :deep(.excel-dropzone) { padding-top: 24px; }
.excel-workspace__notice { display: flex; min-height: 44px; align-items: center; gap: 9px; margin-top: 16px; padding: 8px 11px; border: 1px solid rgba(240, 180, 77, .34); border-radius: var(--ds-radius-control); background: rgba(240, 180, 77, .06); color: var(--warning); font-size: 12px; }
.excel-workspace__notice span { min-width: 0; }.excel-workspace__notice button { min-width: 44px; min-height: 44px; margin-left: auto; border: 0; background: transparent; color: var(--text-secondary); cursor: pointer; }
.excel-workspace__loading { display: grid; min-height: 280px; place-items: center; color: var(--text-muted); }
.excel-workspace__another { margin-top: 28px; padding-top: 16px; border-top: 1px solid var(--border); }
.excel-workspace__another summary { min-height: 44px; color: var(--text-secondary); font-size: 12px; cursor: pointer; }
.excel-workspace__another :deep(.excel-dropzone__target) { min-height: 160px; }.excel-workspace__another :deep(.excel-dropzone) { padding-top: 12px; }
@media (max-width: 840px) { .excel-workspace { grid-template-columns: 1fr; }.excel-workspace__main { min-height: auto; grid-row: 2; } }
@media (max-width: 640px) { .excel-workspace { overflow-x: clip; }.excel-workspace__main { min-width: 0; padding: 20px 14px 26px; overflow-x: clip; }.excel-workspace h2 { font-size: 20px; }.excel-workspace__intro { padding-bottom: 18px; } }
</style>
