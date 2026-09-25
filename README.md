# Company News Timeline

企業のニュースを自動で集めて、日本語の要約つきで時系列に表示するサイトです。現在は IonQ（IONQ）、Jumia（JMIA）、Nebius（NBIS）、浜松ホトニクス（6965）に対応しています。

- 更新: 1日6回（日本時間 2/6/10/14/18/22 時）、GitHub Actions で実行
- 取得元: Google News（英・日）、Yahoo Finance、Seeking Alpha、Nasdaq.com、各社の公式ニュース、SEC EDGAR、東証の適時開示（非公式の中継 RSS）、業界専門メディア
- 要約: Gemini API（見出しと RSS の概要から作成。記事本文は読んでいません）
- 別のサイトが同じ出来事を報じた記事は1件にまとめ、「ほかの報道」としてリンクを並べます
- 開いた記事には「既読」が付きます（ブラウザごとに保存）

## 設定

リポジトリの Secrets（Settings → Secrets and variables → Actions）に次の2つを登録します。

| 名前 | 内容 |
|---|---|
| `GEMINI_API_KEY` | Gemini API のキー |
| `SEC_USER_AGENT` | SEC EDGAR に送る連絡先（例: `company-news-timeline you@example.com`）。SEC は連絡先のメールアドレスを必須としています。未設定なら SEC の書類は取得しません |

- 会社を追加するときは `companies.py` に1件足し、Actions の「過去分の取り込み」を、そのティッカーを指定して実行します。
- 公開ページは GitHub Pages（main ブランチの `/docs`）で配信しています。

記事の著作権は各配信元にあります。このサイトは見出し・リンク・独自の要約のみを掲載しています。
