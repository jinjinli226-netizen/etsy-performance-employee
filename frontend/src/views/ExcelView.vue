<script setup lang="ts">
import { FileSpreadsheet, Upload } from "lucide-vue-next";
</script>

<template>
  <section class="tool-view excel-placeholder" aria-labelledby="excel-empty-title">
    <div class="excel-placeholder__intro">
      <div>
        <span class="tool-view__eyebrow">Excel 自动化</span>
        <h2 id="excel-empty-title">生成 Listing 表格</h2>
        <p>上传产品工作簿后，数字员工会处理每个商品行并生成新的文件副本。</p>
      </div>
      <FileSpreadsheet :size="24" aria-hidden="true" />
    </div>

    <div class="upload-placeholder" aria-label="Excel 上传区域">
      <span class="upload-placeholder__icon" aria-hidden="true"><Upload :size="21" /></span>
      <strong>Excel 上传将在下一阶段接入</strong>
      <span>仅支持 .xlsx，原文件不会被覆盖</span>
      <button type="button" disabled>选择工作簿</button>
    </div>
  </section>
</template>
