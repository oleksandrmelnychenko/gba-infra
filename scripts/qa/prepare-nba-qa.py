#!/usr/bin/env python3
"""Prepare reviewed QA NBA config; never start services, reroute APIs, or copy data."""
import argparse
import json
import os
from pathlib import Path
import secrets


def qa_settings(source, key):
    required = ("DB_HOST", "DB_USER", "DB_PASSWORD", "MONGO_URI")
    if any(not source.get(name) for name in required):
        raise ValueError("DEV NBA connection settings are incomplete")
    if source["DB_USER"].lower() == "sa":
        raise ValueError("NBA requires a read-only SQL identity")
    return {
        **{name: source[name] for name in required},
        "DB_PORT": source.get("DB_PORT", "1433"),
        "DB_NAME": "ConcordDb_V5_QA", "MONGO_DB": "gba_nba_qa",
        "SOURCE_ENVIRONMENT": "qa", "API_PORT": "8012",
        "INTERNAL_API_KEY": key,
        "GENERATION_ENABLED": "false", "WARMERS_ENABLED": "false",
        "SOURCE_HISTORY_START_DATE": source.get("SOURCE_HISTORY_START_DATE", "2025-01-01"),
        "RECO_URL": "http://127.0.0.1:9", "RECO_API_KEY": "",
        "PROCURE_URL": "http://127.0.0.1:9",
    }


def write_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, default=Path("/root/projects/gba-nba/.env"))
    parser.add_argument("--output", type=Path, default=Path("/private/gba-qa/nba"))
    args = parser.parse_args()
    # Run with gba-nba/.venv/bin/python, which already provides python-dotenv.
    from dotenv import dotenv_values
    source = dict(dotenv_values(args.source_env))
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.chmod(0o700)
    key_file = args.output / "api-key"
    key = key_file.read_text().strip() if key_file.exists() else secrets.token_urlsafe(48)
    settings = qa_settings(source, key)
    # systemd EnvironmentFile and python-dotenv both accept these double-quoted values.
    def quote(value):
        if "\n" in value or "\r" in value:
            raise ValueError("Multiline environment values are unsupported")
        return json.dumps(str(value), ensure_ascii=False)
    write_private(args.output / "qa.env", "\n".join(f"{name}={quote(value)}" for name, value in settings.items()) + "\n")
    write_private(key_file, key + "\n")
    write_private(args.output / "api-routing.json", json.dumps({"GbaNbaApi__Url": "http://host.docker.internal:8012", "GbaNbaApi__ApiKey": key, "GbaNbaApi__SourceEnvironment": "qa"}))
    user = settings["DB_USER"].replace("'", "''")
    identifier = settings["DB_USER"].replace("]", "]]")
    sql = f"""USE [ConcordDb_V5_QA];
IF SUSER_ID(N'{user}') IS NULL THROW 51000, 'Existing NBA readonly login is required', 1;
IF USER_ID(N'{user}') IS NULL CREATE USER [{identifier}] FOR LOGIN [{identifier}];
IF IS_ROLEMEMBER(N'db_datareader', N'{user}') <> 1 ALTER ROLE [db_datareader] ADD MEMBER [{identifier}];
SELECT DB_NAME() AS DatabaseName, IS_ROLEMEMBER(N'db_datareader', N'{user}') AS IsReader;
"""
    write_private(args.output / "grant-qa-reader.sql", sql)
    print(json.dumps({"prepared": str(args.output), "sql": "ConcordDb_V5_QA", "mongo": "gba_nba_qa", "port": 8012, "generationEnabled": False, "runtimeChanged": False}))


if __name__ == "__main__":
    main()
