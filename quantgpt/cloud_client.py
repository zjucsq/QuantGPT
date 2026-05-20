"""QuantGPT Cloud API client.

Uploads locally-computed factor values to quant-gpt.com for independent
validation, track-record tracking, and public attestation.
"""

import logging
import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_ENV_API_KEY = "QUANTGPT_CLOUD_API_KEY"
_ENV_BASE_URL = "QUANTGPT_CLOUD_URL"
_DEFAULT_BASE_URL = "https://quant-gpt.com"
_UPLOAD_BATCH_SIZE = 500
_VALID_UNIVERSES = {"hs300", "csi500", "csi1000"}


def is_configured() -> bool:
    return bool(os.environ.get(_ENV_API_KEY))


def get_cloud_url() -> str:
    return os.environ.get(_ENV_BASE_URL, _DEFAULT_BASE_URL).rstrip("/")


class CloudClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.environ.get(_ENV_API_KEY, "")
        self.base_url = (base_url or get_cloud_url()).rstrip("/")
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.trust_env = False
            retry = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
            adapter = HTTPAdapter(max_retries=retry)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
            self._session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "QuantGPT/1.0",
            })
        return self._session

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _raise_for_error(self, resp: requests.Response, action: str) -> None:
        if resp.ok:
            return
        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        raise CloudAPIError(f"{action} failed (HTTP {resp.status_code}): {detail}")

    def create_factor(
        self,
        name: str,
        universe: str,
        expression: str | None = None,
        description: str | None = None,
        claimed_ic_mean: float | None = None,
        claimed_ic_ir: float | None = None,
    ) -> dict:
        payload: dict = {"name": name, "universe": universe}
        if expression:
            payload["expression"] = expression
        if description:
            payload["description"] = description
        if claimed_ic_mean is not None:
            payload["claimed_ic_mean"] = claimed_ic_mean
        if claimed_ic_ir is not None:
            payload["claimed_ic_ir"] = claimed_ic_ir

        resp = self._get_session().post(self._url("/api/v1/factors"), json=payload)
        self._raise_for_error(resp, "create_factor")
        return resp.json()

    def upload_values(self, factor_id: str, data: list[dict]) -> dict:
        resp = self._get_session().post(
            self._url(f"/api/v1/factors/{factor_id}/values"),
            json={"data": data},
        )
        self._raise_for_error(resp, "upload_values")
        return resp.json()

    def upload_values_batched(self, factor_id: str, data: list[dict]) -> dict:
        total_uploaded = 0
        for i in range(0, len(data), _UPLOAD_BATCH_SIZE):
            batch = data[i : i + _UPLOAD_BATCH_SIZE]
            result = self.upload_values(factor_id, batch)
            total_uploaded += result.get("uploaded", len(batch))
        return {"uploaded": total_uploaded, "batches": (len(data) + _UPLOAD_BATCH_SIZE - 1) // _UPLOAD_BATCH_SIZE}

    def submit_factor(self, factor_id: str) -> dict:
        resp = self._get_session().post(self._url(f"/api/v1/factors/{factor_id}/submit"))
        self._raise_for_error(resp, "submit_factor")
        return resp.json()

    def get_factor(self, factor_id: str) -> dict:
        resp = self._get_session().get(self._url(f"/api/v1/factors/{factor_id}"))
        self._raise_for_error(resp, "get_factor")
        return resp.json()

    def upload_and_validate(
        self,
        name: str,
        universe: str,
        factor_values_data: list[dict],
        expression: str | None = None,
        claimed_ic_mean: float | None = None,
        claimed_ic_ir: float | None = None,
    ) -> dict:
        factor = self.create_factor(
            name=name,
            universe=universe,
            expression=expression,
            claimed_ic_mean=claimed_ic_mean,
            claimed_ic_ir=claimed_ic_ir,
        )
        factor_id = factor["id"]
        logger.info("Created cloud factor %s (%s)", factor_id, name)

        upload_result = self.upload_values_batched(factor_id, factor_values_data)
        logger.info(
            "Uploaded %d days in %d batches",
            upload_result["uploaded"],
            upload_result["batches"],
        )

        result = self.submit_factor(factor_id)
        logger.info("Cloud validation: status=%s", result.get("status"))
        return result


class CloudAPIError(Exception):
    pass
