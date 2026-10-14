# jipsooni (집순이) — 한국 공공주택 공고 수집기

LH·마이홈·SH·HUG·청약홈의 공공주택/청약 공고를 한 번에 모아 **내 조건(prefs.json)에 맞는 것만** 골라주는 Python 수집기입니다.
신청은 대신하지 않고, 원문 링크와 일정까지만 안내합니다. Grok Bot 같은 에이전트가 루틴으로 `collect` → `digest`를 돌리고,
결과 JSON을 채팅 문장으로 다시 써서 보내는 용도로 만들었습니다.

- sqlite 하나로 동작 (Docker/Postgres 불필요), 출력은 모두 JSON
- 같은 공고는 내용 해시로 중복 알림 방지, 변경되면 "정정/변경" 알림
- 알림 종류: 새 공고 / 정정·변경 / 접수 시작 임박 / 마감 임박 (이미 마감된 공고는 저장만 하고 알림 없음)
- 요약은 3개 섹션: **입주 안정**(임대·전세·든든전세) / **조건완화**(무순위·잔여세대·선착순·재공급) / **내집마련**(공공분양·사전청약)
- LH와 마이홈에 같은 공고가 올라오면 제목을 정규화해 하나로 묶음

## 수집 대상

| 소스 | 방식 | 엔드포인트 | 한국 IP 필요 |
|---|---|---|---|
| LH 분양임대공고 | 공공데이터 API | `apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1` | 예 |
| 마이홈 모집공고 | 공공데이터 API | `apis.data.go.kr/1613000/HWSPR02/rsdtRcritNtcList` | 예 |
| SH 공공임대 | 웹 목록 | `housing.seoul.go.kr/site/main/sh/publicLease/list` | 예 |
| HUG 든든전세 | 웹 공지(EUC-KR) | `www.khug.or.kr/jeonse/web/s07/s070301.jsp` | 예 |
| 청약홈 잔여세대·무순위 | odcloud API | `api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getRemndrLttotPblancDetail` | **아니오** |
| 청약홈 웹 목록(보조) | 웹 | `www.applyhome.co.kr/ai/aia/selectAPTRemndrLttotPblancListView.do` | 예 |

## 설치

```bash
git clone https://github.com/devicki/jipsooni.git && cd jipsooni
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests
```

## 공공데이터포털 인증키

1. [data.go.kr](https://www.data.go.kr) 가입 → 마이페이지에서 **일반 인증키(Decoding)** 확인. 계정당 키 하나를 여러 API에 씁니다.
2. 쓰려는 API마다 **활용신청**을 해야 그 키로 호출됩니다.
   - LH 분양임대공고문 조회: https://www.data.go.kr/data/15058530/openapi.do
   - 마이홈 공공주택 모집공고: https://www.data.go.kr/data/15108420/openapi.do
   - 청약홈 분양정보(잔여세대): data.go.kr에서 "청약홈 분양정보 조회 서비스" 활용신청
3. 키는 환경변수로만 넘깁니다. 저장소에 커밋하지 마세요.

```bash
export DATA_GO_KR_SERVICE_KEY='발급받은_키'
```

## 사용법

```bash
# 수집 (소스별 결과·오류·힌트가 JSON으로 출력)
.venv/bin/python -m jipsooni collect                       # prefs의 sources 전체
.venv/bin/python -m jipsooni collect --source applyhome    # 하나만
.venv/bin/python -m jipsooni collect --dry-run             # 저장 없이 확인

# 요약 (섹션별 상위 공고 JSON). 보낸 뒤 --mark-sent로 처리 완료 표시
.venv/bin/python -m jipsooni digest --limit 5 --mark-sent

# 한국 IP 프록시용 로컬 포워더 시작 (아래 참고)
.venv/bin/python -m jipsooni proxy
```

- 데이터 위치: `~/.jipsooni/` (`prefs.json`, `jipsooni.sqlite3`). `JIPSOONI_HOME`, `JIPSOONI_PREFS`, `JIPSOONI_DB`로 변경 가능
- `collect` 종료 코드: 하나라도 가져오면 0, 전부 실패하면 2
- `JIPSOONI_HTTP_TIMEOUT` (기본 20초)

## prefs.json

`prefs.example.json`을 `~/.jipsooni/prefs.json`으로 복사해 고칩니다. 빠진 항목은 기본값으로 채워집니다. 전체 정의는 `jipsooni/prefs.py`.

| 키 | 설명 |
|---|---|
| `household` | type(single/newlywed/couple/family/senior), members, is_homeless(무주택), is_householder(세대주), has_children, children_count, has_newborn_or_pregnant, age, marriage_years, prospective_newlywed(예비신혼). newlywed면 신혼 공고 +40 |
| `finance` | monthly_income_krw, total_assets_krw, car_value_krw (자격 확인 참고용) |
| `subscription_account` | 청약통장 has, years, deposit_count, balance_krw |
| `goals` | `move_in`, `relaxed`, `ownership` 중 받을 섹션 |
| `filters` 대상계층 | exclude_target_groups(null=가구정보로 자동: senior, special_occupation, student, disabled, single_parent, youth_only), allow_target_groups |
| `budget` | purchase_max_krw(매매·분양 상한), deposit_max_krw(임대보증금 상한, 이내 +20 / 초과 -80), monthly_rent_max_krw |
| `regions` | sido(광역, 청약홈 API 필터에도 사용), sigungu(최우선 시군구), neighborhoods(동·생활권 키워드) = 1순위 +70, secondary_sigungu / secondary_neighborhoods = 2순위 +40. '…구' 이름은 sido도 맞아야 인정 |
| `housing_types` | include(가산점 키워드), exclude(제외 키워드) |
| `filters` | exclude_child_required(비우면 자녀 유무로 자동), exclude_small_units(원룸·기숙사 제외), drop_outside_regions |
| `digest` | per_section_limit, relaxed_min_score, ownership_min_score |
| `sources` | 사용할 소스 목록 |
| `network` | kr_proxy, kr_proxy_upstream (아래 참고) |

기본값은 지역만 서울·경기·인천이고 시군구·예산은 비어 있습니다. **예시**(실제 기본값 아님):

```json
{
  "household": {"type": "newlywed", "is_homeless": true, "has_children": false},
  "goals": ["move_in", "relaxed", "ownership"],
  "budget": {"purchase_max_krw": 800000000},
  "regions": {"sido": ["서울", "경기"], "sigungu": ["마포구", "은평구"], "neighborhoods": ["상암"]},
  "housing_types": {"include": ["행복주택", "든든전세"], "exclude": []},
  "filters": {"exclude_small_units": true}
}
```

점수 규칙(요약): 마감 임박 +60~120, 오늘 공고 +70, 선호 시군구/동 +70, 첫 번째 선호 시·도 +35·나머지 +15, 지역 밖 −80,
든든전세 +60(HUG면 +40), 전세 +35, 조건완화 키워드 +120, 관심 유형 +40, 분양가가 예산 이내 +60·초과 −80, 결과/후속 공지 −180.

## 한국 IP 프록시 (선택)

LH·마이홈·SH·HUG·청약홈 웹은 **해외 IP를 막습니다**. 봇이 해외 서버에서 돌면 이 소스들은 연결이 끊깁니다.
청약홈 odcloud API는 해외에서도 됩니다. 프록시는 이 한국 IP 소스들에만 쓰이고 odcloud는 항상 직접 연결합니다.

### 1) 한국에 있는 상시 켜진 리눅스 PC에 tinyproxy

```bash
sudo apt install tinyproxy
# /etc/tinyproxy/tinyproxy.conf
#   Port 8888
#   Allow 127.0.0.1
#   Allow 100.64.0.0/10      # Tailscale 대역만 허용
sudo systemctl restart tinyproxy
sudo ufw allow in on tailscale0 to any port 8888 proto tcp   # 인터넷에는 열지 않음
```

이 PC도 [Tailscale](https://tailscale.com)에 로그인해 둡니다. Tailscale IP를 확인하세요 (`tailscale ip -4`).

### 2) 봇 컴퓨터를 tailnet에 userspace 모드로 연결

컨테이너/VM처럼 TUN을 못 쓰는 환경용입니다.

```bash
sudo tailscaled --tun=userspace-networking --socks5-server=localhost:1055 &
sudo tailscale up --hostname=my-bot
```

userspace 모드에서는 100.x 주소가 SOCKS5 `localhost:1055`를 거쳐야만 닿습니다. 그래서 SOCKS5 → tinyproxy 2단 체인이 필요합니다.

### 3) 설정

```bash
export JIPSOONI_KR_PROXY=http://127.0.0.1:18888
export JIPSOONI_KR_PROXY_UPSTREAM='socks5://127.0.0.1:1055__http://<your-tailscale-ip>:8888'
.venv/bin/python -m jipsooni proxy     # pproxy 로컬 포워더 시작 (collect도 필요하면 자동 시작)
```

또는 prefs.json에:

```json
"network": {"kr_proxy": "http://127.0.0.1:18888",
            "kr_proxy_upstream": "socks5://127.0.0.1:1055__http://<your-tailscale-ip>:8888"}
```

봇 컴퓨터가 일반 네트워크로 프록시에 직접 닿으면 `kr_proxy`에 `http://<your-tailscale-ip>:8888`만 넣고 upstream은 비워도 됩니다.
확인: `curl --preproxy socks5h://localhost:1055 -x http://<your-tailscale-ip>:8888 https://housing.seoul.go.kr/`

## 프록시 없이 쓸 때 (축소 모드)

- 청약홈(무순위·잔여세대·불법행위 재공급)만 수집되고 digest도 정상 동작합니다.
- LH·마이홈·SH·HUG는 `status: "failed"`와 함께 `hint`에 "한국 IP 필요"가 표시되고, 나머지 소스는 그대로 저장됩니다(종료 코드 0).
- 국내 PC에서 직접 실행하면 프록시 없이 전부 동작합니다.
- 실패한 소스를 매번 기다리지 않으려면 prefs `sources`를 `["applyhome"]`로 두세요.

## 구조

```
jipsooni/
  run_once.py   수집 진입점 (collect)       digest.py   3섹션 요약 (digest)
  db.py         sqlite 저장·중복·알림       ranking.py  prefs 기반 점수·제외 규칙
  prefs.py      prefs 스키마/기본값         kr_proxy.py 포워더 헬퍼 (proxy)
  notify.py     알림 사유·메시지            sources/    lh, myhome, sh, hug, applyhome
tests/fixtures/ 실제 응답 샘플 (키 없음)
```

## 크레딧

원래 설계는 OpenClaw 위에서 돌던 개인용 에이전트 **집순이(jipsooni)** 의 수집기(Python + PostgreSQL + Docker, 텔레그램 알림)에서 왔습니다.
소스별 파서, 내용 해시 기반 변경 감지, 알림 분류, 점수 규칙, 3섹션 요약 구조를 그대로 가져왔고, 이 저장소는 그것을
개인 설정 없이 누구나 쓸 수 있도록 sqlite·prefs.json·JSON CLI·선택적 한국 IP 프록시로 옮긴 버전입니다.

## 라이선스

MIT
