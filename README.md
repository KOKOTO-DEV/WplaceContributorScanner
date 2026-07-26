# Wplace Contributor Scanner 1.5

English | [한국어](README_KO.md) | [日本語](README_JA.md) | [简体中文](README_ZH_CN.md)

Wplace Contributor Scanner is a local, read-only statistics tool that identifies the current last worker of pixels selected by a template. It can analyze a Blue Marble template or a custom screenshot-region mask, resume long scans, divide work across several computers, and export CSV/PDF reports.

## Requirements

- Windows 10/11 or a modern Linux distribution
- Python 3.10 or newer
- Internet access to the configured Wplace tile and pixel endpoints
- A modern browser
- Enough free disk space for project data, tile cache, snapshots, and exported reports

The program installs Python packages from `requirements.txt` into a local `.venv` when started with the provided launcher.

## Start the program

### Windows

1. Extract the ZIP to a normal writable folder.
2. Install Python 3.10 or newer. The standard Python launcher (`py.exe`) must be available.
3. Run `start_windows.bat`.
4. The launcher creates `.venv`, installs dependencies, starts the server, and opens the browser.

Do not run the program from inside the ZIP archive.

### Linux

```bash
chmod +x start_linux.sh
./start_linux.sh --browser
```

Without `--browser`, open `http://127.0.0.1:8765/` manually.

Common options:

```text
--host 127.0.0.1       Listen address (default: local computer only)
--port 8765            Web port
--browser              Open a browser automatically
--no-browser           Do not open a browser
--lang ko|en|ja|zh-CN  Console language
--version               Print the program version
```

To allow access from another device on a trusted LAN, explicitly use `--host 0.0.0.0`. The web interface has no authentication, so never expose it directly to the public internet.

## Basic workflow

1. Import a template with 'Import template JSON/ZIP', or create one with 'Create screenshot region template'.
2. Select the project.
3. Review the query settings. The defaults are intentionally conservative.
4. Click 'Compare with current canvas'.
5. Confirm the candidate count, then click 'Start/continue collection'.
6. Use 'Pause' before changing important settings, exporting final data, or merging collaboration results.
7. Export 'CSV' or 'PDF' after collection and representative-region analysis finish.

Clicking 'Start/continue collection' on an unprepared project automatically performs the comparison first.

## Template types and calculation modes

### Blue Marble JSON/ZIP

The importer accepts ordinary Blue Marble template exports. If a ZIP contains `template.json`, that file is used. Otherwise, the largest JSON entry is treated as the template document.

### Screenshot region template

1. Click 'Create screenshot region template'.
2. Enter the top-left and bottom-right Wplace tile/pixel coordinates.
3. Click 'Capture current region'.
4. Erase unrelated pixels until only the intended artwork remains.
5. Choose a template name and calculation mode.
6. Click 'Create template project'.

Editor functions:

- Erase/restore brush and rectangle tools
- Hard binary transparency without semi-transparent edges
- Checkerboard, white, black, or custom background preview
- Zoom from 25% to 1600%
- `Ctrl+Z` undo
- `Ctrl+Y` or `Ctrl+Shift+Z` redo
- Restore to the state when the editor was opened
- Restore the entire original capture

'Edit current screenshot template' reloads the original capture and current mask. Saving creates a corrected new project; the existing project and its scan progress remain unchanged.

### Color-match mode

Only opaque template coordinates whose current color exactly matches the template color become candidates. This is useful for measuring pixels that still preserve the captured artwork.

### Region mode

Every opaque template coordinate becomes a candidate regardless of its current color. This is useful when recolored or damaged pixels should still belong to the selected artwork area.

Transparent pixels are always excluded.

## Collection settings

- 'Request interval:' Minimum delay per worker. Lower values increase request volume.
- 'Jitter ratio:' Adds random delay variation so workers do not send perfectly synchronized requests.
- 'Network timeout:' Maximum wait for one request.
- 'Disk checkpoint:' Number of successful pixels between progress writes.
- 'Parallel workers:' Number of simultaneous pixel lookups. Total request volume rises approximately in proportion to this value.
- 'Protective-response retry:' HTTP 403, 429, and 451 cause all workers to wait together and retry the same pixel.
- 'Collaboration shard count / work number:' Fixed partition used when several nodes share the same candidate list.
- 'Advanced endpoints:' Override only when you intentionally use different compatible endpoints.

A browser window is only a controller. Closing it does not stop the server process or an active scan. Stop the console process when you intend to shut down the program.

## Progress, analysis, and recovery

Progress is written to `data/projects`. Normal shutdown saves the latest state. After a power loss or forced termination, only work completed after the most recent checkpoint may need to be queried again.

After a scan pauses, completes, or merges results, the program analyzes:

- Current remaining pixel count per worker
- Share of identified pixels
- Largest connected representative region, using 64 px grid cells
- Representative coordinate nearest the center of that region
- Top five overall colors plus Other
- Top five representative-region colors plus Other

Running 'Compare with current canvas' again resets candidate progress, worker results, collaboration order, and analysis for that project. The interface requires a two-step confirmation because the reset cannot be undone.

## CSV and PDF export

### CSV

The CSV is encoded as UTF-8 with BOM for spreadsheet compatibility. It contains worker ID/name, pixel counts, shares, alliance name, representative-region statistics, color summaries, representative coordinates, latitude/longitude, and Wplace links.

### PDF

The PDF language follows the current interface language. Project names, worker names, alliance names, and notes support mixed Korean, English, Japanese, and Simplified Chinese text. Available system TrueType fonts are embedded as subsets.

Optional PDF fields:

- Work start date/time
- Work end date/time
- Up to 2,000 characters of 'What you want to say'

These fields are stored per project in the current browser and do not affect scan results. Clearing browser site data removes these fields but does not delete project progress.

On minimal Linux systems, install a CJK font package such as Noto Sans CJK if CJK text cannot be rendered correctly.

## Collaboration

All nodes must use files generated by Wplace Contributor Scanner 1.5 and must work from the same collaboration start file.

### Main node

1. Import and prepare the original project.
2. Set the collaboration shard count and save settings.
3. Click 'Export collaboration start file'.
4. Send the same ZIP to every participant node.
5. Keep the original project on the main node; do not import its own start file.
6. Assign the main node a unique work number and start collection.

### Participant node

1. Click 'Import collaboration start file'.
2. Choose a work number that no other node uses.
3. Save settings. Do not run 'Compare with current canvas'.
4. Start collection.
5. Pause and click 'Export work result' when finished or when sending an intermediate result.

### Merge and redistribute

1. On the main node, select one or more participant result ZIPs with 'Merge work result'.
2. Wait for every selected file to finish validation and merging.
3. Merge all received results before redistributing work.
4. To split only the remaining unchecked pixels again, set the new shard count and click 'Restart remaining-work distribution'.
5. Stop every node using the previous start file and distribute the newly generated file to all nodes.

Work numbers are 1-based in the interface and must be unique among concurrently running nodes.

## Project and data management

- 'Rename project' changes the display name only.
- 'Delete project' removes its saved progress. If no other project uses the imported source template, that source is removed as well. Deletion cannot be undone.
- Stop the program before manually copying, moving, or deleting files under `data`.

Important paths:

```text
data/templates/          Imported template sources and generated screenshot-template ZIPs
data/projects/           Progress, candidates, owners, users, cache, snapshots, and exports
data/captures/           Temporary screenshot-editor captures
data/inbox/              Temporary uploads; successful imports are removed automatically
data/deleted-projects.json  Deleted-project record used by the project list
```

For backup or migration, close the program and copy the entire `data` directory. Project and collaboration files are validated against the current 1.5 public formats; do not manually combine or modify their contents.

## Important warnings

- The program performs read-only lookups, but it still generates network traffic.
- Wplace does not publish a guaranteed safe request rate. No configuration can guarantee that rate limits or account restrictions will never occur.
- Increasing parallel workers increases simultaneous requests.
- Repeated 403/429/451 responses mean the request load or access conditions should be reviewed. Increase delays or reduce workers.
- Do not edit `project.json`, binary progress files, collaboration ZIPs, or generated template ZIPs manually.
- Do not run two program instances against the same `data` directory.
- Keep backups before deleting projects or resetting comparison progress.

## Troubleshooting

### A project remains in the list after manual file deletion

Close the console process completely, then restart. Prefer the built-in 'Delete project' button.

### Start/continue is disabled

Wait for the current-canvas comparison to finish. If the node's assigned work is already complete, export/merge collaboration results or select another work number as appropriate.

### PDF characters are missing

Install system fonts covering the required scripts. On Linux, Noto Sans CJK is recommended, then restart the program and export again.

### The Windows launcher prints garbled commands

Use the included `start_windows.bat` and `launcher_i18n.ps1` from the same release folder. Do not save the batch file through an editor that changes its encoding.

### The copied folder cannot reuse `.venv`

The launchers detect a moved or incomplete virtual environment and recreate it automatically. The `.venv` directory is disposable and should not be included in backups.
