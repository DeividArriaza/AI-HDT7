import json
from datetime import date
from pathlib import Path

import pytest

from a2a_server.mas import RuleBasedMAS, parse_date
from parachute import ROOT, load_faq_json
from parachute.scheduling import AppointmentBook
from parachute.weather import DateOutOfRange, FixtureHttp, WeatherService, classify

TODAY = date(2026, 10, 17)
FIXTURES = ROOT / "fixtures"


def weather(name: str) -> WeatherService:
    return WeatherService(FixtureHttp(FIXTURES / f"{name}.json"), today=TODAY)


def values(wind=10, gust=10, rain=0.0, clouds=10):
    return {"wind_speed_10m": wind, "wind_gust_10m": gust, "precipitation": rain, "cloud_cover": clouds,
            "temperature_2m": 25}


def test_faq_finds_known_question_and_cites_id():
    result = load_faq_json(ROOT / "data" / "faqs.json").search("¿Cuál es el peso máximo para saltar?")
    assert result.found and result.faq_id == "FAQ-021" and "100 kg" in result.answer


def test_faq_rejects_unrelated_question():
    assert not load_faq_json(ROOT / "data" / "faqs.json").search("¿Venden pizza de piña?").found


@pytest.mark.parametrize("kwargs,status", [
    ({}, "favorable"),
    ({"wind": 20}, "marginal"),
    ({"clouds": 30}, "marginal"),
    ({"wind": 29}, "rejected"),
    ({"gust": 36}, "rejected"),
    ({"rain": 0.1}, "rejected"),
    ({"clouds": 76}, "rejected"),
])
def test_safety_rules(kwargs, status):
    assert classify(values(**kwargs))[0] == status


def test_weather_rejects_dates_beyond_16_days():
    with pytest.raises(DateOutOfRange):
        weather("favorable").assess(date(2026, 11, 3))
    assert weather("favorable").assess(date(2026, 11, 2)).status == "favorable"


def test_booking_checks_weather_first(tmp_path: Path):
    store = tmp_path / "citas.json"
    assert not AppointmentBook(weather("rechazado"), store).book("Ana", date(2026, 10, 20)).booked
    ok = AppointmentBook(weather("marginal"), store).book("Ana", date(2026, 10, 20), 2)
    assert ok.booked and "solo tándem experimentado" in ok.message
    assert json.loads(store.read_text())[0]["people"] == 2


def test_relative_dates():
    assert parse_date("¿clima para mañana?", TODAY) == date(2026, 10, 18)
    assert parse_date("el 25/10/2026", TODAY) == date(2026, 10, 25)
    assert parse_date("el sábado", TODAY) == date(2026, 10, 24)  # 2026-10-17 es sábado


class FakeTools:
    """Sustituye al cliente MCP para probar el enrutamiento del MAS sin servidores."""

    def __init__(self):
        self.calls = []

    async def call(self, name, **arguments):
        self.calls.append(name)
        return {
            "fecha_actual": {"today": TODAY.isoformat()},
            "agendar_cita": {"booked": True, "message": f"Cita para {arguments.get('nombre')}", "weather": {}},
            "buscar_faq": {"found": False, "answer": "sin respuesta"},
        }[name]


async def test_rule_based_mas_asks_for_name_then_books():
    mas = RuleBasedMAS("http://unused")
    mas.tools = FakeTools()
    first = await mas.run("Quiero agendar una cita para el 2026-10-24", "ctx")
    assert "nombre" in first.text
    second = await mas.run("Ana López", "ctx")
    assert second.text == "Cita para Ana López" and mas.tools.calls[-1] == "agendar_cita"
