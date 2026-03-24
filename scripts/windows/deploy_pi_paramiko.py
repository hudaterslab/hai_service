import argparse
import posixpath
import sys
from pathlib import Path

import paramiko


INCLUDE_PATHS = [
    "config",
    "db",
    "deploy",
    "models",
    "openapi",
    "scripts",
    "services",
    "README.md",
    "yolov8n.pt",
]

EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "bin",
    "data",
    "Ultralytics",
}

EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str):
    parts = []
    p = remote_dir.replace("\\", "/")
    while p not in ("", "/"):
        parts.append(p)
        p = posixpath.dirname(p)
    for d in reversed(parts):
        try:
            sftp.stat(d)
        except FileNotFoundError:
            sftp.mkdir(d)


def upload_path(sftp: paramiko.SFTPClient, local_path: Path, remote_path: str):
    if local_path.is_dir():
        if local_path.name in EXCLUDE_DIRS:
            return
        sftp_mkdir_p(sftp, remote_path)
        for child in local_path.iterdir():
            if child.name in EXCLUDE_DIRS:
                continue
            upload_path(sftp, child, posixpath.join(remote_path, child.name))
        return
    if local_path.suffix.lower() in EXCLUDE_SUFFIXES:
        return
    sftp_mkdir_p(sftp, posixpath.dirname(remote_path))
    sftp.put(str(local_path), remote_path)


def run(ssh: paramiko.SSHClient, cmd: str):
    stdin, stdout, stderr = ssh.exec_command(cmd, get_pty=False)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="ignore")
    err = stderr.read().decode("utf-8", errors="ignore")
    return code, out, err


def safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        data = text.encode(enc, errors="replace")
        sys.stdout.buffer.write(data + b"\n")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--remote-dir")
    parser.add_argument("--install-dxrt", action="store_true", default=True)
    parser.add_argument("--skip-install-dxrt", action="store_true")
    args = parser.parse_args()
    if not args.remote_dir:
        args.remote_dir = f"/home/{args.user}/vms-8ch-webrtc"

    root = Path(__file__).resolve().parents[2]
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=args.host,
        port=args.port,
        username=args.user,
        password=args.password,
        look_for_keys=False,
        allow_agent=False,
        timeout=15,
    )
    try:
        sftp = ssh.open_sftp()
        try:
            sftp_mkdir_p(sftp, args.remote_dir)
            for rel in INCLUDE_PATHS:
                local = root / rel
                if not local.exists():
                    continue
                remote = posixpath.join(args.remote_dir, rel.replace("\\", "/"))
                upload_path(sftp, local, remote)
        finally:
            sftp.close()

        check_docker_cmd = "command -v docker >/dev/null 2>&1"
        code0, _, _ = run(ssh, check_docker_cmd)
        if code0 != 0:
            install_cmd = (
                f"echo {args.password} | sudo -S apt-get update && "
                f"(echo {args.password} | sudo -S apt-get install -y docker.io docker-compose-plugin || "
                f"echo {args.password} | sudo -S apt-get install -y docker.io docker-compose) && "
                f"echo {args.password} | sudo -S systemctl enable --now docker"
            )
            ci, oi, ei = run(ssh, install_cmd)
            print("[install-docker] exit=", ci)
            if oi.strip():
                print(oi)
            if ei.strip():
                print(ei)

        if args.install_dxrt and not args.skip_install_dxrt:
            dxrt_cmd = (
                f"cd {args.remote_dir} && "
                "if [ -f scripts/linux/install_dxrt_host.sh ]; then "
                "  chmod +x scripts/linux/install_dxrt_host.sh && "
                f"  echo {args.password} | sudo -S bash scripts/linux/install_dxrt_host.sh; "
                "else "
                "  echo 'dxrt host installer not found, skip'; "
                "fi"
            )
            cdx, odx, edx = run(ssh, dxrt_cmd)
            print("[install-dxrt-host] exit=", cdx)
            if odx.strip():
                safe_print(odx)
            if edx.strip():
                safe_print(edx)

        dxnn_host_cmd = (
            f"cd {args.remote_dir} && "
            "if [ -f scripts/linux/install_dxnn_host_service.sh ]; then "
            "  chmod +x scripts/linux/install_dxnn_host_service.sh && "
            f"  echo {args.password} | sudo -S bash scripts/linux/install_dxnn_host_service.sh; "
            "else "
            "  echo 'dxnn host installer not found, skip'; "
            "fi"
        )
        chs, ohs, ehs = run(ssh, dxnn_host_cmd)
        print("[install-dxnn-host] exit=", chs)
        if ohs.strip():
            safe_print(ohs)
        if ehs.strip():
            safe_print(ehs)

        deploy_cmd = (
            f"cd {args.remote_dir} && "
            "export DOCKER_API_VERSION=1.41 DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0; "
            "if sudo docker compose version >/dev/null 2>&1; then "
            "  sudo docker compose -f deploy/docker-compose.yml up -d --build || "
            "  (sudo docker-compose version >/dev/null 2>&1 && sudo docker-compose -f deploy/docker-compose.yml up -d --build); "
            "elif sudo docker-compose version >/dev/null 2>&1; then "
            "  sudo docker-compose -f deploy/docker-compose.yml up -d --build; "
            "else "
            "  echo 'docker compose and docker-compose are not available' >&2; exit 1; "
            "fi"
        )
        code, out, err = run(ssh, deploy_cmd)
        print("[compose] exit=", code)
        if out.strip():
            safe_print(out)
        if err.strip():
            safe_print(err)

        sql_cmd = (
            "sudo docker exec -i vms-postgres psql -U vms -d vms -v ON_ERROR_STOP=1 -c "
            "\"CREATE TABLE IF NOT EXISTS camera_model_settings ("
            "camera_id UUID PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,"
            "enabled BOOLEAN NOT NULL DEFAULT FALSE,"
            "model_path TEXT NOT NULL DEFAULT '',"
            "confidence_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.35,"
            "poll_sec INT NOT NULL DEFAULT 2,"
            "cooldown_sec INT NOT NULL DEFAULT 10,"
            "timeout_sec INT NOT NULL DEFAULT 5,"
            "extra_json JSONB NOT NULL DEFAULT '{}'::jsonb,"
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());"
            "CREATE TABLE IF NOT EXISTS camera_event_pack_settings ("
            "camera_id UUID PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,"
            "enabled BOOLEAN NOT NULL DEFAULT FALSE,"
            "pack_id TEXT NOT NULL DEFAULT 'edge-basic',"
            "pack_version TEXT NOT NULL DEFAULT '1.0.0',"
            "params_json JSONB NOT NULL DEFAULT '{}'::jsonb,"
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());"
            "INSERT INTO app_settings(key,value_json) VALUES ('webrtc','{\\\"enabled\\\":true}'::jsonb) ON CONFLICT (key) DO NOTHING;"
            "INSERT INTO app_settings(key,value_json) VALUES ('person_event_rule','{\\\"enabled\\\":true,\\\"dwellSec\\\":5,\\\"cooldownSec\\\":10,\\\"eventType\\\":\\\"person_detected\\\",\\\"severity\\\":\\\"high\\\"}'::jsonb) ON CONFLICT (key) DO NOTHING;\""
        )
        code2, out2, err2 = run(ssh, sql_cmd)
        print("[migration-sql] exit=", code2)
        if out2.strip():
            safe_print(out2)
        if err2.strip():
            safe_print(err2)

        health_cmd = "curl -sS http://127.0.0.1:8080/healthz || true"
        code3, out3, err3 = run(ssh, health_cmd)
        print("[healthz] exit=", code3)
        if out3.strip():
            safe_print(out3)
        if err3.strip():
            safe_print(err3)
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
