"""
Camera memory management for tracking renderings.
Stores and manages rendered images from persistent cameras.
"""
from typing import Dict, List, Any
from datetime import datetime
from collections import deque


class CameraMemory:
    """
    Manages camera rendering history.
    Stores rendered images with timestamps and auto-prunes old entries.
    """
    
    def __init__(self, max_renderings_per_camera: int = 5):
        """
        Initialize camera memory.
        
        Args:
            max_renderings_per_camera: Maximum number of renderings to keep per camera
        """
        self.max_renderings = max_renderings_per_camera
        self._memory: Dict[str, deque] = {}
    
    def add_rendering(
        self, 
        camera_name: str, 
        rendering_data: Any,
        metadata: Dict[str, Any] = None
    ):
        """
        Add a rendering for a camera.
        
        Args:
            camera_name: Name of the camera
            rendering_data: The rendered image data (bytes, base64, etc)
            metadata: Optional metadata about the rendering
        """
        if camera_name not in self._memory:
            self._memory[camera_name] = deque(maxlen=self.max_renderings)
        
        entry = {
            "data": rendering_data,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
        
        self._memory[camera_name].append(entry)
    
    def get_latest_rendering(self, camera_name: str) -> Any:
        """Get the most recent rendering for a camera"""
        if camera_name in self._memory and self._memory[camera_name]:
            return self._memory[camera_name][-1]["data"]
        return None
    
    def get_all_renderings(self, camera_name: str) -> List[Dict[str, Any]]:
        """Get all renderings for a camera"""
        if camera_name in self._memory:
            return list(self._memory[camera_name])
        return []
    
    def get_rendering_history(self, camera_name: str, count: int = 3) -> List[Any]:
        """
        Get the most recent N renderings for a camera.
        
        Args:
            camera_name: Name of the camera
            count: Number of recent renderings to return
            
        Returns:
            List of rendering data (newest first)
        """
        if camera_name in self._memory:
            history = list(self._memory[camera_name])
            return [entry["data"] for entry in reversed(history[-count:])]
        return []
    
    def clear_camera(self, camera_name: str):
        """Clear all renderings for a specific camera"""
        if camera_name in self._memory:
            del self._memory[camera_name]
    
    def get_all_cameras(self) -> List[str]:
        """Get list of all cameras with stored renderings"""
        return list(self._memory.keys())
    
    def to_state_dict(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Convert memory to a state dict for checkpointing.
        
        Returns:
            Dict of {camera_name: [rendering_entries]}
        """
        return {
            camera: list(renderings)
            for camera, renderings in self._memory.items()
        }
    
    @classmethod
    def from_state_dict(
        cls, 
        state_dict: Dict[str, List[Dict[str, Any]]],
        max_renderings_per_camera: int = 5
    ) -> "CameraMemory":
        """
        Restore camera memory from a state dict.
        
        Args:
            state_dict: Dict from to_state_dict()
            max_renderings_per_camera: Max renderings to keep
            
        Returns:
            Restored CameraMemory instance
        """
        memory = cls(max_renderings_per_camera)
        for camera, renderings in state_dict.items():
            memory._memory[camera] = deque(renderings, maxlen=max_renderings_per_camera)
        return memory
