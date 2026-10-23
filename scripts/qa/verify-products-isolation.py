#!/usr/bin/env python3
"""Read source identity, SQL permissions and API routing; temporary Redis probes only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4


def runtime_helper():
    spec = importlib.util.spec_from_file_location("nba_runtime", Path(__file__).with_name("verify-nba-isolation.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_proxy_environment(environment, expected, port, key):
    if environment.get("ProductsApi__Url") != f"http://host.docker.internal:{port}":
        raise ValueError("API products URL differs from its isolated runtime")
    if environment.get("ProductsApi__SourceEnvironment") != expected:
        raise ValueError("API products expected source differs from runtime")
    configured = environment.get("ProductsApi__ApiKey", "")
    if not configured or not secrets.compare_digest(configured, key) or "ProductsApi__Key" in environment:
        raise ValueError("API products key is missing, obsolete or differs from runtime")
    return {"urlMatches": True, "sourceEnvironmentMatches": True, "apiKeyMatches": True}


def redis_probe(dev, qa):
    if dev.connection_pool.connection_kwargs.get("db") != 2 or qa.connection_pool.connection_kwargs.get("db") != 12:
        raise ValueError("Redis probe requires isolated products databases")
    key = "__products_isolation_probe_" + uuid4().hex
    created = []
    try:
        for client, value in ((dev, "dev"), (qa, "qa")):
            if not client.set(key, value, ex=60, nx=True):
                raise RuntimeError("Redis probe key unexpectedly exists")
            created.append(client)
        qa.set(key, "qa-updated", ex=60)
        if dev.get(key) != "dev" or qa.get(key) != "qa-updated":
            raise RuntimeError("Redis database isolation probe failed")
    finally:
        for client in created:
            client.delete(key)
    if dev.exists(key) or qa.exists(key):
        raise RuntimeError("Redis probe cleanup failed")
    return {"independentValues": True, "independentMutation": True, "fixturesRemoved": True}


def verify(products_repo, qa_only=False, check_api=False):
    sys.path.insert(0, str(products_repo))
    from app.core.config import Settings
    import httpx
    import redis
    from sqlalchemy import create_engine, text

    helper = runtime_helper()
    configs, result, clients = {}, {}, []
    try:
        for environment, service, port, database, cache_db, namespace in (
            ("dev", "gba-products.service", 8005, "ConcordDb_V5", 2, "products"),
            ("qa", "gba-products-qa.service", 8015, "ConcordDb_V5_QA", 12, "products-qa"),
        ):
            pid, config = helper.runtime_settings(service, port, Settings)
            configs[environment] = config
            if (config.source_environment, config.db_name, config.redis_db, config.cache_namespace) != (environment, database, cache_db, namespace):
                raise RuntimeError("Running products source does not match expected isolation")
            result[environment] = {"pid": pid, "port": port, "database": database,
                                   "redisDb": cache_db, "cacheNamespace": namespace, "lineageVerified": False}
            if environment == "dev" and qa_only:
                continue
            headers = {"X-Internal-Api-Key": config.internal_api_key, "X-GBA-Source-Environment": environment}
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=30) as api:
                response = api.get("/source")
                response.raise_for_status()
                provenance = response.json()
                if (response.headers.get("X-GBA-Source-Environment"), provenance.get("sourceEnvironment"),
                    provenance.get("database"), provenance.get("redisDb"), provenance.get("cacheNamespace")) != (
                    environment, environment, database, cache_db, namespace):
                    raise RuntimeError("Products response lineage does not match its actual source")
                opposite = "qa" if environment == "dev" else "dev"
                denied = api.get("/product/not-a-number", headers={"X-GBA-Source-Environment": opposite})
                if denied.status_code != 409 or denied.json().get("detail") != "source_environment_mismatch":
                    raise RuntimeError("Products source fence did not reject before endpoint validation")
                if environment == "qa" and (config.warmers_enabled or provenance.get("warmersEnabled") is not False):
                    raise RuntimeError("QA products warmers must remain disabled")
            engine = create_engine(config.sqlalchemy_url, connect_args={"timeout": 15, "login_timeout": 10})
            try:
                with engine.connect() as connection:
                    actual = connection.execute(text("SELECT DB_NAME() AS db, IS_ROLEMEMBER('db_datareader') AS is_reader, HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT') AS can_insert, HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE') AS can_update, HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE') AS can_delete")).mappings().one()
                    if actual["db"] != database or (environment == "qa" and (actual["is_reader"] != 1 or any(actual[key] != 0 for key in ("can_insert", "can_update", "can_delete")))):
                        raise RuntimeError("Products SQL identity is incorrect or QA is writable")
            finally:
                engine.dispose()
            result[environment].update({"lineageVerified": True, "source": provenance,
                                       "wrongEnvironmentRejected": True, "sql": dict(actual)})
            if check_api:
                container = helper.api_container(environment)
                values = json.loads(subprocess.check_output(["docker", "inspect", "--format", "{{json .Config.Env}}", container], text=True))
                environment_vars = dict(value.split("=", 1) for value in values if "=" in value)
                result[environment]["apiConfiguration"] = validate_proxy_environment(environment_vars, environment, port, config.internal_api_key)
        if result["dev"]["pid"] == result["qa"]["pid"] or configs["dev"].internal_api_key == configs["qa"].internal_api_key:
            raise RuntimeError("Products environments must have independent processes and API keys")
        for environment in ("dev", "qa"):
            cfg = configs[environment]
            clients.append(redis.Redis(host=cfg.redis_host, port=cfg.redis_port, db=cfg.redis_db,
                decode_responses=True, socket_connect_timeout=5, socket_timeout=5))
        result["redisProbe"] = redis_probe(*clients)
        result["apiRoutingVerified"] = check_api and not qa_only
        result["devLineageVerified"] = not qa_only
        return result
    finally:
        for client in clients:
            client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products-repo", type=Path, default=Path("/root/projects/gba-ai-services/gba-products"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qa-only", action="store_true", help="Pre-release QA check; DEV lineage is explicitly unverified")
    parser.add_argument("--check-api", action="store_true", help="Also check running API routing after release")
    args = parser.parse_args()
    try:
        if args.qa_only and args.check_api:
            raise ValueError("Full API routing acceptance requires both environments")
        result = verify(args.products_repo, qa_only=args.qa_only, check_api=args.check_api)
    except Exception as error:
        print(json.dumps({"verified": False, "errorType": type(error).__name__}))
        return 1
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(result, file, indent=2)
    args.output.chmod(0o600)
    print(json.dumps({"verified": True, "apiRoutingVerified": result["apiRoutingVerified"],
                      "devLineageVerified": result["devLineageVerified"], "evidence": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
