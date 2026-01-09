import os
import re
import Levenshtein
from pyxdameraulevenshtein import damerau_levenshtein_distance
import pandas as pd
from difflib import SequenceMatcher
import difflib
from collections import defaultdict, Counter
import json
from typing import Dict, Tuple, Any, List, Set
import urllib.request

# ====================================================================-
# --------タイポドメイン抽出----------

# ドメイン部抽出
def extract_domain(email):
    return email.split('@')[1] if '@' in email else ''

# 2つの文字列間の異なる部分を抽出
def get_mismatched_part(correct, input_):
    matcher = SequenceMatcher(None, correct, input_)
    mismatched = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != 'equal':
            mismatched.append(input_[j1:j2])
    return ''.join(mismatched)

def filter_domain_differences_with_mismatch(input_path, output_path, threshold=5):  # タイポデータの抽出と整形
    df = pd.read_csv(input_path)  # CSV読み込み

    # ドメイン部の抽出
    df["correct_domain"] = df["correct_address"].astype(str).apply(extract_domain)
    df["input_domain"] = df["input_address"].astype(str).apply(extract_domain)

    # Damerau-Levenshtein距離を計算
    df["domain_edit_distance"] = df.apply(
        lambda row: damerau_levenshtein_distance(row["correct_domain"], row["input_domain"]),
        axis=1
    )

    # ドメイン部が異なるもののみ抽出
    filtered_df = df[
        (df["domain_edit_distance"] > 0) &
        (df["domain_edit_distance"] <= threshold)
    ].reset_index(drop=True)

    # 差分部分の抽出
    filtered_df["mismatched_part"] = filtered_df.apply(
        lambda row: get_mismatched_part(row["correct_domain"], row["input_domain"]),
        axis=1
    )

    filtered_df["edit_distance"] = filtered_df["domain_edit_distance"]
    final_df = filtered_df[["user_id", "step_id", "correct_address", "input_address", "edit_distance", "mismatched_part"]]

    final_df.to_csv(output_path, index=False) # 保存

# ====================================================================
# --------原因別分類-----------

keyboard_adjacent = { # キーボード隣接キーマップ
    'q': 'wa', 'w': 'qase', 'e': 'wsdr', 'r': 'edft', 't': 'rfgy',
    'y': 'tghu', 'u': 'yhji', 'i': 'ujko', 'o': 'iklp', 'p': 'ol',
    'a': 'qws', 's': 'qwedazx', 'd': 'erfcsx', 'f': 'rtdgvcj',
    'g': 'tyfhvbn', 'h': 'yugjnb', 'j': 'uikhmnf', 'k': 'ijolm', 'l': 'okp',
    'z': 'asx', 'x': 'zsdc', 'c': 'xdfv', 'v': 'cfgb', 'b': 'vghn', 'n': 'bhjm', 'm': 'njk,',
    '.': ',/', ',': 'm.',
    '-': '^'
}

symmetric_key_pairs = [('f', 'j'), ('d', 'k'), ('s', 'l'), ('a', ';')] # 対称配置キー誤打（例: f ↔ j）
homoglyph_pairs = [('1', 'l'), ('0', 'o'), ('i', 'l'), ('rn', 'm'), ('а', 'a'), ('b', 'd')]   # # ホモグラフ, キリル文字の'a'など

def keyboard_adjacent_check(c1, c2):
    return c1.lower() in keyboard_adjacent and c2.lower() in keyboard_adjacent[c1.lower()]

def is_symmetric_mismatch(c1, c2): #対称キーの誤打であるか(一文字ずつ判定)
    for a, b in symmetric_key_pairs:
        if (c1 == a and c2 == b) or (c1 == b and c2 == a):
            return True
    return False

def is_visual_homoglyph(c1, c2): #視覚類似文字（ホモグリフ）の誤打であるか(一文字ずつ判定)
    for a, b in homoglyph_pairs:
        if (c1 == a and c2 == b) or (c1 == b and c2 == a):
            return True
    return False

def is_valid_tld(tld): #TLDとして妥当か(長さを2〜4文字に限定し、TLDの制約に基づいて判定)
    return len(tld) in [2, 3, 4] and tld.isalpha()

# TLDミスの特殊パターンを識別する関数の追加
def is_tld_mismatch(correct_domain, typo_domain):
    if not ('.' in correct_domain and '.' in typo_domain):
        return False, None
    
    # 誤りの起こりやすいTLDペアを定義
    tld_pairs = [
        ('jp', 'co.jp'), ('co.jp', 'jp'), 
        ('com', 'co.jp'), ('co.jp', 'com'), 
        ('ne.jp', 'co.jp'), ('co.jp', 'ne.jp'),
        ('go.jp', 'co.jp'), ('co.jp', 'go.jp')
    ]

    for c_pattern, t_pattern in tld_pairs:
        # 1. ドメインがパターンで終わっているかチェック
        if correct_domain.endswith(c_pattern) and typo_domain.endswith(t_pattern):
            correct_base_part = correct_domain[:-len(c_pattern)] 
            typo_base_part = typo_domain[:-len(t_pattern)]
            # ベース部分が完全に一致する場合
            if correct_base_part == typo_base_part:
                # TLDパーツを決定 (ドットを含む)
                correct_tld_part = correct_domain[len(correct_base_part):]
                typo_tld_part = typo_domain[len(typo_base_part):]
                
                return True, f"{correct_tld_part} -> {typo_tld_part}"

    return False, None

# --------------原因別集計-----------------
# 原因別分類関数
def classify_edit_ops_japanese(correct, typo):
    correct_parts = []
    typo_parts = []
    causes = set()

    if damerau_levenshtein_distance(correct, typo) == 1 and Levenshtein.distance(correct, typo) > 1:
        causes.add("入力順序ミス")

    ops = Levenshtein.editops(correct, typo)
    for tag, src_i, tgt_i in ops:
        c1 = correct[src_i] if src_i < len(correct) else ''
        c2 = typo[tgt_i] if tgt_i < len(typo) else ''

        if tag == 'replace':
            correct_parts.append(c1)
            typo_parts.append(c2)
            if keyboard_adjacent_check(c1, c2):
                causes.add("隣接キー誤打")
            elif is_symmetric_mismatch(c1, c2):
                causes.add("左右対称キー誤打")
            elif is_visual_homoglyph(c1, c2):
                causes.add("ホモグリフ（視覚類似文字）")
            else:
                causes.add("スペルミス（認知ミス）")

        elif tag == 'insert':
            correct_parts.append('')
            typo_parts.append(c2)   
            causes.add("二重入力")

        elif tag == 'delete':
            correct_parts.append(c1)
            typo_parts.append('')
            if c1 == '.':
                causes.add("ドット抜け")
            else:
                causes.add("入力漏れ")

    # 3. TLDミスのチェックを最後に行う
    is_tld_m, tld_diff_str = is_tld_mismatch(correct, typo)
    if is_tld_m:
        # TLDミスに関連する他の原因（スペルミス、二重入力、入力漏れ、ドット抜け）を排除
        causes_to_remove = {"スペルミス（認知ミス）", "二重入力", "入力漏れ", "ドット抜け"}
        causes -= causes_to_remove
        
        # 新しい原因を追加。
        causes.add("TLDミス")
        correct_parts = [tld_diff_str.split(' -> ')[0]]
        typo_parts = [tld_diff_str.split(' -> ')[1]]

    return {
        "cause": '・'.join(sorted(causes)),
        "correct_part": ' '.join(correct_parts),
        "mismatched_part": ' '.join(typo_parts)
    }

def get_transposed_pair(correct, typo):  # 入力順序ミスが単独で発生している場合、入れ替わった文字ペアを返す  # 例: 'ab' -> 'ba'
    if damerau_levenshtein_distance(correct, typo) == 1 and Levenshtein.distance(correct, typo) > 1:
        for i in range(len(correct) - 1):
            if correct[i] == typo[i+1] and correct[i+1] == typo[i] and correct[:i] == typo[:i] and correct[i+2:] == typo[i+2:]:
                return (correct[i], correct[i+1])
    return None

# --------------------------------------------------------------------------
# 識別関数
# --------------------------------------------------------------------------

def identify_single_replacement(correct, typo):
    matcher = SequenceMatcher(None, correct, typo)
    ops = matcher.get_opcodes()
    
    replacements = [(correct[i1:i2], typo[j1:j2]) for tag, i1, i2, j1, j2 in ops if tag == 'replace']
    inserts = [(typo[j1:j2]) for tag, i1, i2, j1, j2 in ops if tag == 'insert']
    deletes = [(correct[i1:i2]) for tag, i1, i2, j1, j2 in ops if tag == 'delete']

    if len(replacements) == 1 and not inserts and not deletes:
        c1 = replacements[0][0]
        c2 = replacements[0][1]
        if len(c1) == 1 and len(c2) == 1:
            return (c1, c2)  # 置換
            
    if len(inserts) == 1 and not replacements and not deletes:
        return ('（空）', inserts[0][0]) # 挿入 (二重入力)

    if len(deletes) == 1 and not replacements and not inserts:
        return (deletes[0][0], '（空）') # 削除 (入力漏れ/ドット抜け)

    return ('', '') # 複合または非距離1ミス

def calculate_positional_freqs(csv_path):
    df = pd.read_csv(csv_path)
    positional_data = defaultdict(lambda: defaultdict(Counter))

    for _, row in df.iterrows():
        correct = extract_domain(str(row['correct_address']))
        typo = extract_domain(str(row['input_address']))
        
        # TLDミス、入力順序ミスは除外（別で処理）
        if "TLDミス" in str(row['cause']) or "入力順序ミス" in str(row['cause']):
            continue

        if damerau_levenshtein_distance(correct, typo) == 1 and Levenshtein.distance(correct, typo) == 1:
            ops = Levenshtein.editops(correct, typo)
            if len(ops) == 1:
                tag, src_i, tgt_i = ops[0]
  
                L = len(correct)

                if tag == 'insert':
                    char = typo[tgt_i].lower()
                    # 挿入ミスの場合、ミスの位置は挿入先の文字の直前（src_i）と見なす
                    pos_absolute = src_i 
                    
                elif tag == 'delete' or tag == 'replace':
                    char = correct[src_i].lower()
                    pos_absolute = src_i # 削除/置換のミス位置は src_i

                else:
                    continue
                
                # 絶対位置を末尾からの相対位置に変換 L = len(correct) を使って変換する
                pos_relative_end = L - 1 - pos_absolute
                
                causes_for_classify = classify_edit_ops_japanese(correct, typo)['cause'].split('・')
                cause = causes_for_classify[0] if causes_for_classify else 'その他'
                
                # ドット抜けの詳細処理
                if tag == 'delete' and char == '.':
                    cause = "ドット抜け"
                elif tag == 'insert':
                    cause = "二重入力"
                elif tag == 'delete':
                    cause = "入力漏れ"
                    
                positional_data[cause][char][pos_relative_end] += 1
    
    return positional_data

def generate_positional_heatmap(positional_freqs, total_events):
    """
    DL=1ミスの発生位置を末尾からの相対位置で集計し、ヒートマップを出力
    """
    # DL=1の全タイポイベント数を計算
    total_dl1_events = sum(sum(c.values()) for char_data in positional_freqs.values() for c in char_data.values())

    # 絶対位置ごとの合計頻度
    absolute_freqs = Counter()

    for char_data in positional_freqs.values():
        for pos_counts in char_data.values():
            absolute_freqs.update(pos_counts)

    # 頻度の最大値
    max_count = max(absolute_freqs.values()) if absolute_freqs else 1
    max_pos = max(absolute_freqs.keys()) if absolute_freqs else 0
    
    def get_symbol(count):
        if count == 0:
            return '・' # なし
        elif count >= max_count * 0.7:
            return '■' # 高 (High)
        elif count >= max_count * 0.3:
            return '█' # 中 (Medium)
        else:
            return '░' # 低 (Low)

    MAX_WIDTH = max_pos + 1

    print("=" * 78)
    print("■ ドメイン全体 タイポ発生位置ヒートマップ（0が末尾）")
    print(f"（総タイポイベント数: {total_events}件, DL=1集計対象件数: {total_dl1_events}件）\n")

    pos_header = "末尾からの位置: "
    freq_line = "頻度:      "

    symbols = []
    for pos in range(MAX_WIDTH):
        count = absolute_freqs.get(pos, 0)
        symbols.append(get_symbol(count))

    pos_list = list(range(MAX_WIDTH))[::-1] 
    pos_header += "".join([f"{p:>4}" for p in pos_list])

    freq_line += "    " # 開始位置の調整
    freq_line += "".join([f"{s:>4}" for s in symbols[::-1]])

    print(pos_header)
    print(freq_line)

    print("\n凡例: ■ (高) █ (中) ░ (低) ・ (なし)")
    print("=" * 78)

def analyze_for_ranking(csv_path):
    """CSVを読み込み、ランキング用の個別ミス重み (W_individual) を計算"""
    df = pd.read_csv(csv_path)
    
    individual_rank_weights = defaultdict(dict)
    all_causes = []
    cause_diff_counter = defaultdict(Counter)

    for _, row in df.iterrows():
        correct = extract_domain(str(row['correct_address']))
        typo = extract_domain(str(row['input_address']))
        cause_field = str(row['cause'])
        causes = [c.strip() for c in cause_field.split('・')]
        
        is_custom_handled = False
        row_causes = []

        for cause in causes:
            if cause == "TLDミス":
                is_tld_m, tld_diff_str = is_tld_mismatch(correct, typo)
                if is_tld_m:
                    cause_diff_counter[cause][tld_diff_str] += 1
                    is_custom_handled = True
            
            elif cause == "入力順序ミス":
                transposed_pair = get_transposed_pair(correct, typo)
                if transposed_pair:
                    c1, c2 = transposed_pair
                    key = f'{c1} {c2} -> {c2} {c1}'
                    cause_diff_counter[cause][key] += 1
                    is_custom_handled = True

            row_causes.append(cause)
        
        if is_custom_handled:
            all_causes.extend(row_causes)
            continue

        diffs = extract_ngram_diffs(correct, typo)
        all_causes.extend(row_causes) 

        for cause in causes:
            if cause in {"TLDミス", "入力順序ミス"}:
                continue

            for c1, c2 in diffs:
                cause_diff_counter[cause][(c1, c2)] += 1


    # 大分類の割合計算 (レポート用)
    cause_counts = Counter(all_causes)
    total_major_events = sum(cause_counts.values())
    major_ratios = {k: round(v / total_major_events, 3) for k, v in cause_counts.items()}

    # W_individual (個別ミス件数 / 全タイポイベント総数) の計算
    total_typo_events = sum(sum(counter.values()) for counter in cause_diff_counter.values())
    
    for cause, counter in cause_diff_counter.items():
        for key, count in counter.items():
            rank_score = count / total_typo_events
            individual_rank_weights[cause][key] = rank_score
            
    return major_ratios, individual_rank_weights

#--------------------------------------------------------------------------------------
# cause, correctの付与csvファイル出力関数
def append_typo_causes(input_csv_path, output_csv_path):
    df = pd.read_csv(input_csv_path)

    causes = []
    correct_parts = []
    typo_parts = []

    for _, row in df.iterrows():
        correct = extract_domain(str(row['correct_address']))
        typo = extract_domain(str(row['input_address']))
        result = classify_edit_ops_japanese(correct, typo)
        causes.append(result["cause"])
        correct_parts.append(result["correct_part"])
        typo_parts.append(result["mismatched_part"])

    df['cause'] = causes
    df['correct_part'] = correct_parts
    df['mismatched_part'] = typo_parts

    desired_columns = [
        'user_id', 'step_id',
        'correct_address', 'input_address',
        'edit_distance',
        'correct_part', 'mismatched_part', 'cause'
    ]

    df = df[[col for col in desired_columns if col in df.columns]]

    df.to_csv(output_csv_path, index=False, encoding="utf-8-sig")


#--------------------------------------------------------------------------------------
# 差分抽出（difflibベース、n文字対応）
def extract_ngram_diffs(correct, typo):
    sm = difflib.SequenceMatcher(None, correct, typo)
    diffs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        src = correct[i1:i2] or '（空）'
        tgt = typo[j1:j2] or '（空）'
        diffs.append((src, tgt))
    return diffs

# 原因ごとの差分集計
def analyze_ngram_differences(csv_path):
    df = pd.read_csv(csv_path)
    cause_diff_counter = defaultdict(Counter)

    for _, row in df.iterrows():
        correct = extract_domain(str(row['correct_address']))
        typo = extract_domain(str(row['input_address']))
        cause_field = str(row['cause'])
        causes = [c.strip() for c in cause_field.split('・')]

        is_tld_m, tld_diff_str = is_tld_mismatch(correct, typo)
        if is_tld_m and "TLDミス" in causes:
             cause_diff_counter["TLDミス"][tld_diff_str] += 1
             continue

        if "入力順序ミス" in causes:
            transposed_pair = get_transposed_pair(correct, typo)
            if transposed_pair:
                c1, c2 = transposed_pair
                # 入れ替わったペアをタグとして集計（例: 'e r' -> 1件）
                cause_diff_counter["入力順序ミス"][f'{c1} {c2} -> {c2} {c1}'] += 1
                
                continue

        diffs = extract_ngram_diffs(correct, typo)

        for cause in causes:
            if cause in {"TLDミス", "入力順序ミス"}:
                continue 
            
            for c1, c2 in diffs: # difflibの差分タグに基づき、原因と差分パターンが一致するか検証する
                is_replace = (c1 != '（空）' and c2 != '（空）')
                is_insert = (c1 == '（空）' and c2 != '（空）')
                is_delete = (c1 != '（空）' and c2 == '（空）')

                # 1. 置換ミス (Replace)
                if is_replace:
                    if cause == "隣接キー誤打" and keyboard_adjacent_check(c1, c2):
                        cause_diff_counter[cause][(c1, c2)] += 1
                    elif cause == "ホモグリフ（視覚類似文字）" and is_visual_homoglyph(c1, c2):
                        cause_diff_counter[cause][(c1, c2)] += 1
                    elif cause == "左右対称キー誤打" and is_symmetric_mismatch(c1, c2):
                        cause_diff_counter[cause][(c1, c2)] += 1
                    elif cause == "スペルミス（認知ミス）":
                        cause_diff_counter[cause][(c1, c2)] += 1
                
                # 2. 構造的なミス (Insert / Delete)
                elif is_insert:
                    if cause == "二重入力":
                        cause_diff_counter[cause][(c1, c2)] += 1

                elif is_delete:
                    if cause == "入力漏れ" and c1 != '.':
                        cause_diff_counter[cause][(c1, c2)] += 1
                    elif cause == "ドット抜け" and c1 == '.':
                        cause_diff_counter[cause][(c1, c2)] += 1
                        
                # --------------------------------------------------------------------------
                # 3. 複雑なミス (順序入れ替え)
                # --------------------------------------------------------------------------
                elif cause == "入力順序ミス":
                    cause_diff_counter[cause][(c1, c2)] += 1

    # 原因別件数出力
    for cause, counter in cause_diff_counter.items():
        print(f"\n【原因: {cause}】")

        if cause in {"TLDミス", "入力順序ミス"}:
            for key_pair, count in counter.most_common(20): # key_pair は 'e r -> r e'
                print(f"  {key_pair:<10}: {count}件")

        else:
            for (c1, c2), count in counter.most_common(20): 
                print(f"  {c1} → {c2:<10}: {count}件")


#---------タイポ原因別集計と割合（重み）--------
def get_cause_ratios(csv_path):
    df = pd.read_csv(csv_path)

    # cause列から個別原因を抽出・集計
    all_causes = []
    for cause in df["cause"].dropna():
        causes = [c.strip() for c in cause.split("・")]
        all_causes.extend(causes)

    # 集計と割合計算
    cause_counts = Counter(all_causes)
    total = sum(cause_counts.values())
    cause_ratios = {k: round(v / total, 3) for k, v in cause_counts.items()}
    return cause_ratios, cause_counts, total

# ===================================================
# --------ドメインランキング----------

def typo_domain_ranking_with_reason_jp(input_path, correct_domain, max_distance=4): #####　←←←←←←←←←←←←←←←←←←←←←←←←←DL距離指定
    df = pd.read_csv(input_path)
    df["input_domain"] = df["input_address"].astype(str).apply(extract_domain)

    typo_df = df[df["input_domain"].apply(lambda d: damerau_levenshtein_distance(correct_domain, d) <= max_distance)].copy()
    typo_df["distance"] = typo_df["input_domain"].apply(lambda d: damerau_levenshtein_distance(correct_domain, d))
    typo_df = typo_df[typo_df["input_domain"] != correct_domain]

    grouped = typo_df.groupby("input_domain").agg(
        count=("input_domain", "count"),
        distance=("distance", "first")
    ).reset_index()

    total_typos = grouped["count"].sum()
    grouped["percentage"] = grouped["count"] / total_typos * 100
    grouped["cause"] = grouped["input_domain"].apply(lambda typo: classify_edit_ops_japanese(correct_domain, typo))

    grouped = grouped.sort_values(by=["count", "distance"], ascending=[False, True]).reset_index(drop=True)

    print(f"\n '{correct_domain}' に対するタイポドメインランキング（DL距離 ≦ {max_distance}）:\n")
    for i, row in grouped.iterrows():
        print(f"{i+1}位　{row['input_domain']}（{row['count']}回, 距離: {row['distance']}, 割合: {row['percentage']:.1f}%, 原因: {row['cause']}）")
    print()

# ===================================================================
# -------- TLD有効性チェック用 --------
# ===================================================================

VALID_TLDS = {
    'com', 'net', 'org', 'edu', 'gov', 'mil', 'int', 'jp', 'co.jp', 'ne.jp',
    'ai', 'io', 'co', 'me', 'info', 'biz', 'us', 'uk', 'ca', 'de', 'fr', 'au',
    'ntt', 'google', 'amazon', 'shop', 'blog', 'tech', 'dev', 'app', 'xyz'
}           

def load_iana_tlds():
    """IANAの公式サイトから最新のTLDリストを取得して更新する"""
    url = "https://data.iana.org/TLD/tlds-alpha-by-domain.txt"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            content = response.read().decode('utf-8')
            for line in content.splitlines():
                if not line.startswith('#') and line.strip():
                    VALID_TLDS.add(line.strip().lower())
        print(f"[INFO] 最新のTLDリストを取得しました（総数: {len(VALID_TLDS)}個）")
    except Exception as e:
        print(f"[WARN] TLDリストの取得に失敗しました。デフォルトのリストを使用します: {e}")

def is_existing_tld(domain: str) -> bool:
    """ドメインのTLD（最後のドット以降）が実在するか判定する"""
    parts = domain.split('.')
    if len(parts) < 2:
        return False
    
    tld = parts[-1].lower()
    return tld in VALID_TLDS

# ===================================================================
# --------予測型タイポドメイン生成関数----------
# ===================================================================

HOMOGLYPHS_FOR_GENERATOR = {'1': ['l'], '0': ['o'], 'i': ['l'], 'l': ['i'], 'r': ['m'], 'b': ['d'], 'd': ['b']} 
TLD_ALTERNATIVES = {'com': ['co.jp', 'net'], 'co.jp': ['com', 'co'], 'net': ['com', 'co.jp']} # tld例

symmetric_key_pairs = [('f', 'j'), ('d', 'k'), ('s', 'l'), ('a', ';')] # 対称配置キー誤打（例: f ↔ j）
homoglyph_pairs = [('1', 'l'), ('0', 'o'), ('i', 'l'), ('rn', 'm'), ('а', 'a'), ('b', 'd')]   # # ホモグラフ, キリル文字の'a'など

def typo_generator_ranked(domain: str, individual_weights: dict, positional_freqs: dict, top_n: int = 30):
    variants = defaultdict(lambda: [set(), 0.0]) # {typo_domain: [causes_set, score]}

    # DL=1のミス全体の集計件数（位置補正ボーナスの正規化に使用）
    total_dl1_count = sum(sum(c.values()) for char_data in positional_freqs.values() for c in char_data.values())
    if total_dl1_count == 0: total_dl1_count = 1

    L = len(domain)

    # 位置ボーナスの増幅係数
    K_POSITION_BOOST = 0.5
    
    # DL=1のミスは全て生成する
    for i in range(len(domain)):
        c = domain[i]
        char = c.lower()
        
        # --- 基本的なDL=1のミス (全候補生成) ---
        
        # 1. 入力漏れ (Deletion)
        typo = domain[:i] + domain[i+1:]
        variants[typo][0].add("入力漏れ")

        # 2. 二重入力 (Insertion/Repetition)
        typo = domain[:i] + c + c + domain[i+1:]
        variants[typo][0].add("二重入力")

        # 3. 隣接キー誤打 (Substitution)
        for adj in keyboard_adjacent.get(char, ''):
            typo = domain[:i] + adj + domain[i+1:]
            if keyboard_adjacent_check(c, adj):
                variants[typo][0].add("隣接キー誤打")

        # 4. ホモグリフ・視覚類似文字 (Substitution)
        for g in HOMOGLYPHS_FOR_GENERATOR.get(char, []):
            typo = domain[:i] + g + domain[i+1:]
            variants[typo][0].add("ホモグリフ（視覚類似文字）")
            
        # 5. 左右対称キー誤打 (Substitution)
        for a, b in symmetric_key_pairs:
            if c == a: 
                typo = domain[:i] + b + domain[i+1:]
                variants[typo][0].add("左右対称キー誤打")
            elif c == b:
                typo = domain[:i] + a + domain[i+1:]
                variants[typo][0].add("左右対称キー誤打")

    # --- 構造的なミス（全候補生成） ---

    for i in range(len(domain) - 1):
        swapped = domain[:i] + domain[i+1] + domain[i] + domain[i+2:]
        variants[swapped][0].add("入力順序ミス")

    # 8. TLDミス (TLD Mismatch)
    base_domain, *tlds = domain.split('.')
    TLD_PAIRS_FOR_GENERATION = {
        'jp': ['co.jp'], 'co.jp': ['jp', 'com', 'ne.jp', 'go.jp'],
        'com': ['co.jp'], 'ne.jp': ['co.jp'], 'go.jp': ['co.jp']
    }
    current_tld = '.'.join(tlds)

    if current_tld in TLD_PAIRS_FOR_GENERATION:
        for alt_tld in TLD_PAIRS_FOR_GENERATION[current_tld]:
            typo = f"{base_domain}.{alt_tld}"
            variants[typo][0].add("TLDミス")


    # --- 統合とスコア集計 ---
    ranked_results = []
    
    for typo, (causes, _) in variants.items():
        if typo == domain: continue
        
        final_score = 0
        
        c1, c2 = identify_single_replacement(domain, typo) 
        is_dl1_error = (c1 != '' or c2 != '') # DL=1の単一操作かどうか

        is_tld_m, tld_diff_str = is_tld_mismatch(domain, typo)
        
        # 1. 個別ミス重み (W_individual) の適用
        for cause in causes:
            W_individual = 0.0
            
            # --- TLDミス (x5倍増幅) ---
            if cause == "TLDミス" and is_tld_m:
                key = tld_diff_str
                W_individual = individual_weights.get(cause, {}).get(key, 0.0)
                final_score += W_individual * 5

            # --- 入力順序ミス ---
            elif cause == "入力順序ミス":
                transposed_pair = get_transposed_pair(domain, typo)
                if transposed_pair:
                    k1, k2 = transposed_pair
                    key = f'{k1} {k2} -> {k2} {k1}'
                    W_individual = individual_weights.get(cause, {}).get(key, 0.0)
                    final_score += W_individual
            
            # --- DL=1 ミス (位置ボーナス加算) ---
            elif is_dl1_error:
                if cause in {"隣接キー誤打", "ホモグリフ（視覚類似文字）", "左右対称キー誤打", "スペルミス（認知ミス）"}:
                    key = (c1, c2)
                    W_individual = individual_weights.get(cause, {}).get(key, 0.0)
                    if W_individual == 0.0 and len(c1) == 1 and len(c2) == 1: 
                        W_individual = individual_weights.get(cause, {}).get((c2, c1), 0.0)
                
                elif cause in {"入力漏れ", "ドット抜け"}:
                    key = (c1, '（空）')
                    W_individual = individual_weights.get(cause, {}).get(key, 0.0)
                
                elif cause == "二重入力":
                    key = ('（空）', c2) 
                    W_individual = individual_weights.get(cause, {}).get(key, 0.0)
                
                final_score += W_individual

                position_bonus_value = 0.0

                if is_dl1_error:
                    sm = difflib.SequenceMatcher(None, domain, typo)
                    ops = sm.get_opcodes()
                    
                    if len(ops) == 3 and ops[0][0] == 'equal' and ops[2][0] == 'equal':
                        tag, i1, i2, j1, j2 = ops[1]
                        i_start = i1 
                        
                        if tag == 'insert':
                            pos_char = typo[j1:j2].lower() # 挿入された文字
                            cause = "二重入力"
                        elif tag == 'delete':
                            pos_char = domain[i1:i2].lower() # 削除された文字
                            cause = "入力漏れ" if domain[i1:i2] != '.' else "ドット抜け"
                        elif tag == 'replace':
                            pos_char = domain[i1:i2].lower() # 置換元の文字
                            # 原因は causes から取得
                            causes_list = causes.copy()
                            causes_list.discard('入力順序ミス')
                            cause = sorted(causes_list)[0] if causes_list else 'スペルミス（認知ミス）'
                        else:
                            i_start = -1
                            
                        if i_start != -1 and pos_char and len(pos_char) == 1: 
                            L = len(domain)
                            i_relative_end = L - 1 - i_start
                            
                            freq_count = positional_freqs.get(cause, {}).get(pos_char, {}).get(i_relative_end, 0)
                            
                            position_bonus_value = freq_count / total_dl1_count
                            final_score += position_bonus_value * K_POSITION_BOOST

        if is_tld_m:
            causes = {"TLDミス"}
        
        distance = damerau_levenshtein_distance(domain, typo)

        variants[typo][1] = final_score
        
        ranked_results.append({
            "typo": typo,
            "causes": '・'.join(sorted(causes)),
            "score": round(final_score,7),
            "distance": distance
        })

    ranked_results.sort(key=lambda x: (x['score'], -x['distance']), reverse=True)

    final_ranked_results = [
        r for r in ranked_results 
        if ',' not in r['typo'] and '/' not in r['typo']
    ]

    return final_ranked_results[:top_n]

# ===================================================================
# -------- TLD有効性チェック用 --------
# ===================================================================

def load_iana_tlds():
    """IANAの公式サイトから最新のTLDリストを取得して更新する"""
    url = "https://data.iana.org/TLD/tlds-alpha-by-domain.txt"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            content = response.read().decode('utf-8')
            for line in content.splitlines():
                if not line.startswith('#') and line.strip():
                    VALID_TLDS.add(line.strip().lower())
        print(f"[INFO] 最新のTLDリストを取得しました（総数: {len(VALID_TLDS)}個）")
    except Exception as e:
        print(f"[WARN] TLDリストの取得に失敗しました。デフォルトのリストを使用します: {e}")

def is_existing_tld(domain: str) -> bool:
    """ドメインのTLD（最後のドット以降）が実在するか判定する"""
    # 最後のドット以降を取得 (例: example.co.jp -> jp, hir.ai -> ai)
    parts = domain.split('.')
    if len(parts) < 2:
        return False
    
    tld = parts[-1].lower()
    return tld in VALID_TLDS

def convert_internal_keys_to_str(individual_weights: Dict[str, Dict[Tuple[Any, Any], float]]) -> Dict[str, Dict[str, float]]:
    """individual_rank_weights内のタプルキーをJSONフレンドリーな文字列キーに変換する。"""
    converted_weights = {}
    for cause, inner_dict in individual_weights.items():
        converted_inner_dict = {}
        for key, score in inner_dict.items():
            if isinstance(key, tuple):
                key_str = "".join(key) 
            else:
                key_str = key 
            converted_inner_dict[key_str] = score
        converted_weights[cause] = converted_inner_dict
    return converted_weights

def convert_positional_freqs_to_json(positional_freqs: Dict) -> Dict:
    converted = {}
    for cause, char_data in positional_freqs.items():
        converted[cause] = {}
        for char, pos_counter in char_data.items():
            converted[cause][char] = dict(pos_counter) 
    return converted

# ===================================================================
# --------実行部分----------
if __name__ == "__main__":
    
    # --------------------------------------------------------------------------
    # 0. 必須ファイルパス設定
    # --------------------------------------------------------------------------
    INPUT_FILE = "filtered_address.csv"
    DL4_FILTERED_FILE = "filtered_domain_typos_dl4.csv"
    CAUSES_CSV_FILE = "domaintypos_dl4_causes2.csv"
    DL_THRESHOLD = 4
    OUTPUT_JSON_FILE = "data.json"
    TLD_PRICES_FILE = "tld_prices.json"

    print(f"[INFO] 分析を開始します...")

    # --------------------------------------------------------------------------
    # 1. & 2. タイポ抽出と原因分類 (ファイル生成)
    # --------------------------------------------------------------------------
    try:
        filter_domain_differences_with_mismatch(INPUT_FILE, DL4_FILTERED_FILE, DL_THRESHOLD)
        append_typo_causes(DL4_FILTERED_FILE, CAUSES_CSV_FILE)
    except FileNotFoundError:
        print(f"[致命的エラー] 入力ファイル ({INPUT_FILE}) が見つかりません。")
        exit()

    # --------------------------------------------------------------------------
    # 3. 分析の実行 (内部計算のみ)
    # --------------------------------------------------------------------------
    # 個別ミスの重み (W_individual) と位置別頻度の計算
    major_weights, individual_rank_weights = analyze_for_ranking(CAUSES_CSV_FILE)
    positional_freqs = calculate_positional_freqs(CAUSES_CSV_FILE)
    
    # 総イベント数の取得 (正規化用)
    _, _, total_events = get_cause_ratios(CAUSES_CSV_FILE)

    # --------------------------------------------------------------------------
    # 4. Web用データのエクスポート (JSON生成)
    # --------------------------------------------------------------------------
    if not individual_rank_weights:
        print("\n[エラー] 重みデータが計算されなかったため、JSONエクスポートをスキップします。")
    else:
        converted_individual_weights = convert_internal_keys_to_str(individual_rank_weights)
        converted_positional_freqs = convert_positional_freqs_to_json(positional_freqs)

        total_dl1_count = sum(sum(c.values()) for char_data in positional_freqs.values() for c in char_data.values())
        if total_dl1_count == 0: total_dl1_count = 1

        # TLD価格をロード
        try:
            with open(TLD_PRICES_FILE, 'r', encoding='utf-8') as f:
                TLD_COSTS = json.load(f)
        except FileNotFoundError:
            print("[WARN] tld_prices.json が見つかりません。デフォルト値を使用します。")
            TLD_COSTS = {
                ".co.jp": "7,678円/年", ".jp": "3,124円/年",
                ".com": "1,408円/年", ".net": "1,628円/年"
            }
        
        # ホモグリフ定義 (Python側)
        HOMOGLYPHS_FOR_GENERATOR = {
            '1': ['l'], 'l': ['1', 'i'], '0': ['o'], 'o': ['0'], 
            'i': ['l'], 'r': ['m'], 'b': ['d'], 'd': ['b']
        } 
        
        web_data_export = {
            "individual_weights": converted_individual_weights, 
            "positional_freqs": converted_positional_freqs,
            "total_dl1_count": total_dl1_count,
            "K_POSITION_BOOST": 0.5,
            "TLD_COSTS": TLD_COSTS,
            "keyboard_adjacent": keyboard_adjacent,
            "symmetric_key_pairs": [list(pair) for pair in symmetric_key_pairs],
            "homoglyphs_for_generator": HOMOGLYPHS_FOR_GENERATOR
        }
        
        try:
            with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
                json.dump(web_data_export, f, indent=4, ensure_ascii=False)
            print(f"[INFO] Web用データのエクスポート完了: {OUTPUT_JSON_FILE}")
        except Exception as e:
            print(f"\n[ERROR] JSONエクスポート中にエラーが発生しました: {e}")

    # --------------------------------------------------------------------------
    # 5. ドメインランキング生成の実行 (ユーザーへの出力)
    # --------------------------------------------------------------------------
    correct_domain = input("\n入力されたドメインのタイポドメイン候補を生成する（例: treasurefactory.co.jp）: ").strip()
    
    if major_weights and individual_rank_weights and correct_domain:
        print("\n" + "=" * 78)
        print(f"'{correct_domain}' に対する予測タイポドメインランキング:\n")
        
        predicted_typos = typo_generator_ranked(
            domain=correct_domain,
            individual_weights=individual_rank_weights,
            positional_freqs=positional_freqs,
            top_n=20
        )

        sorted_tlds = sorted(TLD_COSTS.keys(), key=len, reverse=True)

        for i, r in enumerate(predicted_typos):
            cost_estimate = "費用不明"
            for tld in sorted_tlds:
                if r['typo'].endswith(tld):
                    cost_estimate = TLD_COSTS[tld]
                    break
            
            print(f"{i+1:2}位 {r['typo']:<30} (スコア: {r['score']:.7f}, 距離: {r['distance']}, 費用: {cost_estimate}, 原因: {r['causes']})")
            
        print("=" * 78 + "\n")

    else:
        print("\n[終了] 重みデータがないか、ドメインが入力されませんでした。")