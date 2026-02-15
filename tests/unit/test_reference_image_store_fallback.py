from scene_agent.memory.reference_image_store import ReferenceImageStore


def test_reference_image_store_uses_in_memory_fallback():
    store = ReferenceImageStore(redis_url="invalid://redis-url", key_prefix="sa")
    store.add_images(
        thread_id="thread-store-1",
        images=[
            {
                "id": "img-1",
                "thread_id": "thread-store-1",
                "filename": "a.png",
                "content_type": "image/png",
                "size_bytes": "10",
                "sha256": "abc",
                "stored_path": "/tmp/a.png",
                "uploaded_at": "2026-01-01T00:00:00",
            }
        ],
    )

    images = store.list_images("thread-store-1")
    assert len(images) == 1
    assert images[0]["id"] == "img-1"
