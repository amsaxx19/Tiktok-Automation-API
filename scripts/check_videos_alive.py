"""Quick check: which videos are still alive on TikTok."""
import asyncio
import json
from playwright.async_api import async_playwright

PROXY = {
    "server": "http://178.93.21.156:49644",
    "username": "5sjQhR7dWXPoSuv",
    "password": "gAbLujfGLSP2rWU",
}

VIDEOS = [
    "7622536313668979976",  # used in capture_shop_xhr.py
    "7622303845783309575",  # used in many find_price scripts
    "7485069092459463953",  # used in E2E test
    "7507723498614331653",  # used in debug_reflow
]

async def check_video(page, vid):
    url = f"https://www.tiktok.com/@amosthiosa/video/{vid}"
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=20000)
    except Exception as e:
        return vid, "NAV_FAIL", str(e)[:60]
    await page.wait_for_timeout(2000)
    
    result = await page.evaluate("""() => {
        try {
            const el = document.querySelector('#__UNIVERSAL_DATA_FOR_REHYDRATION__');
            if (!el) return {status: 'NO_REHYDRATION'};
            const data = JSON.parse(el.textContent);
            const scope = data.__DEFAULT_SCOPE__ || {};
            const vd = scope['webapp.video-detail'] || {};
            return {
                statusCode: vd.statusCode,
                statusMsg: vd.statusMsg,
                hasItem: !!vd?.itemInfo?.itemStruct?.id,
                videoId: vd?.itemInfo?.itemStruct?.id || null,
                desc: (vd?.itemInfo?.itemStruct?.desc || '').substring(0, 60),
                anchors: (vd?.itemInfo?.itemStruct?.anchors || []).length,
                anchorTypes: vd?.itemInfo?.itemStruct?.anchorTypes || [],
            };
        } catch(e) { return {error: e.message}; }
    }""")
    
    sc = result.get('statusCode', '?')
    msg = result.get('statusMsg', '')
    has = result.get('hasItem', False)
    anch = result.get('anchors', 0)
    desc = result.get('desc', '')
    at = result.get('anchorTypes', [])
    
    status = "ALIVE" if has else f"DEAD({sc}:{msg})"
    return vid, status, f"anchors={anch}, types={at}, desc={desc[:40]}"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            proxy=PROXY,
            locale='id-ID',
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        page = await context.new_page()

        for vid in VIDEOS:
            vid_id, status, detail = await check_video(page, vid)
            emoji = "✅" if "ALIVE" in status else "❌"
            print(f"{emoji} {vid_id}: {status} | {detail}")

        await browser.close()

asyncio.run(main())
