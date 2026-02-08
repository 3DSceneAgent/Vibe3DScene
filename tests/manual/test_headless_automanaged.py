#!/usr/bin/env python3
"""
Test headless auto-managed mode.

Verifies that the API can automatically start and manage Blender instances
for each thread_id in headless mode.

Prerequisites:
- BLENDER_MODE=headless in .env
- API server running (./scripts/start_services.sh)

Usage:
    python tests/manual/test_headless_automanaged.py
"""
import requests
import time
import subprocess
import sys


def check_api_health():
    """Check if API server is running"""
    try:
        response = requests.get("http://localhost:8000/health", timeout=2)
        return response.status_code == 200
    except:
        return False


def test_single_thread():
    """Test auto-starting Blender for a single thread"""
    print("=== Test 1: Single thread auto-start ===")
    
    thread_id = f"test-single-{int(time.time() * 1000)}"
    print(f"Thread ID: {thread_id}")
    
    try:
        start = time.time()
        response = requests.get(
            f"http://localhost:8000/scene/{thread_id}",
            timeout=20
        )
        elapsed = time.time() - start
        
        if response.status_code == 200:
            data = response.json()
            objects = data.get('scene_objects', {})
            print(f"✅ SUCCESS in {elapsed:.2f}s")
            print(f"   Objects: {len(objects)}")
            
            # Check for process_id in diagnostics (if available)
            if 'diagnostics' in data:
                diag = data['diagnostics']
                if diag.get('process_id'):
                    print(f"   Process ID: {diag['process_id']}")
                    print(f"   Log: {diag.get('log_path', 'N/A')}")
            return True
        else:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        return False


def test_multiple_threads():
    """Test that multiple threads can use auto-managed Blender"""
    print("\n=== Test 2: Multiple threads ===")
    
    results = []
    for i in range(3):
        thread_id = f"test-multi-{i}-{int(time.time() * 1000)}"
        print(f"\nThread {i+1}: {thread_id}")
        
        try:
            response = requests.get(
                f"http://localhost:8000/scene/{thread_id}",
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"  ✅ SUCCESS: {len(data.get('scene_objects', {}))} objects")
                results.append(True)
            else:
                print(f"  ❌ FAILED: HTTP {response.status_code}")
                results.append(False)
                
        except Exception as e:
            print(f"  ❌ EXCEPTION: {e}")
            results.append(False)
        
        time.sleep(0.5)
    
    success_rate = sum(results) / len(results) if results else 0
    print(f"\nSuccess rate: {sum(results)}/{len(results)} ({success_rate:.0%})")
    
    return all(results)


def test_render():
    """Test render functionality in auto-managed mode"""
    print("\n=== Test 3: Render test ===")
    
    thread_id = f"test-render-{int(time.time() * 1000)}"
    print(f"Thread ID: {thread_id}")
    
    try:
        response = requests.get(
            f"http://localhost:8000/scene/{thread_id}/renders",
            timeout=25
        )
        
        if response.status_code == 200:
            data = response.json()
            renders = data.get('renders', [])
            print(f"✅ SUCCESS: {len(renders)} camera(s) rendered")
            for r in renders:
                print(f"   - {r.get('camera_name')}")
            return True
        else:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        return False


def check_blender_processes():
    """Check running Blender processes"""
    print("\n=== Blender Process Check ===")
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True
        )
        
        blender_lines = [
            line for line in result.stdout.split('\n')
            if 'blender' in line.lower() and 'background' in line.lower()
        ]
        
        print(f"Found {len(blender_lines)} Blender process(es):")
        for line in blender_lines[:5]:  # Show max 5
            parts = line.split()
            if len(parts) >= 2:
                print(f"   PID {parts[1]}: {' '.join(parts[10:13])}")
        
    except Exception as e:
        print(f"Could not check processes: {e}")


def main():
    print("=" * 70)
    print("Headless Auto-Managed Mode Test")
    print("=" * 70)
    print()
    
    # Check prerequisites
    if not check_api_health():
        print("❌ API server not running on http://localhost:8000")
        print("   Start with: ./scripts/start_services.sh")
        return 1
    
    print("✅ API server is running\n")
    
    # Run tests
    test1_ok = test_single_thread()
    test2_ok = test_multiple_threads()
    test3_ok = test_render()
    
    check_blender_processes()
    
    # Final summary
    print()
    print("=" * 70)
    print("Final Summary")
    print("=" * 70)
    
    tests = [
        ("Single thread auto-start", test1_ok),
        ("Multiple threads", test2_ok),
        ("Render functionality", test3_ok),
    ]
    
    for name, passed in tests:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
    
    all_passed = all(result for _, result in tests)
    
    print()
    if all_passed:
        print("🎉 All tests passed! Headless auto-managed mode is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Check the output above for details.")
        return 1


if __name__ == "__main__":
    exit(main())
