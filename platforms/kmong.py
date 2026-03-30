"""
크몽 엔터프라이즈 (Kmong Enterprise) 크롤러
- 로그인: https://kmong.com/login
- 프로젝트 목록: https://kmong.com/enterprise/requests
"""
import os
import re
import asyncio
from .base import BasePlatform, Project


class KmongPlatform(BasePlatform):
    name = "kmong"

    async def login(self) -> bool:
        user_id = os.getenv("KMONG_ID")
        user_pw = os.getenv("KMONG_PW")
        if not user_id or not user_pw:
            print("[Kmong] 로그인 정보 없음")
            return False

        try:
            # 크몽 메인에서 로그인 버튼 클릭
            await self.page.goto("https://kmong.com", timeout=30000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            # 이미 로그인된 상태인지 확인
            # "주문 관리"가 보이면 로그인된 상태 (태그 무관하게 텍스트로 확인)
            already_logged = await self.page.get_by_text("주문 관리").count()
            if already_logged:
                print("[Kmong] 이미 로그인된 상태 (세션 유지)")
                return True

            # 상단 로그인 링크 클릭
            login_link = self.page.locator(
                'a:has-text("로그인"):visible, '
                '[href*="login"]:visible, '
                'button:has-text("로그인"):visible'
            ).first
            await login_link.click()
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            # 모달 내부의 이메일 입력 (placeholder: "이메일을 입력해 주세요.")
            email_input = self.page.locator(
                'input[placeholder*="이메일"]:visible'
            ).first
            await email_input.fill(user_id)

            # 모달 내부의 비밀번호 입력
            pw_input = self.page.locator('input[type="password"]:visible').first
            await pw_input.fill(user_pw)

            await asyncio.sleep(1)

            # 모달 내부의 로그인 버튼 (검은색 버튼)
            login_btn = self.page.locator(
                '[data-testid="modal-base"] button:has-text("로그인"), '
                '.fixed button:has-text("로그인"), '
                '[role="dialog"] button:has-text("로그인")'
            ).first
            await login_btn.click()

            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(3)

            current_url = self.page.url
            if "login" not in current_url.lower():
                print("[Kmong] 로그인 성공")
                return True

            print("[Kmong] 로그인 실패 - URL:", current_url)
            await self.screenshot("login_failed")
            return False

        except Exception as e:
            print(f"[Kmong] 로그인 에러: {e}")
            await self.screenshot("login_error")
            return False

    async def fetch_projects(self) -> list[Project]:
        projects = []
        try:
            await self.page.goto(
                "https://kmong.com/enterprise/requests", timeout=30000
            )
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(5)  # SPA 렌더링 대기

            # 프로젝트 카드: <li aria-label="프로젝트 카드">
            card_selector = 'li[aria-label="프로젝트 카드"]'
            card_count = await self.page.locator(card_selector).count()

            if card_count == 0:
                print("[Kmong] 프로젝트 카드를 찾지 못함")
                await self.screenshot("no_projects")
                return projects

            # 먼저 모든 카드 정보를 JS로 한번에 추출
            card_data_list = await self.page.evaluate("""() => {
                const cards = document.querySelectorAll('li[aria-label="프로젝트 카드"]');
                return Array.from(cards).map(card => {
                    const h2 = card.querySelector('h2');
                    const h3 = card.querySelector('h3');
                    const catEl = card.querySelector('p.text-gray-600');
                    const allText = card.textContent.trim();
                    return {
                        title: h2?.textContent?.trim() || '',
                        budget: h3?.textContent?.trim() || '',
                        category: catEl?.textContent?.trim() || '',
                        fullText: allText.substring(0, 500),
                    };
                });
            }""")

            # 각 카드를 클릭해서 URL(ID) 추출
            for i, data in enumerate(card_data_list):
                try:
                    title = data["title"]
                    if not title:
                        continue

                    # 카드 클릭 → URL에서 ID 추출 → 뒤로가기
                    card = self.page.locator(card_selector).nth(i)
                    await card.click()
                    await self.page.wait_for_url(
                        "**/enterprise/requests/*", timeout=5000
                    )
                    detail_url = self.page.url
                    id_match = re.search(r"/requests/(\d+)", detail_url)
                    if not id_match:
                        print(f"[Kmong] 프로젝트 ID 추출 실패 (스킵): {title[:30]}")
                        await self.page.go_back()
                        await self.page.wait_for_load_state("networkidle", timeout=10000)
                        await asyncio.sleep(1)
                        continue
                    pid = id_match.group(1)

                    await self.page.go_back()
                    await self.page.wait_for_load_state("networkidle", timeout=10000)
                    await asyncio.sleep(1)

                    if self.is_already_applied(pid):
                        continue

                    # 예산 파싱
                    budget_str = data["budget"]
                    budget_num = 0
                    budget_match = re.search(r"([\d,]+)\s*만\s*원", budget_str)
                    if budget_match:
                        num_str = budget_match.group(1).replace(",", "")
                        budget_num = int(num_str) * 10000

                    project = Project(
                        platform="kmong",
                        project_id=pid,
                        title=title,
                        description=data["fullText"],
                        budget=budget_str,
                        budget_min=budget_num,
                        budget_max=budget_num,
                        category=data["category"],
                        url=detail_url,
                    )
                    projects.append(project)

                except Exception as e:
                    print(f"[Kmong] 카드 파싱 에러 ({i}): {e}")
                    # 에러 시 목록 페이지로 복귀
                    if "/enterprise/requests" not in self.page.url:
                        await self.page.goto(
                            "https://kmong.com/enterprise/requests",
                            timeout=30000,
                        )
                        await asyncio.sleep(3)
                    continue

            print(f"[Kmong] {len(projects)}개 프로젝트 수집")
        except Exception as e:
            print(f"[Kmong] 크롤링 에러: {e}")
            await self.screenshot("crawl_error")

        return projects

    async def apply(self, project: Project, proposal_text: str) -> bool:
        try:
            if not project.url:
                print(f"[Kmong] URL 없음: {project.title}")
                return False

            await self.page.goto(project.url, timeout=30000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(3)

            # 상세 페이지에서 시작일/종료일/예산 추출
            detail = await self.page.evaluate(
                r"""() => {
                const body = document.body.innerText;
                const startMatch = body.match(/프로젝트 시작일[\s\n]+(\d{4}-\d{2}-\d{2})/);
                const endMatch = body.match(/프로젝트 종료 예정일[\s\n]+(\d{4}-\d{2}-\d{2})/);
                const budgetMatch = body.match(/예산[\s\n]+([\d,]+)/);
                return {
                    startDate: startMatch?.[1] || '',
                    endDate: endMatch?.[1] || '',
                    budget: budgetMatch?.[1] || '',
                };
            }"""
            )

            # 기간 계산: (종료일 - 시작일) + 10일
            propose_days = ""
            if detail.get("startDate") and detail.get("endDate"):
                from datetime import datetime as dt

                start = dt.strptime(detail["startDate"], "%Y-%m-%d")
                end = dt.strptime(detail["endDate"], "%Y-%m-%d")
                days = (end - start).days + 10
                propose_days = str(days)
                print(f"[Kmong] 기간: {detail['startDate']}~{detail['endDate']} + 10일 = {days}일")

            # 예산 계산: 공고 예산(만원) - 150
            propose_budget = ""
            if detail.get("budget"):
                raw = int(detail["budget"].replace(",", ""))
                budget_man = raw // 10000  # 원 → 만원
                propose_budget = str(budget_man - 150)
                print(f"[Kmong] 예산: {budget_man}만원 - 150 = {propose_budget}만원")

            # "제안하기" 버튼 (로그인 상태에서만 활성화)
            apply_btn = self.page.locator(
                'button:has-text("제안하기"):visible'
            ).first
            if not await apply_btn.count():
                print("[Kmong] 제안하기 버튼을 찾을 수 없음 (로그인 필요?)")
                await self.screenshot("no_apply_btn")
                return False

            btn_text = (await apply_btn.text_content() or "").strip()
            if "로그인" in btn_text:
                print("[Kmong] 로그인이 필요합니다")
                await self.screenshot("need_login")
                return False

            await apply_btn.click()
            await asyncio.sleep(3)

            # 1. 제안 예산 입력 (만원)
            budget_input = self.page.locator("input[type='text']:visible").first
            if await budget_input.count() and propose_budget:
                await budget_input.fill(propose_budget)
                print(f"[Kmong] 예산 입력: {propose_budget}만원")

            # 2. 제안 기간 입력 (일)
            duration_input = self.page.locator("input[type='text']:visible").nth(1)
            if await duration_input.count() and propose_days:
                await duration_input.fill(propose_days)
                print(f"[Kmong] 기간 입력: {propose_days}일")

            # 3. 제안 내용 입력
            textarea = self.page.locator("textarea:visible").first
            if not await textarea.count():
                print("[Kmong] 텍스트 입력 영역을 찾을 수 없음")
                await self.screenshot("no_textarea")
                return False
            await textarea.fill(proposal_text)
            await asyncio.sleep(1)

            # 4. 필수 체크박스 체크 (커스텀 UI — force click)
            for cb_id in ["checkBillingAmount", "checkCommission"]:
                cb = self.page.locator(f"#{cb_id}")
                if await cb.count() and not await cb.is_checked():
                    await cb.check(force=True)
                    print(f"[Kmong] 체크: {cb_id}")

            await asyncio.sleep(1)

            # 5. 제출
            submit_btn = self.page.locator(
                'button:has-text("제안하기"):visible'
            ).last
            if not await submit_btn.count():
                print("[Kmong] 제출 버튼을 찾을 수 없음")
                await self.screenshot("no_submit_btn")
                return False

            await self.screenshot("before_submit")
            await submit_btn.click()
            await asyncio.sleep(3)

            self._save_applied(project.project_id)
            print(f"[Kmong] 지원 완료: {project.title}")
            await self.screenshot("applied")
            return True

        except Exception as e:
            print(f"[Kmong] 지원 에러: {e}")
            await self.screenshot("apply_error")
            return False
