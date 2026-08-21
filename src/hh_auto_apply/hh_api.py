from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import json
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import Settings
from .storage import TokenStore


API_BASE = "https://api.hh.ru"
AUTH_URL = "https://hh.ru/oauth/authorize"
TOKEN_URL = "https://api.hh.ru/token"


class HHApiError(RuntimeError):
    def __init__(self, status: int, body: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HH API returned {status}: {body[:500]}")
        self.status = status
        self.body = body
        self.headers = headers or {}

    def error_value(self) -> str:
        try:
            data = json.loads(self.body)
        except json.JSONDecodeError:
            return ""
        errors = data.get("errors") or []
        if not errors:
            return ""
        return str(errors[0].get("value") or errors[0].get("type") or "")


@dataclass
class HHClient:
    settings: Settings
    token_store: TokenStore

    def authorization_url(self, state: str) -> str:
        return f"{AUTH_URL}?{urlencode({
            'response_type': 'code',
            'client_id': self.settings.client_id,
            'redirect_uri': self.settings.redirect_uri,
            'state': state,
        })}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
        }
        token = self._request_token(payload)
        self.token_store.save(token)
        return token

    def refresh_token(self) -> dict[str, Any]:
        current = self.token_store.load()
        refresh_token = current.get("refresh_token")
        if not refresh_token:
            raise SystemExit("No refresh_token found. Run `hh-auto-apply auth` first.")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
        }
        token = self._request_token(payload)
        self.token_store.save(token)
        return token

    def get(self, path: str, params: dict[str, Any] | None = None, auth: bool = True) -> dict[str, Any]:
        query = f"?{urlencode(params or {}, doseq=True)}" if params else ""
        return self._request_json("GET", f"{API_BASE}{path}{query}", auth=auth)

    def post_form(self, path_or_url: str, data: dict[str, Any], auth: bool = True) -> dict[str, Any] | None:
        url = path_or_url if path_or_url.startswith("http") else f"{API_BASE}{path_or_url}"
        return self._request_json("POST", url, data=urlencode(data).encode(), auth=auth)

    def vacancies(self, page: int) -> dict[str, Any]:
        params = dict(self.settings.search_params)
        params["page"] = page
        return self.get("/vacancies", params=params, auth=False)

    def vacancy(self, vacancy_id: str) -> dict[str, Any]:
        return self.get(f"/vacancies/{vacancy_id}", auth=True)

    def resumes_mine(self) -> list[dict[str, Any]]:
        data = self.get("/resumes/mine")
        return list(data.get("items", []))

    def apply(self, vacancy: dict[str, Any], resume_id: str, message: str) -> None:
        payload = {"vacancy_id": vacancy["id"], "resume_id": resume_id}
        if message:
            payload["message"] = message
        response_url = vacancy.get("response_url")
        path = response_url or "/negotiations"
        self.post_form(path, payload)

    def _request_token(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", TOKEN_URL, data=urlencode(payload).encode(), auth=False)

    def _request_json(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        auth: bool = True,
        retry_refresh: bool = True,
    ) -> dict[str, Any] | None:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.settings.user_agent,
        }
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if auth:
            token = self.token_store.load().get("access_token")
            if not token:
                raise SystemExit("No access_token found. Run `hh-auto-apply auth` first.")
            headers["Authorization"] = f"Bearer {token}"

        request = Request(url=url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                if response.status == HTTPStatus.NO_CONTENT:
                    return None
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if auth and retry_refresh and exc.code == HTTPStatus.UNAUTHORIZED:
                self.refresh_token()
                return self._request_json(method, url, data=data, auth=auth, retry_refresh=False)
            if exc.code == HTTPStatus.TOO_MANY_REQUESTS:
                retry_after = exc.headers.get("Retry-After")
                if retry_after and retry_after.isdigit() and retry_refresh:
                    time.sleep(min(int(retry_after), 60))
                    return self._request_json(method, url, data=data, auth=auth, retry_refresh=False)
            raise HHApiError(exc.code, body, dict(exc.headers)) from exc
