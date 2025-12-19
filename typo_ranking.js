/**
 * @fileoverview Pythonのドメインタイポ予測ロジックをJavaScriptに移植。
 * GitHub Pages上で動作するように設計されています。
 * data.json (Pythonで生成) が必要です。
 */

class TypoRanker {
    constructor(data) {
        // data.json から渡された定数と重みデータを格納
        this.keyboardAdjacent = data.keyboard_adjacent;
        this.symmetricKeyPairs = data.symmetric_key_pairs; 
        this.homoglyphsForGenerator = data.homoglyphs_for_generator;
        this.individualWeights = data.individual_weights; // W_individual
        this.positionalFreqs = data.positional_freqs;     // 位置別頻度
        this.totalDl1Count = data.total_dl1_count || 1;    // 正規化用
        this.kPositionBoost = data.K_POSITION_BOOST || 0.5;
        this.tldCosts = data.TLD_COSTS || {}; 
    }

    // ======================================================================
    // 1. 入力正規化・バリデーション
    // ======================================================================

    /** * 入力を正規化する (小文字化、全角→半角、不要文字削除) 
     */
    sanitizeInput(input) {
        if (!input) return "";
        let domain = input.toLowerCase();
        // 全角英数 -> 半角
        domain = domain.replace(/[Ａ-Ｚａ-ｚ０-９]/g, function(s) {
            return String.fromCharCode(s.charCodeAt(0) - 0xFEE0);
        });
        // 使用可能文字(a-z, 0-9, ., -)以外を削除
        domain = domain.replace(/[^a-z0-9.-]/g, '');
        return domain;
    }

    /**
     * ドメイン形式のバリデーション
     * - ドット連続禁止
     * - ハイフン始端/終端禁止
     * - TLDの実在チェック (data.jsonに含まれるTLDのみ許可)
     */
    isValidDomainFormat(domain) {
        // 1. 使用禁止文字のチェック (正規表現)
        // a-z, 0-9, ., - 以外の文字が一つでも入っていたら無効とする
        if (/[^a-z0-9.-]/.test(domain)) return false;

        // 2. 構造的なチェック
        if (domain.includes('..')) return false; // ドット連続
        if (domain.startsWith('.') || domain.endsWith('.')) return false; // ドットで始まる/終わる

        const parts = domain.split('.');
        // ラベルごとのチェック
        for (const part of parts) {
            if (part === '') return false;
            if (part.startsWith('-') || part.endsWith('-')) return false;
        }

        // 3. TLDの実在チェック（費用が引けるか＝実在TLDリストにあるか）
        const cost = this.extractTldAndCost(domain);
        if (cost === "費用不明") return false;

        return true;
    }

    /** * TLDに対応する費用を取得する 
     * 長いTLD (.co.jp) から順にマッチングを行う
     */
    extractTldAndCost(domain) {
        if (!this.tldCosts) return "費用不明";

        // TLDキーを文字数順（降順）にソートしてマッチング漏れを防ぐ
        const tlds = Object.keys(this.tldCosts).sort((a, b) => b.length - a.length);
        
        for (const tld of tlds) {
            // ドット境界のチェック (example.com に対して .om がマッチしないように)
            const tldWithDot = tld.startsWith('.') ? tld : '.' + tld;
            if (domain === tld || domain.endsWith(tldWithDot)) {
                return this.tldCosts[tld];
            }
        }
        return "費用不明";
    }

    // ======================================================================
    // 2. 距離計算・識別ロジック (Pythonの difflib/Levenshtein 相当)
    // ======================================================================

    /** Levenshtein距離 (DP法) */
    _levenshteinDistance(a, b) {
        if (a.length === 0) return b.length;
        if (b.length === 0) return a.length;
        const matrix = [];
        for (let i = 0; i <= b.length; i++) matrix[i] = [i];
        for (let j = 0; j <= a.length; j++) matrix[0][j] = j;

        for (let i = 1; i <= b.length; i++) {
            for (let j = 1; j <= a.length; j++) {
                const cost = (a[j - 1] === b[i - 1]) ? 0 : 1;
                matrix[i][j] = Math.min(
                    matrix[i - 1][j] + 1,    // insert
                    matrix[i][j - 1] + 1,    // delete
                    matrix[i - 1][j - 1] + cost // replace
                );
            }
        }
        return matrix[b.length][a.length];
    }
    
    /** Damerau-Levenshtein距離 (隣接転置=1) */
    _damerauLevenshteinDistance(a, b) {
        const lDist = this._levenshteinDistance(a, b);
        // 簡易実装: レーベンシュタイン距離が2で、かつ文字数が同じ、かつ転置で一致する場合のみ1を返す
        if (lDist === 2 && a.length === b.length) {
            for (let i = 0; i < a.length - 1; i++) {
                if (a[i] === b[i+1] && a[i+1] === b[i]) {
                    // 他の部分が一致しているか確認
                    const swapped = a.slice(0, i) + a[i+1] + a[i] + a.slice(i+2);
                    if (swapped === b) return 1;
                }
            }
        }
        return lDist;
    }

    /** * DL距離1の操作内容（文字ペア）を特定する 
     * @returns {[char1, char2]} 置換:(c1,c2), 挿入:('（空）',c2), 削除:(c1,'（空）')
     */
    identifySingleReplacement(correct, typo) {
        if (this._levenshteinDistance(correct, typo) !== 1) return ['', '']; 
        
        // 置換 (長さが同じ)
        if (correct.length === typo.length) { 
            for (let i = 0; i < correct.length; i++) {
                if (correct[i] !== typo[i]) {
                    // ここ以外が一致しているか確認
                    if (correct.slice(i+1) === typo.slice(i+1)) {
                        return [correct[i], typo[i]];
                    }
                }
            }
        } 
        // 削除 (correctの方が長い) -> Pythonの delete logic
        else if (correct.length === typo.length + 1) { 
            for (let i = 0; i < correct.length; i++) {
                if (correct.slice(0, i) + correct.slice(i + 1) === typo) {
                    return [correct[i], '（空）'];
                }
            }
        } 
        // 挿入 (typoの方が長い) -> Pythonの insert logic
        else if (correct.length === typo.length - 1) { 
            for (let i = 0; i < typo.length; i++) {
                if (typo.slice(0, i) + typo.slice(i + 1) === correct) {
                    return ['（空）', typo[i]];
                }
            }
        }
        return ['', ''];
    }

    /** 転置ペアを特定する */
    getTransposedPair(correct, typo) {
        if (correct.length !== typo.length || this._damerauLevenshteinDistance(correct, typo) !== 1) return null;
        for (let i = 0; i < correct.length - 1; i++) {
            const tempCorrect = correct.slice(0, i) + correct[i + 1] + correct[i] + correct.slice(i + 2);
            if (tempCorrect === typo) {
                return [correct[i], correct[i + 1]];
            }
        }
        return null;
    }
    
    /** TLDミス判定 (特定のペアのみ) */
    isTldMismatch(correctDomain, typoDomain) {
        if (!correctDomain.includes('.') || !typoDomain.includes('.')) return [false, null];
        
        const TLD_PAIRS = {
            'jp': ['co.jp'], 'co.jp': ['jp', 'com', 'ne.jp', 'go.jp'],
            'com': ['co.jp'], 'ne.jp': ['co.jp'], 'go.jp': ['co.jp']
        };

        for (const cPattern in TLD_PAIRS) {
            for (const tPattern of TLD_PAIRS[cPattern]) {
                if (correctDomain.endsWith(cPattern) && typoDomain.endsWith(tPattern)) {
                    const correctBase = correctDomain.slice(0, -cPattern.length);
                    const typoBase = typoDomain.slice(0, -tPattern.length);
                    
                    if (correctBase === typoBase) {
                        const correctTld = correctDomain.substring(correctBase.length);
                        const typoTld = typoDomain.substring(typoBase.length);
                        return [true, `${correctTld} -> ${typoTld}`];
                    }
                }
            }
        }
        return [false, null];
    }

    // ======================================================================
    // 3. タイポ生成とランキング (メイン処理)
    // ======================================================================

    typoGeneratorRanked(rawInput, topN = 20) {
        // 1. 入力サニタイズ
        const domain = this.sanitizeInput(rawInput);
        if (domain.length < 3) return []; 

        const variants = new Map(); // { typo: { causes: Set(), score: 0 } }
        
        const addVariant = (typo, cause) => {
            if (!variants.has(typo)) variants.set(typo, { causes: new Set(), score: 0 });
            variants.get(typo).causes.add(cause);
        };

        // --- A. タイポ候補の生成 ---
        for (let i = 0; i < domain.length; i++) {
            const c = domain[i];
            const char = c; 
            
            // 1. 入力漏れ (Deletion) 
            // ※「ドット抜け」はデータ分析では使うが、生成はしないという要望に対応
            if (c !== '.') {
                addVariant(domain.slice(0, i) + domain.slice(i + 1), "入力漏れ");
            }

            // 2. 二重入力 (Insertion)
            addVariant(domain.slice(0, i) + c + c + domain.slice(i + 1), "二重入力");

            // 3-5. 置換系 (Substitution)
            const subs = new Set();
            // 隣接キー
            const adjacents = this.keyboardAdjacent[char] || '';
            for (let j=0; j<adjacents.length; j++) subs.add({char: adjacents[j], cause: "隣接キー誤打"});
            
            // ホモグリフ
            const homoglyphs = this.homoglyphsForGenerator[char] || [];
            for (const g of homoglyphs) subs.add({char: g, cause: "ホモグリフ（視覚類似文字）"});
            
            // 対称キー
            for (const [a, b] of this.symmetricKeyPairs) {
                if (c === a) subs.add({char: b, cause: "左右対称キー誤打"});
                else if (c === b) subs.add({char: a, cause: "左右対称キー誤打"});
            }

            for(const sub of subs) {
                 addVariant(domain.slice(0, i) + sub.char + domain.slice(i + 1), sub.cause);
            }
            
            // 6. 入力順序ミス (Transposition)
            if (i < domain.length - 1) {
                addVariant(domain.slice(0, i) + domain[i + 1] + domain[i] + domain.slice(i + 2), "入力順序ミス");
            }
        }
        
        // 7. TLDミス生成
        const parts = domain.split('.');
        if (parts.length > 1) {
            // 単純化のため、末尾のパーツをチェック
            const currentTld = parts[parts.length - 1];
            // もし .co.jp のような2階層TLDなら、後ろ2つを見る必要があるが
            // ここでは簡易的にPythonコードのロジックに合わせる
            const fullTld = parts.length >= 2 && (parts[parts.length-2] === 'co' || parts[parts.length-2] === 'ne' || parts[parts.length-2] === 'go') 
                            ? parts.slice(-2).join('.') 
                            : currentTld;

            const TLD_GEN_PAIRS = {
                'jp': ['co.jp'], 'co.jp': ['jp', 'com', 'ne.jp', 'go.jp'],
                'com': ['co.jp'], 'net': ['com', 'co.jp']
            };

            // baseDomainの計算
            let baseDomain = domain.slice(0, -fullTld.length);
            if (baseDomain.endsWith('.')) baseDomain = baseDomain.slice(0, -1);

            if (TLD_GEN_PAIRS[fullTld]) {
                for (const altTld of TLD_GEN_PAIRS[fullTld]) {
                    addVariant(`${baseDomain}.${altTld}`, "TLDミス");
                }
            }
        }

        // ------------------------------------
        // B. スコアリングとランキング
        // ------------------------------------
        
        const rankedResults = [];

        for (const [typo, { causes }] of variants.entries()) {
            if (typo === domain) continue;
            
            // 無効なドメイン形式なら除外
            if (!this.isValidDomainFormat(typo)) continue;

            let finalScore = 0;
            const distance = this._damerauLevenshteinDistance(domain, typo);
            
            // 距離が遠すぎるものは除外 (PythonコードではDL4まで許容)
            if (distance > 4) continue; 

            const [c1, c2] = this.identifySingleReplacement(domain, typo);
            const isDl1Error = (c1 !== '' || c2 !== ''); 
            const [isTldM, tldDiffStr] = this.isTldMismatch(domain, typo);

            for (const cause of causes) {
                let W_individual = 0.0;
                let key = null;
                const weights = this.individualWeights[cause];
                if (!weights) continue;
                
                // --- 重み計算ロジック ---

                if (cause === "TLDミス" && isTldM) {
                    key = tldDiffStr;
                    W_individual = weights[key] || 0.0;
                    finalScore += W_individual * 5; // Pythonコードの最終版に合わせて *5
                }
                else if (cause === "入力順序ミス") {
                    const transposedPair = this.getTransposedPair(domain, typo);
                    if (transposedPair) {
                        const [k1, k2] = transposedPair;
                        key = `${k1} ${k2} -> ${k2} ${k1}`;
                        W_individual = weights[key] || 0.0;
                        finalScore += W_individual;
                    }
                } 
                else if (isDl1Error) {
                    // DL=1系 (置換、挿入、削除)
                    if (c1 || c2) { 
                        if (c1 === '（空）') key = '（空）' + c2; // 挿入/二重入力
                        else if (c2 === '（空）') key = c1 + '（空）'; // 削除/入力漏れ
                        else key = c1 + c2; // 置換
                        
                        W_individual = weights[key] || 0.0;
                        
                        // 逆方向のペアもチェック (置換のみ)
                        if (W_individual === 0.0 && c1 && c2 && c1 !== '（空）' && c2 !== '（空）') {
                            W_individual = weights[c2 + c1] || 0.0;
                        }
                    }

                    // ベーススコア加算
                    finalScore += W_individual;

                    // 位置ボーナス計算 (Pythonの logic 移植)
                    let iStart = -1;
                    let posChar = '';
                    let tempCause = cause;

                    // 単一操作の特定
                    // JSで簡易diffを行う (identifySingleReplacementで既に文字は特定済)
                    if (c1 && c1 !== '（空）') { // 削除 or 置換
                        iStart = domain.indexOf(c1); // ※簡易的。厳密にはdiffが必要だが予測生成ではループ順iを使える
                        // ただしここでは variants ループ内なので i がない。
                        // 文字列検索で代用 (複数ある場合は最初の位置になる制限あり)
                        posChar = c1;
                    } else if (c2 && c2 !== '（空）') { // 挿入
                        // typo内のどこに挿入されたか
                        // domain: example, typo: exxample -> inserted x at index 2
                        // 簡易ロジック: 一致しない最初の場所を探す
                        for(let k=0; k<typo.length; k++) {
                            if (domain[k] !== typo[k]) {
                                iStart = k;
                                posChar = c2;
                                break;
                            }
                        }
                    }

                    if (iStart !== -1 && posChar && posChar.length === 1) {
                         const L = domain.length;
                         const iRelativeEnd = L - 1 - iStart;
                         const charLower = posChar; 
                         const freqCount = this.positionalFreqs[tempCause]?.[charLower]?.[iRelativeEnd] || 0;
                         const positionBonusValue = freqCount / this.totalDl1Count;
                         finalScore += positionBonusValue * this.kPositionBoost;
                    }
                } 
            } // end for causes
            
            // 複合ミスのペナルティ
            if (causes.size > 1) finalScore *= 0.5; 
            // ゼロ防止の微小加算
            if (causes.size > 0) finalScore += 0.0000001;

            const costEstimate = this.extractTldAndCost(typo);
            const finalCauses = isTldM ? "TLDミス" : [...causes].sort().join('・');

            rankedResults.push({
                typo: typo,
                causes: finalCauses,
                score: parseFloat(finalScore.toFixed(7)),
                distance: distance,
                cost: costEstimate
            });
        }

        // ソート (スコア降順 > 距離昇順)
        rankedResults.sort((a, b) => {
            if (b.score !== a.score) return b.score - a.score;
            return a.distance - b.distance;
        });

        // 最後のフィルタリング (1文字ラベルの除外など)
        const finalRankedResults = rankedResults.filter(r => {
            const labels = r.typo.split('.');
            // TLD以外のラベルで1文字のものがあれば除外 (例: a.co.jp)
            const hasSingleCharLabel = labels.slice(0, -1).some(label => label.length === 1);
            return !hasSingleCharLabel;
        });

        return finalRankedResults.slice(0, topN);
    }
}