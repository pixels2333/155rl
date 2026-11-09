#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Query / Set Transform/RectTransform data from RuntimeUnityEditor HTTP API.

Query:
    python query_transform.py <GameObjectName> [port] [--recursive] [--depth N] [--include-inactive]
    python query_transform.py Canvas 9876 -r -d 2
    python query_transform.py Canvas/Button 9876

Set (form-urlencoded or json body):
    python query_transform.py --set <GameObjectName> [port] [key=value ...]
    python query_transform.py --set Button 9876 transform.position.x=100 rectTransform.sizeDelta.x=200
    python query_transform.py --set Canvas/Panel/Button 9876 localPosition.x=50 active=false
    python query_transform.py --set Canvas/Panel 9876 --json '{"rectTransform":{"sizeDelta":{"x":400,"y":300}}}'
"""

import sys
import json
import urllib.request
import urllib.error
import urllib.parse
import argparse


def query_transform(base_url, object_name, recursive=False, depth=None, include_inactive=False):
    params = {"name": object_name}
    if recursive:
        params["recursive"] = "true"
    if depth is not None:
        params["depth"] = str(depth)
    if include_inactive:
        params["includeInactive"] = "true"

    query_str = urllib.parse.urlencode(params)
    url = f"{base_url}/api/transform?{query_str}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return data
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        print(f"Connection failed: {e.reason}")
    except Exception as e:
        print(f"Request failed: {e}")
    return None


def set_transform(base_url, object_name, fields=None, json_body=None):
    url = f"{base_url}/api/transform/set?name={urllib.parse.quote(object_name)}"
    if json_body:
        data_bytes = json_body.encode("utf-8")
        content_type = "application/json"
    else:
        body = urllib.parse.urlencode(fields or {})
        data_bytes = body.encode("utf-8")
        content_type = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=data_bytes, method="POST",
                                  headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return data
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.reason}")
        try:
            err_content = e.read().decode("utf-8")
            print(err_content)
        except Exception:
            pass
    except urllib.error.URLError as e:
        print(f"Connection failed: {e.reason}")
    except Exception as e:
        print(f"Request failed: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(description="Query / Set Unity Transform data via HTTP API")
    parser.add_argument("name", nargs="?", help="GameObject name or hierarchy path (e.g. Canvas/Button)")
    parser.add_argument("port", nargs="?", default="9876", help="HTTP API port (default: 9876)")
    parser.add_argument("-r", "--recursive", action="store_true", help="Query children hierarchy recursively")
    parser.add_argument("-d", "--depth", type=int, default=None, help="Recursion depth limit (default: 1 when -r is set, -1 for unlimited)")
    parser.add_argument("-i", "--include-inactive", action="store_true", help="Include inactive/hidden child GameObjects")
    parser.add_argument("--set", action="store_true", help="Set transform attributes (key=value pairs in subsequent arguments)")
    parser.add_argument("--json", dest="json_body", default=None, help="JSON body string for --set mode")
    parser.add_argument("fields", nargs="*", help="key=value pairs for --set mode (e.g. transform.position.x=100)")

    args, unknown = parser.parse_known_args()

    all_extra = args.fields + unknown

    if not args.name:
        parser.print_help()
        sys.exit(1)

    port = args.port
    if "=" in port or port.startswith("-"):
        all_extra.insert(0, port)
        port = "9876"

    base_url = f"http://localhost:{port}"

    if args.set:
        if args.json_body:
            set_transform(base_url, args.name, json_body=args.json_body)
        else:
            fields = {}
            for item in all_extra:
                if "=" in item:
                    k, v = item.split("=", 1)
                    fields[k] = v
            set_transform(base_url, args.name, fields=fields)
    else:
        query_transform(base_url, args.name, recursive=args.recursive, depth=args.depth, include_inactive=args.include_inactive)


if __name__ == "__main__":
    main()
