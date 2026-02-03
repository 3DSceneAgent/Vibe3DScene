"""
LangGraph node implementations.
Nodes follow best practices: return partial state updates only.
"""
import json
import re
from typing import Dict, Any
from langchain_core.messages import ToolMessage, AIMessage, SystemMessage
from scene_agent.agent.state import AgentState, TodoItem, create_todo, update_todo_status
from scene_agent.memory.scene_memory import SceneMemory


def agent_node(state: AgentState, llm_with_tools) -> Dict[str, Any]:
    """
    Agent node: VLM reasoning with all tools bound.
    The agent decides when to perceive, render, and manipulate the scene.
    
    Args:
        state: Current agent state
        llm_with_tools: LLM with tools bound via bind_tools()
        
    Returns:
        Partial state update with new messages
    """
    # Build messages including system prompt
    from scene_agent.agent.prompts import get_full_system_prompt
    
    messages = [SystemMessage(content=get_full_system_prompt())]
    messages.extend(state["messages"])
    
    # Invoke the LLM
    response = llm_with_tools.invoke(messages)
    
    return {"messages": [response]}


def update_memory_node(state: AgentState) -> Dict[str, Any]:
    """
    Update memory node: Parse tool results and update scene state.
    Extracts scene_objects from get_scene_info results and todos from messages.
    
    Args:
        state: Current agent state
        
    Returns:
        Partial state update with scene_objects and todos
    """
    last_messages = state["messages"][-10:]  # Look at recent messages
    
    result = {}
    
    # Parse scene_objects from get_scene_info results
    for msg in last_messages:
        if isinstance(msg, ToolMessage):
            if "get_scene_info" in str(msg.name):
                scene_updates = SceneMemory.parse_scene_info(msg.content)
                if scene_updates:
                    result["scene_objects"] = scene_updates
                    break
    
    # Extract todo updates from assistant messages
    todo_updates = extract_todo_updates(last_messages)
    if todo_updates:
        result["todos"] = todo_updates
    
    return result


def extract_todo_updates(messages: list) -> list[TodoItem]:
    """
    Extract todo items from messages that contain <todos> tags.
    
    Args:
        messages: List of recent messages
        
    Returns:
        List of TodoItem objects parsed from messages
    """
    todos = []
    
    for msg in messages:
        if isinstance(msg, AIMessage):
            content = msg.content
            if not isinstance(content, str):
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            if "text" in item and isinstance(item["text"], str):
                                parts.append(item["text"])
                            elif "content" in item and isinstance(item["content"], str):
                                parts.append(item["content"])
                        elif isinstance(item, str):
                            parts.append(item)
                    content = "\n".join(parts) if parts else json.dumps(content, ensure_ascii=False)
                else:
                    content = str(content)
            
            # Look for <todos> blocks in the message
            todo_pattern = r'<todos>(.*?)</todos>'
            matches = re.findall(todo_pattern, content, re.DOTALL)
            
            for match in matches:
                # Parse each line in the todos block
                lines = match.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith('-'):
                        # Parse format: - [status] description
                        status_match = re.match(r'-?\s*\[(.*?)\]\s*(.*)', line)
                        if status_match:
                            status = status_match.group(1).strip()
                            description = status_match.group(2).strip()
                            
                            # Map status variations
                            status_map = {
                                'pending': 'pending',
                                'in_progress': 'in_progress',
                                'in progress': 'in_progress',
                                'completed': 'completed',
                                'done': 'completed',
                                'failed': 'failed',
                                'error': 'failed'
                            }
                            
                            status = status_map.get(status.lower(), 'pending')
                            
                            if description:
                                todo = create_todo(description, status)
                                todos.append(todo)
    
    return todos
