import json
import sys


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"trigger": False, "score": 0.0, "label": "no-input"}))
        return
    req = json.loads(raw)
    cam_name = str(req.get("cameraName", "")).lower()

    # Demo logic for detection stage:
    # - trigger when camera name includes 'front' or 'gate'
    # - otherwise no trigger
    if "front" in cam_name or "gate" in cam_name:
        out = {
            "trigger": True,
            "score": 0.91,
            "label": "person",
            "eventType": "motion",
            "severity": "high",
            "payload": {"detector": "sample_model", "rule": "name_match"},
        }
    else:
        out = {"trigger": False, "score": 0.15, "label": "no_target"}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
