<script setup lang="ts">
import { Menu } from "lucide-vue-next";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { fetchEmployeeStatus } from "../api/employee";
import AppSidebar from "../components/AppSidebar.vue";
import EmployeeStatus, { type EmployeeState } from "../components/EmployeeStatus.vue";

const SIDEBAR_KEY = "etsy-workspace-sidebar-collapsed";
const MOBILE_BREAKPOINT = 640;
const EMPLOYEE_STATUS_POLL_MS = 2500;

const route = useRoute();
const employeeStatus = ref<EmployeeState>("offline");
let employeeStatusTimer: number | undefined;
let employeeStatusController: AbortController | undefined;

const refreshEmployeeStatus = async () => {
  employeeStatusController?.abort();
  const controller = new AbortController();
  employeeStatusController = controller;
  try {
    const { status } = await fetchEmployeeStatus(controller.signal);
    if (controller.signal.aborted) return;
    employeeStatus.value = status;
  } catch {
    if (controller.signal.aborted) return;
    employeeStatus.value = "error";
  }
};
const mobile = ref(false);
const mobileOpen = ref(false);
const menuButton = ref<HTMLButtonElement | null>(null);
const pageHeading = ref<HTMLHeadingElement | null>(null);
let hasHandledInitialRoute = false;
let bodyScrollLocked = false;
let previousBodyOverflow = "";
const focusableSelector = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

const readCollapsedPreference = () => {
  try {
    const value = localStorage.getItem(SIDEBAR_KEY);
    return value === "true";
  } catch {
    return false;
  }
};

const collapsed = ref(readCollapsedPreference());
const routeTitle = computed(() => String(route.meta.title ?? "工作台"));

const updateViewport = () => {
  mobile.value = window.innerWidth <= MOBILE_BREAKPOINT;
  if (!mobile.value) mobileOpen.value = false;
};

const persistCollapsed = () => {
  collapsed.value = !collapsed.value;
  try {
    localStorage.setItem(SIDEBAR_KEY, String(collapsed.value));
  } catch {
    // The workspace remains usable when browser storage is unavailable.
  }
};

const openMobile = () => {
  mobileOpen.value = true;
  void nextTick(() => {
    document.querySelector<HTMLElement>("#workspace-navigation a")?.focus();
  });
};

const closeMobile = (restoreFocus = false) => {
  if (!mobileOpen.value) return;
  mobileOpen.value = false;
  if (restoreFocus) void nextTick(() => menuButton.value?.focus());
};

const lockBodyScroll = () => {
  if (bodyScrollLocked) return;
  previousBodyOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  bodyScrollLocked = true;
};

const unlockBodyScroll = () => {
  if (!bodyScrollLocked) return;
  if (document.body.style.overflow === "hidden") {
    document.body.style.overflow = previousBodyOverflow;
  }
  bodyScrollLocked = false;
  previousBodyOverflow = "";
};

const handleKeydown = (event: KeyboardEvent) => {
  if (!mobileOpen.value) return;
  if (event.key === "Escape") {
    closeMobile(true);
    return;
  }
  if (event.key !== "Tab") return;

  const drawer = document.getElementById("workspace-navigation");
  const focusables = Array.from(drawer?.querySelectorAll<HTMLElement>(focusableSelector) ?? []);
  const first = focusables.find((element) => element.getAttribute("aria-label") === "关闭导航菜单") ?? focusables[0];
  const last = focusables.at(-1);
  if (!first || !last) {
    event.preventDefault();
    return;
  }

  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  } else if (!drawer?.contains(document.activeElement)) {
    event.preventDefault();
    first.focus();
  }
};

watch(mobileOpen, (isOpen) => {
  if (isOpen) lockBodyScroll();
  else unlockBodyScroll();
});

watch(
  () => route.fullPath,
  async () => {
    document.title = `${routeTitle.value} · Etsy 表演服数字员工`;
    if (!hasHandledInitialRoute) {
      hasHandledInitialRoute = true;
      return;
    }
    closeMobile();
    await nextTick();
    pageHeading.value?.focus();
  },
  { immediate: true },
);

onMounted(() => {
  updateViewport();
  window.addEventListener("resize", updateViewport);
  window.addEventListener("keydown", handleKeydown);
  void refreshEmployeeStatus();
  employeeStatusTimer = window.setInterval(
    () => void refreshEmployeeStatus(),
    EMPLOYEE_STATUS_POLL_MS,
  );
});

onBeforeUnmount(() => {
  unlockBodyScroll();
  window.removeEventListener("resize", updateViewport);
  window.removeEventListener("keydown", handleKeydown);
  if (employeeStatusTimer !== undefined) {
    window.clearInterval(employeeStatusTimer);
    employeeStatusTimer = undefined;
  }
  employeeStatusController?.abort();
  employeeStatusController = undefined;
});
</script>

<template>
  <div
    class="workspace"
    :class="{ 'is-sidebar-collapsed': collapsed && !mobile }"
    data-workspace
  >
    <AppSidebar
      :collapsed="collapsed"
      :mobile="mobile"
      :mobile-open="mobileOpen"
      :employee-status="employeeStatus"
      @toggle-collapse="persistCollapsed"
      @close-mobile="closeMobile"
    />

    <button
      v-if="mobile && mobileOpen"
      class="workspace__backdrop"
      type="button"
      data-testid="drawer-backdrop"
      aria-label="关闭导航菜单"
      @click="closeMobile(true)"
    />

    <div class="workspace__stage" :inert="mobile && mobileOpen ? true : undefined">
      <header class="workspace__topbar">
        <button
          v-if="mobile"
          ref="menuButton"
          class="icon-button workspace__menu"
          type="button"
          aria-label="打开导航菜单"
          :aria-expanded="mobileOpen"
          aria-controls="workspace-navigation"
          @click="openMobile"
        >
          <Menu :size="19" aria-hidden="true" />
        </button>

        <div class="workspace__heading">
          <span class="workspace__breadcrumb">表演服员工 / 工作台</span>
          <h1 ref="pageHeading" tabindex="-1">{{ routeTitle }}</h1>
        </div>

        <EmployeeStatus class="workspace__status" :status="employeeStatus" compact announce />
      </header>

      <main id="main-content" class="workspace__content" tabindex="-1">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.workspace {
  min-height: 100dvh;
  background: var(--canvas);
}

.workspace__stage {
  min-width: 0;
  min-height: 100dvh;
  margin-left: var(--sidebar-width);
  transition: margin-left 180ms var(--ds-ease);
}

.workspace.is-sidebar-collapsed .workspace__stage {
  margin-left: var(--sidebar-width-collapsed);
}

.workspace__topbar {
  position: sticky;
  z-index: 20;
  top: 0;
  display: flex;
  min-height: var(--topbar-height);
  align-items: center;
  gap: 14px;
  padding: 9px 24px;
  border-bottom: 1px solid var(--border);
  background: rgba(8, 9, 11, 0.96);
}

.workspace__heading {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.workspace__breadcrumb {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace__heading h1 {
  margin: 0;
  color: var(--text);
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
}

.workspace__status {
  margin-left: auto;
}

.workspace__content {
  min-width: 0;
  min-height: calc(100dvh - var(--topbar-height));
}

.workspace__menu {
  flex: 0 0 auto;
}

.workspace__backdrop {
  position: fixed;
  z-index: 25;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
  border-radius: 0;
  background: rgba(0, 0, 0, 0.66);
  cursor: default;
}

@media (max-width: 640px) {
  .workspace__stage,
  .workspace.is-sidebar-collapsed .workspace__stage {
    margin-left: 0;
  }

  .workspace__topbar {
    min-height: 64px;
    padding: 8px 12px;
  }

  .workspace__breadcrumb {
    max-width: calc(100vw - 140px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .workspace__stage {
    transition: none;
  }
}
</style>
