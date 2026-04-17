"""
로스트아크 공지사항 크롤러 → 디스코드 알림
GitHub Actions에서 10분마다 1회 실행되는 버전
"""

import json
import os
import asyncio
import logging
from pathlib import Path
from datetime import datetime

import httpx
from playwright.async_api import async_playwright

# ── 설정 ──────────────────────────────────────────────
# GitHub Secrets에서 읽어옴 (없으면 직접 입력)
DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1494678962687709376/hvofr5HYc1Lwf7jr6W7vVyIIqA3ad-D1DojnxODJLqZ2Q7xjlTRK0t-7zCYwpJ1js0IH",
)
TARGET_URL = "https://lostark.game.onstove.com/News/Notice/List?page=1&searchtype=0&searchtext=&noticetype=all"
KEYWORD = "업데이트 내역"
SEEN_FILE = Path("seen_posts.json")
# ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False), encoding="utf-8")


# ── 크롤링 ─────────────────────────────────────────────
async def fetch_notices() -> list[dict]:
    """공지 목록에서 '업데이트 내역' 포함 글을 가져온다."""
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        log.info("페이지 로딩 중...")
        await page.goto(TARGET_URL, wait_until="networkidle", timeout=30_000)

        # 공지 목록 행 선택
        rows = await page.query_selector_all("ul.list > li, .board-list li, tr.list-item")

        # 사이트 구조에 맞춰 selector 자동 탐색
        if not rows:
            rows = await page.query_selector_all("li")

        for row in rows:
            title_el = await row.query_selector("a")
            if not title_el:
                continue

            title = (await title_el.inner_text()).strip()
            if KEYWORD not in title:
                continue

            href = await title_el.get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://lostark.game.onstove.com" + href

            # 게시글 ID (URL에서 추출)
            post_id = href.split("/")[-1].split("?")[0]

            results.append({"id": post_id, "title": title, "url": href})

        await browser.close()

    log.info(f"필터링된 게시글: {len(results)}건")
    return results


async def fetch_summary(url: str) -> str:
    """본문 첫 200자 요약을 가져온다."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30_000)

            # 본문 영역 selector (로스트아크 공지 페이지)
            selectors = [
                ".fr-view",
                ".news-detail__content",
                ".board-view__content",
                "article",
                ".content",
            ]
            text = ""
            for sel in selectors:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    break

            await browser.close()
            return text[:200].replace("\n", " ") + ("..." if len(text) > 200 else "")
    except Exception as e:
        log.warning(f"본문 요약 실패: {e}")
        return "본문을 가져오지 못했습니다."


# ── 디스코드 전송 ────────────────────────────────────
async def send_discord(post: dict, summary: str):
    embed = {
        "title": post["title"],
        "url": post["url"],
        "description": summary,
        "color": 0xF0A500,  # 로스트아크 골드 컬러
        "footer": {"text": f"로스트아크 공지 · {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
        "thumbnail": {
            "url": "https://cdn.onstove.com/images/game/lostark/game_ico.png"
        },
    }
    payload = {
        "username": "로스트아크 알리미",
        "avatar_url": "https://cdn.onstove.com/images/game/lostark/game_ico.png",
        "embeds": [embed],
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            log.info(f"✅ 디스코드 전송 완료: {post['title']}")
        else:
            log.error(f"❌ 디스코드 전송 실패 ({resp.status_code}): {resp.text}")



async def main():
    log.info("크롤러 시작")
    seen = load_seen()

    try:
        posts = await fetch_notices()
    except Exception as e:
        log.error(f"크롤링 오류: {e}")
        return

    new_posts = [p for p in posts if p["id"] not in seen]
    log.info(f"새 게시글: {len(new_posts)}건")

    for post in new_posts:
        summary = await fetch_summary(post["url"])
        await send_discord(post, summary)
        seen.add(post["id"])
        save_seen(seen)
        await asyncio.sleep(1)

    log.info("완료")


if __name__ == "__main__":
    asyncio.run(main())
