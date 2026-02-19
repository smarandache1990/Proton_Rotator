#!/usr/bin/env python3
"""
Test script to verify ProtonVPN switching logic
"""
import subprocess
import time
from datetime import datetime, timedelta

def test_switching():
    print("Testing VPN switching logic...")
    
    # Test server list
    servers = ["CH-AR#1", "CH-AR#2", "SE-AU#1"]
    interval_minutes = 1  # Short interval for testing
    interval_seconds = interval_minutes * 60
    
    for i, server in enumerate(servers):
        print(f"\n=== Test {i+1}/{len(servers)} ===")
        print(f"Connecting to: {server}")
        
        # Connect
        result = subprocess.run(["protonvpn", "connect", server], 
                              capture_output=True, text=True)
        print(f"Connect result: {result.returncode}")
        print(f"Output: {result.stdout[:200]}")
        
        if result.returncode == 0:
            # Wait for interval
            print(f"Waiting {interval_minutes} minute(s)...")
            start = datetime.now()
            
            while (datetime.now() - start).total_seconds() < interval_seconds:
                elapsed = (datetime.now() - start).total_seconds()
                if elapsed % 10 == 0:  # Print every 10 seconds
                    print(f"  Elapsed: {elapsed:.0f}/{interval_seconds}s")
                time.sleep(1)
            
            print(f"Wait complete. Elapsed: {(datetime.now() - start).total_seconds():.0f}s")
            
            # Disconnect
            print("Disconnecting...")
            result = subprocess.run(["protonvpn", "disconnect"], 
                                  capture_output=True, text=True)
            print(f"Disconnect result: {result.returncode}")
        else:
            print(f"Connection failed: {result.stderr}")
        
        time.sleep(5)  # Brief pause between servers
    
    print("\nTest complete.")

if __name__ == "__main__":
    test_switching()