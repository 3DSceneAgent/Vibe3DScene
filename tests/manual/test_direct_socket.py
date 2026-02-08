#!/usr/bin/env python3
"""
Test direct socket connection to Blender server.

Low-level test that directly connects to Blender's socket server
and sends commands.

Usage:
    python tests/manual/test_direct_socket.py [--host HOST] [--port PORT]
"""
import socket
import json
import argparse
import time


def send_command(host, port, command_type, params=None):
    """Send a command and receive response"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        
        print(f"Connecting to {host}:{port}...")
        sock.connect((host, port))
        print("✅ Connected")
        
        command = {'type': command_type, 'params': params or {}}
        print(f"Sending command: {command_type}")
        
        sock.sendall(json.dumps(command).encode('utf-8'))
        
        print("Waiting for response...")
        response_data = sock.recv(65536)
        response = json.loads(response_data.decode('utf-8'))
        
        sock.close()
        return response
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def test_get_scene_info(host, port):
    """Test get_scene_info command"""
    print("\n=== Test 1: get_scene_info ===")
    
    response = send_command(host, port, 'get_scene_info', {'include_bbox': False})
    
    if response and response.get('status') == 'success':
        result = response.get('result', {})
        print(f"✅ SUCCESS")
        print(f"   Scene: {result.get('name')}")
        print(f"   Objects: {result.get('object_count')}")
        print(f"   Materials: {result.get('materials_count')}")
        return True
    else:
        error = response.get('message') if response else 'No response'
        print(f"❌ FAILED: {error}")
        return False


def test_execute_code(host, port):
    """Test execute_code command"""
    print("\n=== Test 2: execute_code ===")
    
    code = '''
import bpy
print("Hello from Blender!")
result = 2 + 2
print(f"2 + 2 = {result}")
print(f"Blender version: {bpy.app.version_string}")
'''
    
    response = send_command(host, port, 'execute_code', {'code': code})
    
    if response and response.get('status') == 'success':
        result = response.get('result', {})
        output = result.get('result', '').strip()
        print(f"✅ SUCCESS")
        print("Output:")
        for line in output.split('\n'):
            print(f"   {line}")
        return True
    else:
        error = response.get('message') if response else 'No response'
        print(f"❌ FAILED: {error}")
        return False


def test_get_object_info(host, port, object_name='Cube'):
    """Test get_object_info command"""
    print(f"\n=== Test 3: get_object_info ('{object_name}') ===")
    
    response = send_command(host, port, 'get_object_info', {'name': object_name})
    
    if response and response.get('status') == 'success':
        result = response.get('result', {})
        print(f"✅ SUCCESS")
        print(f"   Type: {result.get('type')}")
        print(f"   Location: {result.get('location')}")
        print(f"   Materials: {len(result.get('materials', []))}")
        return True
    else:
        error = response.get('message') if response else 'No response'
        print(f"❌ FAILED: {error}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Test direct socket connection')
    parser.add_argument('--host', default='localhost', help='Blender server host')
    parser.add_argument('--port', type=int, default=9876, help='Blender server port')
    parser.add_argument('--object', default='Cube', help='Object name to query')
    args = parser.parse_args()
    
    print("=" * 70)
    print("Direct Socket Connection Test")
    print("=" * 70)
    print(f"Target: {args.host}:{args.port}")
    print()
    
    # Run tests
    results = []
    results.append(("get_scene_info", test_get_scene_info(args.host, args.port)))
    results.append(("execute_code", test_execute_code(args.host, args.port)))
    results.append(("get_object_info", test_get_object_info(args.host, args.port, args.object)))
    
    # Summary
    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
    
    all_passed = all(result for _, result in results)
    
    print()
    if all_passed:
        print("🎉 All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1


if __name__ == "__main__":
    exit(main())
