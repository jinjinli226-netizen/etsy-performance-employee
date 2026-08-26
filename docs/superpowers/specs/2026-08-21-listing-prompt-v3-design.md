# Listing Prompt v3 Design

## Goal

Make every newly created Excel job produce a concise Etsy US listing with a predictable, customer-facing Specification format while preserving compatibility with already-created v2 jobs.

## Version boundary

- New jobs use `mvp-default-v3` and set `specification_template_version` to `3`.
- Existing jobs without `specification_template_version` keep the prior emoji-section contract.
- The completed v2 workbook is not regenerated.

## v3 generation contract

- Act as an Etsy US operations expert and write English output.
- Title stays within 140 characters, remains concise and search-rich, and excludes movie/character/IP, celebrity, and influencer names.
- Produce exactly 13 distinct tags, each at most 20 characters including spaces.
- Specification contains exactly five nonblank lines:
  1. `🌟 Product Highlights & Details`
  2. Four unique emoji-led bullets in `Short Label: one concise sentence` form.
- The four bullets cover design/silhouette, a verified visual or product detail, occasions/use, and styling recommendations.
- Occasion keywords such as Burning Man or Halloween are used only when supported and appropriate.
- Styling is optional advice and must not imply that suggested accessories are included.
- Size, material, handmade status, and included pieces remain forbidden unless verified.

## Validation

For template version 3, the validator rejects a wrong heading, repeated emoji, a bullet without a colon-delimited label, or the wrong line count. Semantic coverage is requested in the prompt but not guessed by a brittle keyword validator.
