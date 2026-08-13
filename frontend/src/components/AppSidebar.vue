<script setup lang="ts">
import {
  ChevronLeft,
  ChevronRight,
  FileSpreadsheet,
  MessageSquareText,
  X,
} from "lucide-vue-next";

import EmployeeStatus, { type EmployeeState } from "./EmployeeStatus.vue";

defineProps<{
  collapsed: boolean;
  mobile: boolean;
  mobileOpen: boolean;
  employeeStatus: EmployeeState;
}>();

defineEmits<{
  toggleCollapse: [];
  closeMobile: [];
}>();

const navigation = [
  { to: "/chat", label: "长期对话", icon: MessageSquareText },
  { to: "/excel", label: "Listing 表格", icon: FileSpreadsheet },
] as const;
</script>

<template>
  <aside
    id="workspace-navigation"
    class="app-sidebar"
    :class="{ 'is-collapsed': collapsed, 'is-mobile': mobile, 'is-open': mobileOpen }"
    :aria-hidden="mobile ? !mobileOpen : false"
    :inert="mobile && !mobileOpen ? true : undefined"
  >
    <div class="app-sidebar__identity">
      <span class="app-sidebar__monogram" aria-hidden="true">演</span>
      <div v-if="!collapsed || mobile" class="app-sidebar__brand">
        <strong>Etsy 表演服数字员工</strong>
        <span>Listing 工作台</span>
      </div>
      <button
        v-if="mobile"
        class="icon-button app-sidebar__close"
        type="button"
        aria-label="关闭导航菜单"
        :tabindex="mobile && !mobileOpen ? -1 : undefined"
        @click="$emit('closeMobile')"
      >
        <X :size="18" aria-hidden="true" />
      </button>
    </div>

    <nav class="app-sidebar__nav" data-testid="primary-navigation" aria-label="主要功能">
      <RouterLink
        v-for="item in navigation"
        :key="item.to"
        :to="item.to"
        class="app-sidebar__link"
        :title="collapsed && !mobile ? item.label : undefined"
        :aria-label="collapsed && !mobile ? item.label : undefined"
        :tabindex="mobile && !mobileOpen ? -1 : undefined"
        @click="$emit('closeMobile')"
      >
        <component :is="item.icon" :size="18" aria-hidden="true" />
        <span v-if="!collapsed || mobile">{{ item.label }}</span>
      </RouterLink>
    </nav>

    <div class="app-sidebar__footer">
      <EmployeeStatus :status="employeeStatus" :compact="collapsed && !mobile" />
      <button
        v-if="!mobile"
        class="app-sidebar__collapse"
        type="button"
        :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'"
        :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
        @click="$emit('toggleCollapse')"
      >
        <component :is="collapsed ? ChevronRight : ChevronLeft" :size="17" aria-hidden="true" />
        <span v-if="!collapsed">收起侧边栏</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.app-sidebar {
  position: fixed;
  z-index: 30;
  inset: 0 auto 0 0;
  display: grid;
  width: var(--sidebar-width);
  grid-template-rows: auto 1fr auto;
  overflow: hidden;
  border-right: 1px solid var(--border);
  background: var(--canvas-soft);
  transition: width 180ms var(--ds-ease), transform 200ms var(--ds-ease);
}

.app-sidebar.is-collapsed:not(.is-mobile) {
  width: var(--sidebar-width-collapsed);
}

.app-sidebar__identity {
  display: flex;
  min-height: 72px;
  align-items: center;
  gap: 11px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}

.app-sidebar.is-collapsed:not(.is-mobile) .app-sidebar__identity {
  justify-content: center;
  padding-inline: 10px;
}

.app-sidebar__monogram {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid rgba(255, 122, 26, 0.5);
  border-radius: var(--ds-radius-control);
  background: rgba(255, 122, 26, 0.08);
  color: var(--accent);
  font-size: 14px;
  font-weight: 600;
}

.app-sidebar__brand {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.app-sidebar__brand strong {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-sidebar__brand span {
  color: var(--text-muted);
  font-size: 11px;
}

.app-sidebar__close {
  margin-left: auto;
}

.app-sidebar__nav {
  display: grid;
  align-content: start;
  gap: 4px;
  padding: 16px 10px;
}

.app-sidebar__link {
  position: relative;
  display: flex;
  min-height: 40px;
  align-items: center;
  gap: 11px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: var(--ds-radius-control);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  transition: color 140ms ease, background-color 140ms ease, border-color 140ms ease;
}

.app-sidebar.is-collapsed:not(.is-mobile) .app-sidebar__link {
  justify-content: center;
  padding-inline: 0;
}

.app-sidebar__link:hover {
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
}

.app-sidebar__link.router-link-active {
  border-color: var(--border);
  background: var(--surface-raised);
  color: var(--text);
}

.app-sidebar__link.router-link-active::before {
  position: absolute;
  inset: 8px auto 8px -1px;
  width: 2px;
  border-radius: 0 2px 2px 0;
  background: var(--accent);
  content: "";
}

.app-sidebar__footer {
  display: grid;
  gap: 14px;
  padding: 16px;
  border-top: 1px solid var(--border);
}

.app-sidebar.is-collapsed:not(.is-mobile) .app-sidebar__footer {
  padding-inline: 10px;
}

.app-sidebar__collapse {
  display: flex;
  min-height: 36px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px solid var(--border);
  border-radius: var(--ds-radius-control);
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.app-sidebar__collapse:hover {
  border-color: var(--border-strong);
  color: var(--text);
}

@media (max-width: 640px) {
  .app-sidebar.is-mobile {
    width: min(280px, calc(100vw - 40px));
    transform: translateX(-101%);
    box-shadow: 16px 0 40px rgba(0, 0, 0, 0.36);
  }

  .app-sidebar.is-mobile.is-open {
    transform: translateX(0);
  }

  .app-sidebar__identity {
    min-height: 64px;
    padding: 10px 12px;
  }

  .app-sidebar__link,
  .app-sidebar__collapse {
    min-height: 44px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .app-sidebar,
  .app-sidebar__link {
    transition: none;
  }
}
</style>
