<script setup lang="ts">
import { FileSpreadsheet, Upload } from "lucide-vue-next";
import { ref } from "vue";

const props = defineProps<{ uploading: boolean }>();
const emit = defineEmits<{ select: [file: File] }>();
const input = ref<HTMLInputElement | null>(null);
const button = ref<HTMLButtonElement | null>(null);
const dragging = ref(false);

const open = () => { if (!props.uploading) input.value?.click(); };
const receive = (files: FileList | null) => {
  const file = files?.[0] ?? files?.item?.(0);
  if (file && !props.uploading) emit("select", file);
  if (input.value) input.value.value = "";
};
const drop = (event: DragEvent) => {
  dragging.value = false;
  receive(event.dataTransfer?.files ?? null);
};
defineExpose({ focus: () => button.value?.focus(), openPicker: open });
</script>

<template>
  <div
    class="excel-dropzone"
    :class="{ 'is-dragging': dragging, 'is-uploading': uploading }"
    @dragenter.prevent="dragging = true"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="drop"
  >
    <button
      ref="button"
      data-testid="excel-dropzone"
      class="excel-dropzone__target"
      type="button"
      :disabled="uploading"
      aria-describedby="excel-upload-help"
      @click="open"
    >
      <span class="excel-dropzone__icon" aria-hidden="true"><FileSpreadsheet :size="27" /></span>
      <span class="excel-dropzone__copy">
        <strong>{{ uploading ? "正在交给数字员工" : "上传商品工作簿" }}</strong>
        <span>拖入文件，或按回车选择</span>
      </span>
      <span class="excel-dropzone__action"><Upload :size="16" aria-hidden="true" />选择 .xlsx</span>
    </button>
    <input
      ref="input"
      data-testid="excel-file-input"
      class="sr-only"
      type="file"
      accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      :disabled="uploading"
      @change="receive(($event.target as HTMLInputElement).files)"
    />
    <p id="excel-upload-help">仅支持 .xlsx，最大 50 MB。原文件不会被覆盖，员工会自动识别表头。</p>
    <button data-testid="excel-upload-button" class="sr-only" type="button" :disabled="uploading" @click="open">选择工作簿</button>
    <div v-if="dragging" class="excel-dropzone__overlay" aria-hidden="true">松开即可上传</div>
  </div>
</template>

<style scoped>
.excel-dropzone { position: relative; border-bottom: 1px solid var(--border); }
.excel-dropzone__target { display: grid; width: 100%; min-height: 244px; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 20px; padding: 32px 36px; border: 1px dashed var(--border-strong); border-radius: var(--ds-radius-frame); background: var(--canvas-soft); color: var(--text); text-align: left; cursor: pointer; transition: border-color 140ms ease, background-color 140ms ease; }
.excel-dropzone__target:hover, .excel-dropzone.is-dragging .excel-dropzone__target { border-color: rgba(255, 122, 26, .68); background: #101114; }
.excel-dropzone__icon { display: grid; width: 58px; height: 58px; place-items: center; border: 1px solid var(--border-strong); border-radius: var(--ds-radius-control); background: var(--surface); color: var(--accent); }
.excel-dropzone__copy { display: grid; gap: 5px; }
.excel-dropzone__copy strong { font-size: 20px; font-weight: 600; }
.excel-dropzone__copy > span { color: var(--text-muted); font-size: 13px; }
.excel-dropzone__action { display: inline-flex; min-height: 40px; align-items: center; gap: 8px; padding: 0 14px; border: 1px solid var(--border-strong); border-radius: var(--ds-radius-control); background: var(--surface-raised); font-size: 13px; }
.excel-dropzone > p { margin: 11px 0 20px; color: var(--text-muted); font-size: 12px; }
.excel-dropzone__overlay { position: absolute; z-index: 2; inset: 0 0 20px; display: grid; place-items: center; border: 1px solid var(--accent); border-radius: var(--ds-radius-frame); background: rgba(8, 9, 11, .92); color: var(--accent); font-size: 16px; font-weight: 600; pointer-events: none; }
@media (max-width: 640px) { .excel-dropzone__target { min-height: 260px; grid-template-columns: 1fr; justify-items: center; gap: 14px; padding: 28px 18px; text-align: center; } .excel-dropzone__action { min-height: 44px; } }
</style>
