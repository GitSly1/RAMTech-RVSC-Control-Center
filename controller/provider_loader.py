from __future__ import annotations

import os

from .execution_bridge import ExecutionBroker, HttpJsonWorkerAdapter, ProviderConfig


def build_broker_from_environment(prefix: str = "RVSC_PROVIDER_") -> ExecutionBroker:
    """Build a broker from a single approved provider configured by environment.

    Required:
      <prefix>NAME
      <prefix>ENDPOINT

    Optional:
      <prefix>TOKEN_ENV  (name of env var containing bearer token)
      <prefix>TIMEOUT_SECONDS

    This keeps provider credentials out of repository configuration while allowing
    the Controller to select/runtime-configure an execution provider dynamically.
    """

    name = os.environ.get(f"{prefix}NAME", "").strip()
    endpoint = os.environ.get(f"{prefix}ENDPOINT", "").strip()
    token_env = os.environ.get(f"{prefix}TOKEN_ENV", "").strip() or None
    timeout_raw = os.environ.get(f"{prefix}TIMEOUT_SECONDS", "120").strip()

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
            )
        )
    )
    return broker
