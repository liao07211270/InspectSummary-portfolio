"""
產生 v9 版面驗證用的示意圖片（假資料，見 src/mock_data_v9.py）。
2026-08-07依醫師會議需求，匯出格式從PDF改成PNG。

用法：
    python scripts/gen_report_v9.py --out output/v9_demo.png
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.mock_data_v9 import generate_v9_view_model
from src.report_html_v9 import generate_v9_report_html

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--patient", default="王○○")
    ap.add_argument("--out", default="output/v9_demo.png")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    vm = generate_v9_view_model(seed=args.seed, days=args.days, start=args.start, patient=args.patient)
    generate_v9_report_html(vm, args.out)
