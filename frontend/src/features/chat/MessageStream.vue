<script setup lang="ts">
import { AlertTriangle, ArrowDown, Bot, File, LoaderCircle, RotateCcw, UserRound } from "lucide-vue-next";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { Attachment, Message, OperationStatus } from "../../api/chat";

const props = defineProps<{
  messages: Message[];
  operationStatus?: OperationStatus | null;
  attachmentsFor: (messageId: number) => Attachment[];
  loading?: boolean;
}>();

defineEmits<{ retry: [] }>();

const viewport = ref<HTMLElement | null>(null);
const showNewMessages = ref(false);
let wasNearBottom = true;

const visibleMessages = computed(() => props.messages.map((message) => {
  if (message.role !== "assistant") return message;
  const visible = stripInternalFrames(message.content);
  return { ...message, content: visible };
}).filter((message) => message.role !== "system" && message.content));

function stripInternalFrames(content: string) {
  const lines = content.split("\n");
  const visible: string[] = [];
  for (let index = 0; index < lines.length;) {
    if (!lines[index].trimStart().startsWith("{")) {
      visible.push(lines[index++]);
      continue;
    }
    const block: string[] = [];
    let depth = 0;
    let inString = false;
    let escaped = false;
    let end = index;
    for (; end < lines.length && block.join("\n").length <= 128 * 1024; end += 1) {
      block.push(lines[end]);
      for (const char of lines[end]) {
        if (inString) {
          if (escaped) escaped = false;
          else if (char === "\\") escaped = true;
          else if (char === '"') inString = false;
        } else if (char === '"') inString = true;
        else if (char === "{" || char === "[") depth += 1;
        else if (char === "}" || char === "]") depth -= 1;
      }
      if (depth <= 0 && !inString) {
        end += 1;
        break;
      }
    }
    const raw = block.join("\n");
    const internal = /"(?:event|type)"\s*:\s*"(?:learning_batch|knowledge_candidate|control[^\"]*)"/i.test(raw);
    if (!internal) visible.push(...block);
    index = Math.max(end, index + 1);
  }
  return visible.join("\n").trim();
}

const failureCopy = computed(() => {
  if (props.operationStatus === "cancelled") return "本次请求已取消";
  if (props.operationStatus === "waiting_stopped") return "已停止在本页等待，可稍后刷新查看结果";
  if (props.operationStatus === "failed") return "数字员工没有完成本次请求";
  return "";
});

const nearBottom = () => {
  if (!viewport.value) return true;
  return viewport.value.scrollHeight - viewport.value.scrollTop - viewport.value.clientHeight < 96;
};

const handleScroll = () => {
  wasNearBottom = nearBottom();
  if (wasNearBottom) showNewMessages.value = false;
};

const scrollToBottom = (behavior: ScrollBehavior = "smooth") => {
  if (viewport.value?.scrollTo) viewport.value.scrollTo({ top: viewport.value.scrollHeight, behavior });
  else if (viewport.value) viewport.value.scrollTop = viewport.value.scrollHeight;
  showNewMessages.value = false;
  wasNearBottom = true;
};

watch(() => props.messages.length, async () => {
  await nextTick();
  if (wasNearBottom) scrollToBottom("smooth");
  else showNewMessages.value = true;
});

onMounted(() => {
  scrollToBottom("auto");
  viewport.value?.addEventListener("scroll", handleScroll, { passive: true });
});

onBeforeUnmount(() => viewport.value?.removeEventListener("scroll", handleScroll));
</script>

<template>
  <section ref="viewport" class="message-stream" aria-label="对话消息">
    <div v-if="loading" class="stream-state" role="status">
      <LoaderCircle class="spin" :size="18" aria-hidden="true" />
      <span>正在载入对话…</span>
    </div>

    <div v-else-if="!visibleMessages.length" class="stream-empty">
      <span class="stream-empty__mark" aria-hidden="true"><Bot :size="20" /></span>
      <h2>把产品信息交给员工</h2>
      <p>可以描述款式、面料、颜色与目标买家，也可以上传图片或资料。需要分析竞品时再开启教学模式。</p>
    </div>

    <div v-else class="message-stream__inner">
      <article
        v-for="message in visibleMessages"
        :key="message.id"
        class="message"
        :class="`is-${message.role}`"
      >
        <span class="message__avatar" aria-hidden="true">
          <UserRound v-if="message.role === 'user'" :size="16" />
          <Bot v-else :size="17" />
        </span>
        <div class="message__body">
          <span class="message__speaker">{{ message.role === "user" ? "你" : "表演服员工" }}</span>
          <p>{{ message.content }}</p>
          <ul v-if="attachmentsFor(message.id).length" class="message__attachments" aria-label="消息附件">
            <li v-for="attachment in attachmentsFor(message.id)" :key="attachment.id">
              <File :size="13" aria-hidden="true" />
              <span>{{ attachment.filename }}</span>
            </li>
          </ul>
        </div>
      </article>

      <div v-if="operationStatus === 'running'" class="operation-row" role="status">
        <LoaderCircle class="spin" :size="16" aria-hidden="true" />
        <span>员工正在思考并整理回答…</span>
      </div>

      <div v-else-if="failureCopy" class="operation-row is-error">
        <AlertTriangle :size="16" aria-hidden="true" />
        <span>{{ failureCopy }}</span>
        <button v-if="operationStatus !== 'waiting_stopped'" type="button" data-testid="retry-message" @click="$emit('retry')">
          <RotateCcw :size="14" aria-hidden="true" />
          重新发送
        </button>
      </div>
    </div>

    <button v-if="showNewMessages" class="new-messages" type="button" @click="scrollToBottom()">
      <ArrowDown :size="15" aria-hidden="true" />
      有新消息
    </button>
  </section>
</template>

<style scoped>
.message-stream {
  position: relative;
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.message-stream__inner {
  width: min(820px, calc(100% - 40px));
  margin: 0 auto;
  padding: 32px 0 120px;
}

.message {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 13px;
  padding: 18px 0 22px;
  border-bottom: 1px solid var(--border);
}

.message:last-of-type {
  border-bottom-color: transparent;
}

.message__avatar {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border: 1px solid var(--border-strong);
  border-radius: var(--ds-radius-control);
  background: var(--surface);
  color: var(--text-secondary);
}

.message.is-user .message__avatar {
  border-color: rgba(255, 122, 26, 0.34);
  color: var(--accent);
}

.message__body {
  min-width: 0;
}

.message__speaker {
  display: block;
  margin: 4px 0 8px;
  color: var(--text-muted);
  font-size: 10px;
  font-weight: 600;
}

.message p {
  margin: 0;
  color: var(--text);
  font-size: 14px;
  line-height: 1.72;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.message.is-user p {
  color: #dfe3e8;
}

.message__attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}

.message__attachments li {
  display: flex;
  max-width: 260px;
  min-height: 28px;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: var(--ds-radius-label);
  color: var(--text-muted);
  font-size: 10px;
}

.message__attachments span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.operation-row,
.stream-state {
  display: flex;
  min-height: 44px;
  align-items: center;
  gap: 9px;
  color: var(--text-muted);
  font-size: 12px;
}

.operation-row {
  padding: 12px 0 28px 43px;
}

.operation-row.is-error {
  color: var(--danger);
}

.operation-row button {
  display: flex;
  min-height: 34px;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
  padding: 0 10px;
  border: 1px solid var(--border-strong);
  border-radius: var(--ds-radius-control);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-size: 11px;
}

.stream-state,
.stream-empty {
  width: min(620px, calc(100% - 40px));
  margin: 16vh auto 0;
}

.stream-empty__mark {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-left: 2px solid var(--accent);
  background: var(--surface);
  color: var(--text-secondary);
}

.stream-empty h2 {
  margin: 17px 0 7px;
  font-size: 20px;
  font-weight: 600;
}

.stream-empty p {
  max-width: 540px;
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.65;
}

.new-messages {
  position: sticky;
  bottom: 12px;
  display: flex;
  min-height: 36px;
  align-items: center;
  gap: 6px;
  margin: 0 auto;
  padding: 0 11px;
  border: 1px solid var(--border-strong);
  border-radius: var(--ds-radius-control);
  background: var(--surface-raised);
  color: var(--text);
  cursor: pointer;
  font-size: 11px;
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.3);
}

.spin {
  animation: spin 900ms linear infinite;
}

@keyframes spin {
  to { transform: rotate(1turn); }
}

@media (max-width: 640px) {
  .message-stream__inner {
    width: calc(100% - 24px);
    padding-top: 66px;
  }

  .message {
    grid-template-columns: 28px minmax(0, 1fr);
    gap: 10px;
  }

  .message__avatar {
    width: 28px;
    height: 28px;
  }

  .operation-row {
    padding-left: 38px;
  }

  .operation-row button {
    min-height: 44px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spin { animation: none; }
}
</style>
