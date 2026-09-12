# packages/ml-pipeline/scripts/evaluate_model.py
"""Generate a detailed evaluation report for a trained model: confusion
matrix, feature importance, one-vs-rest ROC curves, and a text classification
report. Run after train_xgboost.py.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")  # headless: this runs in CI and from the CLI, never a GUI session
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.metrics import auc, classification_report, confusion_matrix, roc_curve
from sklearn.preprocessing import label_binarize

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "Data" / "processed"
MODELS_DIR = Path(__file__).resolve().parents[1] / "models" / "trained"
EVAL_DIR = Path(__file__).resolve().parents[1] / "Data" / "evaluation"

CLASS_NAMES = ["Normal", "Watch", "Moderate", "Severe", "Extreme"]


def plot_confusion_matrix(y_test, y_pred, out_path: Path) -> None:
    cm = confusion_matrix(y_test, y_pred, labels=list(range(5)))
    fig, ax = plt.subplots(figsize=(6, 5))
    sns.heatmap(
        cm, annot=True, fmt="d", cmap="Blues", xticklabels=CLASS_NAMES, yticklabels=CLASS_NAMES, ax=ax
    )
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title("Drought severity — confusion matrix (2-week-ahead prediction)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_feature_importance(model, feature_names, out_path: Path) -> None:
    importances = model.feature_importances_
    order = np.argsort(importances)[::-1]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.barh(
        [feature_names[i] for i in order][::-1],
        [importances[i] for i in order][::-1],
        color="#2b6cb0",
    )
    ax.set_xlabel("Importance (gain-based)")
    ax.set_title("Feature importance — drought XGBoost")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_roc_curves(model, X_test, y_test, out_path: Path) -> None:
    y_bin = label_binarize(y_test, classes=list(range(5)))
    proba = model.predict_proba(X_test)

    fig, ax = plt.subplots(figsize=(6, 5))
    for i, name in enumerate(CLASS_NAMES):
        if y_bin[:, i].sum() == 0:
            continue  # class absent from this test split — cannot draw a curve
        fpr, tpr, _ = roc_curve(y_bin[:, i], proba[:, i])
        ax.plot(fpr, tpr, label=f"{name} (AUC={auc(fpr, tpr):.2f})")
    ax.plot([0, 1], [0, 1], "k--", linewidth=1)
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("True positive rate")
    ax.set_title("One-vs-rest ROC — drought severity classes")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--model-path", type=Path, default=MODELS_DIR / "drought_xgboost_v1.pkl")
    parser.add_argument("--output-dir", type=Path, default=EVAL_DIR)
    args = parser.parse_args()

    if not args.model_path.exists():
        raise SystemExit(f"{args.model_path} does not exist — run train_xgboost.py first")

    model = joblib.load(args.model_path)
    X_test = pd.read_csv(args.data_dir / "X_test.csv")
    y_test = pd.read_csv(args.data_dir / "y_test.csv").iloc[:, 0]
    y_pred = model.predict(X_test)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    plot_confusion_matrix(y_test, y_pred, args.output_dir / "confusion_matrix.png")
    plot_feature_importance(model, list(X_test.columns), args.output_dir / "feature_importance.png")
    plot_roc_curves(model, X_test, y_test, args.output_dir / "roc_curves.png")

    report = classification_report(
        y_test, y_pred, labels=list(range(5)), target_names=CLASS_NAMES, zero_division=0
    )
    (args.output_dir / "classification_report.txt").write_text(report)

    print(report)
    print(f"Evaluation artifacts written to {args.output_dir}")


if __name__ == "__main__":
    main()
