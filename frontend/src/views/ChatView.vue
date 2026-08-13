<script setup lang="ts">
import { MessageSquareText } from "lucide-vue-next";
</script>

<template>
  <section class="tool-view chat-placeholder" aria-labelledby="chat-empty-title">
    <div class="empty-tool-state">
      <span class="empty-tool-state__icon" aria-hidden="true">
        <MessageSquareText :size="22" />
      </span>
      <div>
        <h2 id="chat-empty-title">开始长期对话</h2>
        <p>在这里持续教员工理解表演服产品、Listing 规范与审核反馈。</p>
      </div>
    </div>
  </section>
</template>
