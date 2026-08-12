<script setup lang="ts">
const capabilities = ["Excel 自动化", "长期对话"];
</script>

<template>
  <main>
    <h1>Etsy Performance Employee</h1>
    <ul aria-label="核心能力">
      <li v-for="capability in capabilities" :key="capability">{{ capability }}</li>
    </ul>
  </main>
</template>
