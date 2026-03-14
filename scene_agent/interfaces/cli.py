"""
Command-line interface for the 3D scene agent.
Interactive REPL with streaming responses and todo tracking.
"""
import asyncio
import sys
import json
import ast
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from langchain_core.messages import HumanMessage

from scene_agent.agent.graph import create_agent_graph
from scene_agent.agent.state import TodoItem

console = Console()


def display_todos(todos: list[TodoItem]):
    """
    Pretty print current todo list with status icons.
    
    Args:
        todos: List of TodoItem objects
    """
    if not todos:
        return
    
    table = Table(title="📋 Task Progress", show_header=True, header_style="bold magenta")
    table.add_column("Status", style="cyan", width=12)
    table.add_column("Task", style="white")
    
    status_icons = {
        "pending": "⏳ Pending",
        "in_progress": "🔄 In Progress",
        "completed": "✅ Completed",
        "failed": "❌ Failed"
    }
    
    for todo in todos:
        status_text = status_icons.get(todo["status"], "❓ Unknown")
        table.add_row(status_text, todo["description"])
    
    console.print(table)


def parse_agent_response(content: str) -> str:
    """
    Parse agent response to extract the core message text.
    
    The agent may return responses in various formats:
    - Plain text
    - JSON string
    - Python list representation (from terminal output)
    
    Args:
        content: Raw agent response content
        
    Returns:
        Extracted message text
    """
    if not content:
        return ""
    
    # Try to parse as JSON array
    try:
        if content.strip().startswith('['):
            # Try JSON parsing first
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                # If JSON fails, try to parse as Python literal
                data = ast.literal_eval(content)
            
            # Extract text from list of message objects
            if isinstance(data, list) and len(data) > 0:
                messages = []
                for item in data:
                    if isinstance(item, dict) and 'text' in item:
                        messages.append(item['text'])
                    elif isinstance(item, dict) and 'content' in item:
                        messages.append(item['content'])
                if messages:
                    return '\n'.join(messages)
    except (json.JSONDecodeError, ValueError, SyntaxError):
        pass
    
    # Return as-is if not a special format
    return content


async def run_cli():
    """
    Run the interactive CLI interface.
    Streams agent responses and displays todos in real-time.
    """
    console.print(Panel.fit(
        "[bold cyan]3D Scene Agent[/bold cyan]\n"
        "Type your requests or commands:\n"
        "  /help - Show available commands\n"
        "  /scene - Show current scene info\n"
        "  /todos - Show current todos\n"
        "  /new - Start a new session\n"
        "  /exit, /quit, exit, quit, q - Exit the agent\n"
        "  Ctrl+C - Exit the agent",
        title="Welcome",
        border_style="cyan"
    ))
    
    # Create agent graph
    console.print("\n[yellow]Initializing agent...[/yellow]")
    try:
        app = await create_agent_graph()
        console.print("[green]✓ Agent ready![/green]\n")
    except Exception as e:
        console.print(f"[red]✗ Failed to initialize agent: {str(e)}[/red]")
        console.print("\n[yellow]Make sure:")
        console.print(
            "1. You have a .env file with the selected provider key set "
            "(for example GEMINI_API_KEY)"
        )
        console.print("2. The Blender MCP server is running (localhost:9876)[/yellow]")
        return
    
    # Session configuration
    thread_id = "cli-session"
    config = {"configurable": {"thread_id": thread_id}}
    
    # Main loop
    while True:
        try:
            # Get user input
            user_input = console.input("\n[bold green]You:[/bold green] ").strip()
            
            if not user_input:
                continue
            
            # Handle exit commands (with or without /)
            if user_input.lower() in ['/exit', '/quit', 'exit', 'quit', 'q']:
                console.print("\n[yellow]Goodbye![/yellow]")
                sys.exit(0)
            
            # Handle commands
            if user_input.startswith("/"):
                if user_input == "/help":
                    console.print(Panel(
                        "Available commands:\n"
                        "  /help - Show this help message\n"
                        "  /scene - Show current scene state\n"
                        "  /todos - Show current todos\n"
                        "  /new - Start a new session\n"
                        "  /exit, /quit, exit, quit, q - Exit the agent\n"
                        "  Ctrl+C - Exit the agent",
                        title="Help",
                        border_style="blue"
                    ))
                    continue
                elif user_input == "/new":
                    import random
                    thread_id = f"cli-session-{random.randint(1000, 9999)}"
                    config = {"configurable": {"thread_id": thread_id}}
                    console.print(f"[green]Started new session: {thread_id}[/green]")
                    continue
                elif user_input == "/scene":
                    # Get current state
                    state = await app.aget_state(config)
                    scene_objects = state.values.get("scene_objects", {})
                    if scene_objects:
                        console.print(f"[cyan]Scene has {len(scene_objects)} objects:[/cyan]")
                        for name in scene_objects.keys():
                            console.print(f"  • {name}")
                    else:
                        console.print("[yellow]No scene information available. Use get_scene_info() to load it.[/yellow]")
                    continue
                elif user_input == "/todos":
                    state = await app.aget_state(config)
                    todos = state.values.get("todos", [])
                    if todos:
                        display_todos(todos)
                    else:
                        console.print("[yellow]No todos yet.[/yellow]")
                    continue
                else:
                    console.print(f"[red]Unknown command: {user_input}[/red]")
                    continue
            
            # Stream agent responses
            console.print("\n[bold blue]Agent:[/bold blue]")
            
            displayed_content = set()  # Track what we've already displayed
            
            async for event in app.astream(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
                stream_mode="values"
            ):
                # Display agent messages
                if "messages" in event:
                    last_message = event["messages"][-1]
                    
                    # Skip echoing the user's input message
                    if hasattr(last_message, "type"):
                        if last_message.type == "human":
                            continue
                    
                    # Process agent responses
                    if hasattr(last_message, "content") and last_message.content:
                        # Only print if it's new content and not a tool call
                        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
                            content = last_message.content
                            
                            # Convert content to string for hashing (handle lists, dicts, etc.)
                            content_str = str(content) if not isinstance(content, str) else content
                            content_hash = hash(content_str)
                            
                            # Only display if we haven't seen this content before
                            if content_hash not in displayed_content:
                                displayed_content.add(content_hash)
                                
                                # Parse and display the response
                                parsed_text = parse_agent_response(content_str)
                                if parsed_text and parsed_text.strip():
                                    console.print(parsed_text)
                
                # Display todos if updated
                if "todos" in event and event["todos"]:
                    console.print()  # Add spacing
                    display_todos(event["todos"])
        
        except KeyboardInterrupt:
            console.print("\n\n[yellow]Goodbye![/yellow]")
            sys.exit(0)
        except EOFError:
            console.print("\n\n[yellow]Goodbye![/yellow]")
            sys.exit(0)
        except Exception as e:
            console.print(f"\n[red]Error: {str(e)}[/red]")


def main():
    """Entry point for CLI"""
    try:
        asyncio.run(run_cli())
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Goodbye![/yellow]")
        sys.exit(0)
    except EOFError:
        console.print("\n\n[yellow]Goodbye![/yellow]")
        sys.exit(0)
