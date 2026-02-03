# blender_mcpv_server.py
from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio
import logging
import tempfile
import time
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List, Optional
import os
from pathlib import Path
import base64
from urllib.parse import urlparse
import requests
# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")

# Default configuration
DEFAULT_CLIENT_HOST = "localhost"
DEFAULT_CLIENT_PORT = 9876
DEFAULT_SERVER_HOST = os.getenv("MCP_SERVER_HOST", "localhost")
DEFAULT_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "9877"))

polyhaven_meta_info = json.load(open("assets/polyhaven_meta.json"))
REQ_HEADERS = {"User-Agent": "blender-mcp-vision"}

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket = None  # Changed from 'socket' to 'sock' to avoid naming conflict
    
    def connect(self) -> bool:
        """Connect to the Blender addon socket server"""
        if self.sock:
            return True
            
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Blender addon"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        # Use a consistent timeout value that matches the addon's timeout
        sock.settimeout(180.0)  # Match the addon's timeout
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)
                    
                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                # If we can't parse it, it's incomplete
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")
        
        command = {
            "type": command_type,
            "params": params or {}
        }
        
        try:
            # Log the command being sent
            logger.info(f"Sending command: {command_type} with params: {params}")
            
            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # Set a timeout for receiving - use the same timeout as in receive_full_response
            self.sock.settimeout(180.0)  # Match the addon's timeout
            
            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Blender"))
            
            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Blender")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            # Just invalidate the current socket so it will be recreated next time
            self.sock = None
            raise Exception("Timeout waiting for Blender response - try simplifying your request")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Blender lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {str(e)}")
            # Try to log what was received
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Blender: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            self.sock = None
            raise Exception(f"Communication error with Blender: {str(e)}")

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools

    try:
        # Just log that we're starting up
        logger.info("BlenderMCP server starting up")

        # Record startup event for telemetry
        try:
            record_startup()
        except Exception as e:
            logger.debug(f"Failed to record startup telemetry: {e}")

        # Try to connect to Blender on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            blender = get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")

        # Return an empty context - we're using the global connection
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _blender_connection
        if _blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            _blender_connection.disconnect()
            _blender_connection = None
        logger.info("BlenderMCP server shut down")

# Create the MCP server with lifespan support
mcp = FastMCP(
    "BlenderMCP",
    lifespan=server_lifespan, 
    host=DEFAULT_SERVER_HOST,
    port=DEFAULT_SERVER_PORT
)

# Resource endpoints

# Global connection for resources (since resources can't access context)
_blender_connection = None
_polyhaven_enabled = True  # Add this global variable

def record_startup():
    """Placeholder for telemetry - can be implemented if needed"""
    pass

def get_blender_connection():
    """Get or create a persistent Blender connection"""
    global _blender_connection, _polyhaven_enabled  # Add _polyhaven_enabled to globals
    
    # If we have an existing connection, check if it's still valid
    if _blender_connection is not None:
        try:
            # First check if PolyHaven is enabled by sending a ping command
            result = _blender_connection.send_command("get_scene_info")
            return _blender_connection
        except Exception as e:
            # Connection is dead, close it and create a new one
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _blender_connection.disconnect()
            except:
                pass
            _blender_connection = None
    
    # Create a new connection if needed
    if _blender_connection is None:
        host = os.getenv("BLENDER_HOST", DEFAULT_CLIENT_HOST)
        port = int(os.getenv("BLENDER_PORT", DEFAULT_CLIENT_PORT))
        _blender_connection = BlenderConnection(host=host, port=port)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")
    
    return _blender_connection

@mcp.resource("resource://polyhaven_types")
def get_polyhaven_types() -> str:
    return json.dumps(["hdris", "textures", "models"], indent=2)

@mcp.resource("resource://polyhaven_categories/{category}")
def get_polyhaven_categories_offline(category: str) -> str:
    return json.dumps(polyhaven_meta_info.get(category, {}), indent=2)


@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get detailed information about the current Blender scene"""
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info")

        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting scene info from Blender: {str(e)}")
        return f"Error getting scene info: {str(e)}"

@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    """
    Get detailed information about a specific object in the Blender scene.
    
    Parameters:
    - object_name: The name of the object to get information about
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_object_info", {"name": object_name})
        
        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting object info from Blender: {str(e)}")
        return f"Error getting object info: {str(e)}"

@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 800) -> Image:
    """
    Capture a screenshot of the current Blender 3D viewport.
    
    Parameters:
    - max_size: Maximum size in pixels for the largest dimension (default: 800)
    
    Returns the screenshot as an Image.
    """
    try:
        blender = get_blender_connection()
        
        # Create temp file path
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")
        
        result = blender.send_command("get_viewport_screenshot", {
            "max_size": max_size,
            "filepath": temp_path,
            "format": "png"
        })
        
        if "error" in result:
            raise Exception(result["error"])
        
        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")
        
        # Read the file
        with open(temp_path, 'rb') as f:
            image_bytes = f.read()
        
        # Delete the temp file
        os.remove(temp_path)
        
        return Image(data=image_bytes, format="png")
        
    except Exception as e:
        logger.error(f"Error capturing screenshot: {str(e)}")
        raise Exception(f"Screenshot failed: {str(e)}")


@mcp.tool()
def execute_blender_code(ctx: Context, code: str) -> str:
    """
    Execute arbitrary Python code in Blender. Make sure to do it step-by-step by breaking it into smaller chunks.

    Parameters:
    - code: The Python code to execute
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command("execute_code", {"code": code})
        return f"Code executed successfully: {result.get('result', '')}"
    except Exception as e:
        logger.error(f"Error executing code: {str(e)}")
        return f"Error executing code: {str(e)}"

@mcp.tool()
def get_polyhaven_categories(ctx: Context, asset_type: str = "hdris") -> str:
    """
    Get a list of categories for a specific asset type on Polyhaven.
    
    Parameters:
    - asset_type: The type of asset to get categories for (hdris, textures, models, all)
    """
    try:
        if asset_type not in ["hdris", "textures", "models", "all"]:
            return f"Error: Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"

        response = requests.get(
            f"https://api.polyhaven.com/categories/{asset_type}",
            headers=REQ_HEADERS,
            timeout=30
        )
        if response.status_code != 200:
            return f"Error: PolyHaven API failed with status code {response.status_code}"

        categories = response.json()
        formatted_output = f"Categories for {asset_type}:\n\n"
        
        # Sort categories by count (descending)
        sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
        
        for category, count in sorted_categories:
            formatted_output += f"- {category}: {count} assets\n"
        
        return formatted_output
    except Exception as e:
        logger.error(f"Error getting Polyhaven categories: {str(e)}")
        return f"Error getting Polyhaven categories: {str(e)}"

@mcp.tool()
def search_polyhaven_assets(
    ctx: Context,
    asset_type: str = "all",
    categories: str = None
) -> str:
    """
    Search for assets on Polyhaven with optional filtering.
    
    Parameters:
    - asset_type: Type of assets to search for (hdris, textures, models, all)
    - categories: Optional comma-separated list of categories to filter by
    
    Returns a list of matching assets with basic information.
    """
    try:
        params = {}
        if asset_type and asset_type != "all":
            if asset_type not in ["hdris", "textures", "models"]:
                return f"Error: Invalid asset type: {asset_type}. Must be one of: hdris, textures, models, all"
            params["type"] = asset_type
        if categories:
            params["categories"] = categories

        response = requests.get(
            "https://api.polyhaven.com/assets",
            params=params,
            headers=REQ_HEADERS,
            timeout=30
        )
        if response.status_code != 200:
            return f"Error: PolyHaven API failed with status code {response.status_code}"

        assets = response.json()
        limited_assets = {}
        for i, (key, value) in enumerate(assets.items()):
            if i >= 20:
                break
            limited_assets[key] = value
        total_count = len(assets)
        returned_count = len(limited_assets)
        
        formatted_output = f"Found {total_count} assets"
        if categories:
            formatted_output += f" in categories: {categories}"
        formatted_output += f"\nShowing {returned_count} assets:\n\n"
        
        # Sort assets by download count (popularity)
        sorted_assets = sorted(limited_assets.items(), key=lambda x: x[1].get("download_count", 0), reverse=True)
        
        for asset_id, asset_data in sorted_assets:
            formatted_output += f"- {asset_data.get('name', asset_id)} (ID: {asset_id})\n"
            formatted_output += f"  Type: {['HDRI', 'Texture', 'Model'][asset_data.get('type', 0)]}\n"
            formatted_output += f"  Categories: {', '.join(asset_data.get('categories', []))}\n"
            formatted_output += f"  Downloads: {asset_data.get('download_count', 'Unknown')}\n\n"
        
        return formatted_output
    except Exception as e:
        logger.error(f"Error searching Polyhaven assets: {str(e)}")
        return f"Error searching Polyhaven assets: {str(e)}"

@mcp.tool()
def download_polyhaven_asset(
    ctx: Context,
    asset_id: str,
    asset_type: str,
    resolution: str = "1k",
    file_format: str = None
) -> str:
    """
    Download and import a Polyhaven asset into Blender.
    
    Parameters:
    - asset_id: The ID of the asset to download
    - asset_type: The type of asset (hdris, textures, models)
    - resolution: The resolution to download (e.g., 1k, 2k, 4k)
    - file_format: Optional file format (e.g., hdr, exr for HDRIs; jpg, png for textures; gltf, fbx for models)
    
    Returns a message indicating success or failure.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("download_polyhaven_asset", {
            "asset_id": asset_id,
            "asset_type": asset_type,
            "resolution": resolution,
            "file_format": file_format
        })
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        if result.get("success"):
            message = result.get("message", "Asset downloaded and imported successfully")
            
            # Add additional information based on asset type
            if asset_type == "hdris":
                return f"{message}. The HDRI has been set as the world environment."
            elif asset_type == "textures":
                material_name = result.get("material", "")
                maps = ", ".join(result.get("maps", []))
                return f"{message}. Created material '{material_name}' with maps: {maps}."
            elif asset_type == "models":
                return f"{message}. The model has been imported into the current scene."
            else:
                return message
        else:
            return f"Failed to download asset: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error downloading Polyhaven asset: {str(e)}")
        return f"Error downloading Polyhaven asset: {str(e)}"

@mcp.tool()
def set_texture(
    ctx: Context,
    object_name: str,
    texture_id: str
) -> str:
    """
    Apply a previously downloaded Polyhaven texture to an object.
    
    Parameters:
    - object_name: Name of the object to apply the texture to
    - texture_id: ID of the Polyhaven texture to apply (must be downloaded first)
    
    Returns a message indicating success or failure.
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command("set_texture", {
            "object_name": object_name,
            "texture_id": texture_id
        })
        
        if "error" in result:
            return f"Error: {result['error']}"
        
        if result.get("success"):
            material_name = result.get("material", "")
            maps = ", ".join(result.get("maps", []))
            
            # Add detailed material info
            material_info = result.get("material_info", {})
            node_count = material_info.get("node_count", 0)
            has_nodes = material_info.get("has_nodes", False)
            texture_nodes = material_info.get("texture_nodes", [])
            
            output = f"Successfully applied texture '{texture_id}' to {object_name}.\n"
            output += f"Using material '{material_name}' with maps: {maps}.\n\n"
            output += f"Material has nodes: {has_nodes}\n"
            output += f"Total node count: {node_count}\n\n"
            
            if texture_nodes:
                output += "Texture nodes:\n"
                for node in texture_nodes:
                    output += f"- {node['name']} using image: {node['image']}\n"
                    if node['connections']:
                        output += "  Connections:\n"
                        for conn in node['connections']:
                            output += f"    {conn}\n"
            else:
                output += "No texture nodes found in the material.\n"
            
            return output
        else:
            return f"Failed to apply texture: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error applying texture: {str(e)}")
        return f"Error applying texture: {str(e)}"


@mcp.tool()
def import_glb_model(
    ctx: Context,
    model_url: str,
    object_name: str = None
) -> str:
    """
    Import a GLB model from URL into Blender.
    
    Parameters:
    - model_url: Direct URL to the GLB file
    - object_name: Optional name for the imported object
    
    Returns a message indicating success or failure.
    """
    try:
        blender = get_blender_connection()
        
        if not object_name:
            object_name = "ImportedModel"
        
        result = blender.send_command("import_glb_model", {
            "model_url": model_url,
            "object_name": object_name
        })
        
        if "error" in result:
            return f"Error importing model: {result['error']}"
        
        if result.get("success"):
            imported_objects = result.get("imported_objects", [])
            message = f"Successfully imported model from '{model_url}'\n"
            message += f"Imported {len(imported_objects)} object(s): {', '.join(imported_objects)}\n"
            
            if result.get("bounding_box"):
                bbox = result["bounding_box"]
                message += f"Bounding box: min={bbox.get('min')}, max={bbox.get('max')}\n"
            
            return message
        else:
            return f"Failed to import model: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error importing GLB model: {str(e)}")
        return f"Error importing GLB model: {str(e)}"


@mcp.tool()
def generate_trellis2_model(
    ctx: Context,
    text_prompt: str,
    object_name: str,
    pipeline_type: str = "512",
    texture_size: int = 1024,
    timeout: int = 300
) -> str:
    """
    Generate a 3D model using TRELLIS2 and return the model URL.
    This is a synchronous operation that will block until the model is generated.
    The 3D asset includes built-in materials and textures.
    
    Parameters:
    - text_prompt: Text description of the desired model (e.g., "a wooden chair", "sports car")
    - object_name: name hint for client-side import
    - pipeline_type: Quality preset - "512" (fast), "1024", "1024_cascade", "1536_cascade" (best quality)
    - texture_size: Output texture resolution in pixels (default: 1024)
    - timeout: Maximum wait time in seconds (default: 300 = 5 minutes)
    
    Note: Provide either text_prompt OR image_path, not both.
    
    Returns a JSON string with model URL and metadata for client-side import.
    """
    try:
        trellis_host = os.getenv("TRELLIS2_HOST", "localhost")
        trellis_port = os.getenv("TRELLIS2_PORT", "8001")
        base_url = f"http://{trellis_host}:{trellis_port}"

        endpoint = f"{base_url}/api/v1/text-to-3d"
        payload = {
            "prompt": text_prompt,
            "generate_model": True,
            "generate_video": False,
            "pipeline_type": pipeline_type.replace('"', "").replace("'", ""),
            "texture_size": texture_size,
            "timeout": timeout
        }

        response = requests.post(endpoint, data=payload, timeout=timeout)
        if response.status_code != 200:
            return f"Error: TRELLIS2 API request failed with status {response.status_code}: {response.text}"

        result = response.json()
        model_url = result.get("model_url")
        if not model_url:
            return "Error: No model_url in TRELLIS2 response"

        if not model_url.startswith("http"):
            model_url = f"{base_url}{model_url}"

        # Automatically import the generated model
        import_result = import_glb_model(ctx, model_url, object_name)
        
        return f"TRELLIS2 generation completed.\n{import_result}"
    except Exception as e:
        logger.error(f"Error generating TRELLIS2 model: {str(e)}")
        return f"Error generating TRELLIS2 model: {str(e)}"


@mcp.tool()
def search_3d_assets_by_text(
    ctx: Context,
    query: str,
    top_k: int = 3
) -> str:
    """
    Search for 3D assets in the retrieval database using text queries.
    
    Parameters:
    - query: The search text (e.g., "medieval sword", "wooden chair", "sports car")
    - top_k: Number of results to return (1-10, default: 3)
    
    Returns a list of matching 3D assets with similarity scores, descriptions, and download URLs.
    Use import_retrieved_asset() to import a selected asset into Blender.
    """
    try:
        import requests
        
        # Get host and port from environment variables
        retrieval_host = os.getenv("RETRIEVAL_API_HOST", "localhost")
        retrieval_port = os.getenv("RETRIEVAL_API_PORT", "8001")
        base_url = f"http://{retrieval_host}:{retrieval_port}"
        
        if top_k < 1 or top_k > 100:
            return f"Error: top_k must be between 1 and 100, got {top_k}"
        
        # Prepare request
        payload = {
            "query": query,
            "top_k": top_k
        }
        
        # Make request
        response = requests.post(
            f"{base_url}/search/text",
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        results_list = result.get("results", [])
        
        if not results_list:
            return f"No results found for query: '{query}'"
        
        # Format results
        output = f"Found {len(results_list)} assets for query: '{query}'\n"
        
        for i, asset in enumerate(results_list, 1):
            output += f"{i}. Asset ID: {asset.get('asset_id', 'N/A')}\n"
            output += f"   Similarity: {asset.get('similarity', 0):.3f}\n"
            output += f"   Description (EN): {asset.get('caption_en', 'N/A')}\n"
            if asset.get('caption_cn'):
                output += f"   Description (CN): {asset.get('caption_cn', '')}\n"
            output += f"   Model URL: {asset.get('model_url', 'N/A')}\n"
            if asset.get('objaverse_id'):
                output += f"   Objaverse ID: {asset.get('objaverse_id', '')}\n"
            output += "\n"
        
        output += "\nTo import an asset, use import_retrieved_asset() with the asset_id and model_url."
        
        return output
        
    except requests.exceptions.ConnectionError:
        return f"Cannot connect to retrieval service at {retrieval_host}:{retrieval_port}."
    except requests.exceptions.Timeout:
        return "Request to retrieval service timed out."
    except requests.exceptions.HTTPError as e:
        return f"HTTP error from retrieval service: {e.response.status_code} - {e.response.text}"
    except Exception as e:
        logger.error(f"Error searching 3D assets: {str(e)}")
        return f"Error searching 3D assets: {str(e)}"

@mcp.tool()
def import_retrieved_asset(
    ctx: Context,
    model_url: str,
    object_name: str = None
) -> str:
    """
    Import a 3D asset from the retrieval database into Blender.
    
    Parameters:
    - model_url: The direct GLB download URL from search results
    - object_name: Optional name for the imported object (default: uses asset_id)
    
    Returns a message indicating success or failure, along with information about the imported object.
    """
    # Delegate to import_glb_model
    if not object_name:
        object_name = "RetrievedAsset"
    
    return import_glb_model(ctx, model_url, object_name)

@mcp.tool()
def create_camera_from_objects(
    ctx: Context,
    object_names: list[str],
    focal_length: str = "normal",
    azimuth: float = 45,
    elevation: float = 30
) -> str:
    """
    Create and position camera to frame specified objects using spherical coordinates.
    
    Parameters:
    - object_names: List of object names to frame in the camera view
    - focal_length: "wide" (26mm), "normal" (50mm), or "far" (85mm)
    - azimuth: Horizontal angle in degrees (0-360, 0=front, 90=right, 180=back, 270=left)
    - elevation: Vertical angle in degrees (0-90, 0=side view, 90=top view)
    
    Returns information about the created camera including its name, position, and rotation.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_camera_from_objects", {
            "object_names": object_names,
            "focal_length": focal_length,
            "azimuth": azimuth,
            "elevation": elevation
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error creating camera from objects: {str(e)}")
        return f"Error creating camera from objects: {str(e)}"

@mcp.tool()
def create_camera_from_params(
    ctx: Context,
    x: float,
    y: float,
    z: float,
    rot_x: float,
    rot_y: float,
    rot_z: float,
    focal: float
) -> str:
    """
    Create camera from explicit parameters (position, rotation, focal length).
    
    Parameters:
    - x, y, z: Camera position in 3D space
    - rot_x, rot_y, rot_z: Camera rotation (Euler angles in radians)
    - focal: Focal length in millimeters
    
    Returns information about the created camera.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("create_camera_from_params", {
            "x": x, "y": y, "z": z,
            "rot_x": rot_x, "rot_y": rot_y, "rot_z": rot_z,
            "focal": focal
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error creating camera from params: {str(e)}")
        return f"Error creating camera from params: {str(e)}"

@mcp.tool()
def render_from_objects(
    ctx: Context,
    object_names: list[str],
    mode: str = "rgb",
    focal_length: str = "normal",
    azimuth: float = 45,
    elevation: float = 30
) -> Image:
    """
    Render image by auto-creating camera focused on specified objects.
    Uses EEVEE renderer for fast results.
    
    Parameters:
    - object_names: List of objects to focus on and render
    - mode: "rgb" for standard render, or "annotated" to add 2D bboxes and object IDs
    - focal_length: "wide" (26mm), "normal" (50mm), or "far" (85mm)
    - azimuth: Horizontal angle in degrees (0-360)
    - elevation: Vertical angle in degrees (0-90)
    
    Returns the rendered image. In annotated mode, objects are highlighted with red
    bounding boxes and labeled with their names.
    """
    try:
        blender = get_blender_connection()
        
        # Create temp file path
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_render_{os.getpid()}_{int(time.time())}.png")
        
        result = blender.send_command("render_from_objects", {
            "object_names": object_names,
            "mode": mode,
            "focal_length": focal_length,
            "azimuth": azimuth,
            "elevation": elevation,
            "filepath": temp_path
        })
        
        if not result.get("success"):
            raise Exception("Render failed")
        
        # Read the rendered image
        filepath = result["filepath"]
        if not os.path.exists(filepath):
            raise Exception(f"Rendered file not found: {filepath}")
        
        with open(filepath, 'rb') as f:
            image_bytes = f.read()
        
        # Note: File is kept on disk for user inspection
        logger.info(f"Rendered image saved to {filepath}")
        
        return Image(data=image_bytes, format="png")
        
    except Exception as e:
        logger.error(f"Error rendering from objects: {str(e)}")
        raise Exception(f"Render failed: {str(e)}")

@mcp.tool()
def render_from_camera(
    ctx: Context,
    camera_name: str,
    object_names: Optional[list[str]] = None,
    mode: str = "rgb"
) -> Image:
    """
    Render from an existing camera in the scene.
    Uses EEVEE renderer for fast results.
    
    Parameters:
    - camera_name: Name of the camera object to render from
    - object_names: Optional list of objects to highlight in annotated mode
    - mode: "rgb" for standard render, or "annotated" to add 2D bboxes and object IDs
    
    Returns the rendered image.
    """
    try:
        blender = get_blender_connection()
        
        # Create temp file path
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_render_{os.getpid()}_{int(time.time())}.png")
        
        result = blender.send_command("render_from_camera", {
            "camera_name": camera_name,
            "object_names": object_names,
            "mode": mode,
            "filepath": temp_path
        })
        
        if not result.get("success"):
            raise Exception("Render failed")
        
        # Read the rendered image
        filepath = result["filepath"]
        if not os.path.exists(filepath):
            raise Exception(f"Rendered file not found: {filepath}")
        
        with open(filepath, 'rb') as f:
            image_bytes = f.read()
        
        logger.info(f"Rendered image saved to {filepath}")
        
        return Image(data=image_bytes, format="png")
        
    except Exception as e:
        logger.error(f"Error rendering from camera: {str(e)}")
        raise Exception(f"Render failed: {str(e)}")


@mcp.prompt()
def asset_creation_strategy() -> str:
    """Defines the preferred strategy for creating assets in Blender"""
    return """When creating 3D content in Blender, always start by checking if integrations are available:

    0. Before anything, always check the scene from get_scene_info()
    1. First use the following tools to verify if the following integrations are enabled:
        1. PolyHaven
            - For objects/models: Use download_polyhaven_asset() with asset_type="models"
            - For materials/textures: Use download_polyhaven_asset() with asset_type="textures"
            - For environment lighting: Use download_polyhaven_asset() with asset_type="hdris"
        
        2. TRELLIS2 (3DAIGC Generation)
            TRELLIS2 is excellent at generating high-quality 3D models from text or images.
            Best practices:
            - Generate single objects (not entire scenes)
            - Don't generate ground/floor planes with TRELLIS2
            - Don't generate complex assemblies - create parts separately and assemble
            - For image-to-3D: Images with clear subjects and removed backgrounds work best
            
            Usage:
            - Use generate_trellis2_model() with either text_prompt OR image_path
            - Text examples: "a wooden chair", "sports car", "medieval sword"
            - The operation is synchronous and may take 30s-1min
            - Reuse generated assets by duplicating objects with Python code
        
        3. 3D Asset Retrieval Database
            - For searching existing 3D models: Use search_3d_assets_by_text() with descriptive queries
                * Examples: "wooden chair", "sports car", "medieval castle", "office desk"
                * Use algorithm="siglip" for English queries (default, recommended for most cases)
                * Use algorithm="qwen" with language="chinese" for Chinese queries
                * Set cross_modal=true to search by visual similarity across modalities
                * The service returns similarity scores - higher scores (closer to 1.0) indicate better matches
            - After finding suitable asset: Use import_retrieved_asset() with asset_id and model_url from search results
            - The retrieval database contains large-scale professionally created 3D assets from Objaverse
            - Assets are in GLB format and include materials and textures
            - Best for: common real-world objects, furniture, vehicles, architecture, props

    2. Always check the world_bounding_box for each item so that:
        - Ensure that all objects that should not be clipping are not clipping.
        - Items have right spatial relationship.
    
    3. Recommended asset source priority:
        - For common real-world objects (furniture, vehicles, everyday items): Try 3D Asset Retrieval first
        - For specific architectural elements or natural materials: Try PolyHaven first, then Retrieval
        - For custom or highly specific unique items: Try Retrieval first, then TRELLIS2 for generation
        - For generating from reference images: Use TRELLIS2 image-to-3D
        - For procedural/primitive objects (cubes, spheres, planes): Use Blender scripting directly
        - For environment lighting: Use PolyHaven HDRIs
        - For materials/textures: Use PolyHaven textures

    Only fall back to scripting when:
    - All asset sources (Retrieval, PolyHaven, TRELLIS2) are disabled or unavailable
    - A simple primitive is explicitly requested
    - No suitable asset exists in any of the libraries after searching
    - TRELLIS2 failed to generate the desired asset or is taking too long
    - The task specifically requires a basic material/color or procedural geometry
    """

# Main execution

def main():
    """Run the MCP server"""
    mcp.run(transport="streamable-http")

if __name__ == "__main__":
    main()