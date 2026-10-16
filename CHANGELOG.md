# Changelog

## 0.8.7 — 2026-10-17

- Document the configured Azure deployment, endpoint, API version, request settings,
  image/text budgets, and names-only Credits policy in README without the API key.
- Normalize the CSV to three columns, clean whitespace and repeated Quality rows,
  and preserve all 16 field definitions, choices, and ordering.
- Consolidate the extraction prompt into one consistent set of mode, evidence,
  Product, Credits, People/Role, Grade, technical, and Restrictions rules.

## 0.8.6 — 2026-10-17

- Credits now contains contributor names only, excluding accompanying titles,
  grade labels, school names, and scene descriptions. Readable names in the first
  five seconds take precedence; otherwise use an unambiguous filename name.
- Preserve explicit JSON image-to-time mappings for opening image review, including
  timecodes, seconds, milliseconds, and frame indices with FPS. Ambiguous links
  or conflicting times are reported instead of guessing from archive order.

## 0.8.5 — 2026-10-17

- Search every prepared current-video text source for contextual product mentions,
  including OCR, auxiliary JSON, and filenames, and reconcile every visual batch.
- Batch large sets of product excerpts without truncation, preserving source
  references and the verified Product hierarchy. Mentions still require semantic
  review for relevance and negation rather than automatic tagging.
- Show product-review coverage alongside existing skipped/unreadable file warnings;
  public metadata exports retain the existing schema.

## 0.8.4 — 2026-10-17

- Preserve detailed transcript phrases even when combined text is present.
- Supply original JSON attributes directly to final reconciliation so missed chunk
  candidates do not hide schools, ownership, dates, or other explicit metadata.
- Clarify visible People versus speaker Role, require an independent completeness
  review of every field, and exclude equipment labels from credits/product inference.

## 0.8.3 — 2026-10-17

- Retain contextual transcript product mentions in final reconciliation even when
  chunk summaries miss them. Mentions require semantic review, including negation
  and relevance; they never automatically populate Product.
- Clarify i-Ready versus i-Ready Classroom Mathematics and preserve the verified
  Core Math hierarchy without inventing parents for other products.
- Check speech/caption/subtitle JSON and auxiliary spoken snippets as well as
  transcript-named files. Regression checks cover every allowed product and
  sequential videos without carrying mentions between videos.

## 0.8.2 — 2026-10-17

- Added a Python fallback for explicit numeric filename grades, numeric grade
  ranges, preschool, and kindergarten. Merge these with validated model grades;
  never use reference filenames or infer grades from appearance. Transcript
  negation, written-number grades, and visual interpretation remain model tasks.
- Preserve current assessment-tag sources alongside the catalog policy when
  enforcing internal-only restrictions. Public exports still contain values only.

## 0.8.1 — 2026-10-16

- Accepted one-record JSON arrays as reference examples so corrected individual
  metadata downloads can be reused directly. Multi-record files still need splitting.

## 0.8.0 — 2026-10-16

- Added speaker/interviewee Role separately from visible People, with six role choices.
- Removed the teacher/coach interview Grade exclusion; include affirmative transcript
  and filename mentions, historical responsibilities, and expanded grade ranges.
- Added Pre-K and grades 9–12. Older examples may omit Role; old results require
  fresh extraction. Metadata stays source-free and downloads remain manual.

## 0.7.1 — 2026-10-15

- Excluded teaching/coaching grade mentions from teacher/coach-only interviews,
  with transcript-context instructions and a final Grade validation guard.
- Preserved supported grades for lessons, student interviews, and student-associated
  grades in mixed interviews. Filename/title grades cannot override the exclusion.

## 0.7.0 — 2026-10-15

- Added internal archive-member inventory and separate coverage counts/warnings.
- Preserved contextual OCR, transcript timing, label/object observations, auxiliary
  JSON attributes, and spoken snippets from emotion files without using predictions.
- Added explicit source-video property precedence and conflict handling, original
  supporting excerpts for long-input reconciliation, and a final field checklist.
- Kept metadata downloads limited to template fields and disabled autosave.

## 0.6.3 — 2026-10-15

- Explicitly supplied the video filename to every text chunk, image batch, and
  final reconciliation request. Required a filename metadata pass for relevant
  fields, with current-video evidence taking priority over conflicting hints.

## 0.6.2 — 2026-10-15

- Made Credits precedence explicit: readable opening title text first, then a
  clearly identifiable contributor name from the filename; otherwise null.
- Excluded technical suffixes and generic scene descriptions from filename credits.

## 0.6.1 — 2026-10-15

- Removed source references, archive paths, evidence explanations, and review
  internals from metadata displays and CSV/JSON downloads. Metadata downloads
  contain only template fields; batch-status diagnostics remain separate.
- Retained internal evidence validation without exposing its sources in metadata.

## 0.6.0 — 2026-10-15

- Removed automatic result files, run directories, and the Output folder input.
  Results and CSV/JSON downloads now stay in session memory until downloaded.

## 0.5.0 — 2026-10-15

- Replaced eight-image sampling with high-detail review of every readable nested
  ZIP image in batches of six, followed by reconciliation across all batches.
- Preserved timestamped OCR from the first five seconds for explicit opening-title
  Credits review. Reports now include opening text and image review coverage.
- Increased image preparation quality and retained archive-entry source references;
  UUID filenames are never treated as video timestamps.

## 0.4.0 — 2026-10-15

- Incorporated review feedback from the student-interview and sixth-grade lesson
  examples: selective activity tags, prominent People roles, and title/filename credits.
- Accepted comments and trailing commas in example JSON without changing originals.
- Added Teaching Sample, removed Subject, and migrated Product to Primary/Secondary
  objects; legacy string examples normalize automatically. Core Math is the known
  parent for i-Ready Classroom Mathematics; other products use a null parent.
- Applied the requested Restrictions policy: default No, preserve explicit internal
  restrictions, and mark actual assessment-tagged footage internal-only.

## 0.3.0 — 2026-10-15

- Added an examples folder for curated desired-output JSON, validation warnings,
  and selection of up to five relevant examples for each video.
- Included reference examples in chunk and final requests, with selected filenames
  recorded in JSON reports and no changes to CSV columns.
- Generalized the prompt across supported video formats while retaining the current
  metadata template and requiring evidence from the current video.

## 0.2.1 — 2026-10-15

- Corrected the invalid `2024-10-21-preview` API version to `2024-10-21` during
  configuration loading, validated resource URLs, and trimmed configuration values.
- Reloaded `.env` settings on Streamlit interactions and added an Azure connection
  check with actionable, sanitized API error messages.

## 0.2.0 — 2026-10-15

- Added direct selection and upload of multiple ZIP files in Streamlit, alongside
  local folder scanning. Uploaded archives are processed in memory through the
  same Azure AI pipeline without saving source ZIPs to disk.

## 0.1.0 — 2026-10-15

- Added a local Streamlit frontend with recursive folder scanning, archive
  selection, progress, evidence inspection, and result downloads.
- Added Azure OpenAI metadata extraction from JSON analysis and nested thumbnails,
  template-driven structured validation, and a separately editable prompt.
- Added automatic per-run CSV/JSON exports and batch failure reporting.
- Added archive resource limits, unknown-field handling, configuration examples,
  setup documentation, mocked integration tests, and Streamlit smoke tests.
