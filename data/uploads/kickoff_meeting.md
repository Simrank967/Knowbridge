# Campus Parking App Kickoff Meeting
SYNTHETIC TEST DATA - not a real project.
Date: 2026-10-12
Attendees: Meera Iyer (Project Lead), Arjun Verma (Backend Developer), Sana Qureshi (Mobile Developer), Vikram Joshi (QA Engineer)

## Decisions
- Decision: Use Firebase for user authentication (decided by Meera Iyer) because it removes the need to build login infrastructure ourselves.
- Decision: We agreed to adopt GraphQL for the mobile API.
- Decision: Use React Native for the mobile app because the team already knows JavaScript.

## Action items
- Action P-201: Design the parking slot database schema (owner: Arjun Verma) due 2026-10-20
- Action P-202: Build the slot availability API (owner: Arjun Verma) due 2026-10-30
- Action P-203: Build the mobile booking screen (owner: Sana Qureshi) due 2026-11-05
- Action P-204: Write the payment gateway integration due 2026-11-10
- P-202 depends on P-201.
- P-203 depends on P-202.
- P-204 depends on P-210.

## Open items
- Open question: Which payment provider will the university approve?
- Open question: Who is responsible for sensor installation at the campus gates?
- Risk: Gate sensors may deliver inaccurate occupancy data. Mitigation: Run a two-week calibration pilot at Gate A.
- Risk: Payment approval from the university could be delayed.
- Blocker: P-204 cannot start because the payment provider is not yet chosen.
- Milestone: Pilot launch (2026-11-25)
