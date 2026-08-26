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
- 默认 `mvp-default-v3` 规则为标题 3–14 个词且不超过 140 字符、13 个标签、每标签最多 20 个字符且规范化后互不重复；任务规则 JSON 可以覆盖这些默认值，若指定 `rule_version`，结果必须精确一致。
- `mvp-default-v3` 的 `specification` 值固定为五个非空行：首行必须精确为 `🌟 Product Highlights & Details`；其余四行必须使用互不重复的 Emoji，并采用 `Emoji 短标签: 一句事实描述` 格式。四条内容分别覆盖设计/版型、已验证的视觉或商品细节、适用场景以及搭配建议；搭配建议不得暗示推荐配件包含在商品中。
- 旧任务未声明 `specification_template_version` 时继续使用旧版 Emoji 分段格式，避免跨版本恢复失败。

## 图片可见事实中间结果

有图行先在独立会话中返回 schema-v1 视觉 JSON。根字段固定为 `schema_version`、`visible_facts`、`uncertain_observations`、`forbidden_inferences` 和 `image_usable`，禁止额外字段。`visible_facts` 仅允许以下数组字段：

- `product_family`
- `colors`
- `silhouette`
- `garment_structure`
- `decorations`
- `visible_components`
- `visual_style`

每个值必须是规范化、非空、无控制字符、无公式前缀、无路径或 URL 的短文本。视觉阶段只观察首张商品图；不得推断材质、尺寸、套装内容、未见配件、性能、品牌、认证、价格、库存、配送。行候选字段与视觉观察冲突时，行文本字段优先。最终 Listing 会话只接收脱敏合并上下文，不接收图片、图片路径或图片字节。

## 缺图行事件

没有 `image_paths` 的商品行必须在 `row_started` 之前发出：

```json
{
  "event": "row_skipped",
  "row_id": "<bounded row identity>",
  "row_number": 6,
  "reason": {
    "code": "missing_product_image",
    "message": "Product image is required; this row was skipped."
  }
}
```

该行不得调用 Hermes，也不得写入五个输出单元格。若全部商品行均缺图，任务以 `no_rows_with_images` 失败，不发布工作簿。
