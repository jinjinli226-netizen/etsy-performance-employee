<script setup lang="ts">
import { CloudOff, LoaderCircle, OctagonX, Square } from "lucide-vue-next";
import { computed, nextTick, onMounted, ref, watch } from "vue";

import type { HttpErrorCode } from "../api/client";
import ConversationList from "../features/chat/ConversationList.vue";
import LearningStatus from "../features/chat/LearningStatus.vue";
import MessageComposer from "../features/chat/MessageComposer.vue";
import MessageStream from "../features/chat/MessageStream.vue";
import { defaultChatStore, type ChatStore } from "../features/chat/chat.store";

const props = withDefaults(defineProps<{ store?: ChatStore }>(), { store: () => defaultChatStore });
const store = props.store;
const conversationCollapsed = ref(false);
const conversationMobileOpen = ref(false);
const learningMode = ref(false);
const composer = ref<InstanceType<typeof MessageComposer> | null>(null);

const errorLabels: Record<HttpErrorCode, string> = {
  bad_request: "请求内容无法处理",
  not_found: "这条对话已不存在",
  conflict: "当前对话正在处理中",
  invalid_input: "请检查消息或附件",
  employee_unavailable: "数字员工暂时不可用",
  timeout: "连接数字员工超时",
  network: "无法连接本地服务",
  server_error: "本地服务发生错误",
};

const connectionCopy = computed(() => {
  if (store.connectionStatus === "connecting") return "正在连接本地员工";
  if (store.errorCode) return errorLabels[store.errorCode];
  return "";
});

const hasCanonicalEtsyUrl = (content: string) => {
  for (const raw of content.match(/https?:\/\/[^\s]+/gi) ?? []) {
    try {
      const url = new URL(raw);
      const authority = raw.match(/^https:\/\/([^/]+)/)?.[1]?.toLowerCase();
      if (url.protocol !== "https:" || !["etsy.com", "www.etsy.com"].includes(authority ?? "") || !["etsy.com", "www.etsy.com"].includes(url.hostname) || url.username || url.password || url.port || url.hash) continue;
      const segments = url.pathname.split("/");
      if (![3, 4].includes(segments.length) || segments[1] !== "listing" || !/^\d+$/.test(segments[2])) continue;
      if (segments.length === 4 && (!segments[3] || !/^[A-Za-z0-9_-]+$/.test(segments[3]))) continue;
      return true;
    } catch { /* Invalid URLs are not learning evidence. */ }
  }
  return false;
};

const handleSubmit = async ({ content, files }: { content: string; files: File[] }) => {
  if (learningMode.value && !hasCanonicalEtsyUrl(content)) {
    composer.value?.setError("请在消息中加入 Etsy 商品链接（etsy.com/listing/…）");
    return;
  }
  const sent = await store.send(content, files, learningMode.value);
  if (sent) {
    learningMode.value = false;
    composer.value?.resetAfterSuccess();
  } else {
    composer.value?.focus();
  }
};

const retry = async () => {
  const retried = await store.retry();
  if (!retried) composer.value?.setError("重试未启动，请稍后再试");
  composer.value?.focus();
};

const createConversation = async () => {
  await store.createConversation();
  conversationMobileOpen.value = false;
  composer.value?.focus();
};

const selectConversation = async (id: number) => {
  await store.selectConversation(id);
  conversationMobileOpen.value = false;
  composer.value?.focus();
};

onMounted(() => {
  if (!store.conversations.length) void store.initialize();
  else if (store.currentConversationId && !store.messages.length) void store.selectConversation(store.currentConversationId);
});

watch(() => store.isBusy, async (busy, previous) => {
  if (previous && !busy) {
    await nextTick();
    composer.value?.focus();
  }
});
</script>

<template>
  <section class="chat-view" aria-label="长期对话工作区">
    <ConversationList
      :conversations="store.conversations"
      :current-id="store.currentConversationId"
      :collapsed="conversationCollapsed"
      :mobile-open="conversationMobileOpen"
      :has-more="store.hasMoreConversations"
      :loading-more="store.loadingMoreConversations"
      @select="selectConversation"
      @create="createConversation"
      @toggle-collapse="conversationCollapsed = !conversationCollapsed"
      @update:mobile-open="conversationMobileOpen = $event"
      @load-more="store.loadMoreConversations()"
    />

    <div class="chat-stage">
      <header class="chat-stage__toolbar">
        <div>
          <h2 data-testid="conversation-title">{{ store.currentConversation?.title ?? "长期对话" }}</h2>
          <span>{{ store.isBusy ? "正在处理" : store.currentConversation ? "内容已保存在本机" : "选择或新建一条对话" }}</span>
        </div>

        <LearningStatus
          :active="learningMode"
          :busy="store.isBusy"
          :candidate-statuses="store.learningStatuses"
          @toggle="learningMode = $event"
        />
      </header>

      <div v-if="connectionCopy" class="connection-banner" :class="`is-${store.connectionStatus}`">
        <LoaderCircle v-if="store.connectionStatus === 'connecting'" class="spin" :size="15" aria-hidden="true" />
        <CloudOff v-else :size="15" aria-hidden="true" />
        <span>{{ connectionCopy }}</span>
        <button v-if="store.connectionStatus !== 'connecting'" type="button" @click="store.initialize()">重试连接</button>
      </div>

      <MessageStream
        :messages="store.messages"
        :operation-status="store.operation?.status"
        :attachments-for="store.attachmentsFor"
        :loading="store.loading && !store.messages.length"
        @retry="retry"
      />

      <div v-if="store.currentConversation" class="chat-stage__bottom">
        <button
          v-if="store.isBusy"
          class="stop-waiting"
          type="button"
          title="只停止当前页面等待，不会中断数字员工后台任务"
          @click="store.stopWaiting()"
        >
          <Square :size="12" aria-hidden="true" />
          停止等待
        </button>
        <MessageComposer
          ref="composer"
          :busy="store.isBusy"
          :learning-mode="learningMode"
          @submit="handleSubmit"
        />
      </div>

      <div v-if="store.connectionStatus === 'error' && !store.errorCode" class="sr-only" aria-live="polite">
        <OctagonX :size="1" aria-hidden="true" />数字员工发生错误
      </div>
      <p class="sr-only" aria-live="polite">{{ connectionCopy }}</p>
    </div>
  </section>
</template>

<style scoped>
.chat-view {
  position: relative;
  display: flex;
  height: calc(100dvh - var(--topbar-height));
  min-height: 500px;
  overflow: hidden;
  background:
    linear-gradient(var(--border) 1px, transparent 1px) 0 0 / 100% 64px,
    var(--canvas);
}

.chat-stage {
  position: relative;
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.chat-stage__toolbar {
  display: flex;
  min-height: 51px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 7px 18px;
  border-bottom: 1px solid var(--border);
  background: rgba(8, 9, 11, 0.96);
}

.chat-stage__toolbar h2 {
  overflow: hidden;
  max-width: min(38vw, 430px);
  margin: 0;
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-stage__toolbar > div > span {
  display: block;
  margin-top: 1px;
  color: var(--text-muted);
  font-size: 9px;
}

.connection-banner {
  display: flex;
  min-height: 34px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border-bottom: 1px solid var(--border);
  background: var(--canvas-soft);
  color: var(--text-muted);
  font-size: 11px;
}

.connection-banner.is-error,
.connection-banner.is-offline {
  color: var(--warning);
}

.connection-banner button {
  min-height: 27px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: var(--ds-radius-label);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-size: 10px;
}

.chat-stage__bottom {
  position: relative;
  flex: 0 0 auto;
  background: linear-gradient(transparent, var(--canvas) 22px);
}

.stop-waiting {
  display: flex;
  min-height: 32px;
  align-items: center;
  gap: 6px;
  margin: 0 auto 7px;
  padding: 0 10px;
  border: 1px solid var(--border-strong);
  border-radius: var(--ds-radius-control);
  background: var(--surface);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 10px;
}

.spin { animation: spin 900ms linear infinite; }
@keyframes spin { to { transform: rotate(1turn); } }

@media (max-width: 760px) {
  .chat-view {
    height: calc(100dvh - 64px);
  }

  .chat-stage__toolbar {
    min-height: 61px;
    padding: 8px 10px 8px 96px;
  }

  .chat-stage__toolbar h2 {
    max-width: min(32vw, calc(100vw - 218px));
  }
}

@media (max-width: 480px) {
  .chat-stage__toolbar {
    align-items: flex-start;
  }

  .chat-stage__toolbar > div > span {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spin { animation: none; }
}
</style>
