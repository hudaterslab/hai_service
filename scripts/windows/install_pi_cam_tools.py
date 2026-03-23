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
        install_cmd = (
            f"echo {args.password} | sudo -S apt-get update && "
            f"echo {args.password} | sudo -S apt-get install -y arp-scan nmap net-tools"
        )
        rc, out, err = run(ssh, install_cmd)
        print("[install] rc=", rc)
        if out.strip():
            print(out)
        if err.strip():
            print(err)

        script = r"""cat > /home/recomputer/hik_scan.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
IFACE="${1:-wlan0}"
SUBNET="${2:-192.168.1.0/24}"
echo "[1/2] ARP scan on ${IFACE} ${SUBNET}"
sudo arp-scan --interface="${IFACE}" "${SUBNET}" | tee /tmp/hik_arp_scan.txt
echo
echo "[2/2] RTSP open-host shortlist (554/8554)"
awk '/^[0-9]+\./{print $1}' /tmp/hik_arp_scan.txt | while read -r ip; do
  if timeout 1 bash -lc "echo > /dev/tcp/${ip}/554" 2>/dev/null; then
    echo "${ip}:554 open"
  fi
  if timeout 1 bash -lc "echo > /dev/tcp/${ip}/8554" 2>/dev/null; then
    echo "${ip}:8554 open"
  fi
done
echo
echo "Hint: Hikvision MAC OUI often starts with 44:19:B6 / BC:AD:28 / D4:E8:53"
EOF
chmod +x /home/recomputer/hik_scan.sh"""
        rc2, out2, err2 = run(ssh, script)
        print("[script] rc=", rc2)
        if out2.strip():
            print(out2)
        if err2.strip():
            print(err2)

        test_cmd = f"echo {args.password} | sudo -S /home/recomputer/hik_scan.sh wlan0 192.168.1.0/24"
        rc3, out3, err3 = run(ssh, test_cmd)
        print("[scan] rc=", rc3)
        if out3.strip():
            print(out3)
        if err3.strip():
            print(err3)
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
