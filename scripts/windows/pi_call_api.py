import argparse
import base64
import json

import paramiko


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--method", default="GET")
    ap.add_argument("--path", required=True)
    ap.add_argument("--json-body", default="")
    ap.add_argument("--json-body-b64", default="")
    args = ap.parse_args()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        args.host,
        username=args.user,
        password=args.password,
        look_for_keys=False,
        allow_agent=False,
        timeout=15,
    )
    try:
        method = args.method.upper().strip() or "GET"
        url = f"http://127.0.0.1:8080{args.path}"
        if method == "GET":
            cmd = f"curl -sS '{url}'"
        else:
            body = args.json_body or "{}"
            if args.json_body_b64:
                body = base64.b64decode(args.json_body_b64).decode("utf-8")
            json.loads(body)
            cmd = f"curl -sS -X {method} '{url}' -H 'Content-Type: application/json' -d '{body}'"
        i, o, e = ssh.exec_command(cmd, get_pty=False)
        rc = o.channel.recv_exit_status()
        print(f"rc={rc}")
        out = o.read().decode("utf-8", errors="ignore")
        err = e.read().decode("utf-8", errors="ignore")
        if out:
            print(out)
        if err:
            print(err)
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
