#!/usr/bin/env python3
"""
pvpn-rotator - rotate Proton VPN OpenVPN tunnels on a timer. Linux + Windows.

Setup:
  1. account.protonvpn.com -> Account -> OpenVPN username. Put user + pass,
     one per line, in <config>/credentials  (NOT your account password).
  2. Download per-server .ovpn files from Downloads -> OpenVPN configuration
     files, correct platform, UDP. Linux: ovpn/linux/   Windows: ovpn/windows/
  3. pvpn-rotator.py init
  4. pvpn-rotator.py interval 10
  5. pvpn-rotator.py install-service

Control needs no admin: pvpn-rotator.py next | pause | resume | stop | interval N | status
"""
import argparse
import ctypes
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

WIN = sys.platform == "win32"
PLATFORM = "windows" if WIN else "linux"

if WIN:
    BASE = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "pvpn-rotator"
    ETC = VAR = BASE
    NO_WINDOW = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
else:
    ETC = Path("/etc/pvpn-rotator")
    VAR = Path("/var/lib/pvpn-rotator")
    NO_WINDOW = 0

OVPN_DIR = ETC / "ovpn" / PLATFORM
CONF = ETC / "config.json"
CREDS = ETC / "credentials"
STATE = VAR / "state.json"
LOG = VAR / "rotator.log"
OVPN_LOG = VAR / "openvpn.log"
CMD = VAR / "command"
RESOLV = Path("/etc/openvpn/update-resolv-conf")

DEFAULTS = {
    "interval_minutes": 10,
    "connect_timeout": 45,
    "verify_ip": True,
    "killswitch": False,
    "order": "sequential",       # or "random"
    "dev_node": "OpenVPN TAP-Windows6" if WIN else "",
    "tun_iface": "OpenVPN TAP-Windows6" if WIN else "tun*",
}

ADMIN_CMDS = {"daemon", "init", "install-service"}

_child = None
_mgmt = None
_stop = False


# ---------------------------------------------------------------- binary lookup
def _version_key(path):
    parts = re.findall(r"\d+", path.parent.parent.name)
    return [int(p) for p in parts] or [0]


def find_openvpn():
    """PATH first, then Community, then the newest Proton bundled binary."""
    found = shutil.which("openvpn")
    if found:
        return found
    if WIN:
        community = Path(r"C:\Program Files\OpenVPN\bin\openvpn.exe")
        if community.exists():
            return str(community)
        proton = Path(r"C:\Program Files\Proton\VPN")
        try:
            for cand in sorted(proton.glob("**/openvpn.exe"),
                               key=_version_key, reverse=True):
                if cand.exists():
                    return str(cand)
        except OSError:
            pass
        return "openvpn"
    return "/usr/sbin/openvpn"


OPENVPN = find_openvpn()


# ---------------------------------------------------------------- utilities
def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        VAR.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:      # Windows: target briefly locked
            time.sleep(0.1)
    tmp.unlink(missing_ok=True)


def write_cmd(text):
    """Direct write - keeps the ACL that atomic replace would discard."""
    CMD.parent.mkdir(parents=True, exist_ok=True)
    CMD.write_text(text, encoding="utf-8")


def clear_cmd():
    try:
        CMD.write_text("", encoding="utf-8")
    except OSError:
        pass


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)


def save_json(path, data):
    write_atomic(path, json.dumps(data, indent=2))


def servers():
    if not OVPN_DIR.is_dir():
        return []
    return sorted(p for p in OVPN_DIR.iterdir() if p.suffix.lower() == ".ovpn")


def read_state():
    return load_json(STATE, {"index": 0, "paused": False, "current": None})


def save_state(st):
    save_json(STATE, st)


def tail(path, n=25):
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def require_admin():
    if WIN:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return
        args = " ".join(f'"{a}"' for a in [os.path.abspath(__file__), *sys.argv[1:]])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1)
        sys.exit(0)
    if os.geteuid() == 0:
        return
    os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__), *sys.argv[1:]])


# ---------------------------------------------------------------- kill switch
def ks_on(host, port, proto):
    iface = load_json(CONF, DEFAULTS).get("tun_iface", "tun*")
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        log(f"killswitch: cannot resolve {host}, skipping")
        return
    if WIN:
        subprocess.run(["netsh", "advfirewall", "set", "allprofiles",
                        "firewallpolicy", "blockinbound,blockoutbound"],
                       capture_output=True)
        subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule",
                        "name=pvpn-ks-tun", "dir=out", "action=allow",
                        f'interface="{iface}"', "enable=yes"], capture_output=True)
        subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule",
                        "name=pvpn-ks-srv", "dir=out", "action=allow",
                        f"remoteip={ip}", "enable=yes"], capture_output=True)
    else:
        rules = f"""
            add table inet pvpn
            add chain inet pvpn out {{ type filter hook output priority 0; policy drop; }}
            add rule inet pvpn out ct state established,related accept
            add rule inet pvpn out oifname "lo" accept
            add rule inet pvpn out oifname "{iface}" accept
            add rule inet pvpn out ip daddr {ip} {proto} dport {port} accept
            add rule inet pvpn out udp dport 67 accept
            add rule inet pvpn out udp dport 53 accept
        """
        r = subprocess.run(["nft", "-f", "-"], input=rules, text=True,
                           capture_output=True)
        if r.returncode:
            log(f"killswitch: nft failed: {r.stderr.strip()}")


def ks_off():
    if WIN:
        subprocess.run(["netsh", "advfirewall", "set", "allprofiles",
                        "firewallpolicy", "blockinbound,allowoutbound"],
                       capture_output=True)
        for n in ("pvpn-ks-tun", "pvpn-ks-srv"):
            subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                            f"name={n}"], capture_output=True)
    else:
        subprocess.run(["nft", "delete", "table", "inet", "pvpn"], capture_output=True)


# ---------------------------------------------------------------- management
def _q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Mgmt:
    """Minimal OpenVPN management client. Identical path on both platforms."""

    def __init__(self, port, timeout):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.buf = b""

    def line(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("management socket closed")
            self.buf += chunk
        raw, self.buf = self.buf.split(b"\n", 1)
        return raw.decode(errors="replace").strip()

    def send(self, cmd):
        self.sock.sendall(cmd.encode() + b"\n")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _attach(port, deadline):
    """Wait for openvpn to open the management port."""
    while time.time() < deadline:
        try:
            m = Mgmt(port, 5)
        except OSError:
            time.sleep(0.25)
            continue
        while time.time() < deadline:
            try:
                if "OpenVPN Management Interface" in m.line():
                    return m
            except (ConnectionError, socket.timeout):
                break
        m.close()
        time.sleep(0.25)
    return None


# ---------------------------------------------------------------- openvpn
def parse_remote(cfg):
    for line in cfg.read_text(errors="replace").splitlines():
        p = line.split()
        if p and p[0] == "remote" and len(p) >= 2:
            port = int(p[2]) if len(p) > 2 and p[2].isdigit() else 1194
            proto = "udp" if ("udp" in p[3:] or "udp" in cfg.name) else "tcp"
            return p[1], port, proto
    return None, 1194, "udp"


def kill_child(timeout=15):
    global _child, _mgmt
    if _mgmt:
        try:
            _mgmt.send("signal SIGTERM")
        except OSError:
            pass
    if _child is not None and _child.poll() is None:
        try:
            _child.wait(timeout)
        except subprocess.TimeoutExpired:
            _child.kill()
            _child.wait(5)
    if _mgmt:
        _mgmt.close()
        _mgmt = None
    _child = None


def connect(cfg, timeout):
    global _child, _mgmt
    try:
        creds = CREDS.read_text(encoding="utf-8").splitlines()
    except OSError:
        log(f"missing credentials file {CREDS}")
        return False
    if len(creds) < 2 or creds[0].startswith("OPENVPN_"):
        log(f"fill in {CREDS} first")
        return False
    user, password = creds[0].strip(), creds[1].strip()

    settings = load_json(CONF, DEFAULTS)
    port = free_port()
    try:
        OVPN_LOG.unlink(missing_ok=True)
    except PermissionError:
        pass
    args = [OPENVPN, "--config", str(cfg),
            "--management", "127.0.0.1", str(port),
            "--management-hold", "--management-query-passwords",
            "--script-security", "2", "--verb", "3",
            "--log", str(OVPN_LOG), "--writepid", str(VAR / "openvpn.pid")]

    node = settings.get("dev_node")
    if WIN:
        args += ["--windows-driver", "tap-windows6", "--disable-dco"]
        if node:
            args += ["--dev-node", node]
    if not WIN and RESOLV.exists():
        args += ["--up", str(RESOLV), "--down", str(RESOLV), "--up-restart"]

    host, vport, proto = parse_remote(cfg)
    if host and settings.get("killswitch"):
        ks_on(host, vport, proto)

    log(f"connecting to {cfg.name}")
    _child = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT,
                              creationflags=NO_WINDOW)

    deadline = time.time() + timeout
    _mgmt = _attach(port, deadline)
    if _mgmt is None:
        log("management interface never came up")
        kill_child()
        return False

    try:
        _mgmt.send("state on")
        _mgmt.send("hold release")
        while time.time() < deadline:
            if _child.poll() is not None:
                log(f"openvpn exited rc={_child.returncode}: {tail(OVPN_LOG, 6)}")
                kill_child()
                return False
            try:
                line = _mgmt.line()
            except socket.timeout:
                continue
            except ConnectionError:
                log("management socket closed")
                kill_child()
                return False

            if line.startswith(">PASSWORD:Need"):
                key = re.search(r"Need '([^']+)'", line)
                if key:
                    _mgmt.send(f"username {_q(key.group(1))} {_q(user)}")
                    _mgmt.send(f"password {_q(key.group(1))} {_q(password)}")
            elif "Verification Failed" in line or "AUTH_FAILED" in line:
                log("AUTH_FAILED - use the OpenVPN username/password, not the account password")
                kill_child()
                return False
            elif line.startswith(">STATE:") and ",CONNECTED,SUCCESS," in line:
                return True
            elif line.startswith(">STATE:") and ",EXITING," in line:
                log(f"state: {line}")
                kill_child()
                return False
        log(f"connect timed out after {timeout}s: {tail(OVPN_LOG, 6)}")
        kill_child()
        return False
    except OSError as e:
        log(f"management error: {e}")
        kill_child()
        return False


def public_ip():
    try:
        req = urllib.request.Request("https://ipinfo.io/ip",
                                     headers={"User-Agent": "pvpn-rotator"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode().strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------- rotation
def rotate_once(st, cfg):
    """Returns 'rotated' | 'next' | 'failed' | 'stop'."""
    files = servers()
    if not files:
        log(f"no .ovpn files in {OVPN_DIR}")
        return "failed"

    if cfg.get("order") == "random":
        idx = random.randrange(len(files))
    else:
        idx = st["index"] % len(files)

    if not connect(files[idx], cfg["connect_timeout"]):
        return "failed"

    st["current"] = files[idx].name
    save_state(st)
    if cfg.get("verify_ip"):
        log(f"connected, exit IP {public_ip() or 'unknown'}")

    start = datetime.now()
    last = 0
    while True:
        if _stop:
            return "stop"
        handle_command(st, cfg)
        if st.get("paused"):
            start = datetime.now()
            time.sleep(2)
            continue
        if st.pop("_next", False):
            save_state(st)
            log("manual switch requested")
            return "next"
        if _child is None or _child.poll() is not None:
            log("tunnel dropped, reconnecting")
            kill_child()
            return "failed"
        elapsed = int((datetime.now() - start).total_seconds())
        if elapsed >= cfg["interval_minutes"] * 60:
            return "rotated"
        if elapsed - last >= 30:
            log(f"{elapsed}s / {cfg['interval_minutes'] * 60}s on {files[idx].name}")
            last = elapsed
        time.sleep(1)


def handle_command(st, cfg):
    if not CMD.exists():
        return
    try:
        raw = CMD.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if not raw:
        return
    clear_cmd()
    log(f"command: {raw}")
    verb, _, arg = raw.partition(" ")
    if verb == "pause":
        st["paused"] = True
    elif verb == "resume":
        st["paused"] = False
    elif verb == "next":
        st["_next"] = True
        kill_child()
    elif verb == "stop":
        global _stop
        _stop = True
    elif verb == "interval" and arg.isdigit() and 1 <= int(arg) <= 1440:
        cfg["interval_minutes"] = int(arg)
        save_json(CONF, cfg)
    save_state(st)


def daemon():
    global _stop
    cfg = load_json(CONF, DEFAULTS)
    st = read_state()
    st["paused"] = False
    st.pop("_next", None)
    save_state(st)

    def bye(*_):
        global _stop
        _stop = True

    for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), bye)
            except (ValueError, OSError):
                pass

    log(f"daemon start ({PLATFORM}): interval={cfg['interval_minutes']}m "
        f"files={len(servers())} openvpn={OPENVPN}")
    backoff = 5
    while not _stop:
        result = rotate_once(st, cfg)
        kill_child()
        ks_off()
        if _stop or result == "stop":
            break
        n = max(len(servers()), 1)
        if result in ("rotated", "next"):
            backoff = 5
            st["index"] = (st["index"] + 1) % n
            save_state(st)
        else:
            st["index"] = (st["index"] + 1) % n
            save_state(st)
            log(f"rotation failed, retry in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)

    kill_child()
    ks_off()
    st["current"] = None
    st.pop("_next", None)
    save_state(st)
    log("daemon stopped")


# ---------------------------------------------------------------- CLI
def cmd_init():
    for d in (ETC, VAR, OVPN_DIR):
        d.mkdir(parents=True, exist_ok=True)
    if not CONF.exists():
        save_json(CONF, DEFAULTS)
    if not CREDS.exists():
        write_atomic(CREDS, "OPENVPN_USERNAME\nOPENVPN_PASSWORD\n")
        try:
            CREDS.chmod(0o600)
        except OSError:
            pass

    # control channel: writable by ordinary users so `next` needs no UAC/sudo
    CMD.touch(exist_ok=True)
    if WIN:
        r = subprocess.run(["icacls", str(CMD), "/grant", "*S-1-5-32-545:(M)"],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"warning: icacls failed: {r.stderr.strip()}")
    else:
        CMD.chmod(0o666)

    print(f"Config:  {ETC}")
    print(f"Configs: {OVPN_DIR}   <- put {PLATFORM} .ovpn files here")
    print(f"Creds:   {CREDS}   <- OpenVPN username, then password")
    print(f"Binary:  {OPENVPN}")
    if WIN and not Path(OPENVPN).exists():
        print("warning: openvpn binary not found")


def cmd_status():
    st = read_state()
    cfg = load_json(CONF, DEFAULTS)
    if WIN:
        r = subprocess.run(["schtasks", "/query", "/tn", "pvpn-rotator"],
                           capture_output=True, text=True)
        running = "Running" in r.stdout
    else:
        running = subprocess.run(["systemctl", "is-active", "--quiet",
                                  "pvpn-rotator"]).returncode == 0
    print(f"platform: {PLATFORM}")
    print(f"binary:   {OPENVPN}")
    print(f"service:  {'running' if running else 'stopped'}")
    print(f"paused:   {st.get('paused')}")
    print(f"current:  {st.get('current') or 'none'}")
    print(f"index:    {st['index']} / {len(servers())}")
    print(f"interval: {cfg['interval_minutes']} min")
    print(f"order:    {cfg.get('order')}")
    print(f"dev node: {cfg.get('dev_node') or '-'}")
    print(f"killswitch: {cfg.get('killswitch')}")
    print(f"exit IP:  {public_ip() or 'unknown'}")
    if OVPN_LOG.exists():
        print("\nlast openvpn lines:")
        print(tail(OVPN_LOG, 5))


def cmd_send(verb, arg=""):
    if not VAR.is_dir():
        print("run init first")
        return
    write_cmd(f"{verb} {arg}".strip() + "\n")
    print(f"sent: {verb} {arg}".strip())


def cmd_servers():
    files = servers()
    if not files:
        print(f"no .ovpn files in {OVPN_DIR}")
        return
    st = read_state()
    cur = st["index"] % len(files)
    for i, f in enumerate(files):
        print(f"{'->' if i == cur else '  '} {i}: {f.name}")


def cmd_install():
    script = os.path.abspath(__file__)
    if WIN:
        tr = f'"{sys.executable}" "{script}" daemon'
        r = subprocess.run(["schtasks", "/create", "/tn", "pvpn-rotator",
                            "/tr", tr, "/sc", "onstart", "/ru", "SYSTEM",
                            "/rl", "HIGHEST", "/f"],
                           capture_output=True, text=True)
        print(r.stdout or r.stderr)
        print("start now with: schtasks /run /tn pvpn-rotator")
        return
    unit = f"""[Unit]
Description=Proton VPN OpenVPN rotator
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={sys.executable} {script} daemon
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    path = Path("/etc/systemd/system/pvpn-rotator.service")
    write_atomic(path, unit)
    subprocess.run(["systemctl", "daemon-reload"])
    print(f"wrote {path}")
    print("enable with: systemctl enable --now pvpn-rotator")


def main():
    p = argparse.ArgumentParser(prog="pvpn-rotator")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("init", "daemon", "status", "servers", "install-service",
                 "pause", "resume", "next", "stop"):
        sub.add_parser(name)
    sub.add_parser("interval").add_argument("minutes", type=int)
    a = p.parse_args()

    if a.cmd == "status":
        return cmd_status()
    if a.cmd in ADMIN_CMDS:
        require_admin()

    if a.cmd == "daemon":
        return daemon()
    if a.cmd == "init":
        return cmd_init()
    if a.cmd == "servers":
        return cmd_servers()
    if a.cmd == "install-service":
        return cmd_install()
    if a.cmd == "interval":
        return cmd_send("interval", str(a.minutes))
    cmd_send(a.cmd)


if __name__ == "__main__":
    main()