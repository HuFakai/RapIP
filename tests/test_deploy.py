"""Exercise deployment orchestration without Docker, supplier traffic or real credentials."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def deployment(tmp_path):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    for name in ("deploy.sh", "deployment_info.py"):
        shutil.copy(ROOT / "scripts" / name, project / "scripts" / name)
    for name in (".env.example", "compose.yaml"):
        shutil.copy(ROOT / name, project / name)
    binary = tmp_path / "bin"
    binary.mkdir()
    openssl = shutil.which("openssl")
    assert openssl
    docker = binary / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, subprocess, sys
from pathlib import Path
from dotenv import dotenv_values
args=sys.argv[1:]
root=Path.cwd()
with (root/'commands.jsonl').open('a') as stream:
    stream.write(json.dumps(args)+'\\n')
if args[:2] == ['compose','up'] and '--help' in args:
    print('--wait --wait-timeout')
elif args == ['ps','-q']:
    print('other-container')
elif args[:1] == ['inspect']:
    print('20000\\n20001')
elif args[:2] == ['compose','build']:
    sys.exit(int(os.getenv('MOCK_BUILD_FAILURE','0')))
elif args[:2] == ['compose','up']:
    sys.exit(int(os.getenv('MOCK_HEALTH_FAILURE','0')))
elif args[:2] == ['compose','exec']:
    values=dotenv_values(root/'.env')
    environment={**os.environ,**{k:v for k,v in values.items() if v is not None}}
    environment['RAPIP_DEPLOY_SERVER_HOST']='YOUR_SERVER_IP'
    sys.exit(subprocess.call([sys.executable,'scripts/deployment_info.py'],env=environment))
"""
    )
    scanner = binary / "ss"
    scanner.write_text("#!/bin/sh\nprintf 'LISTEN 0 128 0.0.0.0:20002 0.0.0.0:*\\n'\n")
    random = binary / "openssl"
    random.write_text(
        f"#!{sys.executable}\n"
        + f"REAL={openssl!r}\n"
        + """import os, sys
from pathlib import Path
if sys.argv[1:] == ['rand','-hex','2']:
    path=Path('random-port-counter')
    n=int(path.read_text()) if path.exists() else 0
    path.write_text(str(n+1))
    print(f'{n:04x}')
else:
    os.execv(REAL,[REAL,*sys.argv[1:]])
"""
    )
    git = binary / "git"
    git.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
from pathlib import Path
args=sys.argv[1:]
with Path('git-commands.jsonl').open('a') as stream:
    stream.write(json.dumps(args)+'\\n')
if args[:1] == ['diff'] and os.getenv('MOCK_GIT_DIRTY'):
    sys.exit(1)
if args[:1] == ['pull'] and os.getenv('MOCK_GIT_FAILURE'):
    sys.exit(1)
"""
    )
    for executable in binary.iterdir():
        executable.chmod(0o755)
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}

    def run(pull=False, **overrides):
        return subprocess.run(
            ["bash", "scripts/deploy.sh", *([] if pull else ["--no-pull"])],
            cwd=project,
            env={**env, **overrides},
            text=True,
            capture_output=True,
            timeout=20,
        )

    return project, run


def commands(project):
    path = project / "commands.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def existing_env(project):
    text = (project / ".env.example").read_text()
    for before, after in (
        ("replace-with-a-long-random-password", "'existing$()notexecuted'"),
        ("replace-with-a-different-random-password", "'existing:#$@proxy'"),
        ("replace-with-a-random-script-token", "existing-token"),
    ):
        text = text.replace(before, after)
    (project / ".env").write_text(text)
    return text


def test_first_deploy_generates_credentials_avoids_system_and_docker_ports(deployment):
    project, run = deployment
    result = run()
    assert result.returncode == 0, result.stderr
    values = dotenv_values(project / ".env")
    credentials = [
        values[key] for key in ("RAPIP_ADMIN_PASSWORD", "RAPIP_SOCKS_PASSWORD", "RAPIP_ADMIN_TOKEN")
    ]
    assert len(set(credentials)) == 3
    assert all(len(value) == 48 and value.isalnum() for value in credentials)
    assert [values[key] for key in ("RAPIP_HTTP_PORT", "RAPIP_SOCKS_PORT", "RAPIP_PROXY_HTTP_PORT")] == [
        "20003",
        "20004",
        "20005",
    ]
    assert (project / ".env").stat().st_mode & 0o777 == 0o600
    report = project / "data/DEPLOYMENT_ACCESS.txt"
    assert report.stat().st_mode & 0o777 == 0o600
    assert "http://127.0.0.1:20003" in report.read_text()
    assert "ssh -N -L 20003:127.0.0.1:20003" in result.stdout
    assert "# 管理页面登录密码" in (project / ".env").read_text()
    assert not (project / ".rapip-deploy.lock").exists()
    calls = commands(project)
    assert calls.index(["compose", "build", "gateway"]) < next(
        i for i, command in enumerate(calls) if command[:3] == ["compose", "up", "-d"]
    )


def test_update_preserves_credentials_ports_and_data(deployment):
    project, run = deployment
    original = existing_env(project)
    (project / "data").mkdir()
    (project / "data/gateway.sqlite3").write_text("existing-state")
    (project / "config").mkdir()
    (project / "config/accounts.json").write_text("[]")
    result = run()
    assert result.returncode == 0, result.stderr
    assert (project / ".env").read_text() == original
    assert (project / "data/gateway.sqlite3").read_text() == "existing-state"
    assert (project / "config/accounts.json").read_text() == "[]"
    assert not (project / "random-port-counter").exists()
    assert "更新/重建" in result.stdout
    assert "existing:#$@proxy" in result.stdout
    assert "existing%3A%23%24%40proxy@" in result.stdout


@pytest.mark.parametrize("failure", ["MOCK_BUILD_FAILURE", "MOCK_HEALTH_FAILURE"])
def test_failed_build_or_health_never_reports_success_and_keeps_config(deployment, failure):
    project, run = deployment
    result = run(**{failure: "1"})
    assert result.returncode != 0
    assert "部署/更新成功" not in result.stdout
    assert (project / ".env").exists()
    assert not (project / "data/DEPLOYMENT_ACCESS.txt").exists()
    assert not (project / ".rapip-deploy.lock").exists()
    assert not any(command[:2] == ["compose", "down"] for command in commands(project))
    if failure == "MOCK_BUILD_FAILURE":
        assert not any(command[:3] == ["compose", "up", "-d"] for command in commands(project))


def test_retry_after_failed_first_deploy_reuses_generated_config(deployment):
    project, run = deployment
    assert run(MOCK_BUILD_FAILURE="1").returncode != 0
    original = (project / ".env").read_bytes()
    assert run().returncode == 0
    assert (project / ".env").read_bytes() == original


def test_missing_env_with_existing_data_requires_recovery(deployment):
    project, run = deployment
    (project / "data").mkdir()
    (project / "data/gateway.sqlite3").write_text("do-not-overwrite")
    result = run()
    assert result.returncode != 0
    assert "恢复原 .env" in result.stderr
    assert not (project / ".env").exists()
    assert (project / "data/gateway.sqlite3").read_text() == "do-not-overwrite"


def test_concurrent_deploy_is_rejected_without_removing_other_lock(deployment):
    project, run = deployment
    (project / ".rapip-deploy.lock").mkdir()
    result = run()
    assert result.returncode != 0
    assert "另一个部署" in result.stderr
    assert (project / ".rapip-deploy.lock").is_dir()
    assert not commands(project)


def test_template_credentials_are_rejected_before_build(deployment):
    project, run = deployment
    shutil.copy(project / ".env.example", project / ".env")
    result = run()
    assert result.returncode != 0
    assert "仍为示例值" in result.stderr
    assert ["compose", "build", "gateway"] not in commands(project)


def test_default_update_pulls_once_then_reexecutes_current_script(deployment):
    project, run = deployment
    original = existing_env(project)
    result = run(pull=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in (project / "git-commands.jsonl").read_text().splitlines()]
    assert calls.count(["pull", "--ff-only"]) == 1
    assert (project / ".env").read_text() == original
    assert not (project / ".rapip-deploy.lock").exists()


@pytest.mark.parametrize("failure", ["MOCK_GIT_DIRTY", "MOCK_GIT_FAILURE"])
def test_failed_git_update_does_not_build_or_restart(deployment, failure):
    project, run = deployment
    original = existing_env(project)
    result = run(pull=True, **{failure: "1"})
    assert result.returncode != 0
    assert (project / ".env").read_text() == original
    assert ["compose", "build", "gateway"] not in commands(project)
    assert not any(command[:3] == ["compose", "up", "-d"] for command in commands(project))
    assert not (project / ".rapip-deploy.lock").exists()


def test_deployment_output_uses_persisted_credentials_without_old_admin_password(deployment):
    project, run = deployment
    existing_env(project)
    (project / "config").mkdir()
    saved = {
        "admin": {"username": "owner", "password_hash": "fixture-hash-not-for-login"},
        "proxy": {"username": "rapip", "password": "fixture-new-proxy-password"},
    }
    path = project / "config/credentials.json"
    path.write_text(json.dumps(saved))
    result = run()
    assert result.returncode == 0, result.stderr
    assert "管理员用户名：owner" in result.stdout
    assert "密码不回显" in result.stdout
    assert "existing$()notexecuted" not in result.stdout
    assert "fixture-hash-not-for-login" not in result.stdout
    assert "rapip-worker-01-session-first" in result.stdout
    assert "fixture-new-proxy-password" in result.stdout
    assert json.loads(path.read_text()) == saved
