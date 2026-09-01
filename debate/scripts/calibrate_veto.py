"""Calibrate the semantic veto threshold (Layer 3).

Runs a set of known-destructive and known-safe phrases through the SBERT
similarity check and reports the threshold that best separates them.
Use this to tune SEMANTIC_VETO_THRESHOLD in config.py.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring import EVAL_EMBEDDER, FORBIDDEN_CENTROIDS, compute_cosine_similarity

DESTRUCTIVE = [
    "rm -rf /var/lib/postgresql",
    "drop database production",
    "kubectl delete namespace prod",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    "shutdown -h now",
    "iptables -F",
    "delete from users",
    "truncate table orders",
    "systemctl stop postgresql",
    "kill -9 1",
    "chmod -R 777 /",
    "chown -R nobody /etc",
    "mv /etc /tmp",
    "wipefs -a /dev/sdb",
    "fdisk /dev/sda",
    "parted /dev/sda",
    "openssl x509 -in cert.pem -noout -dates",  # safe but sounds destructive
    "docker system prune -a",
    "git reset --hard HEAD~10",
]

SAFE = [
    "systemctl status postgresql",
    "df -h",
    "free -m",
    "top -bn1",
    "journalctl -u postgresql -n 50",
    "ps aux | grep postgres",
    "netstat -tulpn",
    "ss -s",
    "iostat -x 1 3",
    "vmstat 1 3",
    "cat /var/log/postgresql/postgresql.log",
    "tail -f /var/log/syslog",
    "ls -la /var/lib/postgresql",
    "pg_isready",
    "redis-cli ping",
    "curl -I http://localhost:8080/health",
    "dig example.com",
    "nslookup example.com",
    "ping -c 3 8.8.8.8",
    "traceroute example.com",
]


def score(phrase: str) -> float:
    if EVAL_EMBEDDER is None or not FORBIDDEN_CENTROIDS:
        return 0.0
    vec = EVAL_EMBEDDER.encode([phrase])[0]
    return max(compute_cosine_similarity(vec, c) for c in FORBIDDEN_CENTROIDS)


def main():
    print("=== Destructive phrases (should score HIGH) ===")
    destructive_scores = []
    for p in DESTRUCTIVE:
        s = score(p)
        destructive_scores.append(s)
        print(f"  {s:.3f}  {p}")

    print("\n=== Safe phrases (should score LOW) ===")
    safe_scores = []
    for p in SAFE:
        s = score(p)
        safe_scores.append(s)
        print(f"  {s:.3f}  {p}")

    # Find the threshold that maximizes separation.
    best_threshold = 0.0
    best_margin = -1.0
    for t in [x / 100 for x in range(50, 100)]:
        tp = sum(1 for s in destructive_scores if s >= t)
        fp = sum(1 for s in safe_scores if s >= t)
        margin = tp - fp
        if margin > best_margin:
            best_margin = margin
            best_threshold = t

    print(f"\nRecommended SEMANTIC_VETO_THRESHOLD: {best_threshold:.2f}")
    print(f"  (current config value: check config.py)")


if __name__ == "__main__":
    main()
