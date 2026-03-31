"""
플랫폼 크롤러 베이스 클래스
"""
import json
import os
import random
import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, date


DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# 일일 지원 횟수 추적 파일
DAILY_LOG_FILE = DATA_DIR / "daily_apply_log.json"


def _load_daily_log() -> dict:
    if DAILY_LOG_FILE.exists():
        with open(DAILY_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_daily_apply_count(platform: str = None) -> int:
    """오늘 지원한 횟수 반환 (platform 지정 시 해당 플랫폼만)"""
    today = date.today().isoformat()
    log = _load_daily_log()
    if platform:
        return log.get(f"{today}_{platform}", 0)
    return log.get(today, 0)


def increment_daily_apply_count(platform: str = None):
    """오늘 지원 횟수 +1 (전체 + 플랫폼별)"""
    today = date.today().isoformat()
    log = _load_daily_log()
    # 전체 카운트
    log[today] = log.get(today, 0) + 1
    # 플랫폼별 카운트
    if platform:
        key = f"{today}_{platform}"
        log[key] = log.get(key, 0) + 1
    # 7일 이전 기록 정리
    cutoff = date.today().isoformat()[:8]
    log = {k: v for k, v in log.items() if k >= cutoff[:8]}
    with open(DAILY_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f)


def can_apply(daily_limit: int, platform: str = None, per_platform_limit: int = 0) -> bool:
    """일일 제한 확인 (전체 + 플랫폼별)"""
    total = get_daily_apply_count()
    if total >= daily_limit:
        print(f"[제한] 오늘 전체 지원 {total}/{daily_limit} — 일일 제한 도달")
        return False
    if platform and per_platform_limit > 0:
        plat_count = get_daily_apply_count(platform)
        if plat_count >= per_platform_limit:
            print(f"[제한] 오늘 {platform} 지원 {plat_count}/{per_platform_limit} — 플랫폼 제한 도달")
            return False
    return True


async def apply_delay(delay_range: list[int]):
    """지원 간 랜덤 딜레이 (초 단위 [min, max])"""
    if delay_range and len(delay_range) == 2:
        wait = random.randint(delay_range[0], delay_range[1])
        print(f"[딜레이] 다음 지원까지 {wait}초 대기...")
        await asyncio.sleep(wait)


class Project:
    """크롤링한 프로젝트 정보"""

    def __init__(
        self,
        platform: str,
        project_id: str,
        title: str,
        description: str = "",
        budget: str = "",
        budget_min: int = 0,
        budget_max: int = 0,
        duration: str = "",
        skills: str = "",
        category: str = "",
        url: str = "",
        deadline: str = "",
        client: str = "",
    ):
        self.platform = platform
        self.project_id = project_id
        self.title = title
        self.description = description
        self.budget = budget
        self.budget_min = budget_min
        self.budget_max = budget_max
        self.duration = duration
        self.skills = skills
        self.category = category
        self.url = url
        self.deadline = deadline
        self.client = client
        self.crawled_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return self.__dict__

    def matches_filter(self, config_filter: dict) -> bool:
        """필터 조건에 맞는지 확인"""
        # 예산 필터
        min_budget = config_filter.get("min_budget", 0)
        if self.budget_max > 0 and self.budget_max < min_budget:
            return False
        if self.budget_min > 0 and self.budget_min < min_budget:
            # budget_min이 있으면 이것으로 판단
            pass  # budget_max가 기준 이상이면 OK

        # 제외 키워드 (title, description, skills, category 모두 검사)
        exclude = config_filter.get("exclude_keywords", [])
        text = f"{self.title} {self.description} {self.skills} {self.category}".lower()
        for kw in exclude:
            if kw.lower() in text:
                return False

        # 턴키 관련 키워드 매칭 (하나라도 포함되면 OK)
        keywords = config_filter.get("keywords", [])
        if keywords:
            found = any(kw.lower() in text for kw in keywords)
            if not found:
                return False

        return True


class BasePlatform(ABC):
    """플랫폼 크롤러 베이스"""

    name: str = ""

    def __init__(self, page):
        self.page = page
        self._applied_file = DATA_DIR / f"{self.name}_applied.json"
        self._applied_ids = self._load_applied()

    def _load_applied(self) -> set:
        if self._applied_file.exists():
            with open(self._applied_file, "r", encoding="utf-8") as f:
                return set(json.load(f))
        return set()

    def _save_applied(self, project_id: str):
        self._applied_ids.add(project_id)
        with open(self._applied_file, "w", encoding="utf-8") as f:
            json.dump(list(self._applied_ids), f, ensure_ascii=False)

    def is_already_applied(self, project_id: str) -> bool:
        return project_id in self._applied_ids

    @abstractmethod
    async def login(self) -> bool:
        """플랫폼 로그인"""
        pass

    @abstractmethod
    async def fetch_projects(self) -> list[Project]:
        """프로젝트 목록 크롤링"""
        pass

    @abstractmethod
    async def apply(self, project: Project, proposal_text: str) -> bool:
        """프로젝트에 지원서 제출"""
        pass

    async def screenshot(self, name: str = ""):
        """디버깅용 스크린샷"""
        ss_dir = DATA_DIR.parent / "screenshots"
        ss_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = ss_dir / f"{self.name}_{name}_{ts}.png"
        try:
            await self.page.screenshot(path=str(path), timeout=10000)
        except Exception:
            print(f"[Screenshot] Failed to save: {path}")
        print(f"[Screenshot] {path}")
