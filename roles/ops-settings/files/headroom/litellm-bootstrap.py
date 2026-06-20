#!/usr/bin/env python3

import os
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import litellm
from litellm.caching.caching import Cache
from litellm.proxy.proxy_cli import run_server
from litellm.types.caching import LiteLLMCacheType


def wait_for_qdrant(url: str, attempts: int = 30) -> None:
    health_urls = (f"{url}/readyz", f"{url}/healthz", f"{url}/collections")
    last_error = None
    for _ in range(attempts):
        for health_url in health_urls:
            try:
                with urlopen(health_url, timeout=2) as response:
                    if response.status < 500:
                        return
            except URLError as exc:
                last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Qdrant did not become ready: {last_error}")


def configure_cache() -> None:
    qdrant_url = os.environ.get("QDRANT_URL", "http://qdrant:6333")
    collection_name = os.environ.get(
        "QDRANT_COLLECTION_NAME", "litellm_semantic_cache"
    )
    similarity_threshold = float(
        os.environ.get("QDRANT_SIMILARITY_THRESHOLD", "0.80")
    )
    embedding_model = os.environ.get(
        "QDRANT_EMBEDDING_MODEL", "openai/text-embedding-3-small"
    )
    vector_size = int(os.environ.get("QDRANT_VECTOR_SIZE", "1536"))
    qdrant_api_key = os.environ.get("QDRANT_API_KEY") or None

    wait_for_qdrant(qdrant_url)

    litellm.cache = Cache(
        type=LiteLLMCacheType.QDRANT_SEMANTIC,
        qdrant_api_base=qdrant_url,
        qdrant_api_key=qdrant_api_key,
        qdrant_collection_name=collection_name,
        similarity_threshold=similarity_threshold,
        qdrant_semantic_cache_embedding_model=embedding_model,
        qdrant_semantic_cache_vector_size=vector_size,
    )


def main() -> None:
    configure_cache()
    run_server.main(["--local", "--host", "0.0.0.0", "--port", "4000"], standalone_mode=False)


if __name__ == "__main__":
    sys.exit(main())
