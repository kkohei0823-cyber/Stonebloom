# -*- coding: utf-8 -*-
"""
lab_stats.py
==========================================================
検証環境（verify_lab.py）用の統計ユーティリティ。外部ライブラリ不要。

基本単位は「ペア」: 同じ対局シードで先手/後手を入れ替えた2局。1ペアのスコアは
A側の2局の平均（勝ち=1, 引き分け=0.5, 負け=0 → 0/0.25/0.5/0.75/1のいずれか）。
先手有利や盤面の偶然が両側に同じだけ効くため、1局ずつ数えるより分散が小さい。
統計はすべてこのペアスコアを標本として計算する（ペア内の2局は独立ではないため、
局数で割るとCIが実際より狭く出てしまう）。
"""

import math


def mean_ci(samples, z=1.96):
    """標本平均と正規近似の信頼区間 (mean, lo, hi)。"""
    n = len(samples)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    m = sum(samples) / n
    if n == 1:
        return m, 0.0, 1.0
    var = sum((x - m) ** 2 for x in samples) / (n - 1)
    half = z * math.sqrt(var / n)
    return m, max(0.0, m - half), min(1.0, m + half)


def score_to_elo(score):
    score = min(max(score, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(1.0 / score - 1.0)


def sprt_llr(samples, s0, s1):
    """一般化SPRT（正規近似）の対数尤度比。H0: 期待スコア=s0 / H1: 期待スコア=s1。
    チェスエンジン開発（fishtest等）で使われているペア単位のGSPRTと同じ近似。"""
    n = len(samples)
    if n < 2:
        return 0.0
    m = sum(samples) / n
    var = sum((x - m) ** 2 for x in samples) / (n - 1)
    if var <= 1e-12:
        # 全ペア同スコア: 分散0。平均がどちらに近いかだけで大きく傾ける
        var = 1e-4
    return n * (s1 - s0) * (2 * m - s0 - s1) / (2 * var)


def sprt_bounds(alpha=0.05, beta=0.05):
    """(下限, 上限)。LLRが下限以下でH0採択、上限以上でH1採択。"""
    return math.log(beta / (1 - alpha)), math.log((1 - beta) / alpha)


def sprt_decision(samples, s0, s1, alpha=0.05, beta=0.05):
    """"H1" / "H0" / None(継続) と LLR を返す。"""
    llr = sprt_llr(samples, s0, s1)
    lo, hi = sprt_bounds(alpha, beta)
    if llr >= hi:
        return "H1", llr
    if llr <= lo:
        return "H0", llr
    return None, llr


def summarize(samples):
    m, lo, hi = mean_ci(samples)
    return {
        "pairs": len(samples),
        "score": round(m, 4), "ci95": [round(lo, 4), round(hi, 4)],
        "elo": round(score_to_elo(m), 1) if samples else None,
    }
