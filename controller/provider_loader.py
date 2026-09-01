from __future__ import annotations

import os

from .execution_bridge import ExecutionBroker, HttpJsonWorkerAdapter, ProviderConfig


def _env_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_broker_from_environment(prefix: str = "RVSC_PROVIDER_") -> ExecutionBroker:
    """Build a broker from a single approved provider configured by environment.

    Required:
      <prefix>NAME
      <prefix>ENDPOINT

    Optional:
      <prefix>TOKEN_ENV
      <prefix>TIMEOUT_SECONDS
      <prefix>REQUIRE_HEALTHCHECK

    Credentials remain outside repository configuration. Health preflight can be
    required for observable worker services so Controller dispatch fails closed
    before sending a mission to an unavailable or unready worker.
    """

    name = os.environ.get(f"{prefix}NAME", "").strip()
    endpoint = os.environ.get(f"{prefix}ENDPOINT", "").strip()
    token_env = os.environ.get(f"{prefix}TOKEN_ENV", "").strip() or None
    timeout_raw = os.environ.get(f"{prefix}TIMEOUT_SECONDS", "120").strip()
    require_healthcheck = _env_bool(os.environ.get(f"{prefix}REQUIRE_HEALTHCHECK", "false"))

    if not name:
        raise ValueError(f"missing {prefix}NAME")
    if not endpoint:
        raise ValueError(f"missing {prefix}ENDPOINT")
    try:
        timeout = int(timeout_raw)
    except ValueError as exc:
        raise ValueError(f"invalid {prefix}TIMEOUT_SECONDS") from exc

    broker = ExecutionBroker()
    broker.register(
        HttpJsonWorkerAdapter(
            ProviderConfig(
                name=name,
                endpoint=endpoint,
                token_env=token_env,
                timeout_seconds=timeout,
                require_healthcheck=require_healthcheck,
            )
        )
    )
    return broker
