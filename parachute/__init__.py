"""Dominio de Parachute S.A.: FAQ, clima de salto y agenda (base HDT-5)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from .faq import FAQRetriever, load_faq_json
from .scheduling import AppointmentBook
from .weather import FixtureHttp, UrllibHttp, WeatherService

ROOT = Path(__file__).resolve().parent.parent


def today_from_env() -> date | None:
    """`PARACHUTE_TODAY=YYYY-MM-DD` fija la fecha de referencia (demos reproducibles)."""
    value = os.environ.get("PARACHUTE_TODAY")
    return date.fromisoformat(value) if value else None


def build_services() -> tuple[FAQRetriever, WeatherService, AppointmentBook]:
    fixture = os.environ.get("PARACHUTE_WEATHER_FIXTURE")
    http = FixtureHttp(fixture) if fixture else UrllibHttp()
    faq = load_faq_json(os.environ.get("PARACHUTE_FAQ", ROOT / "data" / "faqs.json"))
    weather = WeatherService(http, today=today_from_env())
    book = AppointmentBook(weather, os.environ.get("PARACHUTE_APPOINTMENTS", ROOT / "data" / "appointments.json"))
    return faq, weather, book
