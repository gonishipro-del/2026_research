"""決算プロ（ke.kabupro.jp）への HTTP アクセスを担当するモジュール。

サイトは Shift_JIS かつ HTTPS 証明書が不正なため HTTP でアクセスする。
サーバに負荷をかけないよう、全スレッド共通でリクエスト間隔を空ける。
"""
from __future__ import annotations

import threading
import time

import requests


class KabuproClient:
    """リトライとスロットリング付きの薄い HTTP クライアント。"""

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        interval_sec: float,
        timeout_sec: int,
        max_retries: int,
        retry_wait_sec: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.interval_sec = interval_sec
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.retry_wait_sec = retry_wait_sec

        self._headers = {"User-Agent": user_agent}
        self._local = threading.local()
        self._throttle_lock = threading.Lock()
        self._last_request_at = 0.0

    # -- 内部ユーティリティ -------------------------------------------------
    def _session(self) -> requests.Session:
        """スレッドごとに Session を使い回して接続を再利用する。"""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self._headers)
            self._local.session = session
        return session

    def _throttle(self) -> None:
        """直前のリクエストから interval_sec 以上空ける。"""
        with self._throttle_lock:
            wait = self.interval_sec - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()

    def _request(self, url: str) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                response = self._session().get(url, timeout=self.timeout_sec)
                if response.status_code == 404:
                    raise FileNotFoundError(f"404 Not Found: {url}")
                response.raise_for_status()
                return response
            except FileNotFoundError:
                raise
            except Exception as error:  # noqa: BLE001 - リトライ対象をまとめて捕捉
                last_error = error
                if attempt < self.max_retries:
                    time.sleep(self.retry_wait_sec * attempt)
        raise RuntimeError(f"取得に失敗しました: {url} ({last_error})")

    # -- 公開メソッド -------------------------------------------------------
    def fetch_company_page(self, code: str) -> str:
        """企業ページ（適時開示一覧）の HTML を Unicode 文字列で返す。"""
        url = f"{self.base_url}/code/{code}.htm"
        response = self._request(url)
        return response.content.decode("cp932", errors="replace")

    def fetch_binary(self, url: str) -> bytes:
        """PDF などのバイナリを取得する。"""
        return self._request(url).content
