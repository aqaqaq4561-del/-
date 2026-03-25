"""
프리모아 (Freemoa) 크롤러 — 네이버 소셜 로그인
- 로그인: 네이버 OAuth
- 프로젝트 목록: https://www.freemoa.net/m4/s41
"""
import os
import re
import asyncio
from .base import BasePlatform, Project


class FreemoaPlatform(BasePlatform):
    name = "freemoa"

    async def login(self) -> bool:
        """프리모아 로그인 — persistent context의 네이버 쿠키로 자동 로그인 시도"""
        try:
            # 1. 프리모아 접속해서 이미 로그인 상태인지 확인
            await self.page.goto("https://www.freemoa.net/m4/s41", timeout=60000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            if await self._check_logged_in():
                print("[Freemoa] 이미 로그인 상태")
                return True

            # 2. 네이버 OAuth로 자동 로그인 시도
            #    persistent context에 네이버 쿠키가 있으면 캡차 없이 통과
            print("[Freemoa] 네이버 로그인 시도...")
            await self.page.goto("https://www.freemoa.net/m0/s02", timeout=60000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            # 네이버 로그인 버튼 → 팝업으로 열림
            naver_btn = self.page.locator("text=네이버 로그인").first
            if not await naver_btn.count():
                print("[Freemoa] 네이버 로그인 버튼 없음")
                return False

            # 팝업 캡처
            context = self.page.context
            async with context.expect_page() as popup_info:
                await naver_btn.click()

            popup = await popup_info.value
            await popup.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            # 네이버에 이미 로그인 상태면 바로 리다이렉트
            if "nid.naver.com" not in popup.url or popup.is_closed():
                await asyncio.sleep(3)
                if await self._check_logged_in():
                    print("[Freemoa] 네이버 자동 로그인 성공")
                    return True

            # 네이버 로그인 폼이 보이면 자동 입력
            id_input = popup.locator("#id")
            if await id_input.count() and not popup.is_closed():
                naver_id = os.getenv("NAVER_ID")
                naver_pw = os.getenv("NAVER_PW")
                if not naver_id or not naver_pw:
                    print("[Freemoa] 네이버 계정 정보 없음 (.env)")
                    try:
                        await popup.close()
                    except Exception:
                        pass
                    return False

                print("[Freemoa] 네이버 자동 로그인 시도...")
                # JS evaluate로 입력 (자동입력 방지 우회)
                await popup.evaluate(f"""() => {{
                    const id = document.getElementById('id');
                    const pw = document.getElementById('pw');
                    if (id) {{
                        id.value = '{naver_id}';
                        id.dispatchEvent(new Event('input', {{bubbles: true}}));
                    }}
                    if (pw) {{
                        pw.value = '{naver_pw}';
                        pw.dispatchEvent(new Event('input', {{bubbles: true}}));
                    }}
                }}""")
                await asyncio.sleep(1)

                # 로그인 버튼 클릭
                login_btn = popup.locator("#log\\.login, button.btn_login, input.btn_global[type='submit']").first
                if await login_btn.count():
                    await login_btn.click()
                else:
                    # 폼 submit 폴백
                    await popup.evaluate("() => { document.querySelector('form#frmNIDLogin, form')?.submit(); }")

                await asyncio.sleep(5)

                # 캡차 체크
                if not popup.is_closed() and "captcha" in popup.url.lower():
                    print("[Freemoa] 캡차 발생 — 'python main.py --save-login'으로 수동 로그인 필요")
                    await self.screenshot("naver_captcha")
                    try:
                        await popup.close()
                    except Exception:
                        pass
                    return False

            # 리다이렉트 대기
            await asyncio.sleep(5)
            await self.page.goto("https://www.freemoa.net/m4/s41", timeout=60000)
            await asyncio.sleep(2)

            if await self._check_logged_in():
                print("[Freemoa] 네이버 로그인 성공")
                return True

            print("[Freemoa] 로그인 실패")
            await self.screenshot("login_failed")
            return False

        except Exception as e:
            print(f"[Freemoa] 로그인 에러: {e}")
            await self.screenshot("login_error")
            return False

    async def _check_logged_in(self) -> bool:
        """프리모아 로그인 상태 확인 — 헤더에서 정확히 체크"""
        try:
            result = await self.page.evaluate("""() => {
                const header = document.querySelector('header');
                if (!header) return false;
                const text = header.textContent || '';
                // 로그인 안 된 상태: "로그인" + "회원가입" 이 보임
                if (text.includes('회원가입')) return false;
                // 로그인 된 상태: "X Block" 또는 프로필 관련 텍스트
                if (text.includes('X Block') || text.includes('마이페이지') || text.includes('로그아웃')) return true;
                // 로그인 페이지에 있으면 당연히 안됨
                if (document.querySelector('.pop-auth-form')) return false;
                return false;
            }""")
            return result
        except Exception:
            return False

    async def fetch_projects(self) -> list[Project]:
        projects = []
        try:
            await self.page.goto(
                "https://www.freemoa.net/m4/s41", timeout=60000
            )
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(3)

            # JS로 모든 카드 데이터를 한번에 추출
            card_data_list = await self.page.evaluate("""() => {
                const items = document.querySelectorAll('li.proj-list-item_li_new');
                return Array.from(items).map(li => {
                    const titleDiv = li.querySelector('div.projTitle');
                    const pno = titleDiv?.getAttribute('data-pno') || '';
                    const title = li.querySelector('p.title')?.textContent?.trim() || '';

                    // 타입: p.b=도급, p.d=상주
                    const typeEl = li.querySelector('p.b, p.d');
                    const projType = typeEl?.textContent?.trim() || '';

                    // 전체 텍스트에서 정보 추출
                    const allText = li.textContent || '';

                    // 예산: "예상비용5,000 ~ 10,000 만원" 또는 "월 임금300 ~ 500 만원"
                    const budgetMatch = allText.match(/(?:예상비용|월\\s*임금)\\s*([\\d,]+)\\s*~\\s*([\\d,]+)\\s*만\\s*원/);
                    const budgetMin = budgetMatch ? budgetMatch[1].replace(/,/g, '') : '';
                    const budgetMax = budgetMatch ? budgetMatch[2].replace(/,/g, '') : '';
                    const budgetStr = budgetMatch ? budgetMatch[0].replace(/^[^\\d]+/, '').trim() : '';

                    // 기간
                    const durationMatch = allText.match(/예상기간\\s*(\\d+일)/);
                    const duration = durationMatch ? durationMatch[1] : '';

                    // li 직계 자식 div.projectInfo들에서 추출
                    const infoDivs = li.querySelectorAll(':scope > div.projectInfo');
                    // 첫 번째 projectInfo = 카테고리
                    let category = '';
                    if (infoDivs.length >= 1) {
                        category = infoDivs[0].textContent.trim();
                    }
                    // 세 번째 projectInfo = 설명 (있으면)
                    let description = '';
                    if (infoDivs.length >= 3) {
                        description = infoDivs[2].textContent.trim().substring(0, 500);
                    }

                    return {
                        pno, title, projType, budgetStr,
                        budgetMin: budgetMin ? parseInt(budgetMin) * 10000 : 0,
                        budgetMax: budgetMax ? parseInt(budgetMax) * 10000 : 0,
                        duration, category, description,
                    };
                });
            }""")

            if not card_data_list:
                print("[Freemoa] 프로젝트 카드를 찾지 못함")
                await self.screenshot("no_projects")
                return projects

            for data in card_data_list:
                try:
                    pid = data["pno"]
                    title = data["title"]
                    if not pid or not title:
                        continue

                    if self.is_already_applied(pid):
                        continue

                    project = Project(
                        platform="freemoa",
                        project_id=pid,
                        title=title,
                        description=data["description"] or title,
                        budget=data["budgetStr"],
                        budget_min=data["budgetMin"],
                        budget_max=data["budgetMax"],
                        duration=data["duration"],
                        category=data["category"],
                        url=f"https://www.freemoa.net/m4/s41",
                    )
                    projects.append(project)

                except Exception as e:
                    print(f"[Freemoa] 카드 파싱 에러: {e}")
                    continue

            print(f"[Freemoa] {len(projects)}개 프로젝트 수집")
        except Exception as e:
            print(f"[Freemoa] 크롤링 에러: {e}")
            await self.screenshot("crawl_error")

        return projects

    async def _select_portfolio(self, project: Project):
        """포트폴리오 추가하기 모달 → 관련 포트폴리오 선택 → 추가 버튼 클릭"""
        try:
            # 1. "포트폴리오 추가하기" 클릭 → 모달 열기
            await self.page.evaluate(r"""() => {
                const btn = document.querySelector('div.portFolioAppend.modalBtn_new');
                if (btn) btn.click();
            }""")
            await asyncio.sleep(2)

            # 2. 모달 내 포트폴리오 카드 목록
            cards = await self.page.evaluate(r"""() => {
                const wrap = document.querySelector('.modalProjectPushWrap');
                if (!wrap) return [];
                return Array.from(wrap.querySelectorAll('[data-pfno]')).map(c => ({
                    pfno: c.getAttribute('data-pfno'),
                    text: c.textContent.trim().substring(0, 120),
                }));
            }""")

            if not cards:
                print("[Freemoa] 포트폴리오 없음 → 진행 경험 없음")
                await self.page.evaluate(r"""() => {
                    const labels = document.querySelectorAll('label.issamejointRadioLabel');
                    if (labels[1]) labels[1].click();
                }""")
                return

            # 3. 프로젝트와 관련 있는 포트폴리오 매칭 (여러 개 가능)
            proj_text = f"{project.title} {project.description}".lower()
            keyword_map = {
                "커머스": ["쇼핑몰", "공동구매", "특가", "농수산물"],
                "쇼핑": ["쇼핑몰", "공동구매", "특가", "농수산물"],
                "결제": ["결제", "현금"],
                "예약": ["예약", "뷰티", "K-bea", "공간임대"],
                "매칭": ["매칭", "상담", "손해사정"],
                "erp": ["견적관리", "CRM", "ERP"],
                "관리": ["견적관리", "CRM", "ERP"],
                "nft": ["NFT", "경매", "디지털"],
                "블록체인": ["NFT", "STO", "투자"],
                "투자": ["STO", "투자"],
                "건강": ["건강", "피트니스", "운동"],
                "운동": ["피트니스", "운동", "네트워크"],
                "반려": ["반려동물", "커뮤니티", "펫"],
                "커뮤니티": ["반려동물", "커뮤니티"],
                "교육": ["발달지원"],
                "의료": ["상담", "손해사정"],
                "뷰티": ["뷰티", "공간임대", "K-bea"],
                "예매": ["예약", "K-bea", "공간임대"],
                "티켓": ["예약", "K-bea"],
                "자동차": ["중고차", "거래"],
                "중고": ["중고차", "거래"],
                "키오스크": ["결제", "쇼핑몰"],
            }

            # 각 카드의 매칭 점수
            scored = []
            for card in cards:
                card_text = card["text"].lower()
                score = 0
                for kw, matches in keyword_map.items():
                    if kw in proj_text:
                        for m in matches:
                            if m.lower() in card_text:
                                score += 1
                scored.append((card["pfno"], score, card["text"][:40]))

            # score > 0인 것들 중 상위 3개, 없으면 첫 번째 1개
            selected = sorted([s for s in scored if s[1] > 0], key=lambda x: -x[1])[:3]
            if not selected:
                selected = [scored[0]]

            pfno_list = [s[0] for s in selected]
            for s in selected:
                print(f"[Freemoa] 포트폴리오 선택: {s[2]} (score={s[1]})")

            # 4. 모달에서 해당 카드들 클릭 (Playwright locator 사용)
            for pfno in pfno_list:
                card = self.page.locator(f'.modalProjectPushWrap [data-pfno="{pfno}"]')
                if await card.count():
                    await card.scroll_into_view_if_needed()
                    await card.click()
                    await asyncio.sleep(1)

            # 선택 상태 확인
            checked_count = await self.page.evaluate(r"""() => {
                const wrap = document.querySelector('.modalProjectPushWrap');
                if (!wrap) return 0;
                return wrap.querySelectorAll('.portFolioCard.checked, [data-pfno].checked, [data-pfno].on, [data-pfno].selected').length;
            }""")
            print(f"[Freemoa] 선택된 카드: {checked_count}개")

            await asyncio.sleep(1)

            # 5. "선택한 N개의 포트폴리오 추가" 버튼 클릭 (p.portFolioPush)
            add_btn = self.page.locator("p.portFolioPush")
            if await add_btn.count():
                await add_btn.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
                btn_text = await add_btn.inner_text()
                print(f"[Freemoa] 추가 버튼 텍스트: {btn_text}")
                await add_btn.click()
            else:
                print("[Freemoa] 추가 버튼 못 찾음")
            clicked = ""
            if clicked:
                print(f"[Freemoa] 추가 버튼 클릭: {clicked}")
            else:
                print("[Freemoa] '선택한 N개 포트폴리오 추가' 버튼 못 찾음")

            await asyncio.sleep(2)

        except Exception as e:
            print(f"[Freemoa] 포트폴리오 선택 에러: {e}")
            await self.page.evaluate(r"""() => {
                const labels = document.querySelectorAll('label.issamejointRadioLabel');
                if (labels[1]) labels[1].click();
            }""")

    async def modify(self, project: Project, proposal_text: str = None) -> bool:
        """이미 지원한 프로젝트의 지원서 수정 (포트폴리오 첨부 등)
        경로: 프로필 → 프로젝트관리 → 상세열기 → 나의 지원서 (applyview=y)
        """
        try:
            pid = project.project_id

            dialogs = []

            async def handle_dialog(dialog):
                dialogs.append(dialog.message)
                print(f"[Freemoa] 다이얼로그: {dialog.message}")
                await dialog.accept()

            self.page.on("dialog", handle_dialog)

            # 1. 로그인 확인
            await self.page.goto("https://www.freemoa.net/m4/s41", timeout=60000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)
            if not await self._check_logged_in():
                await self.login()
                await self.page.goto("https://www.freemoa.net/m4/s41", timeout=60000)
                await self.page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(2)
                if not await self._check_logged_in():
                    print("[Freemoa] 로그인 실패")
                    return False
            print("[Freemoa] 로그인 확인됨")

            # 2. 프로젝트 관리 페이지
            await self.page.goto(
                "https://www.freemoa.net/m5/s58?status=0&tabstatus=all",
                timeout=60000,
            )
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(3)

            if "/m0/s02" in self.page.url:
                print("[Freemoa] 세션 만료 — 재로그인")
                await self.login()
                await self.page.goto(
                    "https://www.freemoa.net/m5/s58?status=0&tabstatus=all",
                    timeout=60000,
                )
                await self.page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(3)

            print(f"[Freemoa] 프로젝트 관리: {self.page.url}")

            # 3. 나의 지원서 URL로 직접 이동 (applyview=y)
            modify_url = f"https://www.freemoa.net/m4/s41?page=1&pno={pid}&first_pno={pid}&applyview=y"
            await self.page.goto(modify_url, timeout=60000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(3)

            if "/m0/s02" in self.page.url:
                print("[Freemoa] 지원서 페이지 접근 실패 (로그인 필요)")
                return False

            print(f"[Freemoa] 지원서 수정 페이지: {self.page.url}")

            # 4. 지원 폼이 열렸는지 확인
            form_opened = await self.page.evaluate(r"""() => {
                const dur = document.getElementById('projectApplyDuring');
                const cost = document.getElementById('projectApplyCost');
                const ta = document.getElementById('projectApplyText');
                return (dur || cost || ta) ? true : false;
            }""")

            if not form_opened:
                print("[Freemoa] 지원서 수정 폼 없음")
                await self.screenshot("modify_no_form")
                return False

            print("[Freemoa] 지원서 수정 폼 열림")

            # 5. 포트폴리오 추가 (핵심 수정 내용)
            # 먼저 "진행 경험 있음" 선택
            await self.page.evaluate(r"""() => {
                const labels = document.querySelectorAll('label.issamejointRadioLabel');
                if (labels[0]) labels[0].click();
            }""")
            await asyncio.sleep(1)

            await self._select_portfolio(project)

            # 6. 지원 내용도 수정하려면 업데이트
            if proposal_text:
                escaped = proposal_text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                await self.page.evaluate(f"""() => {{
                    const ta = document.getElementById('projectApplyText');
                    if (ta) {{
                        ta.value = `{escaped}`;
                        ta.dispatchEvent(new Event('input', {{bubbles: true}}));
                    }}
                }}""")
                print(f"[Freemoa] 지원 내용 수정: {len(proposal_text)}자")

            await self.screenshot("modify_before_submit")

            # 7. "프로젝트 수정 완료하기" 또는 "프로젝트 지원 완료하기" 클릭
            await self.page.evaluate(r"""() => {
                const btn = document.getElementById('projectApplyProcess');
                if (btn) btn.click();
            }""")
            await asyncio.sleep(5)

            # 8. 결과 확인
            for msg in dialogs:
                if "완료" in msg or "수정" in msg:
                    print(f"[Freemoa] 지원서 수정 완료: {project.title}")
                    await self.screenshot("modify_done")
                    return True
                if "선택" in msg or "입력" in msg:
                    print(f"[Freemoa] 수정 실패 — {msg}")
                    await self.screenshot("modify_failed")
                    return False

            page_text = await self.page.evaluate("() => document.body.innerText.substring(0, 2000)")
            if "내 지원서 확인" in page_text or "지원서 확인" in page_text:
                print(f"[Freemoa] 지원서 수정 완료: {project.title}")
                return True

            print("[Freemoa] 수정 결과 불명확")
            await self.screenshot("modify_unknown")
            return False

        except Exception as e:
            print(f"[Freemoa] 지원서 수정 에러: {e}")
            await self.screenshot("modify_error")
            return False

    async def apply(self, project: Project, proposal_text: str) -> bool:
        try:
            pid = project.project_id

            # 다이얼로그 캡처
            dialog_messages = []

            async def handle_dialog(dialog):
                dialog_messages.append(dialog.message)
                print(f"[Freemoa] 다이얼로그: {dialog.message}")
                await dialog.accept()

            self.page.on("dialog", handle_dialog)

            # 1. 먼저 로그인 확인
            detail_url = f"https://www.freemoa.net/m4/s41?page=1&pno={pid}&first_pno={pid}"

            # 프리모아 메인으로 이동해서 로그인 상태 체크
            await self.page.goto("https://www.freemoa.net/m4/s41", timeout=60000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            if not await self._check_logged_in():
                print("[Freemoa] 로그인 필요")
                logged_in = await self.login()
                if not logged_in:
                    print("[Freemoa] 로그인 실패")
                    return False

                # 로그인 후 재확인
                await self.page.goto("https://www.freemoa.net/m4/s41", timeout=60000)
                await self.page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(2)
                if not await self._check_logged_in():
                    print("[Freemoa] 로그인 실패 (재확인)")
                    await self.screenshot("login_failed_recheck")
                    return False

            print("[Freemoa] 로그인 확인됨")

            # 2. 프로젝트 상세 페이지로 이동
            await self.page.goto(detail_url, timeout=60000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(3)

            # "프로젝트 지원하기" 버튼 클릭
            apply_btn = self.page.locator("#projectApply")
            if not await apply_btn.count():
                print("[Freemoa] '프로젝트 지원하기' 버튼 없음")
                await self.screenshot("no_apply_btn")
                return False

            await apply_btn.click()
            await asyncio.sleep(3)

            # 로그인 필요 다이얼로그 → 재로그인 후 재시도
            if dialog_messages and "로그인" in dialog_messages[-1]:
                print("[Freemoa] 지원 클릭 후 로그인 필요 — 재로그인")
                dialog_messages.clear()
                logged_in = await self.login()
                if not logged_in:
                    return False
                await self.page.goto(detail_url, timeout=60000)
                await self.page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(3)
                apply_btn = self.page.locator("#projectApply")
                await apply_btn.click()
                await asyncio.sleep(3)

            # 3. 지원 폼이 열렸는지 확인 (인라인 폼 — 작업기간/지원금액 필드)
            form_opened = await self.page.evaluate("""() => {
                const dur = document.getElementById('projectApplyDuring');
                const cost = document.getElementById('projectApplyCost');
                return (dur && dur.offsetParent !== null) || (cost && cost.offsetParent !== null);
            }""")

            if not form_opened:
                # 인라인 폼이 아니면 팝업 폼 확인
                form_opened = await self.page.evaluate("""() => {
                    const popup = document.getElementById('projectApplyPopup');
                    return popup && getComputedStyle(popup).display !== 'none';
                }""")

            if not form_opened:
                print("[Freemoa] 지원 폼이 열리지 않음")
                await self.screenshot("form_not_opened")
                return False

            print("[Freemoa] 지원 폼 열림")

            # 4. 폼 필드 채우기
            dur_match = re.search(r"(\d+)", project.duration or "")
            dur_days = dur_match.group(1) if dur_match else "60"

            if project.budget_min > 0:
                cost = str(project.budget_min // 10000)
            elif project.budget_max > 0:
                cost = str(project.budget_max // 10000)
            else:
                cost = "1000"

            escaped_proposal = proposal_text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

            await self.page.evaluate(f"""() => {{
                function setVal(el, val) {{
                    if (!el) return;
                    el.value = val;
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}

                // 작업기간 (인라인 폼)
                setVal(document.getElementById('projectApplyDuring'), '{dur_days}');
                // 지원금액 (인라인 폼)
                setVal(document.getElementById('projectApplyCost'), '{cost}');

                // 투입 인력
                const persons = document.querySelectorAll('input[name="residePerson0"]');
                const pays = document.querySelectorAll('input[name="residePay0"]');
                if (persons[0]) setVal(persons[0], '3');
                if (pays[0]) setVal(pays[0], '400');
                if (persons[1]) setVal(persons[1], '3');
                if (pays[1]) setVal(pays[1], '500');

                // 유사 프로젝트 진행경험 — "진행 경험 있음" 선택
                const labels = document.querySelectorAll('label.issamejointRadioLabel');
                if (labels[0]) labels[0].click();

                // 지원 내용 textarea (인라인)
                const ta = document.getElementById('projectApplyText');
                if (ta) setVal(ta, `{escaped_proposal}`);

                // 팝업 폼 필드도 채우기 (팝업 방식인 경우)
                const popup = document.getElementById('projectApplyPopup');
                if (popup) {{
                    const titleInput = popup.querySelector('input[name="title"]');
                    if (titleInput) setVal(titleInput, '{project.title[:50]}');
                    const costInput = popup.querySelector('input[name="cost"], input#costForFreeInput');
                    if (costInput) setVal(costInput, '{cost}');
                    const duringInput = popup.querySelector('input[name="during"]');
                    if (duringInput) setVal(duringInput, '{dur_days}');
                    const txtArea = popup.querySelector('textarea[name="txt"]');
                    if (txtArea) setVal(txtArea, `{escaped_proposal}`);
                }}
            }}""")
            await asyncio.sleep(2)

            # 포트폴리오 추가하기 클릭 → 모달에서 관련 포트폴리오 선택
            await self._select_portfolio(project)

            await asyncio.sleep(1)
            print(f"[Freemoa] 폼 입력 완료: 기간={dur_days}일 비용={cost}만원 내용={len(proposal_text)}자")

            # 스크롤해서 제출 버튼 보이게
            await self.page.evaluate("""() => {
                const btn = document.getElementById('projectApplyProcess') || document.getElementById('projectApplyBtn');
                if (btn) btn.scrollIntoView({behavior: 'instant', block: 'center'});
            }""")
            await asyncio.sleep(1)
            await self.screenshot("before_submit")

            # 5. 제출 — 인라인 폼과 팝업 폼 모두 시도
            dialog_messages.clear()
            await self.page.evaluate("""() => {
                // 인라인 폼 제출
                const btn1 = document.getElementById('projectApplyProcess');
                if (btn1 && btn1.offsetParent !== null) { btn1.click(); return; }
                // 팝업 폼 제출
                const btn2 = document.getElementById('projectApplyBtn');
                if (btn2) btn2.click();
            }""")
            await asyncio.sleep(5)

            # 6. 결과 확인
            await self.screenshot("after_submit")

            # 다이얼로그로 결과 확인
            for msg in dialog_messages:
                if "완료" in msg or "성공" in msg:
                    self._save_applied(project.project_id)
                    print(f"[Freemoa] 지원 완료: {project.title}")
                    return True
                if "로그인" in msg:
                    print(f"[Freemoa] 제출 실패 — {msg}")
                    return False
                if "선택" in msg or "입력" in msg or "작성" in msg:
                    print(f"[Freemoa] 필수 항목 누락 — {msg}")
                    await self.screenshot("missing_field")
                    return False

            # 페이지 텍스트로 확인
            page_text = await self.page.evaluate("() => document.body.innerText.substring(0, 3000)")
            if "내 지원서 확인" in page_text:
                self._save_applied(project.project_id)
                print(f"[Freemoa] 지원 완료: {project.title}")
                return True
            if "지원 완료" in page_text or "수정 완료" in page_text:
                self._save_applied(project.project_id)
                print(f"[Freemoa] 지원 완료: {project.title}")
                return True

            # 팝업 확인
            for popup_id in ["projectAppliedPopup", "alertMessagePopup"]:
                visible = await self.page.evaluate(f"() => {{ const el = document.getElementById('{popup_id}'); return el && getComputedStyle(el).display !== 'none'; }}")
                if visible:
                    popup_text = await self.page.locator(f"#{popup_id}").inner_text()
                    print(f"[Freemoa] 팝업({popup_id}): {popup_text[:100]}")
                    if "지원" in popup_text:
                        self._save_applied(project.project_id)
                        print(f"[Freemoa] 지원 완료: {project.title}")
                        return True

            print("[Freemoa] 지원 결과 불명확 — 스크린샷 확인 필요")
            return False

        except Exception as e:
            print(f"[Freemoa] 지원 에러: {e}")
            await self.screenshot("apply_error")
            return False
