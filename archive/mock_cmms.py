"""
FASE 3 - Mock CMMS
Simula un database di Computerized Maintenance Management System.
In un sistema reale queste funzioni farebbero query a un DB / API esterna
(es. SAP PM, Maximo, Fiix...). Qui i dati sono statici/in-memory per demo.
"""

from datetime import datetime
import itertools

_wo_counter = itertools.count(1043)  # prossimo ID di work order da assegnare

MACHINES = {
    "P-102": {
        "machine_id": "P-102",
        "type": "centrifugal pump",
        "location": "Process Line 2 - Cooling water loop",
        "status": "running",
        "manufacturer": "PumpCo",
        "model": "P-100 series",
        "install_date": "2021-04-12",
    },
    "C-201": {
        "machine_id": "C-201",
        "type": "reciprocating compressor",
        "location": "Process Line 2 - Gas compression",
        "status": "running",
        "manufacturer": "CompressAll",
        "model": "C-200 series",
        "install_date": "2020-11-03",
    },
}

# Serie storica simulata delle ultime letture sensori (la più recente per ultima)
SENSOR_DATA = {
    "P-102": [
        {"timestamp": "2026-09-20T08:00:00", "bearing_temp_c": 68, "casing_temp_c": 65, "vibration_mm_s": 2.1, "flow_m3h": 245},
        {"timestamp": "2026-09-21T08:00:00", "bearing_temp_c": 74, "casing_temp_c": 69, "vibration_mm_s": 2.4, "flow_m3h": 240},
        {"timestamp": "2026-09-22T08:00:00", "bearing_temp_c": 81, "casing_temp_c": 74, "vibration_mm_s": 2.9, "flow_m3h": 238},
        {"timestamp": "2026-09-23T08:00:00", "bearing_temp_c": 89, "casing_temp_c": 80, "vibration_mm_s": 3.3, "flow_m3h": 235},
        {"timestamp": "2026-09-24T08:00:00", "bearing_temp_c": 93, "casing_temp_c": 84, "vibration_mm_s": 3.6, "flow_m3h": 233},
    ],
    "C-201": [
        {"timestamp": "2026-09-24T08:00:00", "discharge_temp_c": 138, "suction_pressure_bar": 3.1, "discharge_pressure_bar": 11.4, "vibration_mm_s": 1.8},
    ],
}

MAINTENANCE_HISTORY = {
    "P-102": [
        {"date": "2024-02-15", "description": "Bearing replacement (both DE/NDE)", "technician": "M. Rossi"},
        {"date": "2025-03-10", "description": "Oil change + alignment check (OK)", "technician": "L. Bianchi"},
        {"date": "2025-11-02", "description": "Cooling water jacket cleaning (moderate scale found)", "technician": "M. Rossi"},
    ],
    "C-201": [
        {"date": "2025-06-01", "description": "Suction/discharge valve replacement", "technician": "G. Verdi"},
    ],
}

OPEN_WORK_ORDERS = {
    "P-102": [
        {"id": "WO-1042", "description": "Investigate rising bearing temperature trend", "status": "open", "created": "2026-09-23"},
    ],
    "C-201": [],
}


def get_machine_status(machine_id: str) -> dict:
    """Restituisce stato generale e anagrafica della macchina."""
    machine = MACHINES.get(machine_id)
    if not machine:
        return {"error": f"Macchina '{machine_id}' non trovata nel CMMS."}
    return machine


def get_sensor_data(machine_id: str) -> dict:
    """Restituisce le ultime letture sensori disponibili per la macchina, in ordine cronologico."""
    readings = SENSOR_DATA.get(machine_id)
    if not readings:
        return {"error": f"Nessun dato sensori disponibile per '{machine_id}'."}
    return {"machine_id": machine_id, "readings": readings}


def get_maintenance_history(machine_id: str) -> dict:
    """Restituisce lo storico interventi di manutenzione sulla macchina."""
    history = MAINTENANCE_HISTORY.get(machine_id)
    if history is None:
        return {"error": f"Nessuno storico manutenzione per '{machine_id}'."}
    return {"machine_id": machine_id, "history": history}


def get_open_work_orders(machine_id: str) -> dict:
    """Restituisce i work order aperti attualmente associati alla macchina."""
    orders = OPEN_WORK_ORDERS.get(machine_id)
    if orders is None:
        return {"error": f"Macchina '{machine_id}' non trovata nel CMMS."}
    return {"machine_id": machine_id, "open_work_orders": orders}


def create_work_order(machine_id: str, description: str) -> dict:
    """Crea un nuovo work order per la macchina indicata e lo aggiunge alla lista degli aperti."""
    if machine_id not in MACHINES:
        return {"error": f"Macchina '{machine_id}' non trovata nel CMMS."}

    new_id = f"WO-{next(_wo_counter)}"
    order = {
        "id": new_id,
        "description": description,
        "status": "open",
        "created": datetime.now().strftime("%Y-%m-%d"),
    }
    OPEN_WORK_ORDERS.setdefault(machine_id, []).append(order)
    return {"created": order}
