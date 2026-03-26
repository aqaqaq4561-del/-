# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

X-Block (xblock0.com) 앱개발 회사의 영업 자동화 시스템. 위시켓, 크몽 엔터프라이즈, 프리모아에 올라오는 프로젝트를 크롤링하여 자동으로 지원서를 생성/제출한다.

## Commands

```bash
python main.py              # 1회 실행 (크롤링 → 필터 → 지원서 생성)
python main.py --test       # 로그인만 테스트
python main.py --pending    # 승인 대기 목록 보기
python main.py --approve ID # 특정 프로젝트 승인/제출
python main.py --loop       # 반복 실행 (30분 간격)
python main.py --watch      # 감시 모드 (매일 11시 크롤링 + 텔레그램 승인 폴링)
python main.py --save-login # 수동 로그인 후 세션 저장
```

Dependencies: `pip install -r requirements.txt` (playwright, python-dotenv, anthropic)

After installing: `playwright install chromium`

## Architecture

**Flow**: `main.py` → 각 플랫폼 크롤러로 프로젝트 수집 → `config.json` 필터 조건으로 필터링 → `proposal_generator.py`로 Claude API 맞춤 지원서 생성 → 제출 → `notifier.py`로 텔레그램 알림

**플랫폼 크롤러** (`platforms/`):
- `base.py` — `Project` dataclass와 `BasePlatform` 추상 클래스. 모든 크롤러가 상속
- `wishket.py` — 위시켓. 일반 폼 로그인
- `kmong.py` — 크몽 엔터프라이즈. 모달 기반 로그인
- `freemoa.py` — 프리모아. 네이버 소셜 로그인 (JS evaluate로 자동입력 방지 우회)

**모드** (`config.json`의 `mode` 필드):
- `"semi-auto"` — 지원서 생성까지만, 제출은 `--approve`로 수동 승인
- `"auto"` — 크롤링부터 제출까지 완전 자동

## Credentials

All credentials live in `.env` (gitignored). Never hardcode or log credentials.

## 회사 정보 (지원서 생성 컨텍스트)

- 회사: X-Block / CEO: 한정원
- 슬로건: Launch Faster, Build Smarter
- 강점: 원스톱 턴키 개발, 빠른 MVP(4주~4개월), 10단계 프로세스
- 팀: App Planning, UX/UI Design, Development, QA Support
- 포트폴리오: Claim Bridge, PetPle, APEC CEO Summit Korea 2025, 봉선장, Qin Meitian, 실물 NFT 경매

## 필터 조건

- 턴키 프로젝트 (기획+디자인+개발 풀패키지)
- 예산 1,000만원 이상
- **제외**: 기간제, 상주, 파견, 구인, 채용, 출퇴근, 프라이빗 매칭
- 위시켓은 외주(도급) 프로젝트만 대상 (기간제/상주 크롤링 단계에서 스킵)
- 상세 조건은 `config.json` 참조

## 위시켓 지원 폼 주의사항

- **지원서 제출 후 수정 불가** — 제출 전 모든 필드 검증 필수
- **기간 단위**: `term_type` 라디오에서 "일"(두 번째 옵션)을 선택 후 일수 입력. 기본이 "개월"이라 숫자만 넣으면 개월로 들어감
- **필수 라디오**: `has_related_employment`(관련 경력), `has_resume`(이력서) — 미체크 시 제출 버튼 disabled
- **근무시작일**: `custom_launch_date` 필드 + `launch_date_option` 체크박스 함께 처리
- **클라이언트 질문 추출**: 질문 텍스트가 DOM에서 정확히 추출되어야 함. 추출 실패 시 "유사한 프로젝트 경험" 기본 질문으로 대체. 절대로 추출 실패 사실을 답변에 노출하면 안 됨
- **브라우저 프로필 충돌**: persistent context 사용 시 이전 Chrome 프로세스가 남아있으면 충돌 발생. `create_context()`에서 최대 3회 재시도 + taskkill 처리
- **마감 공고**: 마감된 공고의 `/proposal/apply/` 접근 시 로그인 페이지로 리다이렉트됨

## 알려진 이슈

- 프리모아: 파싱 시 제목에 설명 조각이 포함되는 문제
- 크몽: 카드 파싱 시 간헐적 타임아웃 (1~2건 스킵, 치명적이진 않음)
- 위시켓: 질문 추출 시 DOM의 무관한 UI 텍스트("에러상세 메시지" 등)를 잡을 수 있음 — 질문 패턴 매칭으로 필터링
