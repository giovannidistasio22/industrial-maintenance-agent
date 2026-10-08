"""
CMMS - Creazione tabelle e dati di esempio

  python -m cmms.database.seed            # crea le tabelle e inserisce i dati (se il DB è vuoto)
  python -m cmms.database.seed --reset    # cancella tutto e ricrea da zero

Le date delle letture sensori sono relative a "oggi", così i dati risultano
sempre recenti quando lanci la demo.
"""

import sys
from datetime import date, datetime, timedelta

from sqlalchemy import select, text

from cmms.database.db import Base, SessionLocal, engine
from cmms.models.models import Machine, MaintenanceRecord, SensorReading, Technician, WorkOrder


def _pump_readings(now: datetime, rows: list[tuple]) -> list[dict]:
    """rows: (bearing_temp_c, casing_temp_c, vibration_mm_s, flow_m3h), dal più vecchio al più recente."""
    n = len(rows)
    return [
        {
            "timestamp": now - timedelta(days=n - 1 - i),
            "metrics": {"bearing_temp_c": b, "casing_temp_c": c, "vibration_mm_s": v, "flow_m3h": f},
        }
        for i, (b, c, v, f) in enumerate(rows)
    ]


def seed(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        if db.scalar(select(Machine.id).limit(1)) is not None:
            print("Database già popolato (usa --reset per ricrearlo da zero).")
            return

        now = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)

        # --- Tecnici ---------------------------------------------------------
        rossi = Technician(name="M. Rossi", specialty="Meccanica - macchine rotanti", email="m.rossi@plant.example")
        bianchi = Technician(name="L. Bianchi", specialty="Lubrificazione e allineamenti", email="l.bianchi@plant.example")
        verdi = Technician(name="G. Verdi", specialty="Compressori", email="g.verdi@plant.example")
        neri = Technician(name="S. Neri", specialty="Strumentazione e sensori", email="s.neri@plant.example")
        db.add_all([rossi, bianchi, verdi, neri])
        db.flush()

        # --- Macchine --------------------------------------------------------
        db.add_all([
            Machine(id="P-101", type="centrifugal pump", location="Process Line 1 - Process water",
                    status="running", manufacturer="PumpCo", model="P-100 series", install_date=date(2021, 4, 12)),
            Machine(id="P-102", type="centrifugal pump", location="Process Line 2 - Cooling water loop",
                    status="running", manufacturer="PumpCo", model="P-100 series", install_date=date(2021, 4, 12)),
            Machine(id="C-201", type="reciprocating compressor", location="Process Line 2 - Gas compression",
                    status="running", manufacturer="CompressAll", model="C-200 series", install_date=date(2020, 11, 3)),
        ])
        db.flush()

        # --- Sensori ---------------------------------------------------------
        # P-102: stabile per una settimana, poi trend crescente di temperatura/vibrazioni
        p102 = _pump_readings(now, [
            (66, 63, 2.0, 248), (67, 64, 2.0, 247), (66, 64, 2.1, 247), (67, 64, 2.0, 246),
            (68, 65, 2.1, 245), (68, 65, 2.1, 245),
            (68, 65, 2.1, 245), (74, 69, 2.4, 240), (81, 74, 2.9, 238), (89, 80, 3.3, 235), (93, 84, 3.6, 233),
        ])
        # P-101: pompa sana, valori stabili
        p101 = _pump_readings(now, [
            (66, 62, 1.9, 249), (67, 63, 1.9, 248), (66, 62, 2.0, 249), (67, 63, 1.9, 248), (66, 62, 1.9, 249),
        ])
        # C-201: temperatura di mandata in lieve aumento (condivide il circuito di raffreddamento con P-102)
        c201 = [
            {"timestamp": now - timedelta(days=4 - i),
             "metrics": {"discharge_temp_c": t, "suction_pressure_bar": 3.1,
                         "discharge_pressure_bar": p, "vibration_mm_s": v}}
            for i, (t, p, v) in enumerate([(132, 11.3, 1.7), (133, 11.3, 1.7), (135, 11.4, 1.8),
                                           (137, 11.4, 1.8), (138, 11.4, 1.8)])
        ]
        for machine_id, series in (("P-102", p102), ("P-101", p101), ("C-201", c201)):
            db.add_all(SensorReading(machine_id=machine_id, timestamp=r["timestamp"], metrics=r["metrics"])
                       for r in series)

        # --- Storico manutenzione -------------------------------------------
        db.add_all([
            MaintenanceRecord(machine_id="P-102", date=date(2024, 2, 15), type="corrective",
                              description="Bearing replacement (both DE/NDE)", technician_id=rossi.id),
            MaintenanceRecord(machine_id="P-102", date=date(2025, 3, 10), type="preventive",
                              description="Oil change + alignment check (OK)", technician_id=bianchi.id),
            MaintenanceRecord(machine_id="P-102", date=date(2025, 11, 2), type="preventive",
                              description="Cooling water jacket cleaning (moderate scale found)", technician_id=rossi.id),
            MaintenanceRecord(machine_id="P-101", date=date(2025, 9, 1), type="preventive",
                              description="Oil change, bearing temperature and vibration check (OK)", technician_id=bianchi.id),
            MaintenanceRecord(machine_id="C-201", date=date(2025, 6, 1), type="corrective",
                              description="Suction/discharge valve replacement", technician_id=verdi.id),
        ])

        # --- Work order (ID espliciti: WO-1030, 1035, 1042; il prossimo sarà WO-1043) ---
        db.add_all([
            WorkOrder(id=1030, machine_id="P-102", status="closed", priority="medium",
                      description="Cooling water jacket cleaning (scheduled)",
                      created_at=datetime(2025, 10, 28, 9, 0), created_by="planner", assigned_to=rossi.id),
            WorkOrder(id=1035, machine_id="C-201", status="closed", priority="high",
                      description="Replace suction/discharge valves",
                      created_at=datetime(2025, 5, 20, 9, 0), created_by="planner", assigned_to=verdi.id),
            WorkOrder(id=1042, machine_id="P-102", status="open", priority="high",
                      description="Investigate rising bearing temperature trend",
                      created_at=now - timedelta(days=5), created_by="operator-shift-A"),
        ])
        db.commit()

        # Su PostgreSQL l'inserimento con ID espliciti non avanza la sequenza: la riallineiamo
        if engine.dialect.name == "postgresql":
            db.execute(text(
                "SELECT setval(pg_get_serial_sequence('work_orders', 'id'), (SELECT MAX(id) FROM work_orders))"
            ))
            db.commit()

    print("Database CMMS creato e popolato: 3 macchine, 4 tecnici, letture sensori, storico, work order.")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
