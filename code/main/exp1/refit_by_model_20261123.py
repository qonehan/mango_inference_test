#!/usr/bin/env python3
"""2-1회 최종(43시간) 모델별 S8 재추정 (2026-11-23).

analyze_exp2_1_choice_20261121.py의 모델별 S8에서 GLM-5.3이 발산(뉴턴법, 계수 1e11)하고 DeepSeek의
결측 표시 계수가 비정상(−27.5)이었다. 원인은 거의 상수인 가동률(up30)·결측 표시(stats_missing)로 보인다.
두 변수를 빼고 감쇠 뉴턴법 + 작은 L2(1e-3)로(6변수: 실효 입력가·출력가·TTFT·처리 속도·도구 오류율·구조화 오류율) 다시 맞춘다.
반사실 계산에서 Mango 가동률은 동료 수준(100), 결측 없음으로 두므로 두 변수를 빼도 계산 대상이 같다.

사용: python refit_by_model_20261123.py --run <2-1 run 폴더> --out <refit.json>
출력 형식은 원래 분석 JSON의 eligible.by_model[mk]["S8_eff1h_split"]와 같다(반사실 스크립트 입력용).
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import analyze_exp2_1_choice_20261121 as A  # noqa: E402

FEATS6 = ["log_ein1h", "log_pout", "log_ttft", "log_tps", "tool_err", "so_err"]
RIDGE = 1e-3     # 표준화 척도의 작은 L2(요청 수 대비). GLM-5.3 발산 방지용, 다른 모델 계수에는 영향 거의 없음


def loglik(X, M, y, b):
    u = np.where(M, X @ b, -np.inf)
    return float((u[np.arange(len(y)), y] - np.logaddexp.reduce(u, axis=1)).sum())


def fit_safe(X, M, y, iters=60):
    """감쇠 뉴턴법: 벌점 로그우도가 늘 때만 한 걸음 받아들이고, 아니면 걸음을 반으로 줄인다."""
    N, K, p = X.shape
    lam = RIDGE * N
    b = np.zeros(p)
    f = loglik(X, M, y, b) - 0.5 * lam * b @ b
    for _ in range(iters):
        u = np.where(M, X @ b, -np.inf)
        u -= u.max(1, keepdims=True)
        w = np.exp(u)
        P = w / w.sum(1, keepdims=True)
        xbar = (P[..., None] * X).sum(1)
        g = (X[np.arange(N), y] - xbar).sum(0) - lam * b
        D = X - xbar[:, None, :]
        H = -np.einsum("nk,nki,nkj->ij", P, D, D) - lam * np.eye(p)
        step = np.linalg.solve(H, g)
        t = 1.0
        while t > 1e-4:
            nb = b - t * step
            nf = loglik(X, M, y, nb) - 0.5 * lam * nb @ nb
            if nf >= f - 1e-9:
                break
            t /= 2
        if t <= 1e-4:
            break
        b, f = nb, nf
        if np.abs(t * step).max() < 1e-8:
            break
    return b, loglik(X, M, y, b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--boot", type=int, default=50)
    a = ap.parse_args()
    rng = np.random.default_rng(20261123)
    snaps = A.load_public(a.run)
    reqs = A.load_requests(a.run)
    ch, _ = A.build(reqs, snaps, eligible_only=True)
    out = {"eligible": {"by_model": {}}, "feats": FEATS6}
    for mk in A.MODELS:
        cm = [c for c in ch if c["mk"] == mk]
        X, M, y = A.tensor(cm, FEATS6)
        # 선택된 엔드포인트가 후보 행에 없으면 y가 0으로 남는다 → 그런 선택은 뺀다
        ok = np.array([any(r["e"] == c["chosen"] for r in c["rows"]) for c in cm])
        X, M, y = X[ok], M[ok], y[ok]
        cm = [c for c, o in zip(cm, ok) if o]
        Xs, mu, sd = A.standardize(X, M)
        b, ll = fit_safe(Xs, M, y)
        ll0 = float(A.ll_uniform(M).sum())
        hours = np.array([c["hour"] for c in cm])
        uh = np.unique(hours)
        idx_by_h = {h: np.where(hours == h)[0] for h in uh}
        bs = []
        for _ in range(a.boot):
            pick = rng.choice(uh, size=len(uh), replace=True)
            idx = np.concatenate([idx_by_h[h] for h in pick])
            bb, _ = fit_safe(Xs[idx], M[idx], y[idx], iters=25)
            bs.append(bb / sd)
        ci = np.percentile(np.array(bs), [2.5, 97.5], axis=0).T.tolist()
        r = {"feats": FEATS6, "n": int(len(y)), "dropped_chosen_not_in_rows": int((~ok).sum()), "sd": sd.tolist(), "mean_candidates": float(M.sum(1).mean()),
             "coef_raw": (b / sd).tolist(), "coef_std": b.tolist(), "boot_ci95_raw": ci,
             "mcfadden_r2": 1 - ll / ll0}
        out["eligible"]["by_model"][mk] = {"S8_eff1h_split": r}
        print(mk, f"R2={r['mcfadden_r2']:.3f} n={r['n']} cand={r['mean_candidates']:.1f} |",
              ", ".join(f"{f}={c:+.2f}[{lo:+.2f},{hi:+.2f}]" for f, c, (lo, hi) in zip(FEATS6, r["coef_raw"], ci)))
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
