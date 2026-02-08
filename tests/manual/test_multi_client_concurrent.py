#!/usr/bin/env python3
"""
Test multi-client concurrent access to Blender server.

Tests that multiple clients can connect and execute commands simultaneously
without blocking each other.

Usage:
    python tests/manual/test_multi_client_concurrent.py [--port PORT]
"""
import socket
import json
import threading
import time
import argparse


def send_command(client_id, port=9876):
    """Send a single command to Blender server"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(('localhost', port))
        
        start = time.time()
        command = {'type': 'get_scene_info', 'params': {'include_bbox': False}}
        sock.sendall(json.dumps(command).encode('utf-8'))
        
        response = sock.recv(65536)
        result = json.loads(response.decode('utf-8'))
        elapsed = time.time() - start
        
        if result.get('status') == 'success':
            object_count = result.get('result', {}).get('object_count', 0)
            print(f"Client {client_id}: ✅ SUCCESS in {elapsed:.2f}s (objects: {object_count})")
            return True
        else:
            print(f"Client {client_id}: ❌ ERROR - {result.get('message')}")
            return False
        
    except Exception as e:
        print(f"Client {client_id}: ❌ EXCEPTION - {e}")
        return False
    finally:
        try:
            sock.close()
        except:
            pass


def test_sequential(num_clients=3, port=9876):
    """Test sequential execution (baseline)"""
    print(f"=== Test 1: Sequential baseline ({num_clients} clients) ===")
    start = time.time()
    success_count = 0
    
    for i in range(num_clients):
        if send_command(i, port):
            success_count += 1
    
    elapsed = time.time() - start
    print(f"Sequential time: {elapsed:.2f}s")
    print(f"Success rate: {success_count}/{num_clients}\n")
    
    return elapsed, success_count == num_clients


def test_concurrent(num_clients=5, port=9876):
    """Test concurrent execution"""
    print(f"=== Test 2: Concurrent ({num_clients} clients) ===")
    threads = []
    results = []
    
    def worker(client_id):
        success = send_command(client_id, port)
        results.append(success)
    
    start = time.time()
    for i in range(num_clients):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
    
    elapsed = time.time() - start
    success_count = sum(results)
    
    print(f"Concurrent time: {elapsed:.2f}s")
    print(f"Success rate: {success_count}/{num_clients}\n")
    
    return elapsed, success_count == num_clients


def main():
    parser = argparse.ArgumentParser(description='Test multi-client concurrent access')
    parser.add_argument('--port', type=int, default=9876, help='Blender server port')
    parser.add_argument('--sequential', type=int, default=3, help='Number of sequential clients')
    parser.add_argument('--concurrent', type=int, default=5, help='Number of concurrent clients')
    args = parser.parse_args()
    
    print("=" * 70)
    print("Multi-Client Concurrent Access Test")
    print("=" * 70)
    print()
    
    # Test sequential
    seq_time, seq_ok = test_sequential(args.sequential, args.port)
    
    if not seq_ok:
        print("❌ Sequential test failed, aborting concurrent test")
        return 1
    
    # Test concurrent
    conc_time, conc_ok = test_concurrent(args.concurrent, args.port)
    
    # Analyze results
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    
    if conc_ok:
        speedup = seq_time / conc_time if conc_time > 0 else float('inf')
        print(f"Sequential time: {seq_time:.2f}s")
        print(f"Concurrent time: {conc_time:.2f}s")
        print(f"Speedup: {speedup:.2f}x")
        print()
        
        if speedup > 1.5:
            print("🎉 Multi-client support is working!")
            print("   Concurrent execution is significantly faster.")
            return 0
        else:
            print("⚠️  Multi-client may not be working optimally")
            print("   Concurrent execution is not much faster than sequential")
            return 1
    else:
        print("❌ Concurrent test failed")
        return 1


if __name__ == "__main__":
    exit(main())
