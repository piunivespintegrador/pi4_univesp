#!/usr/bin/env python3
"""
Coletor de dados de jogos para análise acadêmica.

Fontes:
- Google Trends via trendspyg
- RAWG API
- SteamSpy

A chave RAWG fica no arquivo .env.
Google Trends, neste modo, não exige uma API key; o trendspyg
automatiza a consulta ao Google Trends usando o navegador.

Uso:
    python collect_games.py
    python collect_games.py --geo BR --timeframe "today 5-y"
    python collect_games.py --no-rawg
    python collect_games.py --no-steamspy
    python collect_games.py --no-trends
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

DATA_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")

RAWG_API_KEY = os.getenv("RAWG_API_KEY", "").strip()
DEFAULT_GEO = os.getenv("TRENDS_GEO", "BR").strip()
DEFAULT_TIMEFRAME = os.getenv("TRENDS_TIMEFRAME", "today 5-y").strip()
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "3"))

RAWG_URL = "https://api.rawg.io/api/games"
STEAMSPY_URL = "https://steamspy.com/api.php"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("game-data")


# ---------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------

def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def save_json(filename: str, data: Any) -> None:
    path = RAW_DIR / filename
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def request_json(
    url: str,
    params: dict[str, Any],
    retries: int = 3,
) -> dict[str, Any]:
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=40,
                headers={
                    "User-Agent": (
                        "GameDataAnalyzer/1.0 "
                        "(academic data analysis project)"
                    )
                },
            )
            response.raise_for_status()
            return response.json()

        except requests.RequestException as exc:
            last_error = exc
            log.warning(
                "Falha HTTP tentativa %s/%s: %s",
                attempt,
                retries,
                exc,
            )
            if attempt < retries:
                time.sleep(2 * attempt)

    raise RuntimeError(f"Falha ao consultar {url}: {last_error}")


def read_games() -> pd.DataFrame:
    path = ROOT / "games.csv"

    if not path.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {path}"
        )

    df = pd.read_csv(path).fillna("")

    if "game" not in df.columns:
        raise ValueError(
            "games.csv precisa possuir a coluna 'game'."
        )

    if "genre" not in df.columns:
        df["genre"] = ""

    if "steam_appid" not in df.columns:
        df["steam_appid"] = ""

    df["game"] = df["game"].astype(str).str.strip()
    df = df[df["game"] != ""].drop_duplicates(
        subset=["game"]
    ).reset_index(drop=True)

    return df


# ---------------------------------------------------------------------
# RAWG
# ---------------------------------------------------------------------

def collect_rawg(game: str) -> dict[str, Any]:
    if not RAWG_API_KEY:
        return {
            "game": game,
            "rawg_found": False,
            "rawg_error": "RAWG_API_KEY não configurada",
        }

    params = {
        "key": RAWG_API_KEY,
        "search": game,
        "page_size": 1,
    }

    data = request_json(RAWG_URL, params)
    save_json(f"rawg_{safe_filename(game)}.json", data)

    results = data.get("results", [])

    if not results:
        return {
            "game": game,
            "rawg_found": False,
        }

    item = results[0]

    genres = [
        x.get("name", "")
        for x in item.get("genres", [])
    ]

    platforms = [
        x.get("platform", {}).get("name", "")
        for x in item.get("platforms", [])
    ]

    return {
        "game": game,
        "rawg_found": True,
        "rawg_id": item.get("id"),
        "name_rawg": item.get("name"),
        "released": item.get("released"),
        "rating": item.get("rating"),
        "ratings_count": item.get("ratings_count"),
        "metacritic": item.get("metacritic"),
        "playtime_hours": item.get("playtime"),
        "genres": ", ".join(filter(None, genres)),
        "platforms": ", ".join(filter(None, platforms)),
    }


def collect_rawg_all(games: list[str]) -> pd.DataFrame:
    rows = []

    for game in games:
        log.info("[RAWG] %s", game)

        try:
            rows.append(collect_rawg(game))
        except Exception as exc:
            log.exception("[RAWG] erro em %s", game)
            rows.append({
                "game": game,
                "rawg_found": False,
                "rawg_error": str(exc),
            })

        time.sleep(REQUEST_DELAY / 3)

    df = pd.DataFrame(rows)

    df.to_csv(
        DATA_DIR / "games_metadata.csv",
        index=False,
        encoding="utf-8-sig",
    )

    return df


# ---------------------------------------------------------------------
# SteamSpy
# ---------------------------------------------------------------------

def collect_steamspy(game: str, appid: str) -> dict[str, Any]:
    appid = str(appid).strip()

    if not appid:
        return {
            "game": game,
            "steamspy_found": False,
        }

    params = {
        "request": "appdetails",
        "appid": appid,
    }

    data = request_json(STEAMSPY_URL, params)
    save_json(
        f"steamspy_{safe_filename(game)}_{appid}.json",
        data,
    )

    if not isinstance(data, dict) or "name" not in data:
        return {
            "game": game,
            "steam_appid": appid,
            "steamspy_found": False,
        }

    return {
        "game": game,
        "steam_appid": appid,
        "steamspy_found": True,
        "steam_name": data.get("name"),
        "developer": data.get("developer"),
        "publisher": data.get("publisher"),
        "owners_estimate": data.get("owners"),
        "ccu": data.get("ccu"),
        "average_forever_min": data.get("average_forever"),
        "median_forever_min": data.get("median_forever"),
        "positive": data.get("positive"),
        "negative": data.get("negative"),
        "price": data.get("price"),
        "genre_steam": data.get("genre"),
    }


def collect_steamspy_all(df_games: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for _, row in df_games.iterrows():
        game = row["game"]

        log.info("[SteamSpy] %s", game)

        try:
            rows.append(
                collect_steamspy(
                    game,
                    row.get("steam_appid", ""),
                )
            )
        except Exception as exc:
            log.exception("[SteamSpy] erro em %s", game)
            rows.append({
                "game": game,
                "steamspy_found": False,
                "steamspy_error": str(exc),
            })

        time.sleep(0.5)

    df = pd.DataFrame(rows)

    df.to_csv(
        DATA_DIR / "steamspy.csv",
        index=False,
        encoding="utf-8-sig",
    )

    return df


# ---------------------------------------------------------------------
# Google Trends
# ---------------------------------------------------------------------

def collect_trends_single(
    games: list[str],
    geo: str,
    timeframe: str,
) -> pd.DataFrame:
    """
    Coleta uma série individual por jogo.

    ATENÇÃO:
    séries individuais do Google Trends são normalizadas
    independentemente. Elas não devem ser comparadas diretamente.
    """

    from trendspyg import (
        download_google_trends_interest_over_time,
    )

    rows = []

    for game in games:
        log.info("[Trends] %s", game)

        try:
            series = download_google_trends_interest_over_time(
                game,
                geo=geo,
                timeframe=timeframe,
                cache="disk",
                cookies="disk",
            )

            for point in series:
                rows.append({
                    "game": game,
                    "date": point.get("date"),
                    "interest": point.get("value"),
                    "is_partial": point.get(
                        "is_partial",
                        False,
                    ),
                    "geo": geo,
                    "timeframe": timeframe,
                })

        except Exception as exc:
            log.exception(
                "[Trends] erro em %s: %s",
                game,
                exc,
            )

        time.sleep(REQUEST_DELAY)

    df = pd.DataFrame(rows)

    df.to_csv(
        DATA_DIR / "google_trends.csv",
        index=False,
        encoding="utf-8-sig",
    )

    return df


def collect_trends_comparison(
    games: list[str],
    geo: str,
    timeframe: str,
) -> pd.DataFrame:
    """
    Compara 2 a 5 jogos em uma escala comum de 0 a 100.

    Se houver mais de 5 jogos, eles são processados em lotes.
    """

    from trendspyg import download_google_trends_comparison

    rows = []

    for start in range(0, len(games), 5):
        batch = games[start:start + 5]

        if len(batch) < 2:
            break

        log.info(
            "[Trends comparação] %s",
            ", ".join(batch),
        )

        try:
            env = download_google_trends_comparison(
                batch,
                geo=geo,
                timeframe=timeframe,
                cache="disk",
                cookies="disk",
            )

            for point in env.get(
                "interest_over_time",
                [],
            ):
                date = point.get("date")
                values = point.get("values", {})

                for game, value in values.items():
                    rows.append({
                        "game": game,
                        "date": date,
                        "interest_comparable": value,
                        "geo": geo,
                        "timeframe": timeframe,
                    })

        except Exception as exc:
            log.exception(
                "[Trends comparação] erro: %s",
                exc,
            )

        time.sleep(REQUEST_DELAY)

    df = pd.DataFrame(rows)

    df.to_csv(
        DATA_DIR / "google_trends_comparable.csv",
        index=False,
        encoding="utf-8-sig",
    )

    return df


# ---------------------------------------------------------------------
# Análise
# ---------------------------------------------------------------------

def analyze_trends(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    rows = []

    for game, group in df.groupby("game"):
        group = group.dropna(
            subset=["interest"]
        ).copy()

        if group.empty:
            continue

        values = group["interest"].astype(float).to_numpy()
        x = np.arange(len(values), dtype=float)

        slope = (
            float(np.polyfit(x, values, 1)[0])
            if len(values) >= 2
            else np.nan
        )

        rows.append({
            "game": game,
            "observations": len(values),
            "mean_interest": float(np.mean(values)),
            "median_interest": float(np.median(values)),
            "max_interest": float(np.max(values)),
            "min_interest": float(np.min(values)),
            "std_interest": float(np.std(values)),
            "trend_slope": slope,
            "first_interest": float(values[0]),
            "last_interest": float(values[-1]),
            "change_first_last": float(
                values[-1] - values[0]
            ),
        })

    result = pd.DataFrame(rows)

    if not result.empty:
        result = result.sort_values(
            "mean_interest",
            ascending=False,
        )

    result.to_csv(
        DATA_DIR / "analysis_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    return result


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Coleta dados de jogos para análise "
            "com Google Trends, RAWG e SteamSpy."
        )
    )

    parser.add_argument(
        "--geo",
        default=DEFAULT_GEO,
        help="Região do Google Trends. Ex.: BR, US, WORLD.",
    )

    parser.add_argument(
        "--timeframe",
        default=DEFAULT_TIMEFRAME,
        help=(
            'Período. Ex.: "today 5-y", '
            '"today 12-m" ou "2020-01-01 2026-09-01".'
        ),
    )

    parser.add_argument(
        "--no-trends",
        action="store_true",
        help="Não consultar Google Trends.",
    )

    parser.add_argument(
        "--no-rawg",
        action="store_true",
        help="Não consultar RAWG.",
    )

    parser.add_argument(
        "--no-steamspy",
        action="store_true",
        help="Não consultar SteamSpy.",
    )

    args = parser.parse_args()

    games_df = read_games()
    games = games_df["game"].tolist()

    log.info(
        "Jogos encontrados no games.csv: %d",
        len(games),
    )

    if not args.no_rawg:
        collect_rawg_all(games)

    if not args.no_steamspy:
        collect_steamspy_all(games_df)

    if not args.no_trends:
        trends = collect_trends_single(
            games,
            args.geo,
            args.timeframe,
        )

        collect_trends_comparison(
            games,
            args.geo,
            args.timeframe,
        )

        analyze_trends(trends)

    config = {
        "geo": args.geo,
        "timeframe": args.timeframe,
        "games": games,
        "generated_at": pd.Timestamp.now(
            tz="America/Sao_Paulo"
        ).isoformat(),
    }

    (DATA_DIR / "collection_config.json").write_text(
        json.dumps(
            config,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log.info("Coleta concluída.")
    log.info("Resultados: %s", DATA_DIR)


if __name__ == "__main__":
    main()
