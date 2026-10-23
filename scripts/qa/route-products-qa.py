#!/usr/bin/env python3
"""Prepare API compose routing after QA products source checks; never restart containers."""
import argparse
import json
import os
from pathlib import Path
import tempfile


def apply_routing(document, routing):
    if routing.get("ProductsApi__Url") != "http://host.docker.internal:8015" or routing.get("ProductsApi__SourceEnvironment") != "qa" or not routing.get("ProductsApi__ApiKey"):
        raise ValueError("Products routing must target the isolated QA source with its own key")
    environment = document["services"]["data-concord-qa"]["environment"]
    if environment.get("ProductsApi__Url") not in ("http://host.docker.internal:8005", "http://host.docker.internal:8015"):
        raise ValueError("Unexpected current products URL; review runtime before rerouting")
    environment.pop("ProductsApi__Key", None)
    environment.update(routing)
    return document


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose", type=Path, default=Path("/private/gba-qa/qa-compose.private.json"))
    parser.add_argument("--routing", type=Path, default=Path("/private/gba-qa/products/api-routing.json"))
    args = parser.parse_args()
    value = apply_routing(json.loads(args.compose.read_text()), json.loads(args.routing.read_text()))
    fd, temporary = tempfile.mkstemp(dir=args.compose.parent, prefix=".products-routing-")
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, indent=2)
            file.write("\n")
        os.replace(temporary, args.compose)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Prepared isolated QA products routing; DEV and running API containers were not changed.")


if __name__ == "__main__":
    main()
