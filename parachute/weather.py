"""Evaluación meteorológica del salto con Open-Meteo (heredado de HDT-5).

Usa la opción `daily` de Open-Meteo para la zona de aterrizaje y aplica los
criterios de seguridad de Parachute S.A. El transporte HTTP es inyectable para
poder ejecutar pruebas y demos sin red.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

LATITUDE = 14.013722
LONGITUDE = -90.771611
API_URL = "https://api.open-meteo.com/v1/forecast"
TIMEZONE = "America/Guatemala"
MAX_FORECAST_DAYS = 16
# Variable lógica del enunciado -> variable `daily` de Open-Meteo.
DAILY = {
    "wind_speed_10m": "wind_speed_10m_max",
    "wind_gust_10m": "wind_gusts_10m_max",
    "temperature_2m": "temperature_2m_max",
    "precipitation": "precipitation_sum",
    "cloud_cover": "cloud_cover_mean",
}


UNITS = {"wind_speed_10m": "km/h", "wind_gust_10m": "km/h", "temperature_2m": "°C",
         "precipitation": "mm", "cloud_cover": "%"}
LOCATION = f"zona de aterrizaje de Parachute S.A. ({LATITUDE}, {LONGITUDE})"


class DateOutOfRange(ValueError):
    """La fecha está en el pasado o más allá de los 16 días de pronóstico."""


class HttpJson(Protocol):
    def get_json(self, url: str, params: dict[str, str]) -> dict: ...


class UrllibHttp:
    def get_json(self, url: str, params: dict[str, str]) -> dict:
        with urlopen(f"{url}?{urlencode(params)}", timeout=15) as response:
            return json.load(response)


class FixtureHttp:
    """Devuelve siempre el mismo JSON; útil para demos y pruebas sin red."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get_json(self, url: str, params: dict[str, str]) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


def forecast_params(requested_date: date) -> dict[str, str]:
    return {
        "latitude": str(LATITUDE),
        "longitude": str(LONGITUDE),
        "daily": ",".join(DAILY.values()),
        "start_date": requested_date.isoformat(),
        "end_date": requested_date.isoformat(),
        "timezone": TIMEZONE,
    }


@dataclass(frozen=True)
class WeatherResult:
    date: str
    status: str  # favorable | marginal | rejected
    reasons: list[str]
    values: dict[str, float]
    citation: str
    notes: list[str] = field(default_factory=list)
    units: dict[str, str] = field(default_factory=lambda: dict(UNITS))
    location: str = LOCATION

    def to_dict(self) -> dict:
        return asdict(self)


def classify(values: dict[str, float]) -> tuple[str, list[str], list[str]]:
    rejected, marginal = [], []
    wind, gust = values["wind_speed_10m"], values["wind_gust_10m"]
    rain, clouds = values["precipitation"], values["cloud_cover"]
    if wind > 28:
        rejected.append(f"viento en superficie {wind} km/h > 28 km/h")
    elif wind >= 20:
        marginal.append(f"viento en superficie {wind} km/h entre 20 y 28 km/h (solo tándem experimentado)")
    if gust > 35:
        rejected.append(f"ráfagas {gust} km/h > 35 km/h")
    if rain > 0.0:
        rejected.append(f"precipitación {rain} mm > 0.0 mm")
    if clouds > 75:
        rejected.append(f"nubosidad {clouds}% > 75%")
    elif clouds >= 30:
        marginal.append(f"nubosidad {clouds}% entre 30 y 75% (nubes dispersas)")
    status = "rejected" if rejected else "marginal" if marginal else "favorable"
    return status, rejected, marginal


class WeatherService:
    def __init__(self, http: HttpJson | None = None, today: date | None = None):
        self.http = http or UrllibHttp()
        self._today = today

    @property
    def today(self) -> date:
        return self._today or date.today()

    def validate_date(self, requested_date: date) -> None:
        days = (requested_date - self.today).days
        if days < 0:
            raise DateOutOfRange(f"La fecha {requested_date} ya pasó; hoy es {self.today}.")
        if days > MAX_FORECAST_DAYS:
            raise DateOutOfRange(
                f"Open-Meteo solo pronostica {MAX_FORECAST_DAYS} días; {requested_date} está a {days} días "
                f"de hoy ({self.today}). Elija una fecha hasta {date.fromordinal(self.today.toordinal() + MAX_FORECAST_DAYS)}."
            )

    def assess(self, requested_date: date) -> WeatherResult:
        self.validate_date(requested_date)
        params = forecast_params(requested_date)
        daily = self.http.get_json(API_URL, params)["daily"]
        values = {name: float(daily[api_name][0] or 0.0) for name, api_name in DAILY.items()}
        status, rejected, marginal = classify(values)
        return WeatherResult(
            date=requested_date.isoformat(),
            status=status,
            reasons=rejected or marginal,
            values=values,
            citation=f"{API_URL}?{urlencode(params)}",
            notes=marginal if rejected else [],
        )
