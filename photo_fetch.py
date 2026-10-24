"""
Topic photo fetching for photocards, via Pixabay and Unsplash (free tiers,
keys from environment variables only - never hardcoded).

Follows both APIs' usage rules:
- Unsplash: triggers the required GET /photos/:id/download tracking ping at
  the moment a photo is actually used (per Unsplash API Guidelines), and
  requires attribution ("Photo by {photographer} on Unsplash").
- Pixabay: no attribution legally required under the Pixabay Content
  License, but photographer/URL are still recorded for our own records.

Every fetched photo's source, photographer, original page URL, and license
note are returned alongside the local downloaded file path, for storage in
captions/metadata files.
"""
import os
import time

import requests
import truststore

truststore.inject_into_ssl()


class PhotoFetchError(Exception):
    pass


def _get(url, **kwargs):
    resp = requests.get(url, timeout=20, **kwargs)
    resp.raise_for_status()
    return resp


def fetch_pixabay(query, orientation="vertical"):
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return None
    resp = _get(
        "https://pixabay.com/api/",
        params={
            "key": api_key, "q": query, "image_type": "photo",
            "orientation": orientation, "per_page": 20, "safesearch": "true",
            "min_width": 1200,
        },
    )
    hits = resp.json().get("hits", [])
    if not hits:
        return None
    h = hits[0]
    return {
        "source": "pixabay",
        "id": str(h["id"]),
        "photographer": h.get("user", ""),
        "source_url": h.get("pageURL", ""),
        "image_url": h.get("largeImageURL", ""),
        "license_note": "Pixabay Content License - free for commercial use, no attribution legally required "
                         "(pixabay.com/service/license-summary).",
    }


def fetch_unsplash(query, orientation="portrait"):
    api_key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not api_key:
        return None
    resp = _get(
        "https://api.unsplash.com/search/photos",
        headers={"Authorization": f"Client-ID {api_key}"},
        params={"query": query, "orientation": orientation, "per_page": 5},
    )
    results = resp.json().get("results", [])
    if not results:
        return None
    r = results[0]
    photo_id = r["id"]

    # Required by the Unsplash API Guidelines: ping the download endpoint
    # the moment a photo is actually used, not just viewed in search results.
    try:
        _get(f"https://api.unsplash.com/photos/{photo_id}/download",
             headers={"Authorization": f"Client-ID {api_key}"})
    except requests.RequestException:
        pass  # non-fatal - the photo can still be used, just log/ignore

    return {
        "source": "unsplash",
        "id": photo_id,
        "photographer": (r.get("user") or {}).get("name", ""),
        "source_url": (r.get("links") or {}).get("html", ""),
        "image_url": (r.get("urls") or {}).get("regular", ""),
        "license_note": "Unsplash License - free for commercial use. Attribution required: "
                         f"\"Photo by {(r.get('user') or {}).get('name', '')} on Unsplash\" "
                         "(unsplash.com/license).",
    }


def fetch_topic_photo(query, orientation, out_dir, filename, prefer=("unsplash", "pixabay")):
    """Tries sources in `prefer` order, downloads the first hit to
    out_dir/filename, and returns the candidate dict plus local_path."""
    fetchers = {"unsplash": fetch_unsplash, "pixabay": fetch_pixabay}
    last_error = None
    for source in prefer:
        try:
            candidate = fetchers[source](query, orientation=orientation)
        except requests.RequestException as exc:
            last_error = exc
            continue
        if candidate:
            os.makedirs(out_dir, exist_ok=True)
            local_path = os.path.join(out_dir, filename)
            img_resp = _get(candidate["image_url"])
            with open(local_path, "wb") as f:
                f.write(img_resp.content)
            candidate["local_path"] = local_path
            candidate["query"] = query
            return candidate
    if last_error:
        raise PhotoFetchError(f"No photo found for '{query}': {last_error}")
    raise PhotoFetchError(f"No photo found for '{query}' (no sources configured or no results).")
