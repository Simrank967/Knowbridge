"""Writes the SYNTHETIC demo project (Smart Campus Energy Dashboard). Not real data. Re-run: python sample_data/generate.py"""
from pathlib import Path
import docx
try: import pymupdf as fitz
except ImportError: import fitz
D = Path(__file__).parent
NOTE = "SYNTHETIC DEMO DATA - not a real project."
(D / "meeting_notes.md").write_text(f"""# Sprint Planning Meeting Notes
{NOTE}
Date: 2026-10-05
Attendees: Anika Rao (Project Lead), Rohan Mehta (Backend Developer), Priya Nair (Data Engineer), Karan Shah (ML Engineer), Dev Malhotra (Frontend Developer)

## Decisions
- Decision: Use PostgreSQL for the sensor database (decided by Anika Rao) because the team already has operational experience with it.
- Decision: We agreed to adopt MQTT for ingesting sensor readings.
- Decision: Use an LSTM model for energy forecasting experiments because it was quick to prototype.

## Action items
- Action T-101: Set up the PostgreSQL schema for sensor data (owner: Rohan Mehta) due 2026-10-15
- Action T-102: Build the MQTT ingestion service (owner: Rohan Mehta) due 2026-10-22
- Action T-104: Create the dashboard charts (owner: Dev Malhotra) due 2026-10-30
- Action T-105: Configure the cloud deployment pipeline due 2026-11-05
- T-102 depends on T-101.
- T-105 depends on T-109.

## Open items
- Open question: Who will own the cloud hosting budget approvals?
- Risk: Sensor data volume may exceed the free-tier storage limit.
- Milestone: Beta release (2026-11-15)
""")
(D / "task_allocation.csv").write_text("""task_id,task,owner,status,due,depends_on,updated
T-101,Set up PostgreSQL schema for sensor data,Rohan Mehta,in progress,2026-10-15,,2026-10-01
T-102,Build MQTT ingestion service,Rohan Mehta,not started,2026-10-22,T-101,2026-10-01
T-103,Train energy forecasting model,Karan Shah,in progress,2026-10-25,T-102,2026-10-01
T-104,Create dashboard charts,Dev Malhotra,in progress,2026-10-30,T-103,2026-10-01
T-106,Write sensor calibration guide,,not started,2026-11-01,,2026-09-18
""")
d = docx.Document(); d.add_heading("Technical Decision Record: Forecasting Approach", 1)
for line in [NOTE, "Date: 2026-10-27",
    "Decision: Adopt XGBoost for energy forecasting instead of the LSTM prototype.",
    "Rationale: Inference is faster on the campus edge hardware and training data is tabular.",
    "T-103 (forecasting model training) is still in progress.",
    "Resource: Campus sensor dataset v2",
    "Risk: The XGBoost model may drift when the seasonal sensor mix changes. Mitigation: Retrain monthly and compare against a rolling baseline."]:
    d.add_paragraph(line)
d.save(D / "technical_decision.docx")
pdf = fitz.open(); pg = pdf.new_page()
pg.insert_textbox(fitz.Rect(50, 50, 545, 790), "\n".join(["Weekly Progress Report", NOTE, "Date: 2026-10-28", "", "Status this week:",
    "T-101 is completed.", "T-102 is in progress.", "T-103 is completed.", "T-104 is blocked by T-103.", "",
    "Blocker: T-105 cannot start because cloud credentials have not been issued.",
    "Risk: Delay in sensor hardware delivery may push the Beta release date.",
    "Open question: Will the beta include the mobile layout?", "Milestone: Beta release (2026-11-15)"]), fontsize=11)
pdf.save(D / "progress_report.pdf")
print("generated", [p.name for p in sorted(D.iterdir()) if p.name != "generate.py"])
