#!/usr/bin/env python3
"""Prepare private QA products config from running, read-only NBA SQL credentials.

No service starts, API routing, SQL writes, sync or cache warming occur here.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def qa_settings(products, readonly, key):
    required = ("db_host", "db_user", "db_password")
    if any(not readonly.get(name) for name in required) or readonly.get("db_user", "").lower() == "sa":
        raise ValueError("Existing read-only NBA SQL credentials are required")
    if not key or key == products.get("internal_api_key"):
        raise ValueError("QA products requires a distinct private API key")
    result = {name.upper(): str(value).lower() if isinstance(value, bool) else str(value)
              for name, value in products.items() if not isinstance(value, (dict, list, set, frozenset))}
    result.update({name.upper(): readonly[name] for name in required})
    result.update({
        "DB_PORT": str(readonly.get("db_port", 1433)),
        "DB_NAME": "ConcordDb_V5_QA", "DB_POOL_SIZE": "3", "DB_MAX_OVERFLOW": "2",
        "SOURCE_ENVIRONMENT": "qa", "CACHE_NAMESPACE": "products-qa", "REDIS_DB": "12",
        "API_PORT": "8015", "INTERNAL_API_KEY": key, "WARMERS_ENABLED": "false",
        "CORS_ALLOW_ORIGINS": "[]",
    })
    return result


def write_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nba-repo", type=Path, default=Path("/root/projects/gba-nba"))
    parser.add_argument("--products-runtime-repo", type=Path, default=Path("/root/projects/gba-products"))
    parser.add_argument("--products-repo", type=Path, default=Path("/root/projects/gba-ai-services/gba-products"))
    parser.add_argument("--output", type=Path, default=Path("/private/gba-qa/products"))
    args = parser.parse_args()
    runtime = load_module("nba_runtime", Path(__file__).with_name("verify-nba-isolation.py"))
    nba_type = load_module("nba_config", args.nba_repo / "app/core/config.py").Settings
    product_type = load_module("products_config", args.products_runtime_repo / "app/core/config.py").Settings
    _, nba = runtime.runtime_settings("gba-nba-qa.service", 8012, nba_type)
    _, products = runtime.runtime_settings("gba-products.service", 8005, product_type)
    if nba.db_name != "ConcordDb_V5_QA":
        raise ValueError("QA NBA must already use the QA database")
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.chmod(0o700)
    key_file = args.output / "api-key"
    key = key_file.read_text().strip() if key_file.exists() else secrets.token_urlsafe(48)
    settings = qa_settings(products.model_dump(), nba.model_dump(), key)
    def quote(value):
        value = str(value)
        if "\n" in value or "\r" in value:
            raise ValueError("Multiline environment values are unsupported")
        return json.dumps(value, ensure_ascii=False)
    write_private(args.output / "qa.env", "\n".join(f"{name}={quote(value)}" for name, value in settings.items()) + "\n")
    write_private(key_file, key + "\n")
    write_private(args.output / "api-routing.json", json.dumps({
        "ProductsApi__Url": "http://host.docker.internal:8015", "ProductsApi__ApiKey": key,
        "ProductsApi__SourceEnvironment": "qa",
    }))
    service = Path(__file__).with_name("gba-products-qa.service").read_text().replace(
        "WorkingDirectory=/root/projects/gba-ai-services/gba-products",
        f"WorkingDirectory={args.products_repo.resolve()}")
    write_private(args.output / "gba-products-qa.service", service)
    print(json.dumps({"prepared": str(args.output), "sql": "ConcordDb_V5_QA", "redisDb": 12,
                      "cacheNamespace": "products-qa", "port": 8015, "warmersEnabled": False,
                      "runtimeChanged": False}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Settings/connection exceptions may contain credentials. Never print their text.
        print(json.dumps({"prepared": False, "errorType": type(error).__name__}))
        raise SystemExit(1)
