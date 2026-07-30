import numpy as np

from plot_loss_ablation import metric_matrix, per_class_matrix


RESULTS = {
    "gru": {
        "uniform": {
            "hybrid": {
                "recall": 0.81,
                "f1": 0.82,
                "per_class_recall": {
                    "ul_flood": 0.71,
                    "dl_flood": 0.72,
                    "burst": 0.73,
                    "roq": 0.74,
                },
            }
        },
        "benign": {
            "hybrid": {
                "recall": 0.91,
                "f1": 0.92,
                "per_class_recall": {
                    "ul_flood": 0.81,
                    "dl_flood": 0.82,
                    "burst": 0.83,
                    "roq": 0.84,
                },
            }
        },
    },
    "lstm": {
        "uniform": {
            "hybrid": {
                "recall": 0.85,
                "f1": 0.86,
                "per_class_recall": {
                    "ul_flood": 0.75,
                    "dl_flood": 0.76,
                    "burst": 0.77,
                    "roq": 0.78,
                },
            }
        },
        "benign": {
            "hybrid": {
                "recall": 0.95,
                "f1": 0.96,
                "per_class_recall": {
                    "ul_flood": 0.85,
                    "dl_flood": 0.86,
                    "burst": 0.87,
                    "roq": 0.88,
                },
            }
        },
    },
}


def test_metric_matrix_preserves_architecture_and_variant_order():
    actual = metric_matrix(RESULTS, "recall")

    np.testing.assert_allclose(actual, [[81.0, 91.0], [85.0, 95.0]])


def test_per_class_matrix_preserves_class_and_variant_order():
    actual = per_class_matrix(RESULTS, "gru")

    np.testing.assert_allclose(
        actual,
        [[71.0, 81.0], [72.0, 82.0], [73.0, 83.0], [74.0, 84.0]],
    )
