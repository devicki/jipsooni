from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HOME = Path(os.getenv("JIPSOONI_HOME", str(Path.home() / ".jipsooni")))


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    db_path: str
    prefs_path: str
    data_go_kr_service_key: str
    lh_notice_endpoint: str
    lh_lookback_days: int
    lh_lookahead_days: int
    myhome_notice_endpoint: str
    myhome_notice_page_url: str
    applyhome_service_key: str
    applyhome_remndr_endpoint: str
    sh_notice_url: str
    hug_notice_url: str
    log_level: str
    applyhome_extra_house_manage_nos: tuple[str, ...] = ()
    kr_proxy: str = ""  # HTTP proxy URL for Korean-IP-only sources; empty = direct
    http_timeout: float = 20.0

    @classmethod
    def from_env(cls) -> "Settings":
        key = os.getenv("DATA_GO_KR_SERVICE_KEY", "")
        return cls(
            db_path=os.getenv("JIPSOONI_DB", str(HOME / "jipsooni.sqlite3")),
            prefs_path=os.getenv("JIPSOONI_PREFS", str(HOME / "prefs.json")),
            data_go_kr_service_key=key,
            lh_notice_endpoint=os.getenv("LH_NOTICE_ENDPOINT", "https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1"),
            lh_lookback_days=_int_env("LH_LOOKBACK_DAYS", 45),
            lh_lookahead_days=_int_env("LH_LOOKAHEAD_DAYS", 120),
            myhome_notice_endpoint=os.getenv("MYHOME_NOTICE_ENDPOINT", "https://apis.data.go.kr/1613000/HWSPR02/rsdtRcritNtcList"),
            myhome_notice_page_url=os.getenv("MYHOME_NOTICE_PAGE_URL", "https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcView.do"),
            applyhome_service_key=os.getenv("APPLYHOME_SERVICE_KEY", key),
            applyhome_remndr_endpoint=os.getenv("APPLYHOME_REMNDR_ENDPOINT", "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getRemndrLttotPblancDetail"),
            sh_notice_url=os.getenv("SH_NOTICE_URL", "https://housing.seoul.go.kr/site/main/sh/publicLease/list"),
            hug_notice_url=os.getenv("HUG_NOTICE_URL", "https://www.khug.or.kr/jeonse/web/s07/s070301.jsp"),
            log_level=os.getenv("LOG_LEVEL", "WARNING"),
            kr_proxy=os.getenv("JIPSOONI_KR_PROXY", ""),
            http_timeout=float(os.getenv("JIPSOONI_HTTP_TIMEOUT", "20")),
            applyhome_extra_house_manage_nos=tuple(
                v.strip() for v in os.getenv("APPLYHOME_EXTRA_HOUSE_MANAGE_NOS", "").split(",") if v.strip()
            ),
        )


def kr_client(settings: "Settings", **kw):
    """httpx client for sites that block foreign IPs (LH, 마이홈, SH, HUG, 청약홈 web)."""
    import httpx
    kw.setdefault("timeout", settings.http_timeout)
    if settings.kr_proxy:
        kw["proxy"] = settings.kr_proxy
    return httpx.Client(**kw)
