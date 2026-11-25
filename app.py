#!/usr/bin/env python3
"""Push Stockholm weather and metro departures to an AWTRIX NG display."""

from __future__ import annotations

import json
from base64 import b64encode
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


LOG = logging.getLogger("awtrix-stockholm")
STOCKHOLM = ZoneInfo("Europe/Stockholm")
SL_API = "https://transport.integration.sl.se/v1"
SMHI_API = "https://opendata-download-metfcst.smhi.se/api/category/snow1g/version/1/geotype/point"
WEATHER_COLORS = {
    "rain": "#00A5FF",
    "cloud": "#909090",
    "snow": "#FFFFFF",
    "sun": "#FFD43B",
    "night": "#B8D5FF",
}
MINUTE_COLORS = ["#FF453A", "#FF9F0A", "#FFD60A", "#30D158", "#0A84FF", "#BF5AF2"]
ASSET_DIR = Path(__file__).resolve().parent / "assets"
WEATHER_BACKGROUNDS = {
    name: "data:image/gif;base64," + b64encode((ASSET_DIR / f"{name}.gif").read_bytes()).decode("ascii")
    for name in ("rain", "snow", "sun", "cloud", "night")
}


def setting(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip()


def get_json(url: str, timeout: int) -> Any:
    request = Request(url, headers={"User-Agent": "awtrix-stockholm-dashboard/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def push_app(base_url: str, app_name: str, payload: dict[str, Any], timeout: int) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{base_url}/api/v1/apps/pushed/{app_name}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status >= 300:
            raise RuntimeError(f"AWTRIX returned HTTP {response.status}")


def flatten_parameters(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize both SMHI's named data object and parameter-array response shapes."""
    out: dict[str, Any] = {}
    data = item.get("data")
    if isinstance(data, dict):
        out.update(data)
    params = item.get("parameters")
    if isinstance(params, list):
        for param in params:
            if isinstance(param, dict):
                name = param.get("name") or param.get("key")
                value = param.get("value")
                if isinstance(value, list) and value:
                    value = value[0]
                if name is not None:
                    out[str(name)] = value
    elif isinstance(params, dict):
        out.update(params)
    # Some point data formats put named values directly on the time step.
    out.update({k: v for k, v in item.items() if k not in {"data", "parameters", "validTime", "valid_time", "time"}})
    return {str(k).casefold(): v for k, v in out.items()}


def value_for(values: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name.casefold() in values:
            return values[name.casefold()]
    return None


def parse_time(value: Any, naive_timezone: Any = timezone.utc) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=naive_timezone) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def sun_is_up(moment: datetime, latitude: float, longitude: float) -> bool:
    """Estimate solar elevation using NOAA's equation-of-time approximation."""
    local = moment.astimezone(STOCKHOLM)
    hour = local.hour + local.minute / 60 + local.second / 3600
    gamma = 2 * math.pi / 365 * (local.timetuple().tm_yday - 1 + (hour - 12) / 24)
    equation = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    utc_offset = local.utcoffset().total_seconds() / 60 if local.utcoffset() else 0
    solar_minutes = (
        local.hour * 60 + local.minute + local.second / 60 + equation + 4 * longitude - utc_offset
    ) % 1440
    hour_angle = math.radians(solar_minutes / 4 - 180)
    lat = math.radians(latitude)
    sine_elevation = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(declination) * math.cos(hour_angle)
    return math.asin(max(-1.0, min(1.0, sine_elevation))) > 0


def weather_payload(timeout: int) -> dict[str, Any]:
    lat = float(setting("SMHI_LAT", "59.3464"))
    lon = float(setting("SMHI_LON", "17.8934"))
    url = f"{SMHI_API}/lon/{lon:.4f}/lat/{lat:.4f}/data.json"
    forecast = get_json(url, timeout)
    series = forecast.get("timeSeries", forecast.get("time_series", [])) if isinstance(forecast, dict) else []
    now = datetime.now(timezone.utc)
    future: list[tuple[datetime, dict[str, Any]]] = []
    for row in series:
        if not isinstance(row, dict):
            continue
        valid = parse_time(row.get("validTime", row.get("valid_time", row.get("time"))))
        if valid and now - timedelta(minutes=10) <= valid <= now + timedelta(hours=6):
            future.append((valid, flatten_parameters(row)))
    future.sort(key=lambda pair: pair[0])
    if not future:
        raise RuntimeError("SMHI returned no forecast steps within the next six hours")

    temp = value_for(future[0][1], "air_temperature", "temperature", "t")
    try:
        temp_text = f"{float(temp):.0f}°C"
    except (TypeError, ValueError):
        temp_text = "--°C"

    precipitation_expected = False
    snow_expected = False
    cloud_cover: list[float] = []
    for _, values in future:
        cloud = value_for(values, "cloud_area_fraction", "cloud_cover", "tcc_mean")
        try:
            cloud_number = float(cloud)
            if 0 <= cloud_number <= 100:
                cloud_cover.append(cloud_number)
        except (TypeError, ValueError):
            pass

        raw = value_for(
            values,
            "precipitation_amount_mean_deterministic",
            "precipitation_amount_mean",
            "precipitation_amount_median",
            "precipitation_amount",
            "precipitation",
            "pmean",
            "precipitation1h",
            "precipitation_amount_1h",
        )
        try:
            amount = float(raw)
            if 0 <= amount < 9999 and amount > 0.05:
                precipitation_expected = True
        except (TypeError, ValueError):
            amount = 0.0
        kind = value_for(
            values,
            "predominant_precipitation_type_at_surface",
            "precipitation_type",
            "pcat",
            "precipitation_category",
        )
        try:
            kind_code = int(float(kind))
            if kind_code in {5, 6, 9}:
                snow_expected = True
            elif kind_code in {1, 2, 3, 4, 7, 8, 10, 11, 12}:
                precipitation_expected = True
        except (TypeError, ValueError):
            pass
        probability = value_for(values, "probability_of_precipitation")
        try:
            if float(probability) >= 40:
                precipitation_expected = True
        except (TypeError, ValueError):
            pass

    if snow_expected:
        weather = "snow"
    elif precipitation_expected:
        weather = "rain"
    elif cloud_cover and sum(cloud_cover) / len(cloud_cover) >= 4:
        weather = "cloud"
    elif sun_is_up(now, lat, lon):
        weather = "sun"
    else:
        weather = "night"
    return {
        "temperature": temp_text,
        "weather": weather,
    }


def destination_matches(destination: str) -> bool:
    options = [part.strip().casefold() for part in setting(
        "SL_CITYBOUND_DESTINATIONS", "Skarpnäck,Farsta strand,Hagsätra"
    ).split(",") if part.strip()]
    folded = destination.casefold()
    return any(option == folded or option in folded for option in options)


def departure_payload(site_id: Any, timeout: int) -> dict[str, Any]:
    response = get_json(f"{SL_API}/sites/{quote(str(site_id), safe='')}/departures?forecast=60", timeout)
    departures = response.get("departures", []) if isinstance(response, dict) else []
    now = datetime.now(STOCKHOLM)
    metro_lines = {
        line.strip()
        for line in setting("SL_METRO_LINES", "17,18,19").split(",")
        if line.strip()
    }
    skip_minutes = max(0, int(setting("TRAIN_SKIP_MINUTES", "5")))
    choices: list[tuple[datetime, dict[str, Any]]] = []
    for departure in departures:
        if not isinstance(departure, dict):
            continue
        line = departure.get("line") or {}
        mode = str(line.get("transport_mode", "")).upper()
        designation = str(line.get("designation", ""))
        destination = str(departure.get("destination", departure.get("direction", "")))
        if mode not in {"METRO", "SUBWAY", "TUNNELBANA"} or designation not in metro_lines or not destination_matches(destination):
            continue
        # SL returns local departure timestamps without an offset; treat them as Stockholm time.
        expected = parse_time(departure.get("expected"), STOCKHOLM) or parse_time(
            departure.get("scheduled"), STOCKHOLM
        )
        if expected is None:
            continue
        expected = expected.astimezone(STOCKHOLM)
        if expected >= now - timedelta(seconds=30):
            choices.append((expected, departure))

    if not choices:
        return {
            "text": "--m",
        }
    choices.sort(key=lambda pair: pair[0])
    def departure_minutes(choice: tuple[datetime, dict[str, Any]]) -> int:
        expected_time, item = choice
        display = item.get("display")
        shown = re.search(r"\d+", display) if isinstance(display, str) else None
        if shown:
            return int(shown.group(0))
        return max(0, int((expected_time - now).total_seconds() // 60))

    expected, departure = next(
        (choice for choice in choices if departure_minutes(choice) > skip_minutes),
        choices[-1],
    )
    minute_count = departure_minutes((expected, departure))
    minutes_text = f"{min(99, max(0, minute_count))}m"
    return {"text": minutes_text}


def dashboard_payload(site_id: Any, timeout: int) -> dict[str, Any]:
    weather = weather_payload(timeout)
    departure = departure_payload(site_id, timeout)
    minutes = departure["text"].removesuffix("m") + "M"
    minute_x = 0 if len(minutes) > 2 else 2
    draw = [
        ["text", minute_x + index * 4, 1, char, MINUTE_COLORS[index % len(MINUTE_COLORS)]]
        for index, char in enumerate(minutes)
    ]
    minute_block_end = minute_x + (len(minutes) - 1) * 4 + 4
    temperature_width = sum(2 if char in {"1", "-"} else 3 for char in weather["temperature"])
    temperature_x = max(minute_block_end + 1, 30 - temperature_width)
    draw.append(["text", temperature_x, 1, weather["temperature"], WEATHER_COLORS[weather["weather"]]])
    return {
        "text": "",
        "draw": draw,
        "icon": WEATHER_BACKGROUNDS[weather["weather"]],
        "textCase": "asTyped",
        "scroll": "static",
        "durationMs": 7000,
        "lifetimeMs": max(180_000, int(float(setting("POLL_SECONDS", "60")) * 3_000)),
        "lifetimeExpiry": "mark",
        "textColor": "#FFFFFF",
    }


def show_only_app(base_url: str, app_name: str, timeout: int) -> None:
    apps = get_json(f"{base_url}/api/v1/apps", timeout)
    other_apps = [
        app.get("name")
        for app in apps
        if isinstance(app, dict) and app.get("name") and app.get("name") != app_name
    ]
    order_request = Request(
        f"{base_url}/api/v1/apps/order",
        data=json.dumps({"order": [app_name], "disabled": other_apps}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urlopen(order_request, timeout=timeout):
        pass
    active_request = Request(
        f"{base_url}/api/v1/apps/active",
        data=json.dumps({"name": app_name, "fast": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urlopen(active_request, timeout=timeout):
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base_url = setting("AWTRIX_URL").rstrip("/")
    timeout = int(setting("HTTP_TIMEOUT", "15"))
    poll_seconds = max(30, int(setting("POLL_SECONDS", "60")))
    site_id = setting("SL_SITE_ID", "9106")
    LOG.info("Using SL site %s (id %s); refreshing every %ss", setting("SL_SITE_NAME", "Islandstorget"), site_id, poll_seconds)
    rotation_configured = False

    while True:
        try:
            push_app(base_url, "StockholmDashboard", dashboard_payload(site_id, timeout), timeout)
            if not rotation_configured:
                if setting("DISABLE_OTHER_APPS", "false").casefold() in {"1", "true", "yes", "on"}:
                    show_only_app(base_url, "StockholmDashboard", timeout)
                rotation_configured = True
            LOG.info("Updated the Stockholm dashboard card")
        except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError, KeyError) as exc:
            LOG.warning("Update failed: %s", exc)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
