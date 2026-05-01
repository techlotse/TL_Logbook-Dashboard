from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import airportsdata
import pdfplumber


CUSTOM_LOCATIONS: dict[str, dict[str, Any]] = {
    "RHINO PARK": {
        "key": "Rhino Park",
        "label": "Rhino Park",
        "lat": -25.833056,
        "lon": 28.540833,
        "country": "ZA",
        "name": "Rhino Park",
        "source": "custom",
    },
    "ROODIA AERO": {
        "key": "Roodia Aero",
        "label": "Roodia Aero",
        "lat": -26.74,
        "lon": 27.78,
        "country": "ZA",
        "name": "Roodia Aero",
        "source": "custom",
    },
    "ROODIA AERO ESTATE": {
        "key": "Roodia Aero Estate",
        "label": "Roodia Aero Estate",
        "lat": -26.74,
        "lon": 27.78,
        "country": "ZA",
        "name": "Roodia Aero Estate",
        "source": "custom",
    },
}


@dataclass
class AirportPoint:
    key: str
    label: str
    lat: float
    lon: float
    name: str = ""
    city: str = ""
    country: str = ""
    source: str = "airportsdata"


@dataclass
class Flight:
    date: str
    year: int
    month: int
    day: int
    dep_key: str
    arr_key: str
    dep_code: str
    arr_code: str
    aircraft_type: str
    registration: str
    total_minutes: int
    pic_minutes: int
    dual_minutes: int
    copi_minutes: int
    instructor_minutes: int
    xc_minutes: int
    pic_xc_minutes: int
    landings: int
    name_pic: str
    remarks: str
    cross_country: bool
    dep_time: str
    arr_time: str
    page: int


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\x00", "").split()).strip()


def clean_remarks(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def to_int(value: Any) -> int | None:
    text = clean(value)
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def minutes_from_columns(row: list[Any], hour_index: int, minute_index: int) -> int:
    hours = to_int(row[hour_index]) if hour_index < len(row) else None
    minutes = to_int(row[minute_index]) if minute_index < len(row) else None
    if hours is None and minutes is None:
        return 0
    return (hours or 0) * 60 + (minutes or 0)


def safe_date(year: int, month: int, day: int) -> str:
    try:
        return f"{year:04d}-{month:02d}-{day:02d}"
    except Exception:
        return ""


def is_location_code(value: Any) -> bool:
    text = clean(value).upper()
    return bool(re.fullmatch(r"[A-Z0-9]{3,4}", text))


def looks_like_flight_row(row: list[Any]) -> bool:
    day = to_int(row[0]) if len(row) > 0 else None
    month = to_int(row[1]) if len(row) > 1 else None
    if day is None or month is None or not (1 <= day <= 31 and 1 <= month <= 12):
        return False
    return is_location_code(row[2] if len(row) > 2 else "") and is_location_code(row[3] if len(row) > 3 else "")


def row_year(table: list[list[Any]], page_text: str) -> int | None:
    for row in table[:8]:
        if row and row[0]:
            match = re.search(r"Year:\s*(\d{4})", str(row[0]))
            if match:
                return int(match.group(1))
    match = re.search(r"Year:\s*(\d{4})", page_text or "")
    return int(match.group(1)) if match else None


def extract_owner(pdf: pdfplumber.PDF) -> str:
    if not pdf.pages:
        return ""
    lines = [line.strip() for line in (pdf.pages[0].extract_text() or "").splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if "Logbook FOCA" in line and idx + 1 < len(lines):
            return re.sub(r"\s*\([^)]*\)", "", lines[idx + 1]).strip()
    return ""


def is_cross_country(remarks: str) -> bool:
    return bool(re.search(r"\bcross\s*[- ]?\s*country\b|\bx[- ]?country\b|\bxc\b", remarks or "", re.IGNORECASE))


def custom_place_from_remarks(remarks: str, direction: str) -> str:
    if not remarks:
        return ""

    match = re.search(rf"\b{direction}\s*:\s*([^\n;|]+)", remarks, flags=re.IGNORECASE)
    if match:
        value = clean(match.group(1))
        if value and value.upper() != "ZZZZ":
            return value

    return ""


def zzzz_place_from_remarks(remarks: str) -> str:
    if not remarks:
        return ""
    matches = re.findall(r"\bzzzz\s*-\s*([^\n;|]+)", remarks, flags=re.IGNORECASE)
    for value in reversed(matches):
        value = clean(value)
        if value and value.upper() != "ZZZZ":
            return value
    return ""


def place_key(code: str, remarks: str, direction: str) -> str:
    code = clean(code).upper()
    if code != "ZZZZ":
        return code
    return custom_place_from_remarks(remarks, direction) or zzzz_place_from_remarks(remarks) or "ZZZZ"


@lru_cache(maxsize=1)
def icao_airports() -> dict[str, dict[str, Any]]:
    return airportsdata.load("ICAO")


@lru_cache(maxsize=1)
def iata_airports() -> dict[str, dict[str, Any]]:
    return airportsdata.load("IATA")


def resolve_airport(key: str) -> AirportPoint | None:
    if not key or key == "ZZZZ":
        return None

    custom = CUSTOM_LOCATIONS.get(key.upper())
    if custom:
        return AirportPoint(**custom)

    code = key.upper()
    record = icao_airports().get(code)
    if not record and len(code) == 3:
        record = iata_airports().get(code)
    if not record:
        return None

    label = record.get("icao") or record.get("iata") or code
    name = record.get("name") or label
    city = record.get("city") or ""
    return AirportPoint(
        key=label,
        label=label,
        lat=float(record["lat"]),
        lon=float(record["lon"]),
        name=name,
        city=city,
        country=record.get("country") or "",
    )


def great_circle_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_nm = 3440.065
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius_nm * (2 * math.asin(math.sqrt(a)))


def parse_logbook(pdf_path: Path) -> tuple[list[Flight], str]:
    flights: list[Flight] = []
    active_year: int | None = None
    previous_month: int | None = None

    with pdfplumber.open(str(pdf_path)) as pdf:
        owner = extract_owner(pdf)

        for page_number, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if "FSTD Sessions" in page_text:
                continue

            table = page.extract_table() or []
            page_year = row_year(table, page_text)
            if active_year is None:
                active_year = page_year
            elif page_year and page_year > active_year:
                active_year = page_year

            for index, row in enumerate(table):
                if not row or not looks_like_flight_row(row):
                    continue

                day = to_int(row[0]) or 1
                month = to_int(row[1]) or 1
                if active_year is None:
                    active_year = page_year or datetime.now().year
                if previous_month and month < previous_month and previous_month >= 10 and month <= 3:
                    active_year += 1
                if page_year and page_year > active_year:
                    active_year = page_year

                next_row = table[index + 1] if index + 1 < len(table) else []
                dep_code = clean(row[2]).upper()
                arr_code = clean(row[3]).upper()
                remarks = clean_remarks(row[28] if len(row) > 28 else "")
                dep_key = place_key(dep_code, remarks, "DEP")
                arr_key = place_key(arr_code, remarks, "ARR")
                cross_country = is_cross_country(remarks)
                pic_minutes = minutes_from_columns(row, 20, 21)

                flights.append(
                    Flight(
                        date=safe_date(active_year, month, day),
                        year=active_year,
                        month=month,
                        day=day,
                        dep_key=dep_key,
                        arr_key=arr_key,
                        dep_code=dep_code,
                        arr_code=arr_code,
                        aircraft_type=clean(row[4]) or "Unknown",
                        registration=clean(next_row[4] if len(next_row) > 4 else "") or "Unknown",
                        total_minutes=minutes_from_columns(row, 11, 12),
                        pic_minutes=pic_minutes,
                        dual_minutes=minutes_from_columns(row, 24, 25),
                        copi_minutes=minutes_from_columns(row, 22, 23),
                        instructor_minutes=minutes_from_columns(row, 26, 27),
                        xc_minutes=minutes_from_columns(row, 11, 12) if cross_country else 0,
                        pic_xc_minutes=pic_minutes if cross_country else 0,
                        landings=to_int(row[14]) or 0,
                        name_pic=clean(row[13]) or "Unknown",
                        remarks=remarks,
                        cross_country=cross_country,
                        dep_time=clean(next_row[2] if len(next_row) > 2 else ""),
                        arr_time=clean(next_row[3] if len(next_row) > 3 else ""),
                        page=page_number,
                    )
                )

                previous_month = month

    return flights, owner


def empty_metrics() -> dict[str, int]:
    return {
        "flights": 0,
        "landings": 0,
        "total_minutes": 0,
        "pic_minutes": 0,
        "dual_minutes": 0,
        "copi_minutes": 0,
        "instructor_minutes": 0,
        "xc_minutes": 0,
        "pic_xc_minutes": 0,
    }


def add_metrics(bucket: dict[str, int], flight: Flight) -> None:
    bucket["flights"] += 1
    bucket["landings"] += flight.landings
    bucket["total_minutes"] += flight.total_minutes
    bucket["pic_minutes"] += flight.pic_minutes
    bucket["dual_minutes"] += flight.dual_minutes
    bucket["copi_minutes"] += flight.copi_minutes
    bucket["instructor_minutes"] += flight.instructor_minutes
    bucket["xc_minutes"] += flight.xc_minutes
    bucket["pic_xc_minutes"] += flight.pic_xc_minutes


def metric_rows(grouped: dict[str, dict[str, int]], label_key: str = "label") -> list[dict[str, Any]]:
    rows = [{label_key: key, **values} for key, values in grouped.items()]
    return sorted(rows, key=lambda row: (row["total_minutes"], row["flights"], row[label_key]), reverse=True)


def summarise_flights(flights: list[Flight], owner: str = "", source_filename: str = "") -> dict[str, Any]:
    airport_cache: dict[str, AirportPoint | None] = {}
    for flight in flights:
        airport_cache.setdefault(flight.dep_key, resolve_airport(flight.dep_key))
        airport_cache.setdefault(flight.arr_key, resolve_airport(flight.arr_key))

    totals = empty_metrics()
    by_type: dict[str, dict[str, int]] = defaultdict(empty_metrics)
    by_registration: dict[str, dict[str, int]] = defaultdict(empty_metrics)
    by_pic_name: dict[str, dict[str, int]] = defaultdict(empty_metrics)
    by_month: dict[str, dict[str, int]] = defaultdict(empty_metrics)
    by_year: dict[str, dict[str, int]] = defaultdict(empty_metrics)
    airport_stats: dict[str, dict[str, Any]] = {}
    route_stats: dict[tuple[str, str], dict[str, Any]] = {}
    unresolved: set[str] = set()

    first_date = min((flight.date for flight in flights if flight.date), default="")
    last_date = max((flight.date for flight in flights if flight.date), default="")

    for flight in flights:
        add_metrics(totals, flight)
        add_metrics(by_type[flight.aircraft_type], flight)
        add_metrics(by_registration[flight.registration], flight)
        add_metrics(by_pic_name[flight.name_pic], flight)
        add_metrics(by_month[flight.date[:7]], flight)
        add_metrics(by_year[str(flight.year)], flight)

        dep_airport = airport_cache.get(flight.dep_key)
        arr_airport = airport_cache.get(flight.arr_key)
        if not dep_airport:
            unresolved.add(flight.dep_key)
        if not arr_airport:
            unresolved.add(flight.arr_key)

        for key, airport, role in (
            (flight.dep_key, dep_airport, "departures"),
            (flight.arr_key, arr_airport, "arrivals"),
        ):
            if not airport:
                continue
            stats = airport_stats.setdefault(
                key,
                {
                    **asdict(airport),
                    "departures": 0,
                    "arrivals": 0,
                    "visits": 0,
                    "landings": 0,
                    "total_minutes": 0,
                    "pic_minutes": 0,
                    "xc_minutes": 0,
                },
            )
            stats[role] += 1
            stats["visits"] += 1
            stats["total_minutes"] += flight.total_minutes
            stats["pic_minutes"] += flight.pic_minutes
            stats["xc_minutes"] += flight.xc_minutes
            if role == "arrivals":
                stats["landings"] += flight.landings

        distance_nm = 0.0
        path: list[list[float]] | None = None
        if dep_airport and arr_airport:
            distance_nm = great_circle_nm(dep_airport.lat, dep_airport.lon, arr_airport.lat, arr_airport.lon)
            path = [[dep_airport.lat, dep_airport.lon], [arr_airport.lat, arr_airport.lon]]

        if flight.cross_country:
            totals["xc_distance_nm"] = round(float(totals.get("xc_distance_nm", 0.0)) + distance_nm, 1)
            if flight.pic_minutes:
                totals["pic_xc_distance_nm"] = round(float(totals.get("pic_xc_distance_nm", 0.0)) + distance_nm, 1)

        route_key = (flight.dep_key, flight.arr_key)
        route = route_stats.setdefault(
            route_key,
            {
                "from_key": flight.dep_key,
                "to_key": flight.arr_key,
                "from_label": flight.dep_key,
                "to_label": flight.arr_key,
                "path": path,
                "distance_nm": distance_nm,
                "flights": 0,
                "landings": 0,
                "total_minutes": 0,
                "pic_minutes": 0,
                "dual_minutes": 0,
                "xc_minutes": 0,
                "pic_xc_minutes": 0,
                "cross_country_flights": 0,
            },
        )
        if path and not route["path"]:
            route["path"] = path
            route["distance_nm"] = distance_nm
        route["flights"] += 1
        route["landings"] += flight.landings
        route["total_minutes"] += flight.total_minutes
        route["pic_minutes"] += flight.pic_minutes
        route["dual_minutes"] += flight.dual_minutes
        route["xc_minutes"] += flight.xc_minutes
        route["pic_xc_minutes"] += flight.pic_xc_minutes
        if flight.cross_country:
            route["cross_country_flights"] += 1

    totals["xc_distance_nm"] = round(float(totals.get("xc_distance_nm", 0.0)), 1)
    totals["pic_xc_distance_nm"] = round(float(totals.get("pic_xc_distance_nm", 0.0)), 1)
    totals["unique_airports"] = len(airport_stats)
    totals["unique_routes"] = len(route_stats)

    airports = sorted(airport_stats.values(), key=lambda item: (item["visits"], item["landings"], item["label"]), reverse=True)
    routes = sorted(route_stats.values(), key=lambda item: (item["flights"], item["total_minutes"]), reverse=True)
    recent = sorted(flights, key=lambda flight: flight.date, reverse=True)[:20]

    return {
        "meta": {
            "owner": owner,
            "source_filename": source_filename,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "flight_count": len(flights),
            "first_date": first_date,
            "last_date": last_date,
        },
        "totals": totals,
        "aircraft_types": metric_rows(by_type, "aircraft_type"),
        "registrations": metric_rows(by_registration, "registration"),
        "pic_names": metric_rows(by_pic_name, "name"),
        "monthly": sorted(metric_rows(by_month, "month"), key=lambda row: row["month"]),
        "yearly": sorted(metric_rows(by_year, "year"), key=lambda row: row["year"]),
        "airports": airports,
        "routes": routes,
        "recent_flights": [asdict(flight) for flight in recent],
        "unresolved_airports": sorted(value for value in unresolved if value and value != "ZZZZ"),
    }


def parse_pdf_to_summary(pdf_path: Path, source_filename: str = "") -> dict[str, Any]:
    flights, owner = parse_logbook(pdf_path)
    if not flights:
        raise ValueError("No flights were found in this FOCA logbook export.")
    return summarise_flights(flights, owner=owner, source_filename=source_filename)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse a FOCA logbook PDF and print dashboard JSON.")
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    print(json.dumps(parse_pdf_to_summary(args.pdf, source_filename=args.pdf.name), indent=2))
