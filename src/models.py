"""
CMMS - Modelli del database

  machines ─┬─< sensor_readings
            ├─< maintenance_records >─ technicians
            └─< work_orders          >─ technicians (assegnatario)
"""

from datetime import date, datetime
from typing import Optional, Any

from sqlalchemy import String, Text, Date, DateTime, ForeignKey, JSON, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

# JSONB su PostgreSQL (indicizzabile/interrogabile), JSON generico altrove (SQLite nei test)
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # tag impianto, es. "P-102"
    type: Mapped[str] = mapped_column(String(80))
    location: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="running")
    manufacturer: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80))
    install_date: Mapped[date] = mapped_column(Date)


class Technician(Base):
    __tablename__ = "technicians"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80))
    specialty: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(120))


class SensorReading(Base):
    __tablename__ = "sensor_readings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    # Le grandezze misurate dipendono dal tipo di macchina (pompa vs compressore):
    # un JSONB evita una tabella con decine di colonne quasi sempre NULL.
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonType)


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    type: Mapped[str] = mapped_column(String(20))  # preventive | corrective
    description: Mapped[str] = mapped_column(Text)
    technician_id: Mapped[Optional[int]] = mapped_column(ForeignKey("technicians.id"))

    technician: Mapped[Optional[Technician]] = relationship(lazy="joined")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), index=True)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open | in_progress | closed
    priority: Mapped[str] = mapped_column(String(10), default="medium")           # low | medium | high
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(80), default="unknown")
    assigned_to: Mapped[Optional[int]] = mapped_column(ForeignKey("technicians.id"))

    technician: Mapped[Optional[Technician]] = relationship(lazy="joined")
