"""CMMS - Schemi Pydantic (contratto pubblico dell'API)."""

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

WorkOrderStatus = Literal["open", "in_progress", "closed"]
Priority = Literal["low", "medium", "high"]


class MachineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    location: str
    status: str
    manufacturer: str
    model: str
    install_date: date


class TechnicianOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    specialty: str
    email: str


class SensorsOut(BaseModel):
    machine_id: str
    # Ogni lettura: {"timestamp": ..., <metriche specifiche della macchina>}
    readings: List[Dict[str, Any]]


class MaintenanceItem(BaseModel):
    date: date
    type: str
    description: str
    technician: Optional[str] = None


class MaintenanceOut(BaseModel):
    machine_id: str
    history: List[MaintenanceItem]


class WorkOrderOut(BaseModel):
    id: str  # codice leggibile, es. "WO-1042"
    machine_id: str
    description: str
    status: WorkOrderStatus
    priority: Priority
    created_at: datetime
    created_by: str
    assigned_to: Optional[str] = None


class WorkOrderCreate(BaseModel):
    machine_id: str = Field(min_length=1)
    description: str = Field(min_length=5, description="Descrizione del problema/intervento")
    priority: Priority = "medium"
    created_by: str = "api-client"
    assigned_to: Optional[int] = Field(None, description="ID del tecnico assegnatario (opzionale)")
