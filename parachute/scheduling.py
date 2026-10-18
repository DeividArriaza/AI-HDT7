"""Agenda de citas de salto: solo reserva si el clima lo permite."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from .weather import WeatherResult, WeatherService


@dataclass(frozen=True)
class Appointment:
    appointment_id: str
    name: str
    date: str
    people: int
    weather_status: str
    created_at: str


@dataclass(frozen=True)
class BookingResult:
    booked: bool
    message: str
    weather: dict
    appointment: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class AppointmentBook:
    def __init__(self, weather: WeatherService, store_path: str | Path | None = None):
        self.weather = weather
        self.store_path = Path(store_path) if store_path else None
        self._lock = threading.Lock()
        self._items: list[Appointment] = []
        if self.store_path and self.store_path.exists():
            self._items = [Appointment(**i) for i in json.loads(self.store_path.read_text(encoding="utf-8"))]

    def _save(self) -> None:
        if self.store_path:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self.store_path.write_text(json.dumps([asdict(i) for i in self._items], ensure_ascii=False, indent=2),
                                       encoding="utf-8")

    def book(self, name: str, requested_date: date, people: int = 1) -> BookingResult:
        if not name.strip():
            raise ValueError("Se requiere el nombre de la persona que reserva.")
        if people < 1:
            raise ValueError("La cantidad de personas debe ser al menos 1.")
        report: WeatherResult = self.weather.assess(requested_date)
        if report.status == "rejected":
            return BookingResult(False, f"No se agendó: el {report.date} no es seguro para saltar ("
                                 + "; ".join(report.reasons) + ").", report.to_dict())
        appointment = Appointment(
            appointment_id=f"CITA-{uuid.uuid4().hex[:8].upper()}",
            name=name.strip(),
            date=report.date,
            people=people,
            weather_status=report.status,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self._lock:
            self._items.append(appointment)
            self._save()
        message = f"Cita {appointment.appointment_id} agendada para {name.strip()} el {report.date}."
        if report.status == "marginal":
            message += " Condición marginal: solo tándem experimentado (" + "; ".join(report.reasons) + ")."
        return BookingResult(True, message, report.to_dict(), asdict(appointment))

    def list(self, name: str | None = None) -> list[dict]:
        with self._lock:
            items = [asdict(i) for i in self._items]
        if name:
            items = [i for i in items if name.lower() in i["name"].lower()]
        return items
