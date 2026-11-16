# Sprint Planning Meeting Notes
SYNTHETIC DEMO DATA - not a real project.
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
