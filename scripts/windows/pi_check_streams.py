import argparse

import paramiko


def run(ssh: paramiko.SSHClient, cmd: str):
    i, o, e = ssh.exec_command(cmd, get_pty=True)
    rc = o.channel.recv_exit_status()
    return rc, o.read().decode("utf-8", errors="ignore"), e.read().decode("utf-8", errors="ignore")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(args.host, username=args.user, password=args.password, look_for_keys=False, allow_agent=False, timeout=15)
    try:
        cmds = [
            "sudo docker ps --format 'table {{.Names}}\\t{{.Status}}'",
            "sudo docker logs --tail 200 vms-mediamtx",
            "ps -ef | grep -E 'ffmpeg|mediamtx|dev_worker|worker.py|python' | grep -v grep",
            "ss -tnp | grep -E ':554|:8554|:10554|192.168.1.2|192.168.1.3|192.168.1.31' || true",
        ]
        for c in cmds:
            rc, out, err = run(ssh, c)
            print(f"--- {c} rc={rc}")
            if out.strip():
                print(out)
            if err.strip():
                print(err)
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
