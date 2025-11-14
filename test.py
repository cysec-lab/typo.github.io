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

# ====================================================================-
# 🗄️ 1. 定数と汎用ヘルパー関数 (元のコードから抽出)
# ====================================================================

# キーボード配列定数
keyboard_adjacent = { 
    'q': 'wa', 'w': 'qase', 'e': 'wsdr', 'r': 'edft', 't': 'rfgy',
    'y': 'tghu', 'u': 'yhji', 'i': 'ujko', 'o': 'iklp', 'p': 'ol',
    'a': 'qws', 's': 'qwedazx', 'd': 'erfcsx', 'f': 'rtdgvcj',
    'g': 'tyfhvbn', 'h': 'yugjnb', 'j': 'uikhmnf', 'k': 'ijolm', 'l': 'okp',
    'z': 'asx', 'x': 'zsdc', 'c': 'xdfv', 'v': 'cfgb', 'b': 'vghn', 'n': 'bhjm', 'm': 'njk,',
    '.': ',/', ',': 'm.', '-': '^'
}
symmetric_key_pairs = [('f', 'j'), ('d', 'k'), ('s', 'l'), ('a', ';')]
homoglyph_pairs = [('1', 'l'), ('0', 'o'), ('i', 'l'), ('rn', 'm'), ('а', 'a'), ('b', 'd')] 
HOMOGLYPHS_FOR_GENERATOR = {'1': ['l'], '0': ['o'], 'i': ['l'], 'l': ['i'], 'r': ['m'], 'b': ['d'], 'd': ['b']} 
TLD_COSTS = {
    ".co.jp": "3,960円/年", 
    ".jp": "3,124円/年",
    ".com": "1,408円/年",
    ".net": "1,628円/年",
}

# ドメイン部抽出
def extract_domain(email):
    return email.split('@')[1] if '@' in email else ''

# キーボード隣接チェック
def keyboard_adjacent_check(c1, c2):
    return c1.lower() in keyboard_adjacent and c2.lower() in keyboard_adjacent[c1.lower()]

# 対称キー誤打チェック
def is_symmetric_mismatch(c1, c2):
    return any((c1 == a and c2 == b) or (c1 == b and c2 == a) for a, b in symmetric_key_pairs)

# ホモグリフ誤打チェック
def is_visual_homoglyph(c1, c2):
    return any((c1 == a and c2 == b) or (c1 == b and c2 == a) for a, b in homoglyph_pairs)

# 単一の転置ミスを識別
def get_transposed_pair(correct, typo):
    if damerau_levenshtein_distance(correct, typo) == 1 and Levenshtein.distance(correct, typo) > 1:
        for i in range(len(correct) - 1):
            if correct[i] == typo[i+1] and correct[i+1] == typo[i] and correct[:i] == typo[:i] and correct[i+2:] == typo[i+2:]:
                return (correct[i], correct[i+1])
    return None

# TLDミスの特殊パターンを識別
def is_tld_mismatch(correct_domain, typo_domain):
    if not ('.' in correct_domain and '.' in typo_domain): return False, None
    tld_pairs = [
        ('jp', 'co.jp'), ('co.jp', 'jp'), ('com', 'co.jp'), ('co.jp', 'com'), 
        ('ne.jp', 'co.jp'), ('co.jp', 'ne.jp'), ('go.jp', 'co.jp'), ('co.jp', 'go.jp')
    ]
    for c_pattern, t_pattern in tld_pairs:
        if correct_domain.endswith(c_pattern) and typo_domain.endswith(t_pattern):
            correct_base_part = correct_domain[:-len(c_pattern)] 
            typo_base_part = typo_domain[:-len(t_pattern)]
            if correct_base_part == typo_base_part:
                correct_tld_part = correct_domain[len(correct_base_part):]
                typo_tld_part = typo_domain[len(typo_base_part):]
                return True, f"{correct_tld_part} -> {typo_tld_part}"
    return False, None

# 編集操作の分類 (原因分類)
def classify_edit_ops_japanese(correct, typo):
    causes = set()
    if damerau_levenshtein_distance(correct, typo) == 1 and Levenshtein.distance(correct, typo) > 1: causes.add("入力順序ミス")
    ops = Levenshtein.editops(correct, typo)
    for tag, src_i, tgt_i in ops:
        c1 = correct[src_i] if src_i < len(correct) else ''
        c2 = typo[tgt_i] if tgt_i < len(typo) else ''
        if tag == 'replace':
            if keyboard_adjacent_check(c1, c2): causes.add("隣接キー誤打")
            elif is_symmetric_mismatch(c1, c2): causes.add("左右対称キー誤打")
            elif is_visual_homoglyph(c1, c2): causes.add("ホモグリフ（視覚類似文字）")
            else: causes.add("スペルミス（認知ミス）")
        elif tag == 'insert': causes.add("二重入力")
        elif tag == 'delete':
            if c1 == '.': causes.add("ドット抜け")
            else: causes.add("入力漏れ")

    is_tld_m, _ = is_tld_mismatch(correct, typo)
    if is_tld_m:
        causes -= {"スペルミス（認知ミス）", "二重入力", "入力漏れ", "ドット抜け"}
        causes.add("TLDミス")
    return {'cause': '・'.join(sorted(causes))}

# 差分部分抽出
def extract_ngram_diffs(correct, typo):
    sm = difflib.SequenceMatcher(None, correct, typo)
    diffs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal': continue
        src = correct[i1:i2] or '（空）'
        tgt = typo[j1:j2] or '（空）'
        diffs.append((src, tgt))
    return diffs

# DL距離1の単一操作を識別（予測生成用）
def identify_single_replacement(correct, typo):
    matcher = SequenceMatcher(None, correct, typo)
    ops = matcher.get_opcodes()
    replacements = [(correct[i1:i2], typo[j1:j2]) for tag, i1, i2, j1, j2 in ops if tag == 'replace']
    inserts = [(typo[j1:j2]) for tag, i1, i2, j1, j2 in ops if tag == 'insert']
    deletes = [(correct[i1:i2]) for tag, i1, i2, j1, j2 in ops if tag == 'delete']
    if len(replacements) == 1 and not inserts and not deletes and len(replacements[0][0]) == 1 and len(replacements[0][1]) == 1: return (replacements[0][0], replacements[0][1])
    if len(inserts) == 1 and not replacements and not deletes: return ('（空）', inserts[0][0])
    if len(deletes) == 1 and not replacements and not inserts: return (deletes[0][0], '（空）')
    return ('', '')

# --- 2. データ処理と分析関数 (元のコードから抽出) ---

# TLDミス集計に対応した重み計算
def analyze_for_ranking(csv_path):
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
            if cause in {"TLDミス", "入力順序ミス"}: continue
            for c1, c2 in diffs:
                cause_diff_counter[cause][(c1, c2)] += 1

    # W_individual の計算
    total_typo_events = sum(sum(counter.values()) for counter in cause_diff_counter.values())
    
    for cause, counter in cause_diff_counter.items():
        for key, count in counter.items():
            rank_score = count / total_typo_events
            individual_rank_weights[cause][key] = rank_score
            
    # 大分類の割合計算 (レポート用)
    cause_counts = Counter(all_causes)
    total_major_events = sum(cause_counts.values())
    major_ratios = {k: round(v / total_major_events, 3) for k, v in cause_counts.items()}
            
    return major_ratios, individual_rank_weights

# 位置別頻度計算
def calculate_positional_freqs(csv_path):
    df = pd.read_csv(csv_path)
    positional_data = defaultdict(lambda: defaultdict(Counter))

    for _, row in df.iterrows():
        correct = extract_domain(str(row['correct_address']))
        typo = extract_domain(str(row['input_address']))
        
        if "TLDミス" in str(row['cause']) or "入力順序ミス" in str(row['cause']): continue
        if damerau_levenshtein_distance(correct, typo) == 1 and Levenshtein.distance(correct, typo) == 1:
            ops = Levenshtein.editops(correct, typo)
            if len(ops) == 1:
                tag, src_i, tgt_i = ops[0]
                L = len(correct)
                
                if tag == 'insert':
                    char = typo[tgt_i].lower()
                    pos_absolute = src_i 
                elif tag == 'delete' or tag == 'replace':
                    char = correct[src_i].lower()
                    pos_absolute = src_i
                else: continue
                
                pos_relative_end = L - 1 - pos_absolute
                
                causes_for_classify = classify_edit_ops_japanese(correct, typo)['cause'].split('・')
                cause = causes_for_classify[0] if causes_for_classify else 'スペルミス（認知ミス）'
                
                if tag == 'delete' and char == '.': cause = "ドット抜け"
                elif tag == 'insert': cause = "二重入力"
                elif tag == 'delete': cause = "入力漏れ"
                    
                positional_data[cause][char][pos_relative_end] += 1
    
    return positional_data

# TLD費用計算
def extract_tld_and_cost(domain: str) -> str:
    for tld, cost in TLD_COSTS.items():
        if domain.endswith(tld): return cost
    return "費用不明"

# 全体原因割合計算
def get_cause_ratios(csv_path):
    df = pd.read_csv(csv_path)
    all_causes = []
    for cause in df["cause"].dropna():
        causes = [c.strip() for c in cause.split("・")]
        all_causes.extend(causes)

    cause_counts = Counter(all_causes)
    total = sum(cause_counts.values())
    cause_ratios = {k: round(v / total, 3) for k, v in cause_counts.items()}
    return cause_ratios, cause_counts, total

# --- 3. JSON変換ヘルパー関数 ---
def convert_internal_keys_to_str(individual_weights: Dict[str, Dict[Tuple[Any, Any], float]]) -> Dict[str, Dict[str, float]]:
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

# --- 4. 中間ファイル生成 (JSONエクスポートに必要) ---
def filter_domain_differences_with_mismatch(input_path, output_path, threshold=5):
    df = pd.read_csv(input_path)
    df["correct_domain"] = df["correct_address"].astype(str).apply(extract_domain)
    df["input_domain"] = df["input_address"].astype(str).apply(extract_domain)
    df["domain_edit_distance"] = df.apply(lambda row: damerau_levenshtein_distance(row["correct_domain"], row["input_domain"]), axis=1)
    filtered_df = df[(df["domain_edit_distance"] > 0) & (df["domain_edit_distance"] <= threshold)].reset_index(drop=True)
    filtered_df["edit_distance"] = filtered_df["domain_edit_distance"]
    filtered_df.to_csv(output_path, index=False)
    print(f"[INFO] 1. ドメインの違いと差分を出力しました: {output_path}")

def append_typo_causes(input_csv_path, output_csv_path):
    df = pd.read_csv(input_csv_path)
    results = df.apply(lambda row: classify_edit_ops_japanese(extract_domain(str(row['correct_address'])), extract_domain(str(row['input_address']))), axis=1, result_type='expand')
    df['cause'] = results['cause']
    desired_columns = ['user_id', 'step_id', 'correct_address', 'input_address', 'edit_distance', 'cause']
    df = df[[col for col in desired_columns if col in df.columns]]
    df.to_csv(output_csv_path, index=False, encoding="utf-8-sig")
    print(f"[INFO] 2. 原因分類を付与しました: {output_csv_path}")

# --- 5. メイン実行ブロック (JSONエクスポート) ---
if __name__ == "__main__":
    
    # 必須ファイルパス設定
    INPUT_FILE = "filtered_address.csv"
    DL4_FILTERED_FILE = "filtered_domain_typos_dl4.csv"
    CAUSES_CSV_FILE = "domaintypos_dl4_causes2.csv"
    DL_THRESHOLD = 4

    # 1 & 2. 中間ファイル生成 (ローカル実行用)
    try:
        filter_domain_differences_with_mismatch(INPUT_FILE, DL4_FILTERED_FILE, DL_THRESHOLD)
        append_typo_causes(DL4_FILTERED_FILE, CAUSES_CSV_FILE)
    except FileNotFoundError:
        print(f"\n[致命的エラー] 入力ファイル ({INPUT_FILE}) が見つかりません。JSONエクスポートをスキップします。")
        exit()
    
    # 3. 分析の実行: 個別ミスの重み (W_individual) と位置別頻度の計算
    major_weights, individual_rank_weights = analyze_for_ranking(CAUSES_CSV_FILE)
    positional_freqs = calculate_positional_freqs(CAUSES_CSV_FILE)
    _, _, total_events = get_cause_ratios(CAUSES_CSV_FILE)

    if not individual_rank_weights:
        print("\n[エラー] 重みデータが計算されなかったため、JSONエクスポートをスキップします。")
    else:
        # JSON変換の適用
        converted_individual_weights = convert_internal_keys_to_str(individual_rank_weights)
        converted_positional_freqs = convert_positional_freqs_to_json(positional_freqs)
        total_dl1_count = sum(sum(c.values()) for char_data in positional_freqs.values() for c in char_data.values())
        if total_dl1_count == 0: total_dl1_count = 1

        OUTPUT_JSON_FILE = "data.json"
        
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
            print(f"\n[INFO] Web用データのエクスポート完了: {OUTPUT_JSON_FILE}")
        except Exception as e:
            print(f"\n[ERROR] JSONエクスポート中にエラーが発生しました: {e}")