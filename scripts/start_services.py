#!/usr/bin/env python3
"""
Start both MCP server and API server.
Press Ctrl+C to stop both services.
"""
import subprocess
import signal
import sys
import time
import os
from pathlib import Path

# Colors for terminal output
class Colors:
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'  # No Color


def print_colored(text, color):
    """Print colored text to terminal"""
    print(f"{color}{text}{Colors.NC}")


def main():
    """Start both services and handle shutdown"""
    # Get project directory
    script_dir = Path(__file__).parent
    project_dir = script_dir.parent
    
    print_colored("Starting 3D Scene Agent services...", Colors.GREEN)
    print_colored("Press Ctrl+C to stop all services", Colors.YELLOW)
    print()
    
    # Change to project directory
    os.chdir(project_dir)

    # Default headless blender command/args if not provided
    os.environ.setdefault("BLENDER_HEADLESS_CMD", "blender")
    os.environ.setdefault(
        "BLENDER_HEADLESS_ARGS",
        "--background --python scripts/blender_headless_client.py -- --host {host} --port {port}",
    )
    
    # Store process references
    processes = []
    
    def cleanup(signum=None, frame=None):
        """Cleanup handler to terminate all processes"""
        print_colored("\nStopping services...", Colors.YELLOW)
        
        # Terminate all processes
        for proc in processes:
            try:
                proc.terminate()
            except:
                pass
        
        # Wait for processes to finish
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        
        print_colored("All services stopped", Colors.GREEN)
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    try:
        # Start MCP server
        print_colored("[1/2] Starting MCP server...", Colors.GREEN)
        mcp_process = subprocess.Popen(
            [sys.executable, "mcp_server/server.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=project_dir
        )
        processes.append(mcp_process)
        print_colored(f"      MCP server started (PID: {mcp_process.pid})", Colors.GREEN)
        
        # Wait for MCP server to initialize
        time.sleep(2)
        
        # Check if MCP server is still running
        if mcp_process.poll() is not None:
            print_colored("✗ MCP server failed to start", Colors.RED)
            cleanup()
        
        # Start API server
        print_colored("[2/2] Starting API server...", Colors.GREEN)
        api_process = subprocess.Popen(
            [sys.executable, "main.py", "--mode", "api", "--host", "0.0.0.0", "--port", "8000"],
            cwd=project_dir
        )
        processes.append(api_process)
        print_colored(f"      API server started (PID: {api_process.pid})", Colors.GREEN)
        
        # Wait a bit for API server to initialize
        time.sleep(2)
        
        # Check if API server is still running
        if api_process.poll() is not None:
            print_colored("✗ API server failed to start", Colors.RED)
            cleanup()
        
        print()
        print_colored("✓ All services running!", Colors.GREEN)
        print(f"  MCP Server: PID {mcp_process.pid}")
        print(f"  API Server: PID {api_process.pid} (http://0.0.0.0:8000)")
        print()
        print_colored("Press Ctrl+C to stop all services", Colors.YELLOW)
        print()
        
        # Wait for processes to complete (they shouldn't unless there's an error)
        while True:
            # Check if any process has died
            for proc in processes:
                if proc.poll() is not None:
                    print_colored(f"\n✗ Process {proc.pid} has stopped unexpectedly", Colors.RED)
                    cleanup()
            
            time.sleep(1)
            
    except Exception as e:
        print_colored(f"\n✗ Error: {e}", Colors.RED)
        cleanup()


if __name__ == "__main__":
    main()
