import argparse
import sys

import paramiko


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--cmd", required=True)
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
        i, o, e = ssh.exec_command(args.cmd, get_pty=False)
        rc = o.channel.recv_exit_status()
        print(f"rc={rc}")
        out = o.read().decode("utf-8", errors="ignore")
        err = e.read().decode("utf-8", errors="ignore")
        if out:
            safe_print(out)
        if err:
            safe_print(err)
    finally:
        ssh.close()


def safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        data = text.encode(enc, errors="replace")
        sys.stdout.buffer.write(data + b"\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
