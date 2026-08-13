<script setup lang="ts">
import { FileSpreadsheet, FileText, Image, Paperclip, Send, X } from "lucide-vue-next";
import { computed, nextTick, onBeforeUnmount, ref } from "vue";

const MAX_FILES = 20;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const supportedTypes = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "text/csv",
  "text/plain",
  "application/pdf",
  "application/zip",
  "application/octet-stream",
]);
const blockedSuffixes = new Set([".bat", ".cmd", ".com", ".exe", ".js", ".msi", ".ps1", ".scr"]);

const props = defineProps<{ busy: boolean; learningMode?: boolean }>();
const emit = defineEmits<{
  submit: [payload: { content: string; files: File[] }];
  "update:learning-mode": [active: boolean];
}>();

const content = ref("");
const files = ref<File[]>([]);
const previews = new Map<File, string>();
const textarea = ref<HTMLTextAreaElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const inlineError = ref("");
const dragging = ref(false);
const hasContent = computed(() => Boolean(content.value.trim()));

const previewFor = (file: File) => {
  if (!file.type.startsWith("image/")) return "";
  const existing = previews.get(file);
  if (existing) return existing;
  const url = URL.createObjectURL(file);
  previews.set(file, url);
  return url;
};

const revokeFile = (file: File) => {
  const url = previews.get(file);
  if (url) URL.revokeObjectURL(url);
  previews.delete(file);
};

const removeFile = (file: File) => {
  revokeFile(file);
  files.value = files.value.filter((item) => item !== file);
  inlineError.value = "";
};

const validateFiles = (incoming: File[]) => {
  const accepted: File[] = [];
  let imageCount = files.value.filter((file) => file.type.startsWith("image/")).length;
  for (const file of incoming) {
    if (files.value.length + accepted.length >= MAX_FILES) {
      inlineError.value = `一次最多添加 ${MAX_FILES} 个文件`;
      break;
    }
    const suffix = file.name.toLocaleLowerCase().match(/\.[^.]+$/)?.[0] ?? "";
    if (blockedSuffixes.has(suffix) || !supportedTypes.has(file.type || "application/octet-stream")) {
      inlineError.value = `不支持文件：${file.name}`;
      continue;
    }
    if (file.size > MAX_FILE_BYTES) {
      inlineError.value = `${file.name} 超过 5 MB`;
      continue;
    }
    if (file.type.startsWith("image/")) {
      if (imageCount >= 1) {
        inlineError.value = "一次最多添加 1 张图片";
        continue;
      }
      imageCount += 1;
    }
    accepted.push(file);
  }
  files.value.push(...accepted);
};

const handleInputFiles = (event: Event) => {
  const input = event.target as HTMLInputElement;
  validateFiles(Array.from(input.files ?? []));
  input.value = "";
};

const handleDrop = (event: DragEvent) => {
  event.preventDefault();
  dragging.value = false;
  validateFiles(Array.from(event.dataTransfer?.files ?? []));
};

const autosize = () => {
  if (!textarea.value) return;
  textarea.value.style.height = "auto";
  textarea.value.style.height = `${Math.min(textarea.value.scrollHeight, 160)}px`;
};

const submit = () => {
  if (props.busy || !content.value.trim()) return;
  inlineError.value = "";
  emit("submit", { content: content.value.trim(), files: [...files.value] });
};

const resetAfterSuccess = () => {
  content.value = "";
  files.value.forEach(revokeFile);
  files.value = [];
  if (textarea.value) textarea.value.style.height = "auto";
  void nextTick(() => textarea.value?.focus());
};

const focus = () => void nextTick(() => textarea.value?.focus());

const setError = (message: string) => {
  inlineError.value = message;
  focus();
};

const onKeydown = (event: KeyboardEvent) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  submit();
};

defineExpose({ resetAfterSuccess, focus, setError });

onBeforeUnmount(() => files.value.forEach(revokeFile));
</script>

<template>
  <form
    class="message-composer"
    :class="{ 'is-dragging': dragging }"
    @submit.prevent="submit"
    @dragenter.prevent="dragging = true"
    @dragover.prevent="dragging = true"
    @dragleave.self="dragging = false"
    @drop="handleDrop"
  >
    <div v-if="dragging" class="message-composer__drop" aria-hidden="true">放到这里添加附件</div>

    <ul v-if="files.length" class="attachment-strip" aria-label="待发送附件">
      <li v-for="file in files" :key="`${file.name}-${file.size}-${file.lastModified}`">
        <img v-if="file.type.startsWith('image/')" :src="previewFor(file)" alt="">
        <FileSpreadsheet v-else-if="file.name.toLowerCase().endsWith('.xlsx')" :size="16" aria-hidden="true" />
        <FileText v-else :size="16" aria-hidden="true" />
        <span>{{ file.name }}</span>
        <button type="button" :aria-label="`移除 ${file.name}`" @click="removeFile(file)">
          <X :size="14" aria-hidden="true" />
        </button>
      </li>
    </ul>

    <div class="message-composer__field">
      <textarea
        ref="textarea"
        v-model="content"
        data-testid="message-input"
        rows="1"
        maxlength="100000"
        :disabled="busy"
        aria-label="发消息给表演服数字员工"
        placeholder="描述产品、提出修改意见，或粘贴 Etsy 商品链接…"
        @input="autosize"
        @keydown="onKeydown"
      />

      <div class="message-composer__actions">
        <input
          ref="fileInput"
          data-testid="attachment-input"
          class="sr-only"
          type="file"
          multiple
          accept="image/png,image/jpeg,image/webp,.xlsx,.xls,.csv,.txt,.pdf,.zip"
          @change="handleInputFiles"
        >
        <button
          class="composer-icon"
          type="button"
          aria-label="添加附件"
          :disabled="busy"
          @click="fileInput?.click()"
        >
          <Paperclip :size="18" aria-hidden="true" />
        </button>
        <span class="message-composer__hint">Enter 发送 · Shift+Enter 换行</span>
        <button
          class="composer-send"
          data-testid="send-message"
          type="submit"
          aria-label="发送消息"
          :disabled="busy || !hasContent"
        >
          <Send :size="17" aria-hidden="true" />
          <span>{{ busy ? "处理中" : "发送" }}</span>
        </button>
      </div>
    </div>

    <p v-if="inlineError" class="message-composer__error" role="alert">{{ inlineError }}</p>
  </form>
</template>

<style scoped>
.message-composer {
  position: relative;
  width: min(820px, calc(100% - 32px));
  margin: 0 auto;
  padding: 0 0 16px;
}

.message-composer__field {
  overflow: hidden;
  border: 1px solid var(--border-strong);
  border-radius: var(--ds-radius-frame);
  background: var(--surface);
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.24);
  transition: border-color 140ms ease;
}

.message-composer__field:focus-within {
  border-color: rgba(255, 122, 26, 0.58);
}

.message-composer textarea {
  display: block;
  width: 100%;
  min-height: 54px;
  max-height: 160px;
  resize: none;
  padding: 15px 16px 8px;
  overflow-y: auto;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text);
  font-size: 14px;
  line-height: 1.55;
}

.message-composer textarea::placeholder {
  color: var(--text-muted);
}

.message-composer__actions {
  display: flex;
  min-height: 44px;
  align-items: center;
  gap: 8px;
  padding: 4px 7px 7px;
}

.composer-icon,
.composer-send,
.attachment-strip button {
  border: 0;
  border-radius: var(--ds-radius-control);
  cursor: pointer;
}

.composer-icon {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  background: transparent;
  color: var(--text-secondary);
}

.composer-icon:hover {
  background: var(--surface-raised);
  color: var(--text);
}

.message-composer__hint {
  color: var(--text-muted);
  font-size: 10px;
}

.composer-send {
  display: flex;
  min-height: 36px;
  align-items: center;
  gap: 7px;
  margin-left: auto;
  padding: 0 13px;
  background: var(--accent);
  color: #1a0d03;
  font-size: 12px;
  font-weight: 600;
}

.composer-send:hover:not(:disabled) {
  background: var(--ds-accent-hover);
}

.attachment-strip {
  display: flex;
  gap: 7px;
  margin: 0 0 7px;
  padding: 0;
  overflow-x: auto;
  list-style: none;
}

.attachment-strip li {
  display: flex;
  max-width: 230px;
  min-height: 38px;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
  padding: 4px 4px 4px 8px;
  border: 1px solid var(--border);
  border-radius: var(--ds-radius-control);
  background: var(--canvas-soft);
  color: var(--text-secondary);
}

.attachment-strip img {
  width: 28px;
  height: 28px;
  border-radius: var(--ds-radius-label);
  object-fit: cover;
}

.attachment-strip span {
  overflow: hidden;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-strip button {
  display: grid;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  place-items: center;
  background: transparent;
  color: var(--text-muted);
}

.message-composer__error {
  margin: 6px 2px 0;
  color: var(--danger);
  font-size: 11px;
}

.message-composer__drop {
  position: absolute;
  z-index: 5;
  inset: -8px -8px 8px;
  display: grid;
  place-items: center;
  border: 1px dashed var(--accent);
  border-radius: var(--ds-radius-frame);
  background: rgba(8, 9, 11, 0.94);
  color: var(--text);
  font-size: 13px;
  pointer-events: none;
}

@media (max-width: 640px) {
  .message-composer {
    width: calc(100% - 20px);
    padding-bottom: max(10px, env(safe-area-inset-bottom));
  }

  .message-composer__hint {
    display: none;
  }

  .composer-icon,
  .composer-send {
    min-width: 44px;
    min-height: 44px;
  }
}
</style>
