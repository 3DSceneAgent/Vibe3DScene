"""
Main entry point for the 3D Scene Agent.
Supports both CLI and API modes.
"""
import argparse
import sys


def main():
    """
    Parse arguments and launch the appropriate interface.
    """
    parser = argparse.ArgumentParser(
        description="3D Scene Agent - LangGraph-based Blender scene manipulation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run CLI interface
  python main.py --mode cli
  
  # Run API server
  python main.py --mode api --port 8000
  
  # Run API with custom host
  python main.py --mode api --host 0.0.0.0 --port 8080

Requirements:
  - Create a .env file with VLM_API_KEY set
  - Start the Blender MCP server (localhost:6274)
  - Optionally: Start 3D asset retrieval API (localhost:8001)
        """
    )
    
    parser.add_argument(
        "--mode",
        choices=["cli", "api"],
        default="cli",
        help="Interface mode: 'cli' for command-line or 'api' for REST server (default: cli)"
    )
    
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="API server host (default: 0.0.0.0, only for api mode)"
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="API server port (default: 8000, only for api mode)"
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="API worker processes (default: uses API_WORKERS env var or 1)"
    )
    
    args = parser.parse_args()
    
    # Check for .env file
    import os
    if not os.path.exists(".env"):
        print("⚠️  Warning: .env file not found!")
        print("   Create a .env file with VLM_API_KEY set.")
        print("   See .env.example for reference.")
        print()
    
    # Launch the selected mode
    if args.mode == "cli":
        print("Starting CLI interface...")
        from scene_agent.interfaces.cli import main as cli_main
        cli_main()
    else:
        print(f"Starting API server on {args.host}:{args.port}...")
        from scene_agent.interfaces.api import run_api
        run_api(host=args.host, port=args.port, workers=args.workers)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        sys.exit(1)
