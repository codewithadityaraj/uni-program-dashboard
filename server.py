"""
Local proxy server for UNI Program Dashboard.
Fetches Google Sheets CSV exports, caches responses (5 min TTL), serves the static UI.
"""

from __future__ import annotations

import csv
import io
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
CACHE_TTL_SECONDS = 300

SHEETS: dict[str, str] = {
    "token-cohort": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=262950175&single=true&output=csv"
    ),
    "token-monthly": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=654588083&single=true&output=csv"
    ),
    "fp-cohort": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=1691431588&single=true&output=csv"
    ),
    "fp-monthly": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=1412605711&single=true&output=csv"
    ),
    "tl-token-cohort": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=1306601082&single=true&output=csv"
    ),
    "tl-token-monthly": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=86980914&single=true&output=csv"
    ),
    "tl-fp-cohort": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=1400812786&single=true&output=csv"
    ),
    "tl-fp-monthly": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=748961899&single=true&output=csv"
    ),
    "gm-token-cohort": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=267153274&single=true&output=csv"
    ),
    "gm-token-monthly": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=100212730&single=true&output=csv"
    ),
    "gm-fp-cohort": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=1622927752&single=true&output=csv"
    ),
    "gm-fp-monthly": (
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vTcztb-A37i4VXvWKnATdaFrGPZGf5tQlsYIDgdb7CViBh_TpL0kdst-OVwlEBxISLK1fHob_G86ffr/pub?gid=1051256120&single=true&output=csv"
    ),
}

_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


def _parse_csv(text: str) -> list[dict[str, str]]:
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for row in reader:
        cleaned = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}
        if any(cleaned.values()):
            rows.append(cleaned)
    return rows


async def _fetch_sheet(key: str) -> list[dict[str, str]]:
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    url = SHEETS.get(key)
    if not url:
        raise HTTPException(status_code=404, detail=f"Unknown dataset: {key}")

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch sheet: {exc}") from exc

    rows = _parse_csv(response.text)
    _cache[key] = (now, rows)
    return rows


app = FastAPI(title="UNI Program Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "cache_ttl_seconds": CACHE_TTL_SECONDS}


@app.post("/api/refresh")
async def refresh_cache():
    _cache.clear()
    return {"status": "ok", "message": "Cache cleared"}


@app.get("/api/data/{dataset}")
async def get_dataset(dataset: str):
    if dataset not in SHEETS:
        raise HTTPException(status_code=404, detail=f"Unknown dataset: {dataset}")
    rows = await _fetch_sheet(dataset)
    return {"dataset": dataset, "rows": rows, "count": len(rows)}


@app.get("/api/dashboard")
async def get_dashboard():
    token_cohort = await _fetch_sheet("token-cohort")
    token_monthly = await _fetch_sheet("token-monthly")
    fp_cohort = await _fetch_sheet("fp-cohort")
    fp_monthly = await _fetch_sheet("fp-monthly")
    tl_token_cohort = await _fetch_sheet("tl-token-cohort")
    tl_token_monthly = await _fetch_sheet("tl-token-monthly")
    tl_fp_cohort = await _fetch_sheet("tl-fp-cohort")
    tl_fp_monthly = await _fetch_sheet("tl-fp-monthly")
    gm_token_cohort = await _fetch_sheet("gm-token-cohort")
    gm_token_monthly = await _fetch_sheet("gm-token-monthly")
    gm_fp_cohort = await _fetch_sheet("gm-fp-cohort")
    gm_fp_monthly = await _fetch_sheet("gm-fp-monthly")

    programs = sorted(
        {r.get("Program Name", "") for r in token_cohort if r.get("Program Name")},
        key=str.casefold,
    )

    return {
        "programs": programs,
        "tokenCohort": token_cohort,
        "tokenMonthly": token_monthly,
        "fpCohort": fp_cohort,
        "fpMonthly": fp_monthly,
        "tlTokenCohort": tl_token_cohort,
        "tlTokenMonthly": tl_token_monthly,
        "tlFpCohort": tl_fp_cohort,
        "tlFpMonthly": tl_fp_monthly,
        "gmTokenCohort": gm_token_cohort,
        "gmTokenMonthly": gm_token_monthly,
        "gmFpCohort": gm_fp_cohort,
        "gmFpMonthly": gm_fp_monthly,
        "fetchedAt": time.time(),
    }


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "index.html")


app.mount("/", StaticFiles(directory=BASE_DIR), name="static")
