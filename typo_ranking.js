/**
 * @fileoverview Pythonのドメインタイポ予測ロジックをJavaScriptに移植。
 * W_individual, 位置ボーナス, TLDミス処理を含むスコアリングを再現します。
 * data.json (Pythonで生成) が必要です。
 */

class TypoRanker {
    constructor(data) {
        // Pythonからエクスポートされたデータと定数を格納
        this.keyboardAdjacent = data.keyboard_adjacent;
        this.symmetricKeyPairs = data.symmetric_key_pairs; 
        this.homoglyphsForGenerator = data.homoglyphs_for_generator;
        this.individualWeights = data.individual_weights; // W_individual (パターン別重み)
        this.positionalFreqs = data.positional_freqs;     // 位置別頻度データ
        this.totalDl1Count = data.total_dl1_count || 1;    // 位置ボーナス正規化用
        this.kPositionBoost = data.K_POSITION_BOOST || 0.5; // 位置ボーナス増幅係数
        this.tldCosts = data.TLD_COSTS; 
    }

    // ======================================================================
    // ヘルパー関数 (距離計算, 識別, TLD処理)
    // ======================================================================

    /** Levenshtein距離を計算する簡易実装 */
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
                    matrix[i - 1][j] + 1, matrix[i][j - 1] + 1, matrix[i - 1][j - 1] + cost
                );
            }
        }
        return matrix[b.length][a.length];
    }
    
    /** Damerau-Levenshtein距離の近似 (単一転置を1として扱う) */
    _damerauLevenshteinDistance(a, b) {
        const lDist = this._levenshteinDistance(a, b);
        if (lDist === 2 && a.length === b.length) {
            for (let i = 0; i < a.length - 1; i++) {
                if (a[i] === b[i+1] && a[i+1] === b[i]) return 1;
            }
        }
        return lDist;
    }

    /**
     * DL距離1の単一ミス (置換/挿入/削除) の差分文字ペアを特定する。
     * @returns {[string, string] | ['', '']} - [correct_char, typo_char]
     */
    identifySingleReplacement(correct, typo) {
        if (this._levenshteinDistance(correct, typo) !== 1) return ['', '']; 
        
        if (correct.length === typo.length) { 
            const diffs = [];
            for (let i = 0; i < correct.length; i++) {
                if (correct[i] !== typo[i]) diffs.push([correct[i], typo[i]]);
            }
            if (diffs.length === 1) return [diffs[0][0], diffs[0][1]];
        } else if (correct.length === typo.length + 1) { 
            for (let i = 0; i < correct.length; i++) {
                if (correct.slice(0, i) + correct.slice(i + 1) === typo) return [correct[i], '（空）'];
            }
        } else if (correct.length === typo.length - 1) { 
            for (let i = 0; i < typo.length; i++) {
                if (typo.slice(0, i) + typo.slice(i + 1) === correct) return ['（空）', typo[i]];
            }
        }
        return ['', ''];
    }

    /** 転置された文字ペアを返す */
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
    
    /** TLDミス判定と差分文字列の取得 */
    isTldMismatch(correctDomain, typoDomain) {
        if (!correctDomain.includes('.') || !typoDomain.includes('.')) return [false, null];
        const TLD_PAIRS_FOR_ANALYSIS = {
            'jp': ['co.jp'], 'co.jp': ['jp', 'com', 'ne.jp', 'go.jp'],
            'com': ['co.jp'], 'ne.jp': ['co.jp'], 'go.jp': ['co.jp']
        };

        for (const cPattern in TLD_PAIRS_FOR_ANALYSIS) {
            for (const tPattern of TLD_PAIRS_FOR_ANALYSIS[cPattern]) {
                if (correctDomain.endsWith(cPattern) && typoDomain.endsWith(tPattern)) {
                    const correctBasePart = correctDomain.slice(0, -cPattern.length);
                    const typoBasePart = typoDomain.slice(0, -tPattern.length);
                    
                    if (correctBasePart === typoBasePart) {
                        const correctTldPart = correctDomain.substring(correctBasePart.length);
                        const typoTldPart = typoDomain.substring(typoBasePart.length);
                        return [true, `${correctTldPart} -> ${typoTldPart}`];
                    }
                }
            }
        }
        return [false, null];
    }
    
    /** 推定費用を返す */
    extractTldAndCost(domain) {
        for (const tld in this.tldCosts) {
            if (domain.endsWith(tld)) {
                return this.tldCosts[tld];
            }
        }
        return "費用不明";
    }

    // ======================================================================
    // コア機能: タイポ生成とランキング
    // ======================================================================

    typoGeneratorRanked(domain, topN = 20) {
        const variants = new Map(); 
        const domainLower = domain.toLowerCase();
        const L = domain.length;
        
        const addVariant = (typo, cause) => {
            if (!variants.has(typo)) variants.set(typo, { causes: new Set(), score: 0 });
            variants.get(typo).causes.add(cause);
        };

        // --- A. タイポ候補の生成 ---
        for (let i = 0; i < domain.length; i++) {
            const c = domain[i];
            const char = c.toLowerCase();
            
            // 1. 削除/ドット抜け (Deletion)
            addVariant(domain.slice(0, i) + domain.slice(i + 1), c === '.' ? "ドット抜け" : "入力漏れ");

            // 2. 挿入 (二重入力)
            addVariant(domain.slice(0, i) + c + c + domain.slice(i + 1), "二重入力");

            // 3-5. 置換系 (隣接キー、ホモグリフ、対称キー)
            const subs = new Set();
            for (const adj of this.keyboardAdjacent[char] || '') subs.add({char: adj, cause: "隣接キー誤打"});
            for (const g of this.homoglyphsForGenerator[char] || []) subs.add({char: g, cause: "ホモグリフ（視覚類似文字）"});
            for (const [a, b] of this.symmetricKeyPairs) {
                if (c === a) subs.add({char: b, cause: "左右対称キー誤打"});
                else if (c === b) subs.add({char: a, cause: "左右対称キー誤打"});
            }

            for(const sub of subs) {
                 addVariant(domain.slice(0, i) + sub.char + domain.slice(i + 1), sub.cause);
                 // スペルミス（認知ミス）もDL=1の置換として生成するが、ここでは他の原因でカバーされない場合にスコアがゼロになることで対応
            }
            
            // 6. 入力順序ミス (Transposition)
            if (i < domain.length - 1) {
                addVariant(domain.slice(0, i) + domain[i + 1] + domain[i] + domain.slice(i + 2), "入力順序ミス");
            }
        }
        
        // 7. TLDミス
        const [baseDomain, ...tlds] = domain.split('.');
        const currentTld = tlds.join('.');
        const TLD_PAIRS_FOR_GENERATION = {
            'jp': ['co.jp'], 'co.jp': ['jp', 'com', 'ne.jp', 'go.jp'],
            'com': ['co.jp'], 'net': ['com', 'co.jp']
        };

        if (currentTld in TLD_PAIRS_FOR_GENERATION) {
            for (const altTld of TLD_PAIRS_FOR_GENERATION[currentTld]) {
                addVariant(`${baseDomain}.${altTld}`, "TLDミス");
            }
        }

        // ------------------------------------
        // B. スコアリングとランキング
        // ------------------------------------
        
        const rankedResults = [];

        for (const [typo, { causes }] of variants.entries()) {
            if (typo === domain) continue;
            
            let finalScore = 0;
            const distance = this._damerauLevenshteinDistance(domain, typo);
            if (distance > 4) continue; 

            const [c1, c2] = this.identifySingleReplacement(domain, typo);
            const isDl1Error = (c1 !== '' || c2 !== ''); 
            const [isTldM, tldDiffStr] = this.isTldMismatch(domain, typo);

            for (const cause of causes) {
                let W_individual = 0.0;
                let key = null;
                const weights = this.individualWeights[cause];
                if (!weights) continue;
                
                // --- TLDミス (x10 増幅) ---
                if (cause === "TLDミス" && isTldM) {
                    key = tldDiffStr;
                    W_individual = weights[key] || 0.0;
                    finalScore += W_individual * 10; 
                }

                // --- 入力順序ミス ---
                else if (cause === "入力順序ミス") {
                    const transposedPair = this.getTransposedPair(domain, typo);
                    if (transposedPair) {
                        const [k1, k2] = transposedPair;
                        key = `${k1} ${k2} -> ${k2} ${k1}`;
                        W_individual = weights[key] || 0.0;
                        finalScore += W_individual;
                    }
                } 
                
                // --- DL=1 ミス (位置ボーナス加算) ---
                else if (isDl1Error) {
                    // 1. W_individual の取得 (JSONキーは文字列)
                    if (c1 || c2) { 
                        if (c1 === '（空）') key = '（空）' + c2;
                        else if (c2 === '（空）') key = c1 + '（空）';
                        else key = c1 + c2; // 置換ミス
                        
                        W_individual = weights[key] || 0.0;
                        
                        // 逆順チェック (置換系のみ)
                        if (W_individual === 0.0 && c1 && c2 && c1 !== '（空）' && c2 !== '（空）') {
                            W_individual = weights[c2 + c1] || 0.0;
                        }
                    }

                    // 2. 位置ボーナス (positionBonusValue) の計算
                    let iStart = -1;
                    let posChar = '';
                    
                    if (c1 && c1 !== '（空）') { // 削除 or 置換
                        iStart = domain.indexOf(c1);
                        posChar = c1;
                    } else if (c2 && c2 !== '（空）') { // 挿入
                        // 挿入された文字の元のドメイン上の位置 (src_i) を特定
                        if (domain.length === typo.length - 1) { 
                            for (let i = 0; i < typo.length; i++) {
                                if (typo.slice(0, i) + typo.slice(i + 1) === domain) {
                                     iStart = i; 
                                     posChar = c2;
                                     break;
                                }
                            }
                        }
                    }

                    if (iStart !== -1 && posChar && posChar.length === 1) {
                         const iRelativeEnd = L - 1 - iStart;
                         const charLower = posChar.toLowerCase();
                         
                         const freqCount = this.positionalFreqs[cause]?.[charLower]?.[iRelativeEnd] || 0;
                         
                         const positionBonusValue = freqCount / this.totalDl1Count;
                         finalScore += positionBonusValue * this.kPositionBoost;
                    }
                    
                    finalScore += W_individual;
                } 

            } // End for causes
            
            // 2. 複合ミス ペナルティ
            if (causes.size > 1) finalScore *= 0.5; 

            // 3. ボーナス
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

        rankedResults.sort((a, b) => {
            if (b.score !== a.score) return b.score - a.score;
            return a.distance - b.distance;
        });

        const finalRankedResults = rankedResults.filter(r => {
            const labels = r.typo.split('.');
            
            // 1. カンマ/スラッシュを含むタイポは引き続き除外
            if (r.typo.includes(',') || r.typo.includes('/') || r.typo.includes('-') || r.typo.includes('^')) {
                return false;
            }
            
            // 2. 1文字ラベルのチェック (TLD規則に基づき、いずれかのラベルが1文字の場合は除外)
            // 例: a.co.jp -> [a, co, jp] -> 'a' の長さが 1 なので除外
            // ただし、最後のラベル (TLD) が 'c' や 'j' などの1文字の場合は、それは別のルールなので、ここでは全ラベルをチェック。
            const hasSingleCharLabel = labels.some(label => label.length === 1);
            
            return !hasSingleCharLabel;
        });

        return finalRankedResults.slice(0, topN);
    }
}