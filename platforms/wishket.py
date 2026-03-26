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
            # 먼저 세션 유지 여부 확인 (persistent context)
            await self.page.goto("https://www.wishket.com/project/", timeout=30000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # networkidle 타임아웃은 무시
            await asyncio.sleep(2)

            current_url = self.page.url
            if "login" not in current_url.lower() and "wishket.com" in current_url:
                print("[Wishket] 이미 로그인된 상태 (세션 유지)")
                return True

            # 로그인 필요
            await self.page.goto("https://auth.wishket.com/login", timeout=30000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(2)

            # 로그인 페이지가 아니면 이미 로그인됨 (리다이렉트)
            if "login" not in self.page.url.lower():
                print("[Wishket] 로그인 성공 (리다이렉트)")
                return True

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

            try:
                await self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
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

            # 외주(도급) 탭 클릭 — 기간제(상주) 제외
            try:
                outsource_tab = self.page.locator('button:has-text("외주"), a:has-text("외주(도급)")').first
                if await outsource_tab.count():
                    await outsource_tab.click()
                    await asyncio.sleep(3)
                    print("[Wishket] 외주(도급) 탭 선택")
            except Exception:
                pass

            # JS로 프로젝트 데이터 직접 추출 — 기간제 제외
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

                    // 기간제/상주/구인 프로젝트 스킵
                    if (text.includes('기간제') || text.includes('상주')
                        || text.includes('구인') || text.includes('채용')) continue;

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

    async def _select_wishket_portfolio(self, project: Project):
        """위시켓 포트폴리오 선택 — + 버튼 → 포트폴리오 선택 → 선택완료 → 설명 입력"""
        try:
            from proposal_generator import _find_relevant_portfolio

            # 포트폴리오 추가 버튼이 있는지 확인
            has_portfolio_section = await self.page.evaluate(r"""() => {
                const addBtn = document.querySelector('[class*=portfolio] button, [class*=portfolio] a, button[class*=add-portfolio]');
                const hasRadio = document.getElementById('has_related_portfolio');
                return !!(addBtn || hasRadio);
            }""")

            if not has_portfolio_section:
                # 포트폴리오 섹션 자체가 없으면 "없습니다" 선택
                await self.page.evaluate(r"""() => {
                    const radio = document.getElementById('has_not_related_portfolio');
                    if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change', {bubbles: true})); }
                }""")
                print("[Wishket] 포트폴리오: 없습니다 (섹션 없음)")
                return

            # "있습니다" 라디오 선택
            await self.page.evaluate(r"""() => {
                const radio = document.getElementById('has_related_portfolio');
                if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change', {bubbles: true})); }
            }""")
            await asyncio.sleep(1)

            # + 버튼 클릭 (포트폴리오 추가)
            add_btn = self.page.locator("button:has-text('+'), a:has-text('추가'), [class*=add-portfolio]").first
            if await add_btn.count():
                await add_btn.click()
                await asyncio.sleep(2)
            else:
                # JS로 시도
                await self.page.evaluate(r"""() => {
                    const btns = document.querySelectorAll('button, a');
                    for (const b of btns) {
                        const text = b.textContent.trim();
                        if ((text === '+' || text.includes('포트폴리오 추가') || text.includes('포트폴리오를 추가'))
                            && b.offsetParent !== null) {
                            b.click();
                            return;
                        }
                    }
                }""")
                await asyncio.sleep(2)

            # 포트폴리오 선택 모달/리스트에서 관련 항목 선택
            portfolios = await self.page.evaluate(r"""() => {
                const items = [];
                // 모달 내 포트폴리오 목록
                document.querySelectorAll('[class*=portfolio] li, [class*=portfolio] div, [class*=modal] li').forEach(el => {
                    const text = el.textContent.trim();
                    const checkbox = el.querySelector('input[type=checkbox], input[type=radio]');
                    if (text.length > 3 && text.length < 200 && el.offsetParent !== null) {
                        items.push({
                            text: text.substring(0, 100),
                            hasCheckbox: !!checkbox,
                        });
                    }
                });
                return items;
            }""")

            if portfolios:
                # 첫 번째 포트폴리오 선택 (Playwright click)
                portfolio_items = self.page.locator("[class*=portfolio] li:visible, [class*=modal] li:visible")
                count = await portfolio_items.count()
                if count > 0:
                    await portfolio_items.first.click()
                    await asyncio.sleep(1)

                # "선택완료" 버튼 클릭
                done_btn = self.page.locator("button:has-text('선택완료'), button:has-text('선택 완료'), button:has-text('확인')").first
                if await done_btn.count():
                    await done_btn.click()
                    await asyncio.sleep(1)

                # 포트폴리오 설명 textarea
                relevant = _find_relevant_portfolio(project.to_dict())
                desc = f"해당 프로젝트와 유사한 {relevant} 개발 경험이 있습니다. " if relevant else ""
                desc += "기획부터 디자인, 개발, QA까지 원스톱으로 진행하며, 체계적인 프로세스를 통해 안정적인 결과물을 납품합니다."

                desc_ta = self.page.locator("textarea[name*=portfolio], textarea[class*=portfolio]").first
                if await desc_ta.count():
                    await desc_ta.fill(desc)

                print(f"[Wishket] 포트폴리오: 선택 완료")
            else:
                # 포트폴리오 목록이 안 나오면 "없습니다"로 폴백
                await self.page.evaluate(r"""() => {
                    const radio = document.getElementById('has_not_related_portfolio');
                    if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change', {bubbles: true})); }
                }""")
                print("[Wishket] 포트폴리오: 없습니다 (목록 없음)")

        except Exception as e:
            print(f"[Wishket] 포트폴리오 선택 에러: {e}")
            # 에러 시 "없습니다"로 폴백
            await self.page.evaluate(r"""() => {
                const radio = document.getElementById('has_not_related_portfolio');
                if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change', {bubbles: true})); }
            }""")
            print("[Wishket] 포트폴리오: 없습니다 (에러 폴백)")

    async def _select_wishket_experience(self):
        """관련 경력 + 이력서 라디오 처리 + 미체크 라디오 그룹 자동 선택"""
        try:
            result = await self.page.evaluate(r"""() => {
                const filled = [];

                // 1. 관련 경력: "없습니다" 선택 (has_related_employment)
                const empRadios = document.querySelectorAll('input[name="has_related_employment"]');
                if (empRadios.length >= 2) {
                    // 두 번째가 "없습니다"
                    empRadios[1].checked = true;
                    empRadios[1].dispatchEvent(new Event('change', {bubbles: true}));
                    empRadios[1].dispatchEvent(new Event('click', {bubbles: true}));
                    filled.push('has_related_employment = 없습니다');
                }

                // 2. 이력서: "없습니다" 선택 (has_resume)
                const resumeRadios = document.querySelectorAll('input[name="has_resume"]');
                if (resumeRadios.length >= 2) {
                    resumeRadios[1].checked = true;
                    resumeRadios[1].dispatchEvent(new Event('change', {bubbles: true}));
                    resumeRadios[1].dispatchEvent(new Event('click', {bubbles: true}));
                    filled.push('has_resume = 없습니다');
                }

                // 3. 나머지 미체크 라디오 그룹도 처리 (visible 여부 무시 — 커스텀 UI)
                const radioGroups = {};
                document.querySelectorAll('input[type=radio]').forEach(r => {
                    if (!radioGroups[r.name]) radioGroups[r.name] = [];
                    radioGroups[r.name].push(r);
                });
                for (const [name, radios] of Object.entries(radioGroups)) {
                    const anyChecked = radios.some(r => r.checked);
                    if (!anyChecked && radios.length > 0) {
                        radios[0].checked = true;
                        radios[0].dispatchEvent(new Event('change', {bubbles: true}));
                        filled.push('auto-radio: ' + name);
                    }
                }

                return filled;
            }""")
            await asyncio.sleep(1)

            for f in result:
                print(f"[Wishket] 라디오 처리: {f}")

        except Exception as e:
            print(f"[Wishket] 경력/라디오 처리 에러: {e}")

    async def _fill_empty_required_fields(self):
        """폼에서 빈 필수 필드(input, select)를 자동으로 채우기
        — 근무시간, 시작일 등 새로 추가되는 필드 대응"""
        filled = await self.page.evaluate(r"""() => {
            const filled = [];
            // 빈 visible input 찾기 (text, number, date 등)
            document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio])').forEach(el => {
                if (el.offsetParent === null) return;  // hidden
                if (el.value.trim()) return;  // 이미 채워짐
                const name = (el.name || el.id || '').toLowerCase();
                const label = el.closest('.form-group, .field-group, div, section')?.querySelector('label, .label');
                const labelText = label ? label.textContent.trim().toLowerCase() : '';
                const placeholder = (el.placeholder || '').toLowerCase();
                const combined = name + ' ' + labelText + ' ' + placeholder;

                let value = '';
                if (combined.includes('근무') && combined.includes('시간')) {
                    value = '8';  // 하루 8시간
                } else if (combined.includes('시작') && (combined.includes('일') || combined.includes('date'))) {
                    const d = new Date();
                    d.setDate(d.getDate() + 14);
                    value = d.toISOString().split('T')[0].replace(/-/g, '.');
                } else if (combined.includes('인원') || combined.includes('명')) {
                    value = '5';
                } else if (combined.includes('기간') && el.type !== 'date') {
                    value = '60';
                }

                if (value) {
                    el.value = value;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    filled.push({name: combined.trim().substring(0, 50), value});
                }
            });

            // 빈 visible select 찾기 (첫 번째 유효 옵션 선택)
            document.querySelectorAll('select').forEach(el => {
                if (el.offsetParent === null) return;
                if (el.value && el.value !== '' && el.selectedIndex > 0) return;
                const options = el.querySelectorAll('option');
                for (let i = 1; i < options.length; i++) {
                    if (options[i].value) {
                        el.value = options[i].value;
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        const name = el.name || el.id || '';
                        filled.push({name, value: options[i].textContent.trim()});
                        break;
                    }
                }
            });

            return filled;
        }""")

        for f in filled:
            print(f"[Wishket] 자동 필드 채움: {f['name']} = {f['value']}")

    async def apply(self, project: Project, proposal_text: str) -> bool:
        try:
            if not project.url:
                print(f"[Wishket] URL 없음: {project.title}")
                return False

            # 지원 폼 URL로 직접 이동
            apply_url = project.url.rstrip("/") + "/proposal/apply/"
            await self.page.goto(apply_url, timeout=30000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(3)

            # 로그인 페이지로 리다이렉트됐는지 확인 → 재로그인 시도
            if "login" in self.page.url:
                print("[Wishket] 세션 만료 — 재로그인 시도")
                logged_in = await self.login()
                if not logged_in:
                    print("[Wishket] 재로그인 실패")
                    return False
                await self.page.goto(apply_url, timeout=30000)
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                await asyncio.sleep(3)
                if "login" in self.page.url:
                    print("[Wishket] 재로그인 후에도 지원 폼 접근 불가")
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
            else:
                # 협의 후 결정인 경우 1000~3000만원 사이 기본값
                propose_budget = str(20000000)  # 2000만원
                print(f"[Wishket] 예산: 협의 → 기본값 20,000,000원")

            # 기간 계산: 공고기간 + 10일
            propose_term = ""
            if detail.get("term"):
                days = int(detail["term"]) + 10
                propose_term = str(days)
                print(f"[Wishket] 기간: {detail['term']}일 + 10 = {propose_term}일")
            else:
                propose_term = "60"
                print(f"[Wishket] 기간: 추출 실패 → 기본값 60일")

            # 1. 근무시작일
            from datetime import datetime as dt, timedelta
            start_date = (dt.now() + timedelta(days=14)).strftime("%Y.%m.%d")  # YYYY.MM.DD (끝 점 없음)

            # date_can_get_in (hidden) + date_can_get_in_input (visible datepicker) 또는 custom_launch_date
            await self.page.evaluate(
                f"""() => {{
                // hidden input
                const hidden = document.querySelector('input[name="date_can_get_in"]');
                if (hidden) {{
                    hidden.value = '{start_date}.';
                    hidden.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
                // visible datepicker input
                const picker = document.getElementById('date_can_get_in_input');
                if (picker) {{
                    picker.value = '{start_date}.';
                    picker.dispatchEvent(new Event('input', {{bubbles: true}}));
                    picker.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
                // custom_launch_date (기간제 프로젝트)
                const custom = document.querySelector('input[name="custom_launch_date"]');
                if (custom) {{
                    custom.value = '{start_date}';
                    custom.dispatchEvent(new Event('input', {{bubbles: true}}));
                    custom.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
            }}"""
            )
            print(f"[Wishket] 근무시작일: {start_date}")

            # "계약 체결 이후 즉시 시작 가능" 체크박스도 체크
            await self.page.evaluate(r"""() => {
                const cb = document.querySelector('input[name="launch_date_option"]');
                if (cb && !cb.checked) {
                    cb.checked = true;
                    cb.dispatchEvent(new Event('change', {bubbles: true}));
                    cb.dispatchEvent(new Event('click', {bubbles: true}));
                }
            }""")

            # 2. 예산 입력 (만원 단위)
            budget_input = self.page.locator("input[name='budget']:visible")
            if await budget_input.count() and propose_budget:
                await budget_input.fill(propose_budget)

            # 3. 기간 입력 (일 단위로 통일)
            # term_type 라디오에서 "일" 선택 (두 번째 옵션)
            await self.page.evaluate(r"""() => {
                const radios = document.querySelectorAll('input[name="term_type"]');
                if (radios.length >= 2) {
                    radios[1].checked = true;
                    radios[1].dispatchEvent(new Event('change', {bubbles: true}));
                    radios[1].dispatchEvent(new Event('click', {bubbles: true}));
                }
            }""")
            term_input = self.page.locator("input[name='term']:visible")
            if await term_input.count() and propose_term:
                await term_input.fill(propose_term)
                print(f"[Wishket] 기간: {propose_term}일 (일 단위 선택)")

            # 4. 클라이언트 질문 답변 (여러 개일 수 있음 — 각 질문별 맞춤 답변)
            questions_data = await self.page.evaluate(r"""() => {
                const textareas = document.querySelectorAll("textarea[name='pre_question_answer']");
                const results = [];
                for (const ta of textareas) {
                    let questionText = '';

                    // 방법 1: textarea 바로 위의 형제 요소들을 역순으로 탐색
                    // (질문 텍스트는 보통 textarea 바로 위에 있음)
                    let prev = ta.previousElementSibling;
                    while (prev) {
                        const text = prev.textContent.trim();
                        // "?" 또는 "입니까", "주세요" 등 질문 패턴이 포함된 텍스트
                        if (text.length > 10 && text.length < 500
                            && (text.includes('?') || text.includes('니까')
                                || text.includes('주세요') || text.includes('있다면')
                                || text.includes('무엇') || text.includes('어떻게')
                                || text.includes('경험') || text.includes('설명'))) {
                            questionText = text;
                            break;
                        }
                        // 10자 이상이고 질문처럼 보이는 텍스트
                        if (text.length > 15 && text.length < 500 && !questionText) {
                            questionText = text;
                        }
                        prev = prev.previousElementSibling;
                    }

                    // 방법 2: 부모 컨테이너에서 질문 아이콘(Q) 옆 텍스트 찾기
                    if (!questionText) {
                        let parent = ta.closest('.form-group') || ta.closest('.question-item')
                            || ta.closest('section') || ta.parentElement?.parentElement?.parentElement;
                        if (parent) {
                            // Q 아이콘이나 질문 라벨 찾기
                            const els = parent.querySelectorAll('p, span, div, label');
                            for (const el of els) {
                                const t = el.textContent.trim();
                                if (t.length > 10 && t.length < 500 && el.offsetParent !== null
                                    && !t.includes('500자') && !t.includes('이내로')) {
                                    questionText = t;
                                    break;
                                }
                            }
                        }
                    }

                    // 방법 3: 최후 수단 — 기본 질문
                    if (!questionText || questionText.length < 10) {
                        questionText = '유사한 프로젝트를 수행한 경험이 있다면 무엇입니까?';
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
                # 질문 추출 실패 체크
                has_bad_question = False
                for qi in range(q_count):
                    q_text = questions_data[qi] if qi < len(questions_data) else ""
                    # 질문 텍스트가 비어있으면 기본 질문으로 대체
                    if not q_text or len(q_text) < 5:
                        q_text = "유사한 프로젝트 경험이 있으신가요?"
                        if qi < len(questions_data):
                            questions_data[qi] = q_text
                        print(f"[Wishket] 질문{qi+1} 추출 실패 → 기본 질문으로 대체")

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

            # 6. 빈 필수 필드 자동 채우기 (근무시간, 시작일 등 새 필드 대응)
            await self._fill_empty_required_fields()

            # 7. 관련 경력 선택 ("있습니다" 선택)
            await self._select_wishket_experience()

            # 8. 관련 포트폴리오 선택
            await self._select_wishket_portfolio(project)

            # 9. 폼 전체 필드 진단
            form_dump = await self.page.evaluate(r"""() => {
                const fields = [];
                // 모든 input
                document.querySelectorAll('input').forEach(el => {
                    const name = el.name || el.id || '';
                    if (!name) return;
                    fields.push({
                        type: el.type || 'text',
                        name: name,
                        value: el.type === 'radio' || el.type === 'checkbox'
                            ? (el.checked ? 'CHECKED' : 'unchecked')
                            : (el.value || '').substring(0, 50),
                        visible: el.offsetParent !== null,
                    });
                });
                // 모든 textarea
                document.querySelectorAll('textarea').forEach(el => {
                    const name = el.name || el.id || '';
                    fields.push({
                        type: 'textarea',
                        name: name,
                        value: el.value ? el.value.substring(0, 30) + '...' : '(empty)',
                        visible: el.offsetParent !== null,
                    });
                });
                return fields;
            }""")
            print(f"\n[Wishket] === 폼 필드 진단 ===")
            for f in form_dump:
                print(f"  {f}")
            print("[Wishket] === 진단 끝 ===\n")

            # 제출 전 스크린샷
            await self.screenshot("before_submit")

            # 빈 필수 필드 검증
            empty_fields = await self.page.evaluate(r"""() => {
                const empties = [];
                document.querySelectorAll('input').forEach(el => {
                    if (el.offsetParent === null) return;
                    if (el.type === 'hidden' || el.type === 'checkbox' || el.type === 'radio') return;
                    const label = el.closest('.form-group, .field-group, div')?.querySelector('label');
                    const name = el.name || el.id || (label ? label.textContent.trim() : '');
                    if (!el.value.trim()) {
                        empties.push(name || el.type);
                    }
                });
                document.querySelectorAll('textarea').forEach(el => {
                    if (el.offsetParent === null) return;
                    const label = el.closest('.form-group, .field-group, div')?.querySelector('label');
                    const name = el.name || el.id || (label ? label.textContent.trim() : '');
                    if (!el.value.trim()) {
                        empties.push(name || 'textarea');
                    }
                });
                return empties;
            }""")
            if empty_fields:
                print(f"[Wishket] 빈 필수 필드 감지: {empty_fields}")

            # "프로젝트 지원" 버튼
            submit_btn = self.page.locator(
                'button:has-text("프로젝트 지원"):visible'
            ).first
            if not await submit_btn.count():
                print("[Wishket] 제출 버튼 없음")
                await self.screenshot("no_submit_btn")
                return False

            # 버튼이 disabled면 필수 필드 누락 — 어떤 필드가 비었는지 로그
            is_disabled = await submit_btn.is_disabled()
            if is_disabled:
                missing = await self.page.evaluate(r"""() => {
                    const missing = [];
                    // * 표시된 섹션 제목 찾기
                    document.querySelectorAll('label, h3, h4, .title, .form-label').forEach(el => {
                        if (el.offsetParent === null) return;
                        const text = el.textContent.trim();
                        if (!text.includes('*')) return;
                        // 해당 섹션의 입력 필드 확인
                        const parent = el.closest('section, .form-group, .field-group') || el.parentElement;
                        if (!parent) return;
                        const inputs = parent.querySelectorAll('input, textarea, select');
                        let filled = false;
                        inputs.forEach(inp => {
                            if (inp.type === 'radio' && inp.checked) filled = true;
                            else if (inp.type !== 'radio' && inp.value.trim()) filled = true;
                        });
                        if (!filled && inputs.length > 0) {
                            missing.push(text.substring(0, 50));
                        }
                    });
                    return missing;
                }""")
                print(f"[Wishket] 제출 버튼 비활성 — 누락 필드: {missing}")
                await self.screenshot("submit_disabled")
                return False

            await submit_btn.click()
            await asyncio.sleep(2)

            # 검증 에러 메시지 확인
            has_error = await self.page.evaluate(r"""() => {
                const errorEls = document.querySelectorAll(
                    '.error-message, .field-error, .text-danger, ' +
                    '[class*=error], [class*=invalid]'
                );
                for (const el of errorEls) {
                    if (el.offsetParent !== null && el.textContent.trim().length > 0) {
                        return el.textContent.trim().substring(0, 200);
                    }
                }
                return '';
            }""")
            if has_error:
                print(f"[Wishket] 폼 검증 에러: {has_error}")
                await self.screenshot("validation_error")
                return False

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
                # 확인 팝업이 없으면 제출 실패일 수 있음 — 스크린샷 찍고 검증
                await self.screenshot("no_confirm_popup")
                print("[Wishket] 확인 팝업 없음 — 제출 실패 가능성")

            # 제출 후 검증: 성공 메시지 또는 URL 변경 확인
            await asyncio.sleep(2)
            current_url = self.page.url
            page_text = await self.page.evaluate("() => document.body.innerText.substring(0, 3000)")
            submitted = (
                "지원하셨습니다" in page_text
                or "지원 완료" in page_text
                or "지원이 완료" in page_text
                or ("지원 내역" in page_text and "/proposal/apply/" not in current_url)
                or "/project/" in current_url and "/proposal/apply/" not in current_url
            )

            # 에러 메시지가 있으면 실패
            has_form_error = (
                "다시 입력해주세요" in page_text
                or "필수 항목" in page_text
                or "입력해 주세요" in page_text
            )
            if has_form_error:
                # 에러 내용 추출
                error_lines = [l.strip() for l in page_text.split("\n")
                               if "입력" in l or "필수" in l or "확인" in l]
                print(f"[Wishket] 제출 에러: {error_lines[:3]}")
                await self.screenshot("submit_form_error")
                return False

            if submitted:
                self._save_applied(project.project_id)
                print(f"[Wishket] 지원 완료 확인: {project.title}")
                await self.screenshot("applied")
                return True
            else:
                print(f"[Wishket] 제출 검증 실패 — 성공 메시지 없음")
                print(f"[Wishket] URL: {current_url}")
                await self.screenshot("submit_not_verified")
                return False

        except Exception as e:
            print(f"[Wishket] 지원 에러: {e}")
            await self.screenshot("apply_error")
            return False
