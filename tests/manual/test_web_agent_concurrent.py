#!/usr/bin/env python3
"""
Test concurrent access from web application and agent.

Simulates the real-world scenario where:
- Web frontend performs a long-running render operation
- Agent simultaneously queries scene information

Usage:
    python tests/manual/test_web_agent_concurrent.py [--port PORT]
"""
import socket
import json
import threading
import time
import argparse


def simulate_web_long_request(port=9876, render_camera='Camera'):
    """Simulate web application's render request (slower operation)"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(('localhost', port))
        
        print("Web: Connected, sending render command...")
        start = time.time()
        
        command = {
            'type': 'render_from_camera',
            'params': {
                'camera_name': render_camera,
                'mode': 'rgb',
                'filepath': '/tmp/test_render_web.png'
            }
        }
        sock.sendall(json.dumps(command).encode('utf-8'))
        
        response = sock.recv(65536)
        result = json.loads(response.decode('utf-8'))
        elapsed = time.time() - start
        
        if result.get('status') == 'success':
            print(f"Web: ✅ Render completed in {elapsed:.2f}s")
            return True
        else:
            print(f"Web: ❌ Error - {result.get('message')}")
            return False
        
    except Exception as e:
        print(f"Web: ❌ Exception - {e}")
        return False
    finally:
        try:
            sock.close()
        except:
            pass


def simulate_agent_quick_requests(port=9876, num_requests=3, delay=0.2):
    """Simulate agent's quick scene queries"""
    # Wait a bit for web to connect first
    time.sleep(0.5)
    
    results = []
    for i in range(num_requests):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect(('localhost', port))
            
            print(f"Agent {i}: Connected, getting scene info...")
            start = time.time()
            
            command = {'type': 'get_scene_info', 'params': {'include_bbox': False}}
            sock.sendall(json.dumps(command).encode('utf-8'))
            
            response = sock.recv(65536)
            result = json.loads(response.decode('utf-8'))
            elapsed = time.time() - start
            
            if result.get('status') == 'success':
                obj_count = result.get('result', {}).get('object_count', 0)
                print(f"Agent {i}: ✅ Got scene in {elapsed:.2f}s ({obj_count} objects)")
                results.append(True)
            else:
                print(f"Agent {i}: ❌ Error")
                results.append(False)
            
            sock.close()
        except Exception as e:
            print(f"Agent {i}: ❌ Exception - {e}")
            results.append(False)
        
        time.sleep(delay)
    
    return all(results)


def main():
    parser = argparse.ArgumentParser(description='Test web + agent concurrent access')
    parser.add_argument('--port', type=int, default=9876, help='Blender server port')
    parser.add_argument('--camera', default='Camera', help='Camera name for rendering')
    parser.add_argument('--agent-requests', type=int, default=3, help='Number of agent requests')
    args = parser.parse_args()
    
    print("=" * 70)
    print("Web + Agent Concurrent Access Test")
    print("=" * 70)
    print("Scenario: Web renders while Agent queries scene info")
    print()
    
    web_result = [None]
    agent_result = [None]
    
    def web_worker():
        web_result[0] = simulate_web_long_request(args.port, args.camera)
    
    def agent_worker():
        agent_result[0] = simulate_agent_quick_requests(args.port, args.agent_requests)
    
    web_thread = threading.Thread(target=web_worker)
    agent_thread = threading.Thread(target=agent_worker)
    
    start = time.time()
    web_thread.start()
    agent_thread.start()
    
    web_thread.join()
    agent_thread.join()
    elapsed = time.time() - start
    
    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Total time: {elapsed:.2f}s")
    print(f"Web result: {'✅ Success' if web_result[0] else '❌ Failed'}")
    print(f"Agent result: {'✅ Success' if agent_result[0] else '❌ Failed'}")
    print()
    
    if web_result[0] and agent_result[0]:
        print("🎉 SUCCESS: Multi-client support is working!")
        print("   Agent completed requests while Web was rendering.")
        return 0
    else:
        print("❌ FAILED: Some operations did not complete successfully")
        return 1


if __name__ == "__main__":
    exit(main())
