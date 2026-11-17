# QR check-in — how it works

There is no camera code in EventPulse AI. The QR code is scanned by each student's **own phone
camera app**, which opens a web page served by this Mac on the venue's Wi-Fi.

1. **Before the event** — create the event with a date in *Event Management* and register students.
   Optionally predict it (*Attendance Prediction → Whole event*) so it can be compared afterwards.
2. **On the event day** — double-click `EventPulse_CheckIn_Server.command`. It runs
   `checkin_app.py` (FastAPI) on port 8010 of this Mac. Allow incoming connections if macOS asks.
3. In the desktop app open *Attendance Records*, choose the event and press **Show check-in QR code**.
   Project the QR code at the entrance. It encodes
   `http://<this Mac's address>:8010/checkin/<EVENT_ID>?t=<secret token>`.
4. A student scans it, types their Student ID and taps **Check in**. The server checks that
   * the link's token is the event's current token (revoking the code invalidates it),
   * the event is not a dataset event with simulated outcomes and is not cancelled,
   * today is the event date,
   * the student is registered (status Registered),
   * the student has not already checked in (enforced by a UNIQUE constraint too),

   then writes the check-in **and** a recorded attendance row (`source = MANUAL`,
   `recorded_by = 'QR check-in'`) in one transaction. Only the pseudonymous Student ID and the time are stored.
5. Students without a phone are checked in at the desk (*Desk check-in*).
6. **After the event** — *Finalise attendance*: registered students without a check-in are recorded
   absent, walk-ins are stored, the event is completed and compared with the predictions stored
   before it (*Analytics → Predicted vs actual*).
7. **Corrections** — select a row, choose Attended/Absent, enter your name and a reason. The old value,
   new value, name and reason are kept in the audit log (shown as *Correction log*). Simulated dataset
   outcomes cannot be edited.

Limitations: phones must be on the same network as the Mac; the server is not exposed to the
internet and has no login (anyone with the current link and a registered Student ID could check
that student in — revoke and re-issue the code if it leaks). No role-based access exists in the
app, so "authorised" corrections mean named and justified corrections with an audit trail.
