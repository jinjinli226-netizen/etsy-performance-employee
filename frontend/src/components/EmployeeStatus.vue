<script setup lang="ts">
import { computed } from "vue";

export type EmployeeState = "online" | "busy" | "offline" | "error";

const props = withDefaults(
  defineProps<{
    status: EmployeeState;
    compact?: boolean;
  }>(),
  { compact: false },
);

const labels: Record<EmployeeState, string> = {
  online: "在线",
  busy: "工作中",
  offline: "离线",
  error: "异常",
};

const label = computed(() => labels[props.status]);
</script>

<template>
  <div
    class="employee-status"
    :class="[`is-${status}`, { 'is-compact': compact }]"
    role="status"
    aria-live="polite"
    :aria-label="`数字员工状态：${label}`"
    :title="compact ? `数字员工状态：${label}` : undefined"
  >
    <span class="employee-status__dot" data-status-dot aria-hidden="true" />
    <span v-if="!compact" class="employee-status__copy">
      <strong>表演服员工</strong>
      <small>{{ label }}</small>
    </span>
    <span v-else class="sr-only">{{ label }}</span>
  </div>
</template>

<style scoped>
.employee-status {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
  color: var(--text-secondary);
}

.employee-status__dot {
  width: 7px;
  height: 7px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--text-muted);
}

.employee-status.is-online .employee-status__dot {
  background: var(--success);
}

.employee-status.is-busy .employee-status__dot {
  background: var(--warning);
}

.employee-status.is-error .employee-status__dot {
  background: var(--danger);
}

.employee-status__copy {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.employee-status__copy strong {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.employee-status__copy small {
  color: var(--text-muted);
  font-size: 11px;
}

.employee-status.is-compact {
  justify-content: center;
}
</style>
