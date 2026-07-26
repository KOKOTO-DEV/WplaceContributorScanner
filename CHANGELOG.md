# Changelog

## 1.5.0 — First public release (2026-07-19)

- Imports Blue Marble JSON/ZIP templates and creates editable templates from a selected Wplace screenshot region.
- Provides binary mask editing with erase/restore brushes, rectangle tools, configurable transparency backgrounds, 25%–1600% zoom, undo/redo, and re-editing of generated screenshot templates.
- Compares templates with the current canvas in color-match or region mode, then retrieves the current last worker for each candidate pixel using read-only requests.
- Saves progress to disk, supports pause/resume, parallel workers, checkpoints, protective-response backoff, and automatic representative-region analysis.
- Exports UTF-8 CSV and multilingual Unicode PDF reports with project information, manual work period, notes, artwork snapshots, representative coordinates, regions, and color statistics.
- Supports distributed collection through collaboration start files, participant result files, sequential result merging, and redistribution of remaining unchecked pixels.
- Includes project rename/delete controls, strict current-format validation, local-only default binding, and Korean, English, Japanese, and Simplified Chinese interfaces.
