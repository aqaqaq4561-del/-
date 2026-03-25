"""
위시켓 (Wishket) 크롤러
- 로그인: https://auth.wishket.com/login/
- 프로젝트 목록: https://www.wishket.com/project/
"""
import os
import re
import asyncio
from .base import BasePlatform, Project
from proposal_generator import generate_pre_question_answer


class WishketPlatform(BasePlatform):
    name = "wishket"

    async def login(self) -> bool:
        user_id = os.getenv("WISHKET_ID")
        user_pw = os.getenv("WISHKET_PW")
        if not user_id or not user_pw:
            print("[Wishket] 로그인 정보 없음")
            return False

        try:
            await self.page.goto("https://auth.wishket.com/login", timeout=30000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            # 페이지의 모든 input 찾기 (디버깅)
            inputs = await self.page.locator("input").all()
            print(f"[Wishket] Found {len(inputs)} inputs")
            for inp in inputs:
                inp_type = await inp.get_attribute("type") or ""
                inp_name = await inp.get_attribute("name") or ""
                inp_ph = await inp.get_attribute("placeholder") or ""
                inp_visible = await inp.is_visible()
                print(f"  type={inp_type} name={inp_name} placeholder={inp_ph} visible={inp_visible}")

            # 첫번째 visible input = 아이디
            visible_inputs = self.page.locator("input:visible")
            count = await visible_inputs.count()
            print(f"[Wishket] Visible inputs: {count}")

            if count >= 2:
                await visible_inputs.nth(0).fill(user_id)
                await visible_inputs.nth(1).fill(user_pw)
            else:
                # JS fallback
                await self.page.evaluate(f"""() => {{
                    const inputs = document.querySelectorAll('input');
                    for (const inp of inputs) {{
                        if (inp.placeholder && inp.placeholder.includes('아이디')) {{
                            inp.value = '{user_id}';
                            inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                        }}
                        if (inp.type === 'password') {{
                            inp.value = '{user_pw}';
                            inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                        }}
                    }}
                }}""")

            await asyncio.sleep(1)

            # 로그인 버튼 클릭 (data-testid="login-button")
            login_btn = self.page.get_by_test_id("login-button")
            await login_btn.click()

            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            # 로그인 확인
            current_url = self.page.url
            if "login" not in current_url.lower():
                print("[Wishket] 로그인 성공")
                return True

            print("[Wishket] 로그인 실패 - URL 확인:", current_url)
            await self.screenshot("login_failed")
            return False

        except Exception as e:
            print(f"[Wishket] 로그인 에러: {e}")
            await self.screenshot("login_error")
            return False

    async def fetch_projects(self) -> list[Project]:
        projects = []
        try:
            # 외주(도급) 탭 직접 접근
            await self.page.goto("https://www.wishket.com/project/", timeout=30000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(5)

            # 모달 팝업 닫기 (있으면)
            try:
                close_btn = self.page.locator(
                    '[class*="modal"] button[class*="close"], '
                    '[class*="modal"] .close, '
                    'button:has-text("닫기"):visible'
                )
                if await close_btn.count() > 0:
                    await close_btn.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            # JS로 프로젝트 데이터 직접 추출 (가장 안정적)
            raw_projects = await self.page.evaluate(r"""() => {
                const links = document.querySelectorAll('a[href*="/project/"]');
                const results = [];
                const seen = new Set();
                for (const a of links) {
                    const match = a.href.match(/\/project\/(\d{5,})/);
                    if (!match) continue;
                    const pid = match[1];
                    if (seen.has(pid)) continue;
                    seen.add(pid);

                    const box = a.closest('.project-info-box');
                    if (!box) continue;

                    const text = box.textContent.trim();
                    results.push({
                        id: pid,
                        title: a.textContent.trim(),
                        url: a.href,
                        text: text.substring(0, 600)
                    });
                    if (results.length >= 20) break;
                }
                return results;
            }""")

            if not raw_projects:
                print("[Wishket] 프로젝트를 찾지 못함")
                await self.screenshot("no_projects")
                return projects

            for rp in raw_projects:
                pid = rp["id"]
                if self.is_already_applied(pid):
                    continue

                full_text = rp["text"]

                # 예산 추출 (예상 금액 or 월 금액)
                budget_match = re.search(r"(\d[\d,]+)\s*원", full_text)
                budget_str = budget_match.group(0) if budget_match else ""
                budget_num = 0
                if budget_match:
                    num_str = budget_match.group(1).replace(",", "")
                    budget_num = int(num_str)

                # 기간 추출
                duration_match = re.search(r"(\d+)\s*(개월|주|일)", full_text)
                duration = duration_match.group(0) if duration_match else ""

                # 카테고리/스킬 추출
                skills = ""
                for kw in ["외주", "기간제", "개발", "디자인", "기획", "웹", "앱"]:
                    if kw in full_text:
                        skills += kw + " "

                project = Project(
                    platform="wishket",
                    project_id=pid,
                    title=rp["title"],
                    description=full_text[:500],
                    budget=budget_str,
                    budget_min=budget_num,
                    budget_max=budget_num,
                    duration=duration,
                    skills=skills.strip(),
                    url=rp["url"],
                )
                projects.append(project)

            print(f"[Wishket] {len(projects)}개 프로젝트 수집")
        except Exception as e:
            print(f"[Wishket] 크롤링 에러: {e}")
            await self.screenshot("crawl_error")

        return projects

    async def apply(self, project: Project, proposal_text: str) -> bool:
        try:
            if not project.url:
                print(f"[Wishket] URL 없음: {project.title}")
                return False

            # 지원 폼 URL로 직접 이동
            apply_url = project.url.rstrip("/") + "/proposal/apply/"
            await self.page.goto(apply_url, timeout=30000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(3)

            # 로그인 페이지로 리다이렉트됐는지 확인
            if "login" in self.page.url:
                print("[Wishket] 로그인 필요 — 세션 만료")
                return False

            # 상세 정보 추출: 예상 금액, 예상 기간
            detail = await self.page.evaluate(
                r"""() => {
                const body = document.body.innerText;
                const budgetMatch = body.match(/예상\s*금액\s*([\d,]+)원/);
                const termMatch = body.match(/예상\s*기간\s*(\d+)일/);
                return {
                    budget: budgetMatch?.[1] || '',
                    term: termMatch?.[1] || '',
                };
            }"""
            )

            # 예산 계산: 공고금액(원) - 150만원 → 원 단위로 입력
            propose_budget = ""
            if detail.get("budget"):
                raw = int(detail["budget"].replace(",", ""))
                propose_raw = raw - 1500000  # 150만원 차감
                propose_budget = str(propose_raw)
                print(f"[Wishket] 예산: {raw:,}원 - 150만 = {propose_raw:,}원")

            # 기간 계산: 공고기간 + 10일
            propose_term = ""
            if detail.get("term"):
                days = int(detail["term"]) + 10
                propose_term = str(days)
                print(f"[Wishket] 기간: {detail['term']}일 + 10 = {propose_term}일")

            # 1. 근무시작일 (hidden이면 JS로 설정)
            start_input = self.page.locator("#date_can_get_in_input")
            if await start_input.count():
                # 공고 시작일 또는 2주 후
                from datetime import datetime as dt, timedelta
                start_date = (dt.now() + timedelta(days=14)).strftime("%Y.%m.%d.")
                await self.page.evaluate(
                    f"""() => {{
                    const el = document.getElementById('date_can_get_in_input');
                    if (el) {{
                        el.value = '{start_date}';
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                }}"""
                )
                print(f"[Wishket] 근무시작일: {start_date}")

            # 2. 예산 입력 (만원 단위)
            budget_input = self.page.locator("input[name='budget']:visible")
            if await budget_input.count() and propose_budget:
                await budget_input.fill(propose_budget)

            # 3. 기간 입력 (일)
            term_input = self.page.locator("input[name='term']:visible")
            if await term_input.count() and propose_term:
                await term_input.fill(propose_term)

            # 4. 클라이언트 질문 답변 (여러 개일 수 있음 — 각 질문별 맞춤 답변)
            questions_data = await self.page.evaluate(r"""() => {
                const textareas = document.querySelectorAll("textarea[name='pre_question_answer']");
                const results = [];
                for (const ta of textareas) {
                    // 질문 텍스트: textarea 상위 컨테이너에서 질문 라벨 찾기
                    let questionText = '';
                    let parent = ta.closest('.form-group') || ta.closest('.question-item') || ta.parentElement?.parentElement;
                    if (parent) {
                        const label = parent.querySelector('label, .question-text, p, span');
                        if (label) questionText = label.textContent.trim();
                    }
                    // 못 찾으면 textarea 바로 위 형제 요소에서 찾기
                    if (!questionText) {
                        let prev = ta.previousElementSibling;
                        while (prev) {
                            if (prev.textContent.trim().length > 5) {
                                questionText = prev.textContent.trim();
                                break;
                            }
                            prev = prev.previousElementSibling;
                        }
                    }
                    results.push(questionText.substring(0, 500));
                }
                return results;
            }""")

            pre_questions = self.page.locator(
                "textarea[name='pre_question_answer']:visible"
            )
            q_count = await pre_questions.count()
            if q_count > 0:
                for qi in range(q_count):
                    q_text = questions_data[qi] if qi < len(questions_data) else ""
                    answer = generate_pre_question_answer(project.to_dict(), q_text)
                    await pre_questions.nth(qi).fill(answer)
                    print(f"[Wishket] 질문{qi+1}: {q_text[:50]}...")
                    print(f"[Wishket] 답변{qi+1}: {answer[:60]}...")

            # 5. 지원 내용 textarea
            body_ta = self.page.locator("textarea#apply_body:visible")
            if not await body_ta.count():
                body_ta = self.page.locator("textarea[name='body']:visible").first
            if not await body_ta.count():
                print("[Wishket] 지원 내용 입력 영역 없음")
                await self.screenshot("no_textarea")
                return False
            await body_ta.fill(proposal_text)
            await asyncio.sleep(1)

            # 6. 관련 포트폴리오 — "없습니다" 선택 (스크롤 밖이므로 JS)
            await self.page.evaluate("""() => {
                const radio = document.getElementById('has_not_related_portfolio');
                if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change', {bubbles: true})); }
            }""")
            print("[Wishket] 포트폴리오: 없습니다")

            # 7. 제출
            await self.screenshot("before_submit")

            # "프로젝트 지원" 버튼 → 확인 팝업 열림
            submit_btn = self.page.locator(
                'button:has-text("프로젝트 지원"):visible'
            ).first
            if not await submit_btn.count():
                print("[Wishket] 제출 버튼 없음")
                await self.screenshot("no_submit_btn")
                return False

            await submit_btn.click()
            await asyncio.sleep(2)

            # "제출 전 확인하기" 팝업 → "제출하기" 클릭
            confirm_btn = self.page.locator(
                'button:has-text("제출하기"):visible'
            ).first
            if await confirm_btn.count():
                await self.screenshot("confirm_popup")
                await confirm_btn.click()
                print("[Wishket] 최종 제출 확인")
                await asyncio.sleep(3)
            else:
                print("[Wishket] 확인 팝업 없음 — 바로 제출됨")

            self._save_applied(project.project_id)
            print(f"[Wishket] 지원 완료: {project.title}")
            await self.screenshot("applied")
            return True

        except Exception as e:
            print(f"[Wishket] 지원 에러: {e}")
            await self.screenshot("apply_error")
            return False
