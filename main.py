"""
X-Block Auto Apply

Usage:
  python main.py              # 1회 실행
  python main.py --loop       # 반복 실행
  python main.py --test       # 테스트 모드 (로그인 확인)
  python main.py --pending    # 승인 대기 목록 보기
  python main.py --approve ID # 특정 프로젝트 승인/제출
  python main.py --save-login # 수동 로그인 후 세션 저장
"""
import asyncio
import io
import sys

# Windows 콘솔 UTF-8 출력
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# .env 먼저 로드 (notifier 등에서 os.getenv 사용)
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from playwright.async_api import async_playwright

from platforms import WishketPlatform, KmongPlatform, FreemoaPlatform
from platforms.base import (
    Project, DATA_DIR,
    can_apply, increment_daily_apply_count, apply_delay, get_daily_apply_count,
)
from proposal_generator import generate_proposal
from notifier import (
    notify_new_projects,
    notify_proposal_ready,
    notify_applied,
    notify_summary,
    check_approvals,
    send_telegram,
)

# config 로드
CONFIG_PATH = Path(__file__).parent / "config.json"
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

# 세션 저장 디렉토리
SESSION_DIR = DATA_DIR / "sessions"
SESSION_DIR.mkdir(exist_ok=True)


def get_session_path(platform_name: str) -> Path:
    return SESSION_DIR / f"{platform_name}_session.json"


# 대기 중인 지원 목록 파일
PENDING_FILE = DATA_DIR / "pending_proposals.json"


def load_pending() -> list[dict]:
    if PENDING_FILE.exists():
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_pending(pending: list[dict]):
    PENDING_FILE.parent.mkdir(exist_ok=True)
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


def format_project_summary(project: Project, proposal: str) -> str:
    """OpenClaw 알림용 프로젝트 요약"""
    return (
        f"📋 새 프로젝트 발견!\n"
        f"━━━━━━━━━━━━━━━\n"
        f"플랫폼: {project.platform}\n"
        f"제목: {project.title}\n"
        f"예산: {project.budget or '미정'}\n"
        f"URL: {project.url}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"생성된 지원서:\n{proposal[:300]}...\n"
        f"━━━━━━━━━━━━━━━\n"
        f"승인하려면 '승인 {project.project_id}' 라고 입력하세요."
    )


BROWSER_PROFILES_DIR = DATA_DIR / "browser_profiles"
BROWSER_PROFILES_DIR.mkdir(exist_ok=True)


def get_profile_dir(platform_name: str) -> Path:
    return BROWSER_PROFILES_DIR / platform_name


def _kill_chrome_for_profile(profile_dir: str):
    """해당 프로필을 사용 중인 Chrome 프로세스 강제 종료"""
    import subprocess
    try:
        # Windows: 해당 user-data-dir을 쓰는 chrome 프로세스 찾아서 종료
        result = subprocess.run(
            ["wmic", "process", "where",
             f"commandline like '%{Path(profile_dir).name}%' and name='chrome.exe'",
             "get", "processid"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.isdigit():
                subprocess.run(["taskkill", "/F", "/PID", line],
                               capture_output=True, timeout=5)
                print(f"[Browser] 잔여 Chrome 프로세스 종료: PID {line}")
    except Exception:
        pass


async def create_context(playwright, platform_name: str = "", max_retries: int = 3):
    """persistent context 생성 — 브라우저 프로필 유지 (충돌 시 재시도)"""
    profile_dir = get_profile_dir(platform_name) if platform_name else BROWSER_PROFILES_DIR / "_default"
    profile_dir.mkdir(exist_ok=True)

    for attempt in range(max_retries):
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            print(f"[{platform_name}] 브라우저 프로필: {profile_dir}")
            return context
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[{platform_name}] 브라우저 열기 실패 (시도 {attempt+1}/{max_retries}): {e}")
                _kill_chrome_for_profile(str(profile_dir))
                await asyncio.sleep(3)
            else:
                raise


async def save_session(context, platform_name: str):
    """persistent context에서는 자동 저장 — no-op"""
    print(f"[{platform_name}] 세션 자동 유지 (persistent context)")


async def save_login_sessions():
    """수동 로그인 모드 — persistent context로 브라우저 열어두고 직접 로그인"""
    async with async_playwright() as p:
        platforms_to_login = {
            "wishket": "https://auth.wishket.com/login",
            "kmong": "https://kmong.com",
            "freemoa": "https://www.freemoa.net/m0/s02",
        }

        contexts = {}
        pages = {}
        for name, url in platforms_to_login.items():
            if not CONFIG["platforms"].get(name, {}).get("enabled"):
                continue
            ctx = await create_context(p, name)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(url, timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)

            # 프리모아: 네이버 로그인 버튼까지 자동 클릭
            if name == "freemoa":
                await asyncio.sleep(2)
                naver_btn = page.locator("text=네이버 로그인").first
                if await naver_btn.count():
                    async with ctx.expect_page() as popup_info:
                        await naver_btn.click()
                    popup = await popup_info.value
                    await popup.wait_for_load_state("networkidle", timeout=15000)
                    pages[name] = popup  # 네이버 로그인 팝업을 추적
                    print(f"[FREEMOA] 네이버 로그인 팝업 열림 — 직접 로그인하세요")
                else:
                    pages[name] = page

            contexts[name] = ctx
            if name != "freemoa":
                pages[name] = page
            print(f"[{name.upper()}] 브라우저 열림: {url}")

        print(f"\n{'='*50}")
        print("모든 브라우저에서 직접 로그인해주세요.")
        print("로그인 완료되면 자동으로 감지합니다. (최대 5분 대기)")
        print(f"{'='*50}")

        login_checks = {
            "wishket": lambda page: "auth.wishket.com/login" not in page.url,
            "kmong": lambda page: "login" not in page.url.lower() and "kmong.com" in page.url,
            "freemoa": lambda page: page.is_closed(),  # 네이버 팝업이 닫히면 완료
        }
        saved = set()
        for _ in range(60):
            await asyncio.sleep(5)
            for name, pg in pages.items():
                if name in saved:
                    continue
                try:
                    check_fn = login_checks.get(name, lambda p: True)
                    cookies = await contexts[name].cookies()
                    has_session = len(cookies) > 3
                    if check_fn(pg) and has_session:
                        saved.add(name)
                        print(f"[{name.upper()}] 로그인 감지! (프로필 자동 저장)")
                except Exception:
                    pass
            if len(saved) == len(pages):
                break

        for name in pages:
            if name not in saved:
                print(f"[{name.upper()}] 타임아웃 — 현재 상태 저장")

        for name, ctx in contexts.items():
            await ctx.close()

        print("\n모든 브라우저 프로필이 저장되었습니다!")


async def run_once(test_mode: bool = False):
    """1회 실행: 크롤링 → 필터 → 지원서 생성"""
    async with async_playwright() as p:
        platform_classes = []
        if CONFIG["platforms"]["wishket"]["enabled"]:
            platform_classes.append(WishketPlatform)
        if CONFIG["platforms"]["kmong"]["enabled"]:
            platform_classes.append(KmongPlatform)
        if CONFIG["platforms"]["freemoa"]["enabled"]:
            platform_classes.append(FreemoaPlatform)

        all_filtered = []

        for PlatformClass in platform_classes:
            # 플랫폼별 persistent context
            platform_name = PlatformClass.name
            context = await create_context(p, platform_name)
            page = context.pages[0] if context.pages else await context.new_page()
            platform = PlatformClass(page)

            print(f"\n{'='*50}")
            print(f"[{platform.name.upper()}] 시작...")
            print(f"{'='*50}")

            # 로그인
            logged_in = await platform.login()
            if not logged_in:
                print(f"[{platform.name}] 로그인 실패. 스킵합니다.")
                await page.close()
                await context.close()
                continue

            # 로그인 성공 시 세션 저장
            await save_session(context, platform.name)

            if test_mode:
                print(f"[{platform.name}] 테스트 모드 - 로그인 성공 확인")
                await page.close()
                await context.close()
                continue

            # 프로젝트 크롤링
            projects = await platform.fetch_projects()
            print(f"[{platform.name}] 총 {len(projects)}개 프로젝트 수집")

            # 필터링
            filtered = [p for p in projects if p.matches_filter(CONFIG["filter"])]
            print(f"[{platform.name}] 필터 통과: {len(filtered)}개")

            daily_limit = CONFIG.get("daily_apply_limit", 9)
            per_platform_limit = CONFIG.get("daily_apply_limit_per_platform", 3)
            delay_range = CONFIG.get("apply_delay_seconds", [180, 600])

            # 이미 pending/submitted된 프로젝트 ID 목록
            existing_pending = load_pending()
            existing_ids = {
                item["project"]["project_id"]
                for item in existing_pending
                if item["status"] in ("pending", "submitted")
            }

            for proj in filtered:
                # 이미 pending 또는 submitted면 스킵
                if proj.project_id in existing_ids:
                    print(f"[{platform.name}] 이미 처리됨 (스킵): {proj.title}")
                    continue

                # 지원서 생성
                print(f"\n[{platform.name}] 지원서 생성 중: {proj.title}")
                proposal = generate_proposal(proj.to_dict())

                if CONFIG["mode"] == "auto":
                    # 일일 제한 확인 (전체 + 플랫폼별)
                    if not can_apply(daily_limit, platform.name, per_platform_limit):
                        print(f"[{platform.name}] 제한 도달. 나머지 프로젝트는 대기열로 이동.")
                        item = {
                            "project": proj.to_dict(),
                            "proposal": proposal,
                            "status": "pending",
                            "created_at": datetime.now().isoformat(),
                        }
                        all_filtered.append(item)
                        continue

                    # 완전 자동: 바로 제출
                    success = await platform.apply(proj, proposal)
                    notify_applied(proj.to_dict(), success)
                    if success:
                        increment_daily_apply_count(platform.name)
                        count = get_daily_apply_count()
                        plat_count = get_daily_apply_count(platform.name)
                        print(f"[{platform.name}] ✅ 자동 지원 완료: {proj.title} (전체 {count}/{daily_limit}, {platform.name} {plat_count}/{per_platform_limit}건)")
                        # 다음 지원 전 랜덤 딜레이
                        await apply_delay(delay_range)
                else:
                    # 반자동: 대기 목록에 추가 (텔레그램 알림은 watch_mode에서 하나씩)
                    item = {
                        "project": proj.to_dict(),
                        "proposal": proposal,
                        "status": "pending",
                        "notified": False,
                        "created_at": datetime.now().isoformat(),
                    }
                    all_filtered.append(item)
                    print(f"[{platform.name}] 📝 승인 대기: {proj.title}")

            await context.close()

        # 반자동 모드: 대기 목록 저장
        if CONFIG["mode"] == "semi-auto":
            if all_filtered:
                existing = load_pending()
                existing.extend(all_filtered)
                save_pending(existing)
                print(f"\n{'='*50}")
                print(f"총 {len(all_filtered)}개 프로젝트가 승인 대기 중입니다.")
                print(f"파일: {PENDING_FILE}")
                print(f"{'='*50}")
            else:
                print("\n새 공고 없음")
                send_telegram("[X-Block Auto Apply]\n크롤링 완료 — 새 공고가 없습니다.")


async def approve_and_submit(project_id: str) -> bool:
    """승인된 프로젝트에 지원서 제출. 성공 시 True 반환."""
    daily_limit = CONFIG.get("daily_apply_limit", 9)
    per_platform_limit = CONFIG.get("daily_apply_limit_per_platform", 3)

    pending = load_pending()
    target = None
    for item in pending:
        if item["project"]["project_id"] == project_id and item["status"] == "pending":
            target = item
            break

    if not target:
        print(f"프로젝트 ID '{project_id}'를 찾을 수 없습니다.")
        return False

    plat_name = target["project"]["platform"]
    if not can_apply(daily_limit, plat_name, per_platform_limit):
        print("일일 지원 제한에 도달했습니다. 내일 다시 시도하세요.")
        return False

    async with async_playwright() as p:
        platform_map = {
            "wishket": WishketPlatform,
            "kmong": KmongPlatform,
            "freemoa": FreemoaPlatform,
        }

        proj_data = {k: v for k, v in target["project"].items() if k != "crawled_at"}
        PlatformClass = platform_map[proj_data["platform"]]
        context = await create_context(p, PlatformClass.name)
        page = context.pages[0] if context.pages else await context.new_page()
        platform = PlatformClass(page)

        logged_in = await platform.login()
        if not logged_in:
            print("로그인 실패")
            await context.close()
            return False

        project = Project(**proj_data)
        success = await platform.apply(project, target["proposal"])

        if success:
            increment_daily_apply_count(plat_name)
            count = get_daily_apply_count()
            plat_count = get_daily_apply_count(plat_name)
            target["status"] = "submitted"
            target["submitted_at"] = datetime.now().isoformat()
            save_pending(pending)
            print(f"✅ 지원 완료: {project.title} (전체 {count}/{daily_limit}, {plat_name} {plat_count}/{per_platform_limit}건)")
        else:
            print(f"❌ 지원 실패: {project.title}")

        await context.close()
        return success


async def modify_application(project_id: str):
    """이미 지원한 프로젝트의 지원서 수정 (포트폴리오 첨부 등)"""
    pending = load_pending()
    target = None
    for item in pending:
        if item["project"]["project_id"] == project_id:
            target = item
            break

    if not target:
        print(f"프로젝트 ID '{project_id}'를 찾을 수 없습니다.")
        return

    async with async_playwright() as p:
        platform_map = {
            "wishket": WishketPlatform,
            "kmong": KmongPlatform,
            "freemoa": FreemoaPlatform,
        }

        proj_data = {k: v for k, v in target["project"].items() if k != "crawled_at"}
        PlatformClass = platform_map.get(proj_data["platform"])
        if not PlatformClass:
            print(f"지원되지 않는 플랫폼: {proj_data['platform']}")
            return

        context = await create_context(p, PlatformClass.name)
        page = context.pages[0] if context.pages else await context.new_page()
        platform = PlatformClass(page)

        project = Project(**proj_data)
        success = await platform.modify(project, target.get("proposal"))

        if success:
            print(f"✅ 지원서 수정 완료: {project.title}")
        else:
            print(f"❌ 지원서 수정 실패: {project.title}")

        await context.close()


async def main():
    args = sys.argv[1:]

    if "--save-login" in args:
        print("수동 로그인 세션 저장 모드")
        await save_login_sessions()

    elif "--test" in args:
        print("테스트 모드: 로그인만 확인합니다.")
        await run_once(test_mode=True)

    elif "--approve" in args:
        idx = args.index("--approve")
        if idx + 1 < len(args):
            project_id = args[idx + 1]
            await approve_and_submit(project_id)
        else:
            print("사용법: python main.py --approve <project_id>")

    elif "--modify" in args:
        idx = args.index("--modify")
        if idx + 1 < len(args):
            project_id = args[idx + 1]
            await modify_application(project_id)
        else:
            print("사용법: python main.py --modify <project_id>")

    elif "--pending" in args:
        pending = load_pending()
        pending_only = [p for p in pending if p["status"] == "pending"]
        if not pending_only:
            print("승인 대기 중인 프로젝트가 없습니다.")
        else:
            for i, item in enumerate(pending_only):
                proj = item["project"]
                print(f"\n[{i+1}] {proj['platform']} | {proj['title']}")
                print(f"    예산: {proj.get('budget', 'N/A')}")
                print(f"    URL: {proj.get('url', 'N/A')}")
                print(f"    ID: {proj['project_id']}")

    elif "--loop" in args:
        interval = CONFIG.get("check_interval_minutes", 30) * 60
        print(f"반복 모드: {interval // 60}분 간격으로 실행합니다.")
        while True:
            try:
                await run_once()
            except Exception as e:
                print(f"에러 발생: {e}")
            print(f"\n{interval // 60}분 후 다시 실행합니다...")
            await asyncio.sleep(interval)

    elif "--watch" in args:
        await watch_mode()

    else:
        await run_once()


async def watch_mode():
    """크롤링 + 텔레그램 승인 폴링 통합 모드
    - 매일 지정 시간(기본 11:00)에 크롤링
    - 10초마다 텔레그램 승인 메시지 확인
    """
    crawl_hours = CONFIG.get("crawl_hours", [11])  # 크롤링 시간 (24시간제)
    poll_interval = 10

    print(f"[Watch] 감시 모드 시작")
    print(f"  크롤링 시간: 매일 {crawl_hours}시")
    print(f"  텔레그램 폴링: {poll_interval}초 간격")

    # 시작 메시지 중복 방지 — 마지막 전송 시간 기록
    start_msg_file = DATA_DIR / "watch_start_msg.txt"
    now_str = datetime.now().strftime("%Y-%m-%d %H")
    last_start_msg = ""
    if start_msg_file.exists():
        last_start_msg = start_msg_file.read_text().strip()
    if last_start_msg != now_str:
        send_telegram(
            f"[X-Block Auto Apply]\n"
            f"감시 모드 시작됨\n\n"
            f"크롤링: 매일 {crawl_hours}시\n"
            f"프로젝트를 하나씩 보내드립니다.\n\n"
            f"'승인 [ID]' — 지원\n"
            f"'거절 [ID]' — 거절\n"
            f"'패스' 또는 '다음' — 스킵 후 다음\n"
            f"'전체승인' — 전부 지원"
        )
        start_msg_file.write_text(now_str)
    else:
        print("[Watch] 시작 메시지 중복 — 스킵")

    crawled_today = set()  # 오늘 이미 크롤링한 시간
    waiting_response = False  # 현재 응답 대기 중인지

    while True:
        try:
            now = datetime.now()
            today_key = now.strftime("%Y-%m-%d")
            current_hour = now.hour

            # 1. 지정 시간에 크롤링 (해당 시간에 1회만)
            if current_hour in crawl_hours and f"{today_key}_{current_hour}" not in crawled_today:
                crawled_today.add(f"{today_key}_{current_hour}")
                crawled_today = {k for k in crawled_today if k.startswith(today_key)}
                print(f"\n[Watch] {now.strftime('%H:%M')} 크롤링 시작...")
                try:
                    await run_once()
                except Exception as e:
                    print(f"[Watch] 크롤링 에러: {e}")
                    send_telegram(f"[WARN] 크롤링 에러: {e}")
                waiting_response = False  # 크롤링 후 새 프로젝트 알림 시작

            # 2. 응답 대기 중이 아니면 — 미알림 pending 하나 보내기
            if not waiting_response:
                pending = load_pending()
                unnotified = next(
                    (item for item in pending
                     if item["status"] == "pending" and not item.get("notified")),
                    None,
                )
                if unnotified:
                    proj = unnotified["project"]
                    proposal = unnotified["proposal"]
                    notify_proposal_ready(proj, proposal)
                    unnotified["notified"] = True
                    save_pending(pending)
                    waiting_response = True
                    remaining = sum(1 for p in pending
                                    if p["status"] == "pending" and not p.get("notified"))
                    print(f"[Watch] 알림 전송: {proj['title']} (남은 {remaining}건)")

            # 3. 텔레그램 응답 확인
            commands = check_approvals()
            for cmd in commands:
                action = cmd["action"]
                aid = cmd["id"]

                if action == "approve_all":
                    print(f"[Watch] 전체 승인 요청!")
                    send_telegram("전체 승인 처리 시작합니다...")
                    pending = load_pending()
                    for item in pending:
                        if item["status"] != "pending":
                            continue
                        pid = item["project"]["project_id"]
                        title = item["project"]["title"]
                        try:
                            success = await approve_and_submit(pid)
                            if success:
                                send_telegram(f"[OK] 지원 완료: {title}")
                            else:
                                send_telegram(f"[FAIL] 지원 실패: {title}\n수동 확인 필요")
                        except Exception as e:
                            send_telegram(f"[FAIL] 지원 실패: {title}\n{e}")
                    waiting_response = False

                elif action == "approve":
                    pending = load_pending()
                    target = next(
                        (p for p in pending if p["project"]["project_id"] == aid and p["status"] == "pending"),
                        None,
                    )
                    if target:
                        title = target["project"]["title"]
                        print(f"[Watch] 승인: {title} ({aid})")
                        send_telegram(f"승인 접수: {title}\n지원 처리 중...")
                        try:
                            success = await approve_and_submit(aid)
                            if success:
                                send_telegram(f"[OK] 지원 완료: {title}")
                            else:
                                send_telegram(f"[FAIL] 지원 실패: {title}\n수동 확인 필요")
                        except Exception as e:
                            send_telegram(f"[FAIL] 지원 실패: {title}\n{e}")
                    else:
                        send_telegram(f"프로젝트 ID '{aid}'를 찾을 수 없습니다.")
                    waiting_response = False  # 다음 프로젝트 보내기

                elif action == "reject":
                    pending = load_pending()
                    target = next(
                        (p for p in pending if p["project"]["project_id"] == aid and p["status"] == "pending"),
                        None,
                    )
                    if target:
                        title = target["project"]["title"]
                        target["status"] = "rejected"
                        save_pending(pending)
                        send_telegram(f"거절됨: {title}")
                        print(f"[Watch] 거절: {title} ({aid})")
                    else:
                        send_telegram(f"프로젝트 ID '{aid}'를 찾을 수 없습니다.")
                    waiting_response = False  # 다음 프로젝트 보내기

                elif action == "skip":
                    waiting_response = False  # 다음 프로젝트 보내기

        except Exception as e:
            print(f"[Watch] 에러: {e}")

        await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    asyncio.run(main())
