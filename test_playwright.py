from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        try:
            page.goto(
                "https://www.baidu.com",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            print("网页访问成功")
            print("标题：", page.title())
            print("地址：", page.url)
        except Exception as error:
            print("网页访问失败：")
            print(repr(error))

        input("按 Enter 关闭浏览器...")
        browser.close()


if __name__ == "__main__":
    main()