<script setup lang="ts">
import { BookOpenCheck } from "lucide-vue-next";

defineProps<{ active: boolean; busy: boolean }>();
defineEmits<{ toggle: [active: boolean] }>();
</script>

<template>
  <div class="learning-mode" :class="{ 'is-active': active }">
    <BookOpenCheck :size="15" aria-hidden="true" />
    <div>
      <label>
        <input
          data-testid="learning-toggle"
          type="checkbox"
          :checked="active"
          :disabled="busy"
          @change="$emit('toggle', ($event.target as HTMLInputElement).checked)"
        >
        <span>教学模式</span>
      </label>
      <small v-if="active">只分析消息中的 Etsy 商品链接，学习后生成时不会照抄竞品。</small>
    </div>
  </div>
</template>

<style scoped>
.learning-mode {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 8px;
  color: var(--text-muted);
}

.learning-mode.is-active {
  color: var(--warning);
}

.learning-mode > svg {
  margin-top: 3px;
  flex: 0 0 auto;
}

.learning-mode label {
  display: flex;
  min-height: 24px;
  align-items: center;
  gap: 7px;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 11px;
  font-weight: 500;
}

.learning-mode input {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: var(--accent);
}

.learning-mode small {
  display: block;
  max-width: 480px;
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.45;
}
</style>
