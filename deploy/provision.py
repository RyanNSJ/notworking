"""Create the AgentDown VM on DigitalOcean (idempotent).

Usage: uv run python deploy/provision.py
Reads DIGITALOCEAN_TOKEN from .env. Registers the admin and deploy SSH public keys,
renders cloud-init.yaml and creates the droplet if one with the same name doesn't exist.
Prints the droplet's public IPv4 address.
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SSH_DIR = Path.home() / ".ssh"

DROPLET = {
    "name": "agentdown-1",
    "region": "sgp1",
    "size": "s-1vcpu-1gb",
    "image": "ubuntu-24-04-x64",
    "monitoring": True,
    "ipv6": True,
    "tags": ["agentdown"],
}
KEYS = {
    "admin": SSH_DIR / "variks_desk_ed25519.pub",
    "deploy": SSH_DIR / "agentdown_deploy_ed25519.pub",
}


def load_env() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("\"'")
    return env


TOKEN = load_env()["DIGITALOCEAN_TOKEN"]


def api(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.digitalocean.com/v2{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r) if r.status != 204 else {}


def ensure_key(name: str, pub: str) -> int:
    existing = api("GET", "/account/keys?per_page=200")["ssh_keys"]
    for k in existing:
        if k["public_key"].split()[:2] == pub.split()[:2]:
            return k["id"]
    return api("POST", "/account/keys", {"name": f"agentdown-{name}", "public_key": pub})[
        "ssh_key"
    ]["id"]


def public_ipv4(droplet: dict) -> str | None:
    for net in droplet["networks"]["v4"]:
        if net["type"] == "public":
            return net["ip_address"]
    return None


def main() -> None:
    pubs = {role: path.read_text().strip() for role, path in KEYS.items()}
    key_ids = [ensure_key(role, pub) for role, pub in pubs.items()]

    found = api("GET", f"/droplets?tag_name={DROPLET['tags'][0]}")["droplets"]
    droplet = next((d for d in found if d["name"] == DROPLET["name"]), None)
    if droplet is None:
        user_data = (
            (ROOT / "deploy" / "cloud-init.yaml")
            .read_text()
            .replace("__ADMIN_KEY__", pubs["admin"])
            .replace("__DEPLOY_KEY__", pubs["deploy"])
        )
        droplet = api(
            "POST", "/droplets", {**DROPLET, "ssh_keys": key_ids, "user_data": user_data}
        )["droplet"]
        print(
            f"created droplet {droplet['id']}, waiting for it to become active...", file=sys.stderr
        )
    else:
        print(f"droplet {droplet['id']} already exists", file=sys.stderr)

    for _ in range(60):
        droplet = api("GET", f"/droplets/{droplet['id']}")["droplet"]
        if droplet["status"] == "active" and public_ipv4(droplet):
            break
        time.sleep(5)
    else:
        sys.exit("timed out waiting for the droplet to become active")
    print(public_ipv4(droplet))


if __name__ == "__main__":
    main()
