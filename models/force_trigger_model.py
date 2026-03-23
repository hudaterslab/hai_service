import json
import sys


def main():
    raw = sys.stdin.read().strip()
    req = json.loads(raw) if raw else {}
    out = {
        "trigger": True,
        "score": 0.99,
        "label": "force-trigger-test",
        "eventType": "motion",
        "severity": "high",
        "payload": {"source": "force_trigger_model", "cameraId": req.get("cameraId")},
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()

