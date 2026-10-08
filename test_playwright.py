import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        # Login to get a cookie and token
        await page.goto('http://127.0.0.1:8000/')
        
        if 'Sign In' in await page.content() or 'Notebook Intelligence System' in await page.title():
            print('On landing page. Opening modal...')
            try:
                await page.evaluate('openAuthModal("signin")')
                await page.wait_for_selector('#signinEmail')
                await page.fill('#signinEmail', 'testuser@nis.com')
                await page.fill('#signinPassword', 'testpass123')
                await page.click('#signinSubmitBtn')
                await page.wait_for_url('**/app', timeout=5000)
                print('Logged in successfully.')
            except Exception as e:
                print('Login error:', e)
                
        # Clear localStorage but KEEP cookies
        print('Clearing localStorage (removing nis_token)...')
        await page.evaluate('localStorage.clear()')
        
        # Navigate to /app directly
        print('Navigating to /app without token but with cookie...')
        
        page.on('framenavigated', lambda frame: print('Navigated to:', frame.url))
        
        await page.goto('http://127.0.0.1:8000/app')
        await page.wait_for_timeout(3000)
        
        profile_name = await page.evaluate('document.getElementById("userNameLabel")?.textContent')
        print('Profile Name:', profile_name)
        
        # Check if the token was refreshed
        new_token = await page.evaluate('localStorage.getItem("nis_token")')
        print('Refreshed Token exists:', bool(new_token))
        
        print('Clicking Code Lab tab...')
        await page.click('.tab-btn[data-target="view-code"]')
        
        active_view = await page.evaluate('document.querySelector(".view-panel.active")?.id')
        print('Active View after clicking:', active_view)

        await browser.close()

asyncio.run(main())
