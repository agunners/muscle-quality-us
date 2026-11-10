"""Thin subprocess wrappers around the authenticated `gws` CLI for Drive + Sheets.

The repo has no Google API client; `gws` is the machine's ratified, authenticated
interface (full /auth/drive scope). Notes baked in from verification this session:
* every gws call prints a 'Using keyring backend:' line before its JSON — stripped here;
* args are passed as a list (no shell), so Turkish text / quotes in bodies need no escaping;
* large-file download uses `files get alt=media -o` — the `files download` command 500s
  on big files — and `-o` must resolve INSIDE the cwd, so downloads use repo-relative paths.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]


class GwsError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if s.startswith("{") or s.startswith("["):
            return json.loads("\n".join(lines[i:]))
    raise GwsError(f"no JSON in gws output:\n{text[:500]}")


def _run(args: Sequence[str], *, json_body: Optional[dict] = None, parse: bool = True) -> Any:
    cmd = ["gws", *args]
    if json_body is not None:
        cmd += ["--json", json.dumps(json_body, ensure_ascii=False)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    out, err = proc.stdout or "", proc.stderr or ""
    if proc.returncode != 0 or "error[" in out or "error[" in err:
        raise GwsError(f"gws {' '.join(args[:3])} failed (rc={proc.returncode}):\n{out}\n{err}")
    return out if not parse else _extract_json(out)


# ---------- Drive ----------
def drive_list_children(parent_id: str, *, folders_only: bool = False,
                        zips_only: bool = False) -> List[Dict]:
    q = f"'{parent_id}' in parents and trashed=false"
    if folders_only:
        q += " and mimeType='application/vnd.google-apps.folder'"
    if zips_only:
        q += " and name contains '.zip'"
    params = {"q": q, "fields": "files(id,name,mimeType,size)",
              "orderBy": "folder,name", "pageSize": 500}
    return _run(["drive", "files", "list", "--params", json.dumps(params)]).get("files", [])


def drive_get_meta(file_id: str, fields: str = "id,name,size,mimeType") -> Dict:
    return _run(["drive", "files", "get", "--params",
                 json.dumps({"fileId": file_id, "fields": fields})])


def drive_download(file_id: str, out_rel_path: str) -> str:
    """Download to a path RELATIVE to the repo root (gws restricts -o to the cwd)."""
    (REPO_ROOT / out_rel_path).parent.mkdir(parents=True, exist_ok=True)
    _run(["drive", "files", "get", "--params",
          json.dumps({"fileId": file_id, "alt": "media"}), "-o", out_rel_path], parse=False)
    p = REPO_ROOT / out_rel_path
    if not p.exists() or p.stat().st_size == 0:
        raise GwsError(f"download produced no file: {out_rel_path}")
    return str(p)


# ---------- Sheets ----------
def sheets_meta(spreadsheet_id: str) -> Dict:
    return _run(["sheets", "spreadsheets", "get", "--params",
                 json.dumps({"spreadsheetId": spreadsheet_id,
                             "fields": "properties.locale,sheets.properties(sheetId,title,gridProperties)"})])


def first_sheet(meta: Dict) -> Dict:
    return meta["sheets"][0]["properties"]


def sheets_values_get(spreadsheet_id: str, rng: str) -> List[List[str]]:
    res = _run(["sheets", "spreadsheets", "values", "get", "--params",
                json.dumps({"spreadsheetId": spreadsheet_id, "range": rng})])
    return res.get("values", []) or []


def sheets_values_update(spreadsheet_id: str, rng: str, values: List[List[str]],
                         value_input: str = "USER_ENTERED") -> Dict:
    """Write `values` (a 2-D list) to `rng`. `value_input`='RAW' stores strings verbatim — use it for
    numeric strings that must stay literal in a text-formatted cell (e.g. a CoV '26.8'); the default
    'USER_ENTERED' parses as if typed (formulas, dates), which existing callers rely on."""
    return _run(["sheets", "spreadsheets", "values", "update", "--params",
                 json.dumps({"spreadsheetId": spreadsheet_id, "range": rng,
                             "valueInputOption": value_input})],
                json_body={"values": values})


def sheets_values_clear(spreadsheet_id: str, rng: str) -> Dict:
    return _run(["sheets", "spreadsheets", "values", "clear", "--params",
                 json.dumps({"spreadsheetId": spreadsheet_id, "range": rng})], json_body={})


def sheets_protected_range_ids(spreadsheet_id: str, sheet_id: int) -> List[int]:
    """Existing protectedRange ids on a sheet — so a rebuild can clear them before re-adding
    (protections aren't removed by a values clear, so they'd otherwise stack)."""
    res = _run(["sheets", "spreadsheets", "get", "--params",
                json.dumps({"spreadsheetId": spreadsheet_id,
                            "fields": "sheets(properties.sheetId,protectedRanges.protectedRangeId)"})])
    ids: List[int] = []
    for sh in res.get("sheets", []):
        if sh.get("properties", {}).get("sheetId") == sheet_id:
            ids += [pr["protectedRangeId"] for pr in sh.get("protectedRanges", []) or []]
    return ids


def sheets_column_groups(spreadsheet_id: str, sheet_id: int) -> List[Dict]:
    """Existing column-group (dimension-group) ranges on a sheet — so a re-group can delete them
    before re-adding (an addDimensionGroup over an already-grouped span stacks a second, deeper
    group instead of replacing it). Returns [{startIndex, endIndex}] for the matching sheet."""
    res = _run(["sheets", "spreadsheets", "get", "--params",
                json.dumps({"spreadsheetId": spreadsheet_id,
                            "fields": "sheets(properties.sheetId,columnGroups(range(startIndex,endIndex),depth))"})])
    groups: List[Dict] = []
    for sh in res.get("sheets", []):
        if sh.get("properties", {}).get("sheetId") == sheet_id:
            for g in sh.get("columnGroups", []) or []:
                rng = g.get("range", {}) or {}
                groups.append({"startIndex": rng.get("startIndex"), "endIndex": rng.get("endIndex")})
    return groups


def sheets_batch_update(spreadsheet_id: str, requests: List[Dict]) -> Dict:
    return _run(["sheets", "spreadsheets", "batchUpdate", "--params",
                 json.dumps({"spreadsheetId": spreadsheet_id})],
                json_body={"requests": requests})
