"""
X-Block 맞춤 지원서 생성기 (Claude API 사용)
"""
import json
import os
from pathlib import Path

try:
    import anthropic
except ImportError:
    anthropic = None


def load_company_info():
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)["company"]


def generate_proposal(project_info: dict, api_key: str = None) -> str:
    """프로젝트 정보를 받아 X-Block 맞춤 지원서를 생성합니다."""
    company = load_company_info()

    portfolio_text = "\n".join(
        f"- {p['name']}: {p['description']} ({p['category']})"
        for p in company["portfolio"]
    )

    # 플랫폼별 글자 수 제한
    platform = project_info.get("platform", "")
    char_limits = {"wishket": 2000, "kmong": 2000, "freemoa": 1000}
    max_chars = char_limits.get(platform, 1000)

    # 포트폴리오 설명 (프로젝트 고유명 대신 일반 설명으로)
    portfolio_list = "\n".join(
        f"- {name}: 키워드={', '.join(kws)}"
        for name, kws in _ALL_PORTFOLIOS.items()
    )

    prompt = f"""당신은 앱 개발 외주 업체의 영업 담당자입니다.
아래 프로젝트 공고에 지원서를 작성해주세요.

[프로젝트 정보]
- 제목: {project_info.get("title", "")}
- 설명: {project_info.get("description", "")[:400]}
- 예산: {project_info.get("budget", "")}
- 기간: {project_info.get("duration", "")}

[우리 회사 정보 — 자연스럽게 녹여서 쓸 것]
- 총 15명 구성의 앱 개발 전문 회사
- 프로젝트당 PM, 프론트엔드 개발자, 백엔드 개발자, 개발 아키텍처, DB 설계자, UI/UX 디자이너 총 6명 투입
- 빠르고 정확한 개발 원칙
- 계약 후 바로 기획 및 디자인 착수
- 디자인 검수, 개발 중간검수, 최종 검수 총 3번의 미팅 (필요시 추가 미팅 가능)
- 기획-디자인-개발-QA 원스톱 턴키 개발

[유사 수행 경험 목록 — 관련 있는 것만 1~2개 골라서 언급]
{portfolio_list}

[작성 규칙 — 반드시 지킬 것]
1. 글자 수: 반드시 {max_chars}자 이내로 작성 (초과 금지)
2. 실제 사람이 쓴 것처럼 자연스럽게 쓸 것. 편지체("대표님께", "귀사의") 금지.
3. "안녕하세요"로 시작, "감사합니다"로 끝. 제목/헤딩 넣지 말 것.
4. 마크다운(#, *, **, ```) 절대 사용 금지. 순수 텍스트만.
5. 회사명, URL, 연락처 절대 포함 금지. "저희", "저희 팀"으로만 표현.
6. 포트폴리오 프로젝트 고유명(클레임브릿지, 봉선장, PetPle 등) 절대 사용 금지. "수산물 쇼핑몰 앱", "보험금 청구 매칭 앱" 같이 일반적으로 설명.
7. 없는 경험을 지어내지 말 것. 포트폴리오 목록에 있는 경험만 언급.
8. 구체적이지만 과장하지 말 것. 담담하고 신뢰감 있는 톤.
"""

    key = api_key or os.getenv("ANTHROPIC_API_KEY")

    if not key:
        return _generate_template_proposal(project_info, company)

    if anthropic is None:
        print("[WARN] anthropic 패키지 미설치. 템플릿 지원서를 생성합니다.")
        return _generate_template_proposal(project_info, company)

    try:
        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        result = message.content[0].text
        # 잘림 방지: "감사합니다"로 안 끝나면 마지막 완성 문장까지 자르고 "감사합니다." 추가
        if not result.rstrip().endswith("감사합니다.") and not result.rstrip().endswith("감사합니다"):
            # 마지막 마침표/느낌표/물음표 위치 찾기
            last_end = max(result.rfind(". "), result.rfind(".\n"), result.rfind("다."), result.rfind("다.\n"))
            if last_end > len(result) // 2:  # 절반 이상 있으면 거기까지 자르기
                result = result[:last_end + 2].rstrip()
            result = result.rstrip() + "\n\n감사합니다."
        return result
    except Exception as e:
        print(f"[WARN] 지원서 API 에러: {e}")
        return _generate_template_proposal(project_info, company)


PORTFOLIO_URL = "https://xblocksystem.bubbleapps.io/"

# 전체 포트폴리오 매칭 키워드 (config.json + 실제 포트폴리오)
_ALL_PORTFOLIOS = {
    "보험금 청구 전문가 매칭 앱": ["보험", "청구", "매칭", "금융", "핀테크", "사정", "인슈어"],
    "실물 자산 연계 NFT 경매 플랫폼": ["블록체인", "NFT", "경매", "토큰", "USDT", "자산", "디지털", "지갑", "web3"],
    "디지털 자산 투자 플랫폼": ["블록체인", "STO", "투자", "토큰", "증권", "디지털자산"],
    "간편결제 앱": ["결제", "페이", "송금", "핀테크", "간편결제", "PG"],
    "B2B 식자재 유통 플랫폼": ["유통", "식자재", "B2B", "자영업", "요식업", "도매"],
    "공동구매 커머스 앱": ["커머스", "쇼핑", "공동구매", "소셜커머스", "특가", "쇼핑몰", "자사몰"],
    "수산물 직거래 쇼핑몰 앱": ["커머스", "직거래", "식품", "수산", "배달", "주문"],
    "맞춤형 건강관리 플랫폼": ["헬스케어", "건강", "영양", "맞춤추천", "웰니스", "건기식", "의료"],
    "운동 커뮤니티 소셜 앱": ["피트니스", "운동", "커뮤니티", "소셜", "모임", "헬스"],
    "중고차 매매 매칭 플랫폼": ["자동차", "중고차", "매칭", "딜러", "모빌리티", "중고"],
    "교육 통합 관리 플랫폼": ["교육", "학원", "매칭", "플랫폼", "육아", "복지", "강의", "ERP"],
    "IoT 스마트홈 제어 앱": ["IoT", "스마트홈", "부동산", "센서", "블루투스", "디바이스"],
    "뷰티 공간 임대 예약 플랫폼": ["뷰티", "공간", "예약", "임대", "미용", "O2O"],
    "사고 복구 정산 관리 시스템": ["보험", "사고", "복구", "정산", "관리시스템", "웹"],
    "외국인 대상 의료 시술 플랫폼": ["의료", "시술", "병원", "다국어", "상담", "진료"],
    "글로벌 뷰티/웰니스 예약 앱": ["뷰티", "예약", "외국인", "웰니스", "관광", "여행", "글로벌"],
    "반려동물 종합 정보 앱": ["반려", "동물", "펫", "커뮤니티", "후기", "리뷰"],
}

# 매칭 안 될 때 기본 답변에 들어갈 난이도 높은 프로젝트 예시
_FALLBACK_EXPERIENCE = (
    "블록체인 거래 플랫폼, IoT 디바이스 연동, 교육 통합 관리 시스템 등 "
    "더 난이도가 높은 작업을 했기 때문에 해당 프로젝트 수행은 어렵지 않게 가능합니다."
)


def _find_relevant_portfolio(project_info: dict, company: dict = None) -> str:
    """프로젝트와 가장 유사한 포트폴리오 반환. 없으면 빈 문자열."""
    text = f"{project_info.get('title', '')} {project_info.get('description', '')}".lower()

    best_name = ""
    best_score = 0
    for name, keywords in _ALL_PORTFOLIOS.items():
        score = sum(1 for kw in keywords if kw.lower() in text)
        if score > best_score:
            best_score = score
            best_name = name

    return best_name if best_score >= 2 else ""


def generate_pre_question_answer(project_info: dict, question_text: str = "") -> str:
    """위시켓 클라이언트 사전 질문에 대한 맞춤 답변 생성"""
    company = load_company_info()
    key = os.getenv("ANTHROPIC_API_KEY")

    # Claude API로 질문별 맞춤 답변 생성
    if key and anthropic and question_text:
        portfolio_text = "\n".join(
            f"- {name}: 키워드={', '.join(kws)}"
            for name, kws in _ALL_PORTFOLIOS.items()
        )
        prompt = f"""당신은 앱 개발 회사의 영업 담당자입니다.
위시켓에서 클라이언트가 사전 질문을 했습니다. 이 질문에 맞는 답변을 작성해주세요.

## 회사 포트폴리오 (유사 경험 있으면 언급)
{portfolio_text}

## 회사 강점
- 기획-디자인-개발-QA 원스톱 턴키 개발
- 빠른 MVP (4주~4개월)
- 체계적인 10단계 프로세스

## 프로젝트 정보
- 제목: {project_info.get("title", "")}
- 설명: {project_info.get("description", "")[:300]}

## 클라이언트 질문
{question_text}

## 답변 작성 지침
1. 질문에 정확히 대응하는 답변을 작성하세요
2. 관련 포트폴리오가 있으면 일반적인 설명으로 언급하세요 (프로젝트 고유명 사용 금지, "수산물 쇼핑몰 앱" 같이)
3. 없더라도 유사 기술/경험을 바탕으로 자신 있게 답변하세요
4. 200자 이내로 간결하게 작성하세요
5. 존댓말 사용, 자연스러운 톤. 편지체 금지.
6. 회사명, 연락처, 외부 링크 절대 포함 금지
7. 마크다운(#, *, **) 사용 금지
"""
        try:
            client = anthropic.Anthropic(api_key=key)
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            print(f"[WARN] 질문 답변 API 에러: {e}")

    # API 실패 시 폴백: 질문 내용 분석하여 맞춤 답변
    return _generate_template_question_answer(project_info, question_text)


def _generate_template_question_answer(project_info: dict, question_text: str = "") -> str:
    """질문 내용을 분석하여 적절한 템플릿 답변 생성 (API 없이)"""
    q = question_text.lower()

    # 질문 유형별 맞춤 답변 (구체적인 키워드를 먼저 매칭)
    if any(kw in q for kw in ["커스터마이징", "커스텀"]):
        return (
            "다양한 플랫폼 기반 커스터마이징 경험이 있습니다. "
            "API 연동, UI/UX 전면 개편, 결제 시스템 커스텀, 관리자 기능 확장 등 "
            "깊은 수준의 커스터마이징을 수행해왔습니다. 구체적인 사례는 미팅에서 공유드리겠습니다."
        )

    elif any(kw in q for kw in ["하이브리드", "웹뷰", "프레임워크", "flutter", "react native"]):
        return (
            "네, 하이브리드 앱 개발 경험이 풍부합니다. "
            "React Native, Flutter, 웹뷰 기반 등 프로젝트 특성에 맞는 프레임워크를 선택하여 "
            "다수의 앱을 출시한 경험이 있습니다. 상세 기술 스택은 미팅에서 논의드리겠습니다."
        )

    elif any(kw in q for kw in ["api 연동", "결제 연동", "pg", "외부 서비스", "서드파티"]):
        return (
            "네, 다양한 외부 API 연동 경험이 있습니다. "
            "결제(PG), 소셜 로그인, 지도, 알림, CRM 등 다수의 서드파티 연동을 수행해왔으며, "
            "안정적인 연동 아키텍처 설계가 가능합니다. 자세한 내용은 미팅에서 말씀드리겠습니다."
        )

    elif any(kw in q for kw in ["디자인 시안", "ui/ux", "ux/ui", "figma", "퍼블리싱", "디자인 작업"]):
        return (
            "네, UX/UI 디자인 전문 인력을 보유하고 있습니다. "
            "Figma 기반 디자인 시스템 구축부터 퍼블리싱까지 일원화하여 진행하며, "
            "사용자 경험 중심의 설계를 지향합니다. 포트폴리오는 미팅에서 공유드리겠습니다."
        )

    elif any(kw in q for kw in ["기간", "일정", "스케줄", "얼마나"]):
        return (
            "프로젝트 요구사항을 상세히 검토한 후 정확한 일정을 산정해드리겠습니다. "
            "일반적으로 체계적인 10단계 프로세스를 통해 효율적으로 진행하며, "
            "주 단위 마일스톤 리포트를 제공합니다."
        )

    elif any(kw in q for kw in ["팀", "인력", "인원", "구성"]):
        return (
            "기획자, UX/UI 디자이너, 프론트엔드/백엔드 개발자, QA 엔지니어로 구성된 "
            "전담 팀을 배정합니다. 프로젝트 규모에 따라 적정 인원을 유연하게 편성하며, "
            "PM이 전체 일정과 커뮤니케이션을 관리합니다."
        )

    elif any(kw in q for kw in ["비용", "견적", "금액", "예산"]):
        return (
            "상세 요구사항을 확인한 후 정확한 견적을 산정해드리겠습니다. "
            "기획-디자인-개발-QA 전 과정을 포함한 합리적인 비용 구조를 제안드리며, "
            "미팅에서 상세 논의 가능합니다."
        )

    elif any(kw in q for kw in ["레퍼런스", "해보", "구축해 본", "만들어 본", "경험"]):
        # 일반적인 경험/레퍼런스 질문 (구체 유형에 안 걸린 경우)
        relevant = _find_relevant_portfolio(project_info)
        if not relevant:
            q_info = {"title": question_text, "description": question_text}
            relevant = _find_relevant_portfolio(q_info)
        if relevant:
            desc = relevant.split("(", 1)[1].rstrip(")") if "(" in relevant else relevant
            return f"네, {desc} 프로젝트에서 유사한 기능을 구현한 경험이 있습니다. 구체적인 구현 방식과 결과물은 미팅에서 상세히 말씀드리겠습니다."
        return (
            "동일한 기능의 구축 경험은 없으나, 유사한 수준의 커머스/플랫폼 개발 경험이 다수 있습니다. "
            "기획-디자인-개발-QA 원스톱 프로세스를 통해 안정적으로 구현 가능하며, "
            "자세한 기술 검토 내용은 미팅에서 말씀드리겠습니다."
        )

    else:
        # 일반 폴백
        relevant = _find_relevant_portfolio(project_info)
        if relevant:
            desc = relevant.split("(", 1)[1].rstrip(")") if "(" in relevant else relevant
            return (
                f"네, 관련 경험이 있습니다. {desc} 등 유사 프로젝트를 성공적으로 수행한 바 있으며, "
                f"구체적인 내용은 미팅에서 말씀드리겠습니다."
            )
        return (
            "관련 기술 역량과 유사 프로젝트 수행 경험을 보유하고 있습니다. "
            "자세한 내용은 미팅에서 말씀드리겠습니다."
        )


def _analyze_project_domain(project_info: dict) -> dict:
    """프로젝트 설명을 분석하여 도메인/기술 키워드 추출"""
    text = f"{project_info.get('title', '')} {project_info.get('description', '')}".lower()

    domain_map = {
        "커머스/쇼핑": {
            "keywords": ["커머스", "쇼핑몰", "쇼핑", "자사몰", "상품", "결제", "장바구니", "주문", "배송"],
            "hook": "커머스 플랫폼은 결제 안정성과 사용자 구매 경험이 핵심입니다. 저희는 PG 연동, 장바구니, 주문/배송 관리 등 커머스 핵심 기능을 다수 구현해본 경험이 있습니다.",
            "tech": "결제 시스템(PG 연동), 상품 관리, 주문/배송 트래킹, 관리자 대시보드",
        },
        "매칭/플랫폼": {
            "keywords": ["매칭", "연결", "중개", "마켓플레이스", "O2O", "o2o"],
            "hook": "매칭 플랫폼은 양면 시장 설계와 직관적인 UX가 성패를 좌우합니다. 저희는 사용자-공급자 매칭 로직, 실시간 알림, 리뷰/평점 시스템 등을 다수 구축해본 경험이 있습니다.",
            "tech": "실시간 매칭 알고리즘, 채팅/알림, 리뷰 시스템, 관리자 CMS",
        },
        "예약/티켓": {
            "keywords": ["예약", "예매", "티켓", "좌석", "스케줄", "부킹", "booking"],
            "hook": "예약 시스템은 실시간 좌석/재고 관리와 동시성 처리가 가장 중요합니다. 저희는 실시간 좌석 선점, 결제 연동, QR 입장 처리 등 예약 플랫폼 핵심 기능을 구현해본 경험이 있습니다.",
            "tech": "실시간 좌석/재고 관리, 동시성 제어, QR 발권, 결제 연동",
        },
        "ERP/관리시스템": {
            "keywords": ["erp", "관리 시스템", "관리시스템", "대시보드", "통합 관리", "교적", "재정", "인사"],
            "hook": "관리 시스템은 복잡한 데이터 흐름을 직관적인 UI로 풀어내는 것이 핵심입니다. 저희는 교육/금융/운영 분야의 통합 관리 시스템을 다수 구축하면서, 권한 관리, 통계 대시보드, 데이터 연동 등을 안정적으로 구현해왔습니다.",
            "tech": "역할 기반 권한 관리, 통계/리포트 대시보드, 데이터 import/export, 알림 시스템",
        },
        "AI/데이터": {
            "keywords": ["ai", "인공지능", "머신러닝", "추천", "자동화", "챗봇", "gpt"],
            "hook": "AI 기반 서비스는 모델 성능뿐 아니라 사용자가 결과를 신뢰할 수 있는 UX 설계가 중요합니다. 저희는 AI API 연동, 추천 시스템, 데이터 파이프라인 등을 구축한 경험이 있습니다.",
            "tech": "AI/ML API 연동, 추천 알고리즘, 데이터 분석 파이프라인, 자동화 워크플로우",
        },
        "IoT/하드웨어": {
            "keywords": ["iot", "블루투스", "ble", "센서", "키오스크", "하드웨어", "디바이스", "임베디드"],
            "hook": "IoT/하드웨어 연동 프로젝트는 디바이스 통신의 안정성과 예외 처리가 핵심입니다. 저희는 BLE 통신, 센서 데이터 수집, 키오스크 UI 등 하드웨어 연동 개발 경험이 있습니다.",
            "tech": "BLE/Wi-Fi 디바이스 연동, 실시간 데이터 수집, 키오스크 UI, 원격 모니터링",
        },
        "콘텐츠/커뮤니티": {
            "keywords": ["커뮤니티", "소셜", "콘텐츠", "피드", "게시판", "후기", "리뷰", "뽑기", "라플", "굿즈"],
            "hook": "콘텐츠/커뮤니티 플랫폼은 사용자 참여를 유도하는 UX와 안정적인 트래픽 처리가 중요합니다. 저희는 피드, 댓글, 좋아요, 실시간 알림 등 소셜 기능을 다수 구현한 경험이 있습니다.",
            "tech": "소셜 피드, 실시간 알림, 미디어 업로드/관리, 사용자 참여 기능",
        },
    }

    best_domain = None
    best_score = 0
    for domain, info in domain_map.items():
        score = sum(1 for kw in info["keywords"] if kw in text)
        if score > best_score:
            best_score = score
            best_domain = domain

    if best_domain and best_score >= 1:
        return domain_map[best_domain]
    return {
        "hook": "저희는 다양한 도메인의 웹/앱 개발을 성공적으로 수행해왔으며, 프로젝트의 핵심 요구사항을 정확히 파악하여 최적의 솔루션을 제안드립니다.",
        "tech": "풀스택 웹/앱 개발, API 설계, DB 설계, 클라우드 배포",
    }


def _generate_template_proposal(project_info: dict, company: dict) -> str:
    """API 키 없을 때 사용하는 템플릿 기반 지원서"""
    title = project_info.get("title", "프로젝트")
    platform = project_info.get("platform", "")
    domain = _analyze_project_domain(project_info)

    # 포트폴리오 매칭
    relevant = _find_relevant_portfolio(project_info)
    portfolio_line = ""
    if relevant:
        desc = relevant.split("(", 1)[1].rstrip(")") if "(" in relevant else relevant
        portfolio_line = f"실제로 {desc} 프로젝트를 성공적으로 납품한 경험이 있어, 유사한 요구사항에 대한 노하우를 보유하고 있습니다.\n"

    if platform == "wishket":
        # 위시켓: 회사명/연락처/외부 링크 금지
        return f"""안녕하세요, '{title}' 프로젝트 공고를 확인하고 지원드립니다.

{domain['hook']}

{portfolio_line}저희는 기획부터 디자인, 개발, QA까지 원스톱 턴키 개발을 전문으로 하고 있으며, 프로젝트 규모에 따라 4주~4개월 내 MVP 출시가 가능합니다.

미팅에서 구체적인 기술 검토와 진행 방안을 말씀드리겠습니다.
감사합니다."""
    else:
        # 크몽/프리모아: 회사명/링크 금지, 실력 어필 중심
        return f"""안녕하세요, '{title}' 프로젝트 공고를 확인하고 지원드립니다.

{domain['hook']}

{portfolio_line}저희는 기획부터 UX/UI 디자인, 프론트엔드/백엔드 개발, QA까지 전 과정을 자체 인력으로 수행하는 턴키 개발 전문 팀입니다.

【 보유 기술 】
• {domain['tech']}
• React Native, Flutter, Next.js 등 최신 기술 스택
• 체계적인 10단계 프로세스: 요구분석 → 기획 → 설계 → 디자인 → 개발 → 테스트 → 배포 → 운영

【 진행 방식 】
• PM 전담 배정, 주 단위 진행 보고 및 마일스톤 관리
• Figma 디자인 시안 공유 → 개발 → 중간 시연 → QA 순서로 투명하게 진행
• 프로젝트 완료 후 안정화 기간 유지보수 지원

미팅을 통해 상세 요구사항을 확인하고 최적의 진행 방안을 제안드리겠습니다.
감사합니다."""
