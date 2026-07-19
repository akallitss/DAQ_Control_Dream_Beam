---
name: p2-hv-conventions
description: P2 detector HV conventions and telescope voltage limits — drift gap potential = drift − mesh; scan rule; per-detector maxima
metadata: 
  node_type: memory
  type: project
  originSessionId: 62da2cee-3ee5-42fc-8006-330e5520defe
---

P2 HV conventions (from Alexandra, 2026-07-18, for the Fe55 scans and later
analysis — she explicitly asked to note this):

- **The potential across the drift gap is (drift voltage − mesh voltage).**
- When changing the mesh voltage in a scan, the drift voltage must move by the
  same amount, so the drift-gap potential stays constant.
- Telescope maxima / operating points ([[rays-daq-deployment]]):
  - P2_OUT: drift 700 V, mesh 420 V max → drift gap 280 V
  - P2_MID: drift 700 V, mesh 510 V max → drift gap 190 V
- Mesh HV scans start AT the operating (max) voltage and step DOWN
  (2026-07-18 Fe55 scan: 5 V steps, 5 min/point, 12 points = 1 h).
- Fe55 offline pedestals: taken at 200 V on all four channels before the
  scan; scan sub-runs reference them via pedestal_run.txt / 'latest'.
