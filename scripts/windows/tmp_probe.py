import paramiko

HOST='192.168.1.161'
USER='recomputer'
PASSWORD='1234'

cmds = [
    "sudo docker exec -i vms-postgres psql -U vms -d vms -At -c \"SELECT created_at,trigger,label,coalesce(detail_json->>'error','') FROM ai_detection_logs ORDER BY created_at DESC LIMIT 12;\"",
    "sudo docker exec -i vms-postgres psql -U vms -d vms -At -c \"SELECT occurred_at,event_type,severity FROM events ORDER BY occurred_at DESC LIMIT 12;\"",
    "sudo docker exec -i vms-event-recorder sh -lc 'id; ls -l /opt/vms/models/yolov8n.pt; stat -c %a /opt/vms/models/yolov8n.pt; head -c 8 /opt/vms/models/yolov8n.pt | od -An -tx1'",
]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False, allow_agent=False, timeout=15)
try:
    for c in cmds:
        i,o,e = ssh.exec_command(c, get_pty=False)
        rc = o.channel.recv_exit_status()
        print(f"--- rc={rc} cmd={c}")
        out = o.read().decode('utf-8', errors='ignore')
        err = e.read().decode('utf-8', errors='ignore')
        if out.strip():
            print(out)
        if err.strip():
            print(err)
finally:
    ssh.close()
