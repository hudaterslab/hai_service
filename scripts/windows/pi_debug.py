import argparse
import base64

import paramiko


def run(ssh: paramiko.SSHClient, cmd: str):
    i, o, e = ssh.exec_command(cmd, get_pty=False)
    rc = o.channel.recv_exit_status()
    return rc, o.read().decode("utf-8", errors="ignore"), e.read().decode("utf-8", errors="ignore")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--run", default="", help="additional remote command to run")
    ap.add_argument("--run-b64", default="", help="base64-encoded additional remote command to run")
    args = ap.parse_args()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(args.host, username=args.user, password=args.password, look_for_keys=False, allow_agent=False, timeout=15)
    try:
        cmds = [
            "sudo docker ps --format 'table {{.Names}}\\t{{.Status}}'",
            "sudo docker logs --tail 200 vms-api",
            "sudo docker logs --tail 120 vms-event-recorder",
        ]
        if args.full:
            cmds.extend(
                [
                    "sudo docker inspect vms-event-recorder --format '{{range .Config.Env}}{{println .}}{{end}}'",
                    "sudo docker inspect vms-event-recorder --format '{{json .NetworkSettings.Networks}}'",
                    "sudo docker inspect vms-postgres --format '{{json .NetworkSettings.Networks}}'",
                    "sudo docker exec vms-event-recorder getent hosts postgres || true",
                    "sudo docker exec vms-event-recorder sh -lc 'python - <<\"PY\"\nimport os, socket\nprint(\"DATABASE_URL=\", os.getenv(\"DATABASE_URL\", \"\"))\ntry:\n  print(\"resolve(postgres)=\", socket.gethostbyname(\"postgres\"))\nexcept Exception as e:\n  print(\"resolve_error\", repr(e))\nPY'",
                ]
            )
        if args.run:
            cmds.append(args.run)
        if args.run_b64:
            decoded = base64.b64decode(args.run_b64).decode("utf-8", errors="ignore").strip()
            if decoded:
                cmds.append(decoded)
        for cmd in cmds:
            rc, out, err = run(ssh, cmd)
            print(f"--- {cmd} rc={rc}")
            if out.strip():
                print(out)
            if err.strip():
                print(err)
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
