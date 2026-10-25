# Vouch front end (prototype)

The new dashboard design for the event vendor payables plan: Overview, Books and Audit trail, rebuilt from the "Vouch Merged Dashboard" design.

**Not connected to the backend yet.** Every name, amount and date on these pages is simulated sample content from the design. Nothing here reads `vouch.db` or calls the API, and `vouch/ui` (the current dashboard) is unchanged.

Open it by double-clicking `frontend/index.html`, or serve the folder:

```bash
python -m http.server 8080 --directory frontend    # then open http://127.0.0.1:8080
```

On the Overview, each decision can be approved, rejected or expanded with **Why?**, and **Undo (demo)** puts it back. The counters and the headline follow.
