"""
CMMS - API REST

  GET  /machines                     elenco macchine
  GET  /machines/{id}                anagrafica e stato
  GET  /machines/{id}/sensors        ultime letture sensori
  GET  /machines/{id}/maintenance    storico manutenzione
  GET  /work-orders                  elenco work order (filtri: machine_id, status)
  POST /work-orders                  crea un work order
  GET  /technicians                  elenco tecnici
  GET  /health                       liveness (senza autenticazione)

Autenticazione: header  X-API-Key: <CMMS_API_KEY>
"""

import os
import secrets
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import get_db
from models import Machine, MaintenanceRecord, SensorReading, Technician, WorkOrder
import schemas

API_KEY = os.getenv("CMMS_API_KEY", "dev-cmms-key")


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key mancante o non valida.")


app = FastAPI(title="CMMS API (simulato)", version="1.0.0")
protected = [Depends(require_api_key)]


# --- helper -----------------------------------------------------------------

def _get_machine_or_404(db: Session, machine_id: str) -> Machine:
    # I tag impianto sono case-insensitive per chi chiama ("p-102" == "P-102")
    machine = db.get(Machine, machine_id.strip().upper())
    if machine is None:
        raise HTTPException(status_code=404, detail=f"Macchina '{machine_id}' non trovata nel CMMS.")
    return machine


def _wo_out(wo: WorkOrder) -> schemas.WorkOrderOut:
    return schemas.WorkOrderOut(
        id=f"WO-{wo.id}",
        machine_id=wo.machine_id,
        description=wo.description,
        status=wo.status,
        priority=wo.priority,
        created_at=wo.created_at,
        created_by=wo.created_by,
        assigned_to=wo.technician.name if wo.technician else None,
    )


# --- endpoints --------------------------------------------------------------

@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))  # verifica anche la raggiungibilità del database
    return {"status": "ok", "database": "up"}


@app.get("/machines", response_model=List[schemas.MachineOut], dependencies=protected)
def list_machines(db: Session = Depends(get_db)):
    return db.scalars(select(Machine).order_by(Machine.id)).all()


@app.get("/machines/{machine_id}", response_model=schemas.MachineOut, dependencies=protected)
def get_machine(machine_id: str, db: Session = Depends(get_db)):
    return _get_machine_or_404(db, machine_id)


@app.get("/machines/{machine_id}/sensors", response_model=schemas.SensorsOut, dependencies=protected)
def get_sensors(machine_id: str, limit: int = Query(5, ge=1, le=200), db: Session = Depends(get_db)):
    machine = _get_machine_or_404(db, machine_id)
    rows = db.scalars(
        select(SensorReading)
        .where(SensorReading.machine_id == machine.id)
        .order_by(SensorReading.timestamp.desc())
        .limit(limit)
    ).all()
    rows.reverse()  # ultime N letture, in ordine cronologico
    readings = [{"timestamp": r.timestamp.isoformat(), **r.metrics} for r in rows]
    return schemas.SensorsOut(machine_id=machine.id, readings=readings)


@app.get("/machines/{machine_id}/maintenance", response_model=schemas.MaintenanceOut, dependencies=protected)
def get_maintenance(machine_id: str, db: Session = Depends(get_db)):
    machine = _get_machine_or_404(db, machine_id)
    rows = db.scalars(
        select(MaintenanceRecord)
        .where(MaintenanceRecord.machine_id == machine.id)
        .order_by(MaintenanceRecord.date)
    ).unique().all()
    history = [
        schemas.MaintenanceItem(
            date=r.date, type=r.type, description=r.description,
            technician=r.technician.name if r.technician else None,
        )
        for r in rows
    ]
    return schemas.MaintenanceOut(machine_id=machine.id, history=history)


@app.get("/work-orders", response_model=List[schemas.WorkOrderOut], dependencies=protected)
def list_work_orders(
    machine_id: Optional[str] = None,
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="open | in_progress | closed | active (= open + in_progress)",
    ),
    db: Session = Depends(get_db),
):
    query = select(WorkOrder).order_by(WorkOrder.id)

    if machine_id:
        query = query.where(WorkOrder.machine_id == _get_machine_or_404(db, machine_id).id)

    if status_filter == "active":
        query = query.where(WorkOrder.status.in_(["open", "in_progress"]))
    elif status_filter:
        if status_filter not in ("open", "in_progress", "closed"):
            raise HTTPException(status_code=422, detail="status deve essere: open, in_progress, closed o active.")
        query = query.where(WorkOrder.status == status_filter)

    return [_wo_out(wo) for wo in db.scalars(query).unique().all()]


@app.post("/work-orders", response_model=schemas.WorkOrderOut, status_code=201, dependencies=protected)
def create_work_order(payload: schemas.WorkOrderCreate, db: Session = Depends(get_db)):
    machine = _get_machine_or_404(db, payload.machine_id)

    if payload.assigned_to is not None and db.get(Technician, payload.assigned_to) is None:
        raise HTTPException(status_code=404, detail=f"Tecnico {payload.assigned_to} non trovato.")

    wo = WorkOrder(
        machine_id=machine.id,
        description=payload.description,
        priority=payload.priority,
        created_by=payload.created_by,
        assigned_to=payload.assigned_to,
        status="open",
    )
    db.add(wo)
    db.commit()
    db.refresh(wo)
    return _wo_out(wo)


@app.get("/technicians", response_model=List[schemas.TechnicianOut], dependencies=protected)
def list_technicians(db: Session = Depends(get_db)):
    return db.scalars(select(Technician).order_by(Technician.id)).all()
