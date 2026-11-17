# llama-ui (v2)

Local Windows agent for llama.cpp. Writes and runs Python scripts, and summarizes documents.

## Setup
1. `pip install -r requirements.txt`
2. Edit the paths at the top of `start_server.bat`, pick a model, run it.
3. open cmd and run: `python app.py`, it connects at local host at `http://127.0.0.1:110`, port: `110` you can change it in cofig.py

`-c 4096` in start_server.bat must equal `CONTEXT_TOKENS` in config.py.
If you raise one, raise the other (for example 8192 + 8192). Chunk sizes and history limits scale from it.

## Using it
| You type | What happens |
|---|---|
| `/summarize` | summarizes every supported file in the workspace folder |
| `/summarize report.pdf` or `/summarize *.pdf` | only those files |
| attach a file, then `/summarize` | just that file |
| `/summarize --force` | redo files that already have a summary |
| `summarize report.pdf` (plain English) | same as the command |
| anything else | agent mode: model writes a script, it runs, the model sees the output |

Summaries are saved as `<filename>.summary.md` beside the original (or in the workspace if the
file lives elsewhere). Long files are summarized section by section, then combined.

## Safety
- Scripts that delete, rename/move, run programs, or use absolute paths show a confirmation dialog.
- Tick "Confirm each script" to be asked every time.
- Press Stop at any time (the button replaces Send while the agent works).

## Skills
Drop `.md` files in `skills/` (front matter: name, description, triggers). A skill is added to the
prompt only when a trigger word appears in your message. Included: summarize-documents,
rename-files, csv-excel-cleanup. Their scripts use `helpers.py` (preview/apply/undo renames,
clean_csv, clean_xlsx, pdf_to_text) so a small model does not have to write that logic itself.

## Files
config.py (settings) - app.py (window + threads) - ui.py (layout) - agent.py (LLM client, loop,
summarizer) - tools.py (file reading, chunking, script runner, skills) - helpers.py (safe functions
for generated scripts) - sessions.json (auto-saved chats, created on first run)
