<script setup lang="ts">
import { Menu, MessageSquarePlus, PanelLeftClose, X } from "lucide-vue-next";

import type { Conversation } from "../../api/chat";

defineProps<{
  conversations: Conversation[];
  currentId: number | null;
  collapsed: boolean;
  mobileOpen: boolean;
  hasMore: boolean;
  loadingMore: boolean;
}>();

defineEmits<{
  select: [id: number];
  create: [];
  "toggle-collapse": [];
  "update:mobile-open": [open: boolean];
  "load-more": [];
}>();
</script>

<template>
  <button
    v-if="!mobileOpen"
    class="conversation-mobile-trigger"
    type="button"
    data-testid="conversation-mobile-trigger"
    aria-label="打开对话列表"
    @click="$emit('update:mobile-open', true)"
  >
    <Menu :size="19" aria-hidden="true" />
    <span>对话</span>
  </button>

  <button
    v-if="mobileOpen"
    class="conversation-backdrop"
    type="button"
    aria-label="关闭对话列表"
    @click="$emit('update:mobile-open', false)"
  />

  <aside
    class="conversation-list"
    :class="{ 'is-collapsed': collapsed, 'is-mobile-open': mobileOpen }"
    aria-label="长期对话列表"
  >
    <div class="conversation-list__top">
      <span v-if="!collapsed" class="conversation-list__label">对话记录</span>
      <button
        class="conversation-list__icon"
        type="button"
        :aria-label="mobileOpen ? '关闭对话列表' : collapsed ? '展开对话列表' : '收起对话列表'"
        @click="mobileOpen ? $emit('update:mobile-open', false) : $emit('toggle-collapse')"
      >
        <X v-if="mobileOpen" :size="17" aria-hidden="true" />
        <PanelLeftClose v-else :size="17" aria-hidden="true" />
      </button>
    </div>

    <button
      class="conversation-list__new"
      type="button"
      data-testid="new-conversation"
      :title="collapsed ? '新建对话' : undefined"
      @click="$emit('create')"
    >
      <MessageSquarePlus :size="17" aria-hidden="true" />
      <span v-if="!collapsed">新建对话</span>
    </button>

    <nav v-if="!collapsed" class="conversation-list__items" aria-label="历史对话">
      <button
        v-for="conversation in conversations"
        :key="conversation.id"
        class="conversation-item"
        :class="{ 'is-active': conversation.id === currentId }"
        type="button"
        :data-testid="`conversation-${conversation.id}`"
        :aria-current="conversation.id === currentId ? 'page' : undefined"
        @click="$emit('select', conversation.id)"
      >
        <span>{{ conversation.title }}</span>
        <time :datetime="conversation.updated_at">
          {{ new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(new Date(conversation.updated_at)) }}
        </time>
      </button>
      <p v-if="!conversations.length" class="conversation-list__empty">暂无对话</p>
      <button
        v-if="hasMore"
        class="conversation-list__more"
        type="button"
        data-testid="load-more-conversations"
        :disabled="loadingMore"
        @click="$emit('load-more')"
      >{{ loadingMore ? "正在加载" : "加载更多对话" }}</button>
    </nav>
  </aside>
</template>

<style scoped>
.conversation-list {
  position: relative;
  z-index: 6;
  display: flex;
  width: 236px;
  min-width: 236px;
  flex-direction: column;
  border-right: 1px solid var(--border);
  background: var(--canvas-soft);
  transition: width 180ms var(--ds-ease), min-width 180ms var(--ds-ease);
}

.conversation-list.is-collapsed {
  width: 60px;
  min-width: 60px;
  align-items: center;
}

.conversation-list__top {
  display: flex;
  height: 51px;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid var(--border);
}

.conversation-list.is-collapsed .conversation-list__top {
  justify-content: center;
  padding: 0;
}

.conversation-list__label {
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
}

.conversation-list__icon,
.conversation-list__new,
.conversation-item,
.conversation-mobile-trigger {
  border: 0;
  border-radius: var(--ds-radius-control);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.conversation-list__icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
}

.conversation-list__icon:hover,
.conversation-item:hover {
  background: var(--surface);
  color: var(--text);
}

.conversation-list__new {
  display: flex;
  min-height: 40px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 12px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
  font-weight: 500;
}

.conversation-list__new:hover {
  border-color: rgba(255, 122, 26, 0.48);
}

.conversation-list.is-collapsed .conversation-list__new {
  width: 38px;
  margin: 12px 0;
}

.conversation-list__items {
  display: grid;
  gap: 3px;
  padding: 0 8px 16px;
  overflow-y: auto;
}

.conversation-item {
  display: grid;
  min-height: 52px;
  gap: 3px;
  padding: 8px 10px;
  text-align: left;
}

.conversation-item.is-active {
  background: var(--surface-raised);
  color: var(--text);
  box-shadow: inset 2px 0 var(--accent);
}

.conversation-item span {
  overflow: hidden;
  font-size: 12px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-item time,
.conversation-list__empty {
  color: var(--text-muted);
  font-size: 10px;
}

.conversation-list__more {
  min-height: 44px;
  border: 0;
  border-radius: var(--ds-radius-control);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 12px;
}

.conversation-list__more:hover { background: var(--surface); color: var(--text); }
.conversation-list__more:disabled { cursor: wait; opacity: 0.65; }

.conversation-list__empty {
  padding: 8px 10px;
}

.conversation-mobile-trigger,
.conversation-backdrop {
  display: none;
}

@media (max-width: 760px) {
  .conversation-list {
    position: fixed;
    z-index: 42;
    inset: var(--topbar-height) auto 0 0;
    width: min(280px, 86vw);
    min-width: 0;
    transform: translateX(-102%);
    transition: transform 180ms var(--ds-ease);
  }

  .conversation-list.is-collapsed {
    width: min(280px, 86vw);
    align-items: stretch;
  }

  .conversation-list.is-mobile-open {
    transform: translateX(0);
  }

  .conversation-mobile-trigger {
    position: absolute;
    z-index: 5;
    top: 10px;
    left: 10px;
    display: flex;
    min-height: 44px;
    align-items: center;
    gap: 7px;
    padding: 0 12px;
    border: 1px solid var(--border);
    background: var(--canvas-soft);
    font-size: 12px;
  }

  .conversation-backdrop {
    position: fixed;
    z-index: 41;
    inset: var(--topbar-height) 0 0;
    display: block;
    width: 100%;
    height: 100%;
    border: 0;
    border-radius: 0;
    background: rgba(0, 0, 0, 0.66);
  }
}
</style>
