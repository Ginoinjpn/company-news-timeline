# 会社を追加するときは、このリストに1件足すだけでよい。
# sec_cik / official_rss / official_title_prefix / industry_feeds / industry_keywords は省略できる。
# us_listed=False の会社は、米国のティッカー別フィード（Yahoo Finance・Seeking Alpha・Nasdaq）を使わない。
COMPANIES = [
    {
        "ticker": "IONQ",
        "name": "IonQ",
        "queries_en": ["IonQ"],
        "queries_ja": ["IonQ", "イオンキュー"],
        "color": "#0077B6",
        "description": "トラップドイオン方式の量子コンピュータ企業",
        "sec_cik": "0001824920",
        "official_rss": "https://ionq.com/news/rss.xml",
        "official_title_prefix": "IonQ | ",
        "industry_feeds": ["https://thequantuminsider.com/feed/"],
        "industry_keywords": ["IonQ"],
    },
    {
        "ticker": "JMIA",
        "name": "Jumia",
        "queries_en": ["Jumia"],
        "queries_ja": ["Jumia", "ジュミア"],
        "color": "#C45A00",
        "description": "アフリカ各国でEコマースを展開する企業（NYSE上場、ドイツ法人）",
        "sec_cik": "0001756708",
        "industry_feeds": ["https://techcabal.com/feed/"],
        "industry_keywords": ["Jumia"],
    },
    {
        "ticker": "NBIS",
        "name": "Nebius",
        "queries_en": ["Nebius"],
        "queries_ja": ["Nebius", "ネビウス"],
        "color": "#5B3FD9",
        "description": "AI向けGPUクラウドを提供するインフラ企業（Nasdaq上場、旧Yandex N.V.）",
        "sec_cik": "0001513845",
        "industry_feeds": ["https://www.datacenterdynamics.com/en/rss/"],
        "industry_keywords": ["Nebius"],
    },
    {
        "ticker": "6965",
        "name": "浜松ホトニクス",
        "queries_en": ['"Hamamatsu Photonics"'],
        "queries_ja": ["浜松ホトニクス"],
        "color": "#004A99",
        "description": "光電子増倍管や光センサーなどの光技術製品のメーカー（東証プライム 6965）",
        "us_listed": False,
        # 東証の適時開示（TDnet）を中継する非公式サービスの RSS
        "official_rss": "https://webapi.yanoshin.jp/webapi/tdnet/list/6965.rss?limit=300",
        "official_title_prefix": "浜松ホトニク:",
    },
]
