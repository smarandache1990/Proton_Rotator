#!/usr/bin/env python3
"""
ProtonVPN Server Rotator Daemon & CLI
Manages two server lists, rotates connections on a timer.
Control via system signals or command file.
"""

import os
import sys
import time
import signal
import json
import threading
from pathlib import Path
from subprocess import run, PIPE, CalledProcessError
from datetime import datetime, timedelta

# --- Configuration Paths ---
CONFIG_DIR = Path.home() / ".config" / "pvpn-rotator"
CONFIG_FILE = CONFIG_DIR / "config.json"
LIST_A_FILE = CONFIG_DIR / "list_a.txt" 
LIST_B_FILE = CONFIG_DIR / "list_b.txt"
CONTROL_FILE = CONFIG_DIR / "control.fifo"  # Named pipe for commands
PID_FILE = CONFIG_DIR / "daemon.pid"
LOG_FILE = CONFIG_DIR / "daemon.log"

# Default configuration
DEFAULT_CONFIG = {
    "active_list": "A",  # "A" or "B"
    "switch_interval_minutes": 10,
    "current_index": 0,
    "running": False,
    "paused": False
}

# --- Configuration Management ---
def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        # Create example list files if they don't exist
        if not LIST_A_FILE.exists():
            LIST_A_FILE.write_text("US-FREE#1\nCA#5\nNL-FREE#1\n")
        if not LIST_B_FILE.exists():
            LIST_B_FILE.write_text("JP#3\nSG#5\nHK#2\n")
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return DEFAULT_CONFIG.copy()

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

# --- Server List Management ---
def get_active_list(config):
    """Returns list of servers from the active list file."""
    list_file = LIST_A_FILE if config["active_list"] == "A" else LIST_B_FILE
    if not list_file.exists():
        return []
    servers = list_file.read_text().strip().splitlines()
    return [s.strip() for s in servers if s.strip()]

def update_list(list_name, servers):
    """Overwrites list A or B with new server entries."""
    list_file = LIST_A_FILE if list_name.upper() == "A" else LIST_B_FILE
    list_file.write_text("\n".join(servers) + "\n")

def search_list(list_name, pattern):
    """Returns servers matching pattern (case-insensitive substring)."""
    list_file = LIST_A_FILE if list_name.upper() == "A" else LIST_B_FILE
    if not list_file.exists():
        return []
    servers = list_file.read_text().strip().splitlines()
    return [s for s in servers if pattern.lower() in s.lower()]

def find_replace_list(list_name, find_str, replace_str):
    """Replaces all occurrences of find_str with replace_str in the list."""
    list_file = LIST_A_FILE if list_name.upper() == "A" else LIST_B_FILE
    content = list_file.read_text()
    new_content = content.replace(find_str, replace_str)
    list_file.write_text(new_content)
    return content != new_content

# --- VPN Control ---
def connect_to_server(server_id):
    """Connects to a specific ProtonVPN server using CLI."""
    try:
        result = run(["protonvpn", "connect", server_id], check=True, stdout=PIPE, stderr=PIPE)
        output = result.stdout.decode()
        
        # Extract server name from success message
        # Format: "Connected to CH-HR#2 in Zagreb, Switzerland. Your new IP address is 178.218.167.211."
        server_info = None
        if "Connected to" in output:
            # Get everything from "Connected to " to the next period
            connected_part = output.split("Connected to ")[1]
            server_info = connected_part.split(".")[0].strip()
        
        return True, output, server_info  # Return tuple with 3 elements now
    except CalledProcessError as e:
        return False, f"Failed to connect to {server_id}: {e.stderr.decode()}", None

def disconnect_vpn():
    """Disconnects from VPN."""
    run(["protonvpn", "disconnect"], stdout=PIPE, stderr=PIPE)

# --- Daemon Core ---
class VPNRotatorDaemon:
    def __init__(self):
        self.config = load_config()
        self.control_pipe = None
        self.pipe_fd = None
        self.shutdown = False
        self.paused = self.config.get("paused", False)
        self.current_connection = None

    def disconnect_vpn(self):
        """Disconnects from VPN and clears current connection info."""
        run(["protonvpn", "disconnect"], stdout=PIPE, stderr=PIPE)
        self.current_connection = None

    def setup_control_pipe(self):
        """Creates and opens a named pipe for receiving commands."""
        if not os.path.exists(CONTROL_FILE):
            os.mkfifo(CONTROL_FILE)
        # Open pipe once and keep it open
        self.pipe_fd = os.open(CONTROL_FILE, os.O_RDONLY | os.O_NONBLOCK)

    def read_command(self):
        """Reads a single command from the control pipe (non-blocking)."""
        if self.pipe_fd is None:
            return None
        try:
            data = os.read(self.pipe_fd, 1024).decode('utf-8').strip()
            return data if data else None
        except BlockingIOError:
            return None
        except OSError as e:
            # Pipe might have been closed on the other end
            with open(LOG_FILE, 'a') as f:
                f.write(f"{datetime.now()}: Error reading pipe: {str(e)}\n")
            return None

    def process_command(self, cmd):
        """Processes commands from control pipe or signals."""
        if not cmd:
            return
        parts = cmd.split()
        if not parts:
            return

        action = parts[0].lower()
        if action == "stop":
            self.shutdown = True
            self.config["running"] = False
            save_config(self.config)
            self.disconnect_vpn()
        elif action == "pause":
            self.paused = True
            self.config["paused"] = True
            save_config(self.config)
        elif action == "resume":
            self.paused = False
            self.config["paused"] = False
            save_config(self.config)
        elif action == "switch" and len(parts) > 1:
            new_list = parts[1].upper()
            if new_list in ["A", "B"]:
                self.config["active_list"] = new_list
                self.config["current_index"] = 0  # Reset to start of new list
                save_config(self.config)
        elif action == "interval" and len(parts) > 1:
            try:
                minutes = int(parts[1])
                if 1 <= minutes <= 1440:  # Reasonable bounds
                    self.config["switch_interval_minutes"] = minutes
                    save_config(self.config)
            except ValueError:
                pass
        elif action == "skip":
            self.config["current_index"] = (self.config.get("current_index", 0) + 1)
            save_config(self.config)
            self.disconnect_vpn()
        elif action == "status":
            self.log_status()

    def log_status(self):
        status = "RUNNING" if self.config["running"] else "STOPPED"
        paused = "PAUSED" if self.paused else "ACTIVE"
        active_list = self.config["active_list"]
        interval = self.config["switch_interval_minutes"]
        idx = self.config["current_index"]
        servers = get_active_list(self.config)
        current_server = self.current_connection if self.current_connection else "None"  # Use stored info
        
        log_msg = (f"[STATUS] Daemon: {status}, State: {paused}, "
                f"Active List: {active_list}, Interval: {interval}min, "
                f"Index: {idx}/{len(servers)}, Current VPN: {current_server}")
        with open(LOG_FILE, 'a') as f:
            f.write(f"{datetime.now()}: {log_msg}\n")
        print(log_msg)

    def run(self):
        """Main daemon loop."""
        self.config["running"] = True
        save_config(self.config)
        self.setup_control_pipe()

        signal.signal(signal.SIGINT, lambda s, f: self.stop())
        signal.signal(signal.SIGTERM, lambda s, f: self.stop())

        with open(LOG_FILE, 'a') as log:
            log.write(f"{datetime.now()}: Daemon started. Paused: {self.paused}, Active List: {self.config['active_list']}, Interval: {self.config['switch_interval_minutes']}min\n")

        while not self.shutdown:
            try:
                # Check for commands
                cmd = self.read_command()
                if cmd:
                    with open(LOG_FILE, 'a') as log:
                        log.write(f"{datetime.now()}: Received command: {cmd}\n")
                    self.process_command(cmd)

                if self.shutdown:
                    with open(LOG_FILE, 'a') as log:
                        log.write(f"{datetime.now()}: Shutdown requested\n")
                    break

                if not self.paused:
                    servers = get_active_list(self.config)
                    if not servers:
                        with open(LOG_FILE, 'a') as log:
                            log.write(f"{datetime.now()}: No servers in list {self.config['active_list']}\n")
                        time.sleep(10)
                        continue

                    idx = self.config.get("current_index", 0) % len(servers)
                    server = servers[idx]

                    # Log connection attempt
                    with open(LOG_FILE, 'a') as log:
                        log.write(f"{datetime.now()}: Connecting to server {idx+1}/{len(servers)}: {server}\n")

                    # Connect
                    success, msg, server_info = connect_to_server(server)  # Unpack 3 values
                    now = datetime.now()
                    with open(LOG_FILE, 'a') as log:
                        log.write(f"{now}: Connection {'SUCCESS' if success else 'FAILED'}: {msg[:100]}\n")

                    if success:
                        # Store the current connection info
                        self.current_connection = server_info if server_info else server
                        # ... rest of the success handling
                    else:
                        self.current_connection = None
                        # ... rest of the failure handling

                    if success:
                        # Wait for interval
                        wait_start = datetime.now()
                        interval_seconds = self.config["switch_interval_minutes"] * 60
                        
                        with open(LOG_FILE, 'a') as log:
                            log.write(f"{datetime.now()}: Connected. Waiting {interval_seconds} seconds until {wait_start + timedelta(seconds=interval_seconds)}\n")
                        
                        # Wait loop
                        elapsed = 0
                        while elapsed < interval_seconds and not self.shutdown:
                            time.sleep(5)  # Check every 5 seconds
                            elapsed = (datetime.now() - wait_start).total_seconds()
                            
                            # Log progress every 30 seconds
                            if int(elapsed) % 30 == 0:
                                with open(LOG_FILE, 'a') as log:
                                    log.write(f"{datetime.now()}: Waiting... {elapsed:.0f}/{interval_seconds}s elapsed\n")
                            
                            # Check for commands
                            cmd = self.read_command()
                            if cmd:
                                with open(LOG_FILE, 'a') as log:
                                    log.write(f"{datetime.now()}: Received command during wait: {cmd}\n")
                                self.process_command(cmd)
                                if self.shutdown or self.paused:
                                    with open(LOG_FILE, 'a') as log:
                                        log.write(f"{datetime.now()}: Breaking wait loop due to command\n")
                                    break

                        # Check why we exited the loop
                        if self.shutdown:
                            with open(LOG_FILE, 'a') as log:
                                log.write(f"{datetime.now()}: Shutdown during wait\n")
                            break
                        elif self.paused:
                            with open(LOG_FILE, 'a') as log:
                                log.write(f"{datetime.now()}: Paused during wait\n")
                            # Don't disconnect, stay connected but paused
                            continue
                        else:
                            # Time's up - disconnect and move to next
                            with open(LOG_FILE, 'a') as log:
                                log.write(f"{datetime.now()}: Interval complete ({elapsed:.1f}s). Disconnecting...\n")
                            
                            self.disconnect_vpn()
                            time.sleep(2)  # Give time for disconnect
                            
                            # Update index
                            self.config["current_index"] = (idx + 1) % len(servers)
                            save_config(self.config)
                            
                            with open(LOG_FILE, 'a') as log:
                                log.write(f"{datetime.now()}: Moved to next server. New index: {self.config['current_index']}\n")
                    else:
                        # Connection failed - move to next server
                        with open(LOG_FILE, 'a') as log:
                            log.write(f"{datetime.now()}: Connection failed, moving to next server in 10s\n")
                        
                        self.config["current_index"] = (idx + 1) % len(servers)
                        save_config(self.config)
                        time.sleep(10)
                else:
                    # Paused state
                    if int(time.time()) % 30 == 0:  # Log every 30 seconds when paused
                        with open(LOG_FILE, 'a') as log:
                            log.write(f"{datetime.now()}: Daemon paused. Current VPN: {self.current_connection}\n")
                    time.sleep(1)
                    
            except Exception as e:
                with open(LOG_FILE, 'a') as log:
                    log.write(f"{datetime.now()}: ERROR in main loop: {str(e)}\n")
                    import traceback
                    log.write(traceback.format_exc() + "\n")
                time.sleep(5)

        # Cleanup at the end
        self.config["running"] = False
        save_config(self.config)
        with open(LOG_FILE, 'a') as log:
            log.write(f"{datetime.now()}: Daemon stopped\n")
        
        # Close pipe if open
        if self.pipe_fd is not None:
            os.close(self.pipe_fd)
            self.pipe_fd = None
            
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)

    def stop(self):
        self.shutdown = True

# --- CLI Control Interface ---
def send_command(cmd):
    """Sends a command to the daemon via the named pipe."""
    if not os.path.exists(CONTROL_FILE):
        print("Daemon control pipe not found. Is the daemon running?")
        return False
    
    # Try multiple times since pipe might be temporarily closed
    for _ in range(3):
        try:
            with open(CONTROL_FILE, 'w', encoding='utf-8') as pipe:
                pipe.write(cmd + "\n")
            return True
        except BrokenPipeError:
            time.sleep(0.1)  # Short delay before retry
            continue
        except Exception as e:
            print(f"Error sending command: {e}")
            return False
    
    print("Failed to send command after multiple attempts.")
    print("Try restarting the daemon: ./pvpn-rotator.py stop && ./pvpn-rotator.py start")
    return False

def cli_control():
    """Handles user commands from terminal."""
    if len(sys.argv) < 2:
        print("Usage: pvpn-rotate <command> [args]")
        print("Commands:")
        print("  start                 - Start the daemon")
        print("  stop                  - Stop the daemon")
        print("  pause                 - Pause rotation")
        print("  resume                - Resume rotation")
        print("  switch <A|B>          - Switch active list")
        print("  interval <minutes>    - Set switch interval")
        print("  skip                  - Skip to next server")
        print("  status                - Show current status")
        print("  list <A|B>            - Show servers in list")
        print("  search <A|B> <pattern>- Search list for pattern")
        print("  add <A|B> <server>    - Add server to list")
        print("  remove <A|B> <server> - Remove server from list")
        print("  replace <A|B> <find> <replace> - Find and replace")
        return

    command = sys.argv[1].lower()

    if command == "start":
        # Run as daemon
        daemon = VPNRotatorDaemon()
        # Write PID file
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        daemon.run()
    elif command in ["stop", "pause", "resume", "skip", "status"]:
        send_command(command)
        if command == "status":
            time.sleep(0.5)  # Give daemon time to log
            if LOG_FILE.exists():
                with open(LOG_FILE, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        print(lines[-1].strip())
    elif command == "switch" and len(sys.argv) == 3:
        send_command(f"switch {sys.argv[2]}")
    elif command == "interval" and len(sys.argv) == 3:
        send_command(f"interval {sys.argv[2]}")
    elif command == "list" and len(sys.argv) == 3:
        config = load_config()
        servers = get_active_list({"active_list": sys.argv[2].upper()})
        print(f"List {sys.argv[2].upper()} ({len(servers)} servers):")
        for i, s in enumerate(servers):
            print(f"  {i+1}. {s}")
    elif command == "search" and len(sys.argv) == 4:
        results = search_list(sys.argv[2], sys.argv[3])
        print(f"Found {len(results)} matches:")
        for r in results:
            print(f"  {r}")
    elif command == "add" and len(sys.argv) == 4:
        list_name = sys.argv[2]
        server = sys.argv[3]
        list_file = LIST_A_FILE if list_name.upper() == "A" else LIST_B_FILE
        with open(list_file, 'a') as f:
            f.write(server + "\n")
        print(f"Added '{server}' to list {list_name.upper()}")
    elif command == "remove" and len(sys.argv) == 4:
        list_name = sys.argv[2]
        server = sys.argv[3]
        list_file = LIST_A_FILE if list_name.upper() == "A" else LIST_B_FILE
        servers = list_file.read_text().strip().splitlines()
        if server in servers:
            servers.remove(server)
            list_file.write_text("\n".join(servers) + "\n")
            print(f"Removed '{server}' from list {list_name.upper()}")
        else:
            print(f"Server '{server}' not found in list {list_name.upper()}")
    elif command == "replace" and len(sys.argv) == 5:
        changed = find_replace_list(sys.argv[2], sys.argv[3], sys.argv[4])
        print(f"Find/replace {'performed' if changed else 'no changes'}")

# --- Systemd Service Setup ---
def create_systemd_service():
    """Creates a systemd user service file for autostart."""
    service_content = f"""[Unit]
Description=ProtonVPN Server Rotator
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={sys.executable} {os.path.abspath(__file__)} start
Restart=on-failure
RestartSec=5
StandardOutput=append:{LOG_FILE}
StandardError=inherit

[Install]
WantedBy=default.target
"""
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_file = service_dir / "pvpn-rotator.service"
    service_file.write_text(service_content)
    print(f"Systemd service file created at {service_file}")
    print("To enable autostart on boot, run:")
    print(f"  systemctl --user enable pvpn-rotator.service")
    print("To start now:")
    print(f"  systemctl --user start pvpn-rotator.service")

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "install-service":
        create_systemd_service()
    else:
        cli_control()