# helpers.py - safe building blocks that model-written scripts can import:
#     from helpers import suggest_names, preview_renames, apply_renames, clean_csv ...
# Scripts run inside the workspace folder, so all paths here are relative to it.
# Output is plain ASCII on purpose (Windows console safe).
import csv
import glob
import io
import json
import os
import re
import time

LOG_NAME = "rename_log.json"


# =========================================================================== files / renaming
def list_files(pattern="*"):
    """Files (not folders) in the current folder matching a glob pattern, e.g. '*.pdf'."""
    return sorted(p for p in glob.glob(pattern) if os.path.isfile(p))


def clean_name(filename, style="snake"):
    """Return a tidy version of a file name. style: 'snake', 'kebab', 'title' or 'keep'."""
    stem, ext = os.path.splitext(filename)
    s = re.sub(r'[\\/:*?"<>|]', "", stem).strip()
    if style == "snake":
        s = re.sub(r"[\W_]+", "_", s).strip("_").lower()
    elif style == "kebab":
        s = re.sub(r"[\W_]+", "-", s).strip("-").lower()
    elif style == "title":
        s = " ".join(re.sub(r"[_\-]+", " ", s).split()).title()
    else:
        s = " ".join(s.split())
    return (s or "file") + ext.lower()


def suggest_names(pattern="*", style="snake"):
    """List of (old, new) pairs for files whose cleaned name differs."""
    pairs = []
    for f in list_files(pattern):
        if f == LOG_NAME or f.lower().endswith(".summary.md"):
            continue                            # never touch the rename log or llama-ui summaries
        new = clean_name(f, style)
        if new != f:
            pairs.append((f, new))
    return pairs


def _validate(pairs):
    good, problems, targets = [], [], set()
    for old, new in pairs:
        if old == new:
            continue
        if not os.path.exists(old):
            problems.append(f"missing: {old}")
            continue
        same_file = os.path.normcase(os.path.abspath(old)) == os.path.normcase(os.path.abspath(new))
        if os.path.exists(new) and not same_file:
            problems.append(f"target already exists: {new}")
            continue
        key = os.path.normcase(os.path.abspath(new))
        if key in targets:
            problems.append(f"two files would get the same name: {new}")
            continue
        targets.add(key)
        good.append((old, new))
    return good, problems


def preview_renames(pairs):
    """Print what WOULD be renamed. Does not change anything."""
    good, problems = _validate(pairs)
    print("PREVIEW - nothing has been renamed yet")
    for old, new in good:
        print(f"  {old}  ->  {new}")
    for p in problems:
        print(f"  SKIPPED ({p})")
    print(f"{len(good)} file(s) can be renamed, {len(problems)} skipped.")
    return good


def apply_renames(pairs):
    """Rename files for real (never overwrites). Writes rename_log.json so undo_renames() works."""
    good, problems = _validate(pairs)
    done = []
    for old, new in good:
        try:
            os.rename(old, new)
            done.append([old, new])
            print(f"  {old}  ->  {new}")
        except OSError as e:
            print(f"  FAILED {old}: {e}")
    for p in problems:
        print(f"  SKIPPED ({p})")
    if done:
        log = []
        if os.path.exists(LOG_NAME):
            try:
                with open(LOG_NAME, "r", encoding="utf-8") as f:
                    log = json.load(f)
            except (OSError, ValueError):
                log = []
        log.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "renames": done})
        with open(LOG_NAME, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    print(f"RENAMED {len(done)} file(s). Say 'undo' to reverse.")
    return done


def undo_renames():
    """Reverse the most recent apply_renames() batch."""
    if not os.path.exists(LOG_NAME):
        print("Nothing to undo (no rename_log.json here).")
        return []
    with open(LOG_NAME, "r", encoding="utf-8") as f:
        log = json.load(f)
    if not log:
        print("Nothing to undo.")
        return []
    batch = log.pop()
    undone = []
    for old, new in reversed(batch["renames"]):
        if os.path.exists(new) and not os.path.exists(old):
            os.rename(new, old)
            undone.append([new, old])
            print(f"  {new}  ->  {old}")
        else:
            print(f"  SKIPPED {new} (missing, or original name is taken)")
    with open(LOG_NAME, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    print(f"UNDID {len(undone)} rename(s).")
    return undone


# =========================================================================== tables (CSV / XLSX)
def _read_csv(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = ""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        delim = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    rows = [list(r) for r in csv.reader(io.StringIO(text, newline=""), delimiter=delim)]
    return rows, delim


def _clean_cell(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return " ".join(v.split())
    return v


def _clean_rows(rows, dedupe=True):
    """Trim cells, drop blank rows, equalize widths, fix header names, drop exact duplicates."""
    stats = {"rows_in": len(rows), "blank_dropped": 0, "duplicates_dropped": 0, "padded": 0}
    cleaned = []
    for r in rows:
        r = [_clean_cell(c) for c in r]
        if all(c == "" for c in r):
            stats["blank_dropped"] += 1
            continue
        cleaned.append(r)
    if not cleaned:
        stats["rows_out"] = 0
        return [], stats

    width = max(len(r) for r in cleaned)
    for r in cleaned:
        if len(r) < width:
            r.extend([""] * (width - len(r)))
            stats["padded"] += 1

    seen, header = {}, []
    for i, h in enumerate(cleaned[0]):
        name = str(h).strip() or f"column_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        header.append(name)

    body = cleaned[1:]
    if dedupe:
        seen_rows, unique = set(), []
        for r in body:
            key = tuple(r)
            if key in seen_rows:
                stats["duplicates_dropped"] += 1
                continue
            seen_rows.add(key)
            unique.append(r)
        body = unique
    stats["rows_out"] = len(body) + 1
    return [header] + body, stats


def _report(label, stats, out):
    print(f"{label}: {stats['rows_in']} rows in -> {stats['rows_out']} rows out "
          f"(blank rows dropped: {stats['blank_dropped']}, duplicates dropped: {stats['duplicates_dropped']}, "
          f"short rows padded: {stats['padded']})")
    if out:
        print(f"Saved: {out}   (original file was not changed)")


def peek(path, rows=5):
    """Print the first few rows of a .csv or .xlsx so you can see its columns."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            print(f"Sheet '{ws.title}':")
            for i, r in enumerate(ws.iter_rows(values_only=True)):
                if i >= rows:
                    break
                print("  ", [("" if c is None else c) for c in r])
    else:
        data, _ = _read_csv(path)
        print(f"{len(data)} rows. First {rows}:")
        for r in data[:rows]:
            print("  ", r)


def clean_csv(path, out=None, dedupe=True):
    """Write a cleaned copy of a CSV: <name>_clean.csv. The original is never touched."""
    rows, delim = _read_csv(path)
    result, stats = _clean_rows(rows, dedupe)
    out = out or os.path.splitext(path)[0] + "_clean.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f, delimiter=delim).writerows(result)
    _report(os.path.basename(path), stats, out)
    return out


def clean_xlsx(path, out=None, dedupe=True):
    """Write a cleaned copy of an Excel file: <name>_clean.xlsx (formulas become their values)."""
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        print("openpyxl is not installed. Run in a terminal:  pip install openpyxl")
        return None
    src = openpyxl.load_workbook(path, data_only=True)
    dst = openpyxl.Workbook()
    dst.remove(dst.active)
    for ws in src.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        result, stats = _clean_rows(rows, dedupe)
        new_ws = dst.create_sheet(ws.title[:31])
        for r in result:
            new_ws.append(r)
        if result:
            for cell in new_ws[1]:
                cell.font = Font(bold=True)
            new_ws.freeze_panes = "A2"
            for i, col in enumerate(zip(*result), 1):
                longest = max(len(str(v)) for v in col)
                new_ws.column_dimensions[new_ws.cell(row=1, column=i).column_letter].width = min(longest + 2, 60)
        _report(f"sheet '{ws.title}'", stats, None)
    out = out or os.path.splitext(path)[0] + "_clean.xlsx"
    dst.save(out)
    print(f"Saved: {out}   (original file was not changed)")
    return out


def clean_table(path, out=None):
    """Clean a .csv or .xlsx depending on its extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return clean_csv(path, out)
    if ext in (".xlsx", ".xlsm"):
        return clean_xlsx(path, out)
    print(f"Unsupported file type '{ext}'. Use .csv or .xlsx (save old .xls files as .xlsx first).")
    return None


# =========================================================================== PDF
def pdf_to_text(path, out=None):
    """Save the text of a PDF (or .docx) as <name>.txt next to it."""
    import tools
    text = tools.extract_attachment_text(path)
    if text.startswith("[Error"):
        print(text)
        return None
    out = out or os.path.splitext(path)[0] + ".txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Saved {len(text)} characters to {out}")
    return out
