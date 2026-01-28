"""
Base tool utilities.
Helper functions for tool management.
"""
from typing import List, Any


def filter_tools_by_name(tools: List[Any], names: List[str]) -> List[Any]:
    """
    Filter tools by their names.
    
    Args:
        tools: List of LangChain tools
        names: List of tool names to keep
        
    Returns:
        Filtered list of tools
    """
    return [tool for tool in tools if tool.name in names]


def get_tool_names(tools: List[Any]) -> List[str]:
    """
    Get the names of all tools.
    
    Args:
        tools: List of LangChain tools
        
    Returns:
        List of tool names
    """
    return [tool.name for tool in tools]
