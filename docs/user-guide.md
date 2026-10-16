# User guide

## Operators

1. Sign in with your own account. Never use someone else's: everything you record carries your name.
2. Open **Operator station** and scan the QR code on the traveller (or type the order number).
3. The highlighted step is the next one to do. Choose your machine and press **Start**.
4. When the batch is done, enter **Good** and **Scrap** counts and press **Finish**. They must add
   up to the number of parts that came into the step.
5. If an order shows **On hold**, stop and tell Quality. The system will not let anyone continue.

## Supervisors

- **Work orders → Release a new work order**: choose part, quantity and material heat. Serial
  numbers are created automatically. Print the traveller from the order page.
- **Materials → Receive material**: record each heat number with its mill certificate reference
  before using it.
- **Parts**: create parts with their route (one operation per line). Once a part has orders its
  route is locked; create a new revision (e.g. `HIP-STEM-12-RevB`) to change the process.
- **Machines**: take a machine out of service so it can't be selected at the station.
- A supervisor can finish a step started by someone else (e.g. at shift change).

## Quality

- **Import CMM results**: upload the CSV, or let the CMM PC save it into the shared inbox folder.
  A bad file is rejected as a whole with a line-by-line reason; the same file twice is ignored.
- Out-of-tolerance results put the order **on hold** and send an alert.
- **Hold queue → Record disposition**:
  - *Rework and re-inspect*: Inspection goes back to pending; the order can only pass after
    new results are imported and are all in tolerance.
  - *Use as is*: concession; Inspection is closed and the order continues. Justify it fully.
  - *Scrap*: the order is closed.
  Enter the NCR number and justification, then re-enter your password to sign.
- **Audit trail**: filter by table, record, user and date; export to CSV for audits.

## Everyone

- **Traceability**: enter a heat number to see every implant made from it, or a serial number to
  see its complete history.
- **Account**: change your password. Other sessions are signed out.
