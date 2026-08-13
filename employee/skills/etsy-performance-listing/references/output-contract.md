# Listing 输出契约

## Employee control-frame contract

Hermes learning control frames must be emitted at the final tail of the response and should use one compact JSON object per line. The app parser also accepts bounded pretty-printed JSON objects for compatibility. Control frames are never user-visible; malformed, incomplete, or oversized JSON object tails are rejected fail-closed without persisting their raw content.

## Detached knowledge trust contract

A knowledge export is data, not proof of trust. The trusted caller must supply `expected_knowledge_export_id`, `expected_knowledge_payload_sha256`, and `expected_knowledge_file_sha256` independently of the export file. The export root has exactly `schema_version`, `export_id`, `issuer`, `records`, and `content_sha256`; every record has exactly `id`, `status`, `approved`, `abstract`, and `content_sha256`. Canonical SHA-256 uses UTF-8 JSON with sorted keys and compact separators. Only active, approved records with matching record digests may reach the model, and only their `id` and `abstract` fields are included in the prompt. A `signed: true` value inside the file has no authority and is not accepted.

契约版本由任务显式选择，并随每次结果返回。规则必须可版本化、可配置和可回滚；参考行、示例文字、列号、列顺序及当前工作簿习惯都不得硬编码为永久规则。

## 固定工作簿表头

1. `head titles`
2. `13 tags`
3. `SPECIFICATION`
4. `Category`
5. `Instructions for buyers`

规范化先执行 Unicode NFKC，再折叠全部 Unicode 空白（含换行）并去除首尾空白；之后执行区分大小写的精确匹配。列字母和顺序不固定。任一表头缺失、重复或处于合并单元格歧义区域时停止写入。

## 结构化 JSON 中间结果

中间结果必须与后端 `GeneratedListingFields` 一致：

```json
{
  "head_titles": "Original English title",
  "tags": ["tag one", "tag two"],
  "specification": "English specification text",
  "category": "Validated category value",
  "instructions_for_buyers": "English buyer instructions",
  "confidence": 0.0,
  "fact_warnings": ["中文事实警告"],
  "quality_warnings": ["中文质量警告"],
  "rule_version": "rules-v1"
}
```

约束：

- `extra = "forbid"`：严格禁止任何额外字段。
- 五个业务值必须来自当前商品行的可信事实与当前生效规则；未知内容进入警告，不得补写为事实。
- `tags` 是字符串数组；具体数量、长度、去重、格式和词汇限制由所选规则版本验证，不在本契约中永久固化参考行规则。
- `confidence` 范围为 0 到 1。
- 两类警告均为字符串数组；没有警告时返回空数组。
- `rule_version` 必须指向本次实际使用的规则版本，不能省略或写成未解析的默认值。
- 所有字符串去除首尾空白后必须非空（警告数组可以为空），不得含控制字符，也不得以 `=`, `+`, `-`, `@` 开头，避免 Excel 公式注入。
- 默认参考规则为标题 3–14 个词、13 个标签、每标签最多 20 个字符且规范化后互不重复；任务规则 JSON 可以覆盖这些默认值，若指定 `rule_version`，结果必须精确一致。
