# Model Export 2026-03-20 Status

Date: 2026-03-20
NAS source: `/volume1/Hudaters/dhkim/Data/HanjinCCTV/model_export_2026_03_20_best`

## Summary

The new export was inspected on NAS.

It contains:

- `best.pt`
- `best_640.onnx`
- `best_640_op13_noslim.onnx`
- `best_640_op16_noslim.onnx`
- `obj_names.txt`
- DXNN compile status/log files

It does **not** currently contain a compiled `.dxnn` artifact.

## Current Blocker

The NAS export status files show DXNN compilation failure:

- `dxnn_status.json`
- `dxnn_640_status.json`

Reported error:

- `Compile error at PhaseLabels.SURGERY.`

Because there is no compiled `.dxnn` output yet, the edge package could not be switched to the new DXNN model in this round.

## Class Names In The New Export

`obj_names.txt` currently contains:

1. `person`
2. `person_lower_body`
3. `helmet`
4. `person_head`
5. `reflective_vest`
6. `work_vehicle_front`
7. `general_vehicle`

## Runtime Compatibility Changes Applied

File:

- `models/dxnn_helmet_runner.py`

Applied changes:

- `person_head` is now treated as a head class
- if sidecar JSON does not provide `classNames`, the runner can read sibling `obj_names.txt`

This means once a valid `.dxnn` is produced, the export format is closer to plug-in ready.

## Next Step

Provide a successful compiled DXNN artifact, for example:

- `best_640.dxnn`

Recommended companion files in the same directory:

- `obj_names.txt`
- optional sidecar JSON with `classNames`, `inputWidth`, `inputHeight`

Once the `.dxnn` file exists, copy it into the edge `models/` directory, update `modelPath`, and redeploy or restart the relevant runtime.

## Packaged Path

The compiled model is packaged in this repository as:

- `models/hf/HudatersU_Safety_helmet/safety_helmet_20260320.dxnn`
- `models/hf/HudatersU_Safety_helmet/safety_helmet_20260320.json`
