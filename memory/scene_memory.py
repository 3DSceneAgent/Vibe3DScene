"""
Scene memory management for tracking 3D objects.
Parses and structures scene information from Blender.
"""
import json
from typing import Dict, Any


class SceneMemory:
    """
    Manages scene object tracking.
    Parses get_scene_info() results into structured format.
    """
    
    @staticmethod
    def parse_scene_info(scene_info_str: str) -> Dict[str, Any]:
        """
        Parse scene info JSON string from Blender into structured dict.
        
        Args:
            scene_info_str: JSON string from get_scene_info() tool
            
        Returns:
            Dict of {object_name: {position, size, type, bounding_box}}
        """
        try:
            scene_data = json.loads(scene_info_str) if isinstance(scene_info_str, str) else scene_info_str
            
            objects = {}
            if "objects" in scene_data:
                for obj in scene_data["objects"]:
                    name = obj.get("name", "Unknown")
                    objects[name] = {
                        "type": obj.get("type", "UNKNOWN"),
                        "location": obj.get("location", [0, 0, 0]),
                        "dimensions": obj.get("dimensions", [0, 0, 0]),
                        "bounding_box": obj.get("world_bounding_box", None),
                        "visible": obj.get("visible", True),
                        "material_count": obj.get("material_count", 0),
                    }
            
            return objects
        except json.JSONDecodeError as e:
            print(f"Error parsing scene info: {e}")
            return {}
        except Exception as e:
            print(f"Unexpected error parsing scene info: {e}")
            return {}
    
    @staticmethod
    def get_object_names(scene_objects: Dict[str, Any]) -> list[str]:
        """Get list of all object names in the scene"""
        return list(scene_objects.keys())
    
    @staticmethod
    def get_objects_by_type(scene_objects: Dict[str, Any], obj_type: str) -> Dict[str, Any]:
        """Filter objects by type (MESH, CAMERA, LIGHT, etc)"""
        return {
            name: data 
            for name, data in scene_objects.items() 
            if data.get("type") == obj_type
        }
    
    @staticmethod
    def check_overlap(obj1_bbox: list, obj2_bbox: list) -> bool:
        """
        Check if two bounding boxes overlap.
        
        Args:
            obj1_bbox: [min_corner, max_corner] of first object
            obj2_bbox: [min_corner, max_corner] of second object
            
        Returns:
            True if bounding boxes overlap
        """
        if not obj1_bbox or not obj2_bbox:
            return False
        
        min1, max1 = obj1_bbox
        min2, max2 = obj2_bbox
        
        # Check for separation on any axis
        if (max1[0] < min2[0] or max2[0] < min1[0] or
            max1[1] < min2[1] or max2[1] < min1[1] or
            max1[2] < min2[2] or max2[2] < min1[2]):
            return False
        
        return True
