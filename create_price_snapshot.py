import json

OUTPUT_FILE = "tld_prices.json"

def create_price_snapshot():
    print("[INFO] 価格データのスナップショットを生成します...")

# お名前.com等の市場価格を参考にした主要TLDリスト (2025年想定価格)
    tld_data = {
        ".jp": "3,124円/年",
        ".co.jp": "4,378円/年",
        ".ne.jp": "4,378円/年",
        ".or.jp": "4,378円/年",
        ".gr.jp": "4,378円/年",
        ".ac.jp": "4,378円/年",
        ".ed.jp": "4,378円/年",
        ".go.jp": "4,378円/年",
        ".com": "1,580円/年",
        ".net": "1,680円/年",
        ".org": "1,780円/年",
        ".info": "2,280円/年",
        ".biz": "2,280円/年",
        ".mobi": "2,860円/年",
        ".asia": "2,500円/年",
        ".xyz": "1,480円/年",
        ".shop": "4,378円/年",
        ".site": "4,378円/年",
        ".online": "4,980円/年",
        ".store": "6,980円/年",
        ".tech": "5,980円/年",
        ".app": "2,580円/年",
        ".dev": "2,580円/年",
        ".work": "990円/年",
        ".cloud": "2,980円/年",
        ".tokyo": "990円/年",
        ".yokohama": "990円/年",
        ".nagoya": "990円/年",
        ".email": "2,480円/年",
        ".link": "1,480円/年",
        ".click": "1,280円/年",
        ".ai": "12,980円/年",   # アンギラ (AI用として人気)
        ".io": "8,980円/年",    # イギリス領インド洋地域 (IT用として人気)
        ".me": "2,980円/年",    # モンテネグロ (個人用)
        ".tv": "4,980円/年",    # ツバル (動画用)
        ".cc": "1,580円/年",    # ココス諸島
        ".co": "3,500円/年",    # コロンビア (企業用)
        ".ntt": "要問い合わせ",  # .ntt (一般登録制限ありだが実在する)
        ".club": "1,980円/年",
        ".guru": "3,980円/年",
        ".life": "3,980円/年",
        ".world": "3,980円/年",
        ".today": "2,980円/年",
    }

    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(tld_data, f, indent=4, ensure_ascii=False)
        
        print(f"[SUCCESS] {len(tld_data)} 件の価格リストを保存しました: {OUTPUT_FILE}")
        print("メインの分析プログラムを実行してください。")
        
    except Exception as e:
        print(f"[ERROR] ファイル保存中にエラー: {e}")

if __name__ == "__main__":
    create_price_snapshot()