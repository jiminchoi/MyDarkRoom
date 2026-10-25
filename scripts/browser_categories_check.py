"""Startup layout ignores stale panel state without erasing saved presets."""
import argparse
from playwright.sync_api import sync_playwright

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8765');args=p.parse_args()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--enable-unsafe-swiftshader'])
        page=browser.new_page();page.goto(args.url)
        page.evaluate("for(const key of ['presets','빛','색','톤 커브','흑백 채널 믹서','색조','그레인','geometry','masks'])localStorage.setItem('mydarkroom.category.v2.'+key,'open');localStorage.setItem('mydarkroom.test','keep')")
        page.reload();page.wait_for_selector('details.category')
        assert page.locator('details.category').evaluate_all("es=>es.filter(e=>e.open).map(e=>e.dataset.category)")==['빛']
        page.locator('[data-category="masks"]>summary').click();assert page.locator('[data-category="masks"]').evaluate('e=>e.open')
        page.reload();page.wait_for_selector('details.category')
        assert page.locator('details.category').evaluate_all("es=>es.filter(e=>e.open).map(e=>e.dataset.category)")==['빛']
        assert page.evaluate("localStorage.getItem('mydarkroom.test')")=='keep'
        print('Only Light opens on startup/reload; masks close; in-session toggles and saved data preserved')
        browser.close()
