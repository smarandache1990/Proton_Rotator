ProtonVPN Server Rotator - Complete Walkthrough
Overview

This script automates rotating between ProtonVPN servers with a simple CLI for control. It runs as a background daemon, switching servers on a timer, and provides commands to manage lists, adjust timing, and control the rotation.
Step 1: Prerequisites
1.1 Install ProtonVPN CLI
bash

# On Debian/Ubuntu
sudo apt update
sudo apt install protonvpn-cli

# On Fedora/RHEL
sudo dnf install protonvpn-cli

# On Arch
sudo pacman -S protonvpn-cli

1.2 Log in to ProtonVPN
bash

protonvpn-cli login [your_username]
# Follow prompts for password

1.3 Verify Installation
bash

protonvpn-cli status  # Should show "Not connected"
protonvpn-cli s       # List available servers

Step 2: Setup Script
2.1 Download/Create Script

Save the Python script from the previous response as pvpn-rotator.py:
bash

nano pvpn-rotator.py
# Paste the entire script, then save (Ctrl+X, Y, Enter)

2.2 Make Script Executable
bash

chmod +x pvpn-rotator.py

2.3 Create Default Configuration

Run the script once to generate config files:
bash

./pvpn-rotator.py status

This creates ~/.config/pvpn-rotator/ with:

    config.json - Settings file
    list_a.txt - Example server list A
    list_b.txt - Example server list B

Step 3: Configure Server Lists
3.1 Find Server IDs

Get available server IDs from ProtonVPN:
bash

protonvpn-cli s | grep -E '^\w+-\w+.*#' | head -10
# Example output: US-FREE#1, CA#5, CH-10, JP#3, etc.

3.2 Edit Your Lists
bash

# Edit list A
nano ~/.config/pvpn-rotator/list_a.txt

# Edit list B  
nano ~/.config/pvpn-rotator/list_b.txt

Format: One server ID per line:

US-FREE#1
CA#5
NL-FREE#1
CH-10

Step 4: Autostart Setup (Optional but Recommended)
4.1 Install Systemd Service
bash

./pvpn-rotator.py install-service

4.2 Enable & Start Service
bash

systemctl --user daemon-reload
systemctl --user enable pvpn-rotator.service
systemctl --user start pvpn-rotator.service

4.3 Verify Service Status
bash

systemctl --user status pvpn-rotator.service
# Should show "active (running)"
journalctl --user -u pvpn-rotator.service -f
# Watch live logs (Ctrl+C to exit)

Step 5: Basic Usage
5.1 Start Rotation (If not using systemd)
bash

./pvpn-rotator.py start

The daemon will:

    Read your active list (default: list A)
    Connect to first server
    Wait configured time (default: 10 minutes)
    Disconnect and move to next server
    Repeat

5.2 Check Status
bash

./pvpn-rotator.py status

Shows:

    Daemon state (running/stopped)
    Active list (A/B)
    Rotation interval
    Current server index
    Connected VPN server

5.3 Stop Rotation
bash

./pvpn-rotator.py stop
# Or if using systemd:
systemctl --user stop pvpn-rotator.service

Step 6: Control Commands
6.1 Switch Between Lists
bash

# Switch to list B
./pvpn-rotator.py switch B

# Switch back to list A
./pvpn-rotator.py switch A

6.2 Adjust Rotation Interval
bash

# Change to 15 minutes per server
./pvpn-rotator.py interval 15

# Change to 5 minutes (minimum)
./pvpn-rotator.py interval 5

# Change to 60 minutes (1 hour)
./pvpn-rotator.py interval 60

6.3 Pause/Resume
bash

# Pause rotation (stay on current server)
./pvpn-rotator.py pause

# Resume rotation
./pvpn-rotator.py resume

6.4 Manual Skip
bash

# Skip to next server immediately
./pvpn-rotator.py skip

Step 7: List Management Commands
7.1 View Lists
bash

# Show all servers in list A
./pvpn-rotator.py list A

# Show all servers in list B  
./pvpn-rotator.py list B

7.2 Search Lists
bash

# Search list A for "free" servers
./pvpn-rotator.py search A free

# Search list B for Japanese servers
./pvpn-rotator.py search B JP

7.3 Add/Remove Servers
bash

# Add server to list A
./pvpn-rotator.py add A US-FREE#2

# Remove server from list B
./pvpn-rotator.py remove B CA#5

7.4 Find and Replace
bash

# Replace all "FREE" with "PLUS" in list A
./pvpn-rotator.py replace A FREE PLUS

# Replace country codes
./pvpn-rotator.py replace B US CA

Step 8: Monitoring & Logs
8.1 View Live Rotation
bash

# Tail the log file
tail -f ~/.config/pvpn-rotator/daemon.log

8.2 Check Current VPN Connection
bash

protonvpn-cli status
# Should show connected server and timestamp

8.3 Verify IP Rotation
bash

# Quick check of public IP
curl ifconfig.me
# Or with more detail
curl ipinfo.io

Step 9: Troubleshooting
9.1 Common Issues

"protonvpn-cli: command not found"
bash

# Ensure it's installed and in PATH
which protonvpn-cli
# If not found, check installation
sudo apt install --reinstall protonvpn-cli

"Failed to connect" errors
bash

# Check login status
protonvpn-cli status

# Try manual connection
protonvpn-cli connect US-FREE#1

# Verify server exists in ProtonVPN's list
protonvpn-cli s | grep "US-FREE#1"

Daemon not starting
bash

# Check if already running
ps aux | grep pvpn-rotator

# Kill existing process
pkill -f pvpn-rotator

# Check permissions on control pipe
ls -la ~/.config/pvpn-rotator/control.fifo

# Remove and let script recreate
rm ~/.config/pvpn-rotator/control.fifo
./pvpn-rotator.py start

Systemd service issues
bash

# Check logs
journalctl --user -u pvpn-rotator.service -n 50

# Reload service files
systemctl --user daemon-reload

# Restart service
systemctl --user restart pvpn-rotator.service

9.2 Reset Configuration
bash

# Stop daemon first
./pvpn-rotator.py stop

# Backup old config
mv ~/.config/pvpn-rotator ~/.config/pvpn-rotator.backup

# Start fresh
./pvpn-rotator.py status  # Creates new config
# Then reconfigure your lists

Step 10: Advanced Usage
10.1 Custom Configuration File

Edit ~/.config/pvpn-rotator/config.json directly:
json

{
  "active_list": "B",
  "switch_interval_minutes": 30,
  "current_index": 0,
  "running": true,
  "paused": false
}

10.2 Multiple Instances

For multiple rotation profiles, copy the script:
bash

cp pvpn-rotator.py pvpn-work.py
# Edit config path in script to different directory

10.3 Integration with Other Tools
bash

# Script to notify on server change
echo 'Server changed to $1' | notify-send "VPN Rotator"
# Add to script after connection successful

Quick Reference Cheat Sheet
bash

# START/STOP
./pvpn-rotator.py start          # Start daemon
./pvpn-rotator.py stop           # Stop daemon
systemctl --user enable pvpn-rotator.service  # Enable autostart

# CONTROL
./pvpn-rotator.py switch A|B     # Change active list
./pvpn-rotator.py interval N     # Set minutes between switches
./pvpn-rotator.py pause          # Pause rotation
./pvpn-rotator.py resume         # Resume rotation
./pvpn-rotator.py skip           # Skip to next server

# LIST MANAGEMENT
./pvpn-rotator.py list A|B       # View servers
./pvpn-rotator.py search A|B PATTERN  # Search list
./pvpn-rotator.py add A|B SERVER # Add server
./pvpn-rotator.py remove A|B SERVER  # Remove server
./pvpn-rotator.py replace A|B FIND REPLACE  # Find & replace

# MONITORING
./pvpn-rotator.py status         # Current status
tail -f ~/.config/pvpn-rotator/daemon.log  # Live logs
protonvpn-cli status             # VPN connection status

Example Workflow

    Initial Setup

bash

# Install protonvpn-cli and login
sudo apt install protonvpn-cli
protonvpn-cli login myusername

# Setup rotation script
wget https://example.com/pvpn-rotator.py
chmod +x pvpn-rotator.py
./pvpn-rotator.py status  # Creates config

# Edit lists with favorite servers
nano ~/.config/pvpn-rotator/list_a.txt
# Add: US-FREE#1, CA#5, NL-FREE#1, JP#3

# Enable autostart
./pvpn-rotator.py install-service
systemctl --user enable --now pvpn-rotator.service

    Daily Use

bash

# Check status
./pvpn-rotator.py status

# Switch to faster list for work
./pvpn-rotator.py switch B

# Adjust timing for downloads
./pvpn-rotator.py interval 5

# Pause for video call
./pvpn-rotator.py pause
# ... then resume
./pvpn-rotator.py resume

    Maintenance

bash

# Add new servers found
protonvpn-cli s | grep "Singapore"
# Output: SG#5
./pvpn-rotator.py add A SG#5

# Remove problematic server
./pvpn-rotator.py remove A US-FREE#1

# Check logs for errors
tail -100 ~/.config/pvpn-rotator/daemon.log

Security Notes

    Permissions: The script runs under your user account, not root
    Credentials: ProtonVPN credentials are handled by protonvpn-cli
    Logs: Connection logs stored locally in ~/.config/pvpn-rotator/
    Network: Ensure firewall allows ProtonVPN ports (default UDP 1194/443)

This gives you automated server rotation with full control through simple CLI commands. The systemd service ensures it starts on boot and runs reliably in the background.