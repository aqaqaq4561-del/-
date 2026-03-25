"""
텔레그램 알림 모듈
자동화 결과를 텔레그램으로 전송합니다.
"""
import json
import os
import urllib.request
from pathlib import Path


TELEGRAM_BOT_TOKEN = "8018409801:AAEWvekJb60OSIuSAKfXrRJNAjitvC9RXI8"
TELEGRAM_CHAT_ID = 8546568283


def send_telegram(text: str, parse_mode: str = None) -> bool:
    """텔레그램으로 메시지 전송 (4096자 초과 시 분할)"""
    MAX_LEN = 4000  # 여유 두고 4000자
    chunks = []
    if len(text) <= MAX_LEN:
        chunks = [text]
    else:
        # 줄 단위로 분할
        lines = text.split("\n")
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > MAX_LEN:
                chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)

    success = True
    for chunk in chunks:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=10)
            result = json.loads(resp.read().decode())
            if not result.get("ok", False):
                success = False
        except Exception as e:
            print(f"[Telegram] 전송 실패: {e}")
            success = False
    return success


def notify_new_projects(projects: list[dict]):
    """새 프로젝트 발견 알림"""
    if not projects:
        return

    msg = f"[X-Block Auto Apply]\n새 프로젝트 {len(projects)}건 발견!\n"
    msg += "=" * 30 + "\n"

    for i, item in enumerate(projects, 1):
        proj = item["project"]
        msg += (
            f"\n{i}. [{proj['platform']}] {proj['title']}\n"
            f"   예산: {proj.get('budget', '미정')}\n"
            f"   URL: {proj.get('url', 'N/A')}\n"
            f"   ID: {proj['project_id']}\n"
        )

    msg += "\n" + "=" * 30
    msg += "\n승인하려면 여기서 '승인 [ID]' 라고 보내세요."
    msg += "\n전체 승인: '전체승인'"

    send_telegram(msg)


def notify_proposal_ready(project: dict, proposal: str):
    """지원서 초안 완성 알림"""
    msg = (
        f"[지원서 준비 완료]\n"
        f"플랫폼: {project['platform']}\n"
        f"프로젝트: {project['title']}\n"
        f"예산: {project.get('budget', '미정')}\n"
        f"URL: {project.get('url', 'N/A')}\n"
        f"\n--- 지원서 전문 ---\n"
        f"{proposal}\n"
        f"--- 끝 ---\n"
        f"\nID: {project['project_id']}\n"
        f"승인하려면 '승인 {project['project_id']}' 입력"
    )
    send_telegram(msg)


def notify_applied(project: dict, success: bool):
    """지원 완료/실패 알림"""
    status = "지원 완료" if success else "지원 실패"
    emoji = "[OK]" if success else "[FAIL]"
    msg = (
        f"{emoji} {status}\n"
        f"플랫폼: {project['platform']}\n"
        f"프로젝트: {project['title']}\n"
        f"URL: {project.get('url', 'N/A')}"
    )
    send_telegram(msg)


def notify_summary(total: int, filtered: int, pending: int):
    """실행 요약 알림"""
    msg = (
        f"[X-Block Auto Apply 실행 완료]\n"
        f"수집: {total}건\n"
        f"필터 통과: {filtered}건\n"
        f"승인 대기: {pending}건\n"
        f"\n대기 목록 확인: python main.py --pending"
    )
    send_telegram(msg)


def check_approvals() -> list[str]:
    """텔레그램에서 승인 메시지 확인"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read().decode())

        approved_ids = []
        if result.get("ok"):
            for update in result.get("result", []):
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = msg.get("chat", {}).get("id")

                if chat_id != TELEGRAM_CHAT_ID:
                    continue

                if text.startswith("승인 "):
                    project_id = text.replace("승인 ", "").strip()
                    approved_ids.append(project_id)
                elif text == "전체승인":
                    approved_ids.append("__ALL__")

            # 읽은 메시지 확인 처리
            if result.get("result"):
                last_id = result["result"][-1]["update_id"]
                confirm_url = f"{url}?offset={last_id + 1}"
                urllib.request.urlopen(confirm_url, timeout=5)

        return approved_ids

    except Exception as e:
        print(f"[Telegram] 승인 확인 실패: {e}")
        return []
