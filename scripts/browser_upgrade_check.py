"""Run the persistent-cache upgrade regression against the local app."""
import argparse
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'packaging')]
from darkroom.version import VERSION
from upgrade_smoke import verify_upgrade

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8765');args=parser.parse_args()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--enable-unsafe-swiftshader'])
        page=browser.new_page();print('\n'.join(verify_upgrade(page,args.url,VERSION)));browser.close()
